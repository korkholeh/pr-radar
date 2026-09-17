import datetime

import pytest
from django.utils import timezone

from apps.catalog.models import Organization, Repository
from apps.connections.models import GitHubConnection
from apps.github_sync.models import SyncRun


@pytest.mark.django_db
def test_stats_default_to_empty_dict():
    run = SyncRun.objects.create(trigger=SyncRun.Trigger.CLI)
    assert run.stats == {}
    assert run.stats_by_connection == {}


@pytest.mark.django_db
def test_repositories_m2m_accepts_zero_and_many():
    run = SyncRun.objects.create(trigger=SyncRun.Trigger.CLI)
    assert run.repositories.count() == 0

    org = Organization.objects.create(login="acme", type=Organization.Type.ORG, github_id="O_1")
    connection = GitHubConnection.objects.create(name="conn", kind=GitHubConnection.Kind.FINE_GRAINED_PAT)
    repo_a = Repository.objects.create(
        organization=org, connection=connection, name="a", full_name="acme/a", github_id="R_1"
    )
    repo_b = Repository.objects.create(
        organization=org, connection=connection, name="b", full_name="acme/b", github_id="R_2"
    )
    run.repositories.add(repo_a, repo_b)
    assert run.repositories.count() == 2


@pytest.mark.django_db
def test_ordering_puts_newest_run_first():
    older = SyncRun.objects.create(trigger=SyncRun.Trigger.CLI)
    newer = SyncRun.objects.create(trigger=SyncRun.Trigger.CLI)
    assert list(SyncRun.objects.all()) == [newer, older]


@pytest.mark.django_db
def test_error_log_blank_by_default():
    run = SyncRun.objects.create(trigger=SyncRun.Trigger.CLI)
    assert run.error_log == ""


@pytest.mark.django_db
def test_explicit_started_at_is_honoured():
    explicit = timezone.now() - datetime.timedelta(days=1)
    run = SyncRun.objects.create(trigger=SyncRun.Trigger.CLI, started_at=explicit)
    run.refresh_from_db()
    assert run.started_at == explicit
