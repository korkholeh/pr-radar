"""T17: a credential failure on one connection quarantines only that connection, and a
connection whose primary rate budget is low yields rounds to the other instead of stalling
the run (PLAN.md §3/§7, ADR 0003)."""

import httpx
import pytest

from apps.activity.models import PullRequest
from apps.catalog.factories import RepositoryFactory
from apps.connections.factories import GitHubConnectionFactory
from apps.connections.models import GitHubConnection
from apps.connections.services import set_token
from apps.github_sync.models import SyncRun
from apps.github_sync.services import run_sync
from apps.github_sync.tests.conftest import mock_graphql_responses
from apps.github_sync.tests.test_sync import (
    _commits_page,
    _empty_nested_page,
    _files_page,
    _pr_list_page,
    _rate_limit_block,
    _reviews_page,
)

TOKEN = "ghp_secrettokenvalue0123456789"


def _connection_with_repo(full_name):
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    repository = RepositoryFactory(connection=connection, full_name=full_name)
    return connection, repository


def _pr_sequence(number, sha, *, remaining=4900):
    responses = [_pr_list_page(number, "2026-01-05T09:00:00Z"), _commits_page([sha])]
    responses[0]["data"]["rateLimit"] = _rate_limit_block()
    responses.append(_reviews_page())
    responses.append(_empty_nested_page("reviewThreads"))
    responses.append(_files_page())
    timeline = _empty_nested_page("timelineItems")
    timeline["data"]["rateLimit"] = {**_rate_limit_block(), "remaining": remaining}
    responses.append(timeline)
    return [httpx.Response(200, json=body) for body in responses]


@pytest.mark.django_db
def test_401_quarantines_only_its_own_connection():
    connection_a, repo_a = _connection_with_repo("acme/a")
    connection_b, repo_b = _connection_with_repo("acme/b")

    responses = [httpx.Response(401, json={"message": "Bad credentials"})]
    responses += _pr_sequence(1, "shab1")

    mock_graphql_responses(*responses)

    run = run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/a", "acme/b"])

    run.refresh_from_db()
    connection_a.refresh_from_db()
    connection_b.refresh_from_db()

    assert connection_a.status == GitHubConnection.Status.INVALID
    assert connection_b.status != GitHubConnection.Status.INVALID
    assert PullRequest.objects.filter(repository=repo_a).count() == 0
    assert PullRequest.objects.filter(repository=repo_b).count() == 1
    assert run.status == SyncRun.Status.PARTIAL

    stats_a = run.stats_by_connection[str(connection_a.id)]
    stats_b = run.stats_by_connection[str(connection_b.id)]
    assert stats_a["errors"] == 1
    assert stats_a["repositories"] == 0
    assert stats_b["errors"] == 0
    assert stats_b["repositories"] == 1


@pytest.mark.django_db
def test_undecryptable_token_quarantines_only_its_own_connection():
    """A token that can no longer be decrypted (FIELD_ENCRYPTION_KEYS rotated/lost after it was
    stored — docs/GITHUB_CONNECTIONS.md's documented recovery scenario) must not crash the whole
    run — only the affected connection is skipped, and the run still finishes with a terminal
    status instead of being stuck at running."""
    connection_a, repo_a = _connection_with_repo("acme/a")
    connection_b, repo_b = _connection_with_repo("acme/b")

    connection_a.token_encrypted = b"not-a-valid-fernet-token"
    connection_a.save(update_fields=["token_encrypted"])

    responses = _pr_sequence(1, "shab1")
    mock_graphql_responses(*responses)

    run = run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/a", "acme/b"])

    run.refresh_from_db()
    assert run.status == SyncRun.Status.PARTIAL
    assert PullRequest.objects.filter(repository=repo_a).count() == 0
    assert PullRequest.objects.filter(repository=repo_b).count() == 1

    stats_a = run.stats_by_connection[str(connection_a.id)]
    assert stats_a["skipped"] == 1
    assert stats_a["errors"] == 1
    assert TOKEN not in run.error_log


@pytest.mark.django_db
def test_403_sso_quarantines_only_its_own_connection():
    connection_a, repo_a = _connection_with_repo("acme/a")
    connection_b, repo_b = _connection_with_repo("acme/b")

    sso_url = "https://github.com/orgs/acme/sso?authorization_request=abc"
    responses = [
        httpx.Response(
            403, json={"message": "SSO required"}, headers={"X-GitHub-SSO": f"required; url={sso_url}"}
        )
    ]
    responses += _pr_sequence(1, "shab1")

    mock_graphql_responses(*responses)

    run = run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/a", "acme/b"])

    connection_a.refresh_from_db()
    connection_b.refresh_from_db()

    assert connection_a.status == GitHubConnection.Status.DEGRADED
    assert PullRequest.objects.filter(repository=repo_a).count() == 0
    assert PullRequest.objects.filter(repository=repo_b).count() == 1
    assert run.status == SyncRun.Status.PARTIAL


@pytest.mark.django_db
def test_low_budget_connection_yields_to_the_other_instead_of_stalling():
    connection_a, repo_a = _connection_with_repo("acme/a")
    connection_b, repo_b = _connection_with_repo("acme/b")
    repo_a2 = RepositoryFactory(connection=connection_a, full_name="acme/a2")
    repo_b2 = RepositoryFactory(connection=connection_b, full_name="acme/b2")

    responses = (
        _pr_sequence(1, "sha-a1", remaining=100)  # connection A drops below RATE_LIMIT_MIN_REMAINING
        + _pr_sequence(2, "sha-b1", remaining=4900)
        + _pr_sequence(3, "sha-b2", remaining=4800)  # round 2: A is skipped, B keeps going
        + _pr_sequence(4, "sha-a2", remaining=4700)  # round 3: A is the only one left, forced through
    )
    mock_graphql_responses(*responses)

    run = run_sync(
        SyncRun.Trigger.CLI,
        repo_full_names=["acme/a", "acme/a2", "acme/b", "acme/b2"],
        sleep=lambda seconds: None,
    )

    run.refresh_from_db()
    assert run.status == SyncRun.Status.SUCCESS
    assert PullRequest.objects.filter(repository__in=[repo_a, repo_a2]).count() == 2
    assert PullRequest.objects.filter(repository__in=[repo_b, repo_b2]).count() == 2

    stats_a = run.stats_by_connection[str(connection_a.id)]
    assert stats_a["repositories"] == 2
    assert stats_a["rate_limit_waits"] >= 1
