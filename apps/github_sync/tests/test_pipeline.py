import datetime

import pytest

import apps.github_sync.services as services_module
from apps.activity.models import AIStatus, PullRequest
from apps.ai_detection.factories import DetectionRuleFactory
from apps.ai_detection.models import AISignal, Confidence, Detector, Tool
from apps.catalog.factories import RepositoryFactory
from apps.connections.factories import GitHubConnectionFactory
from apps.connections.services import set_token
from apps.github_sync.models import SyncRun
from apps.github_sync.services import run_sync
from apps.github_sync.tests.conftest import mock_graphql_sequence
from apps.policy.factories import AIPolicyFactory
from apps.policy.models import PolicyViolation

TOKEN = "ghp_secrettokenvalue0123456789"


def _rate_limit_block():
    return {"remaining": 4900, "resetAt": "2026-01-01T01:00:00Z", "cost": 1}


def _pr_list_page(number, updated_at):
    return {
        "data": {
            "repository": {
                "pullRequests": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {
                            "id": f"PR_{number}",
                            "number": number,
                            "title": "A pull request",
                            "body": "",
                            "state": "OPEN",
                            "isDraft": False,
                            "baseRefName": "main",
                            "headRefName": f"feature/{number}",
                            "createdAt": "2026-01-01T09:00:00Z",
                            "updatedAt": updated_at,
                            "mergedAt": None,
                            "closedAt": None,
                            "additions": 1,
                            "deletions": 0,
                            "changedFiles": 1,
                            "mergeCommit": None,
                            "labels": {"nodes": []},
                            "author": {"login": "octocat"},
                            "mergedBy": None,
                        }
                    ],
                }
            },
            "rateLimit": _rate_limit_block(),
        }
    }


def _empty_nested_page(container_key):
    return {
        "data": {
            "node": {container_key: {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}},
            "rateLimit": _rate_limit_block(),
        }
    }


def _one_pr_sequence(number=1, updated_at="2026-01-05T09:00:00Z"):
    return [
        _pr_list_page(number, updated_at),
        _empty_nested_page("commits"),
        _empty_nested_page("reviews"),
        _empty_nested_page("reviewThreads"),
        _empty_nested_page("files"),
        _empty_nested_page("timelineItems"),
    ]


def _make_repository(**kwargs):
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    return RepositoryFactory(connection=connection, **kwargs)


@pytest.mark.django_db(transaction=True)
def test_hook_runs_once_per_synced_pull_request(monkeypatch):
    """process_pull_request is imported into services.py at module level (`from
    apps.github_sync.pipeline import process_pull_request`) and called through services.py's own
    module-level name, not pipeline's — so the fake must be installed there to actually intercept
    the orchestrator, not on apps.github_sync.pipeline (which services.py no longer looks at once
    it has its own bound reference). transaction=True is required: sync_repository's per-PR
    transaction.atomic() only triggers on_commit for a real commit, and the default django_db
    marker wraps the whole test in one outer, never-committed transaction."""
    calls = []
    monkeypatch.setattr(services_module, "process_pull_request", lambda pk: calls.append(pk))
    repository = _make_repository(full_name="acme/widget")
    mock_graphql_sequence(*_one_pr_sequence())

    run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    pull_request = PullRequest.objects.get(repository=repository)
    assert calls == [pull_request.id]


@pytest.mark.django_db(transaction=True)
def test_raising_hook_does_not_roll_back_the_pull_requests_rows_but_is_recorded(monkeypatch):
    def _boom(pk):
        raise RuntimeError("boom")

    monkeypatch.setattr(services_module, "process_pull_request", _boom)
    repository = _make_repository(full_name="acme/widget")
    mock_graphql_sequence(*_one_pr_sequence())

    run = run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    run.refresh_from_db()
    assert PullRequest.objects.filter(repository=repository).exists()
    assert run.status == SyncRun.Status.PARTIAL
    assert run.stats["errors"] == 1
    assert "acme/widget" in run.error_log
    assert "post-processing hook failed" in run.error_log


def _policy_before_fixture_prs(**kwargs):
    return AIPolicyFactory(effective_from=datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC), **kwargs)


@pytest.mark.django_db(transaction=True)
def test_hook_evaluates_policy_and_leaves_the_violation_on_the_pull_request():
    _policy_before_fixture_prs(require_disclosure=True)
    repository = _make_repository(full_name="acme/widget")
    mock_graphql_sequence(*_one_pr_sequence())

    run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    pull_request = PullRequest.objects.get(repository=repository)
    violation = PolicyViolation.objects.get(
        pull_request=pull_request, rule_code=PolicyViolation.RuleCode.DISCLOSURE_MISSING
    )
    assert violation.status == PolicyViolation.Status.OPEN


