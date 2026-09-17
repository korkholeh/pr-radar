"""T18: manage.py sync — filters, --since, --full, the lock and clean error messages."""

import datetime
import io

import pytest
from django.core.management import call_command

from apps.catalog.factories import ProjectFactory, RepositoryFactory
from apps.connections.factories import GitHubConnectionFactory
from apps.connections.services import set_token
from apps.github_sync.models import SyncLock, SyncRun
from apps.github_sync.tests.conftest import mock_graphql_sequence
from apps.github_sync.tests.test_sync import _one_pr_sequence

TOKEN = "ghp_secrettokenvalue0123456789"


def _make_repository(**kwargs):
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    return RepositoryFactory(connection=connection, **kwargs)


@pytest.mark.django_db
def test_repo_filter_selects_only_the_named_repository():
    repository = _make_repository(full_name="acme/widget")
    _make_repository(full_name="acme/other")
    mock_graphql_sequence(*_one_pr_sequence())

    out = io.StringIO()
    call_command("sync", "--repo", "acme/widget", stdout=out)

    run = SyncRun.objects.get()
    assert run.stats["repositories"] == 1
    assert repository.pull_requests.count() == 1


@pytest.mark.django_db
def test_project_filter_selects_only_that_projects_repositories():
    project = ProjectFactory()
    in_project = _make_repository(full_name="acme/in-project")
    project.repositories.add(in_project)
    _make_repository(full_name="acme/outside")
    mock_graphql_sequence(*_one_pr_sequence())

    call_command("sync", "--project", project.slug, stdout=io.StringIO())

    run = SyncRun.objects.get()
    assert run.stats["repositories"] == 1


@pytest.mark.django_db
def test_unknown_repo_fails_with_a_named_error_not_a_traceback():
    err = io.StringIO()
    with pytest.raises(SystemExit):
        call_command("sync", "--repo", "nobody/nothing", stderr=err)
    assert "nobody/nothing" in err.getvalue()
    assert SyncRun.objects.count() == 0


@pytest.mark.django_db
def test_since_overrides_the_watermark():
    repository = _make_repository(
        full_name="acme/widget",
        last_synced_at=datetime.datetime(2026, 1, 10, tzinfo=datetime.UTC),
    )
    mock_graphql_sequence(*_one_pr_sequence(updated_at="2026-01-05T09:00:00Z"))

    call_command("sync", "--repo", "acme/widget", "--since", "2026-01-01", stdout=io.StringIO())

    assert repository.pull_requests.count() == 1


@pytest.mark.django_db
def test_full_starts_from_sync_since():
    repository = _make_repository(
        full_name="acme/widget",
        sync_since=datetime.date(2025, 1, 1),
        last_synced_at=datetime.datetime(2026, 1, 10, tzinfo=datetime.UTC),
    )
    mock_graphql_sequence(*_one_pr_sequence(updated_at="2025-06-01T09:00:00Z"))

    call_command("sync", "--repo", "acme/widget", "--full", stdout=io.StringIO())

    assert repository.pull_requests.count() == 1


@pytest.mark.django_db
def test_second_invocation_while_locked_exits_non_zero_with_a_clear_message():
    run = SyncRun.objects.create(trigger=SyncRun.Trigger.CLI, status=SyncRun.Status.RUNNING)
    SyncLock.objects.create(name="global", sync_run=run)

    err = io.StringIO()
    with pytest.raises(SystemExit):
        call_command("sync", stderr=err)
    assert "already running" in err.getvalue().lower()


@pytest.mark.django_db
def test_output_contains_no_token():
    _make_repository(full_name="acme/widget")
    mock_graphql_sequence(*_one_pr_sequence())

    out = io.StringIO()
    call_command("sync", "--repo", "acme/widget", stdout=out)

    assert TOKEN not in out.getvalue()