@pytest.mark.django_db(transaction=True)
def test_second_sync_creates_no_duplicate_violation():
    _policy_before_fixture_prs(require_disclosure=True)
    repository = _make_repository(full_name="acme/widget")
    mock_graphql_sequence(*_one_pr_sequence())
    run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    mock_graphql_sequence(*_one_pr_sequence())
    run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    pull_request = PullRequest.objects.get(repository=repository)
    assert PolicyViolation.objects.filter(pull_request=pull_request).count() == 1


@pytest.mark.django_db(transaction=True)
def test_evaluate_failure_inside_the_hook_is_still_contained(monkeypatch):
    import apps.github_sync.pipeline as pipeline_module

    def _boom(pk):
        raise RuntimeError("evaluate boom")

    monkeypatch.setattr(pipeline_module, "evaluate_pull_request", _boom)
    monkeypatch.setattr(services_module, "process_pull_request", pipeline_module.process_pull_request)
    repository = _make_repository(full_name="acme/widget")
    mock_graphql_sequence(*_one_pr_sequence())

    run = run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    run.refresh_from_db()
    assert PullRequest.objects.filter(repository=repository).exists()
    assert run.status == SyncRun.Status.PARTIAL
    assert run.stats["errors"] == 1


@pytest.mark.django_db(transaction=True)
def test_hook_resolves_identities_then_derives():
    repository = _make_repository(full_name="acme/widget")
    mock_graphql_sequence(*_one_pr_sequence())

    run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    pull_request = PullRequest.objects.get(repository=repository)
    assert pull_request.author.person is not None
    assert pull_request.author.person.display_name == "octocat"
    assert pull_request.ready_for_review_at is not None
    assert pull_request.review_rounds == 1


@pytest.mark.django_db(transaction=True)
def test_derive_failure_inside_the_hook_is_still_contained(monkeypatch):
    import apps.github_sync.pipeline as pipeline_module

    def _boom(pk):
        raise RuntimeError("derive boom")

    monkeypatch.setattr(pipeline_module, "derive_pull_request", _boom)
    monkeypatch.setattr(services_module, "process_pull_request", pipeline_module.process_pull_request)
    repository = _make_repository(full_name="acme/widget")
    mock_graphql_sequence(*_one_pr_sequence())

    run = run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    run.refresh_from_db()
    assert PullRequest.objects.filter(repository=repository).exists()
    assert run.status == SyncRun.Status.PARTIAL
    assert run.stats["errors"] == 1


@pytest.mark.django_db(transaction=True)
def test_second_sync_leaves_derived_fields_identical():
    repository = _make_repository(full_name="acme/widget")
    mock_graphql_sequence(*_one_pr_sequence())
    run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])
    pull_request = PullRequest.objects.get(repository=repository)
    fields = ["size_bucket", "ready_for_review_at", "is_hotfix", "is_revert", "review_rounds"]
    before = {field: getattr(pull_request, field) for field in fields}

    mock_graphql_sequence(*_one_pr_sequence())
    run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    pull_request.refresh_from_db()
    after = {field: getattr(pull_request, field) for field in fields}
    assert before == after


@pytest.mark.django_db(transaction=True)
def test_hook_resolves_ai_status_and_writes_signals():
    DetectionRuleFactory(
        detector=Detector.PR_AUTHOR,
        pattern="octocat",
        tool=Tool.OTHER,
        confidence=Confidence.HIGH,
    )
    repository = _make_repository(full_name="acme/widget")
    mock_graphql_sequence(*_one_pr_sequence())

    run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    pull_request = PullRequest.objects.get(repository=repository)
    assert pull_request.ai_status == AIStatus.AI_EXPLICIT
    assert AISignal.objects.filter(pull_request=pull_request).count() == 1


@pytest.mark.django_db(transaction=True)
def test_second_sync_creates_no_duplicate_ai_signal():
    DetectionRuleFactory(
        detector=Detector.PR_AUTHOR,
        pattern="octocat",
        tool=Tool.OTHER,
        confidence=Confidence.HIGH,
    )
    repository = _make_repository(full_name="acme/widget")
    mock_graphql_sequence(*_one_pr_sequence())
    run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    mock_graphql_sequence(*_one_pr_sequence())
    run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    pull_request = PullRequest.objects.get(repository=repository)
    assert AISignal.objects.filter(pull_request=pull_request).count() == 1


@pytest.mark.django_db(transaction=True)
def test_detect_failure_inside_the_hook_is_still_contained(monkeypatch):
    import apps.github_sync.pipeline as pipeline_module

    def _boom(pk):
        raise RuntimeError("detect boom")

    monkeypatch.setattr(pipeline_module, "detect_pull_request", _boom)
    monkeypatch.setattr(services_module, "process_pull_request", pipeline_module.process_pull_request)
    repository = _make_repository(full_name="acme/widget")
    mock_graphql_sequence(*_one_pr_sequence())

    run = run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    run.refresh_from_db()
    assert PullRequest.objects.filter(repository=repository).exists()
    assert run.status == SyncRun.Status.PARTIAL
    assert run.stats["errors"] == 1
