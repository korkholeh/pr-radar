"""T2: the page-level empty state on Overview/Project/Repository/Person, the index pages and
Reviews — empty period vs a real zero vs nothing-ever-synced, and scope isolation (plan §1)."""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.factories import UserProjectAccessFactory
from apps.activity.factories import PullRequestFactory
from apps.catalog.factories import IdentityFactory, PersonFactory, ProjectFactory, RepositoryFactory
from apps.github_sync.factories import SyncRunFactory
from apps.github_sync.models import SyncRun
from apps.metrics.rollups import rebuild
from apps.metrics.services import bump_data_version

PERIOD_QS = "preset=custom&from=2026-08-01&to=2026-08-31&granularity=week"


def _seed_pr_in_august() -> None:
    repository = RepositoryFactory()
    project = ProjectFactory()
    project.repositories.add(repository)
    identity = IdentityFactory(person=PersonFactory())
    PullRequestFactory(
        repository=repository,
        author=identity,
        state="merged",
        created_at=datetime.datetime(2026, 8, 5, tzinfo=datetime.UTC),
        merged_at=datetime.datetime(2026, 8, 10, tzinfo=datetime.UTC),
        last_activity_at=datetime.datetime(2026, 8, 10, tzinfo=datetime.UTC),
    )
    rebuild(datetime.date(2026, 8, 1), datetime.date(2026, 8, 31))
    bump_data_version()


@pytest.mark.django_db
def test_nothing_ever_synced_shows_the_run_a_sync_explanation(client, lead_user):
    client.force_login(lead_user)
    response = client.get(reverse("dashboards:overview") + f"?{PERIOD_QS}")
    content = response.content.decode()
    assert 'data-testid="period-empty-state"' in content
    assert "Nothing has been synced from GitHub yet." in content
    assert reverse("github_sync:sync") in content


@pytest.mark.django_db
def test_synced_but_empty_period_shows_the_widen_period_explanation(client, lead_user):
    SyncRunFactory(status=SyncRun.Status.SUCCESS, finished_at=timezone.now())
    client.force_login(lead_user)
    response = client.get(reverse("dashboards:overview") + f"?{PERIOD_QS}")
    content = response.content.decode()
    assert 'data-testid="period-empty-state"' in content
    assert "No pull requests match this period and filter." in content
    assert "Nothing has been synced" not in content


@pytest.mark.django_db
def test_a_real_non_empty_period_shows_no_empty_state(client, lead_user):
    SyncRunFactory(status=SyncRun.Status.SUCCESS, finished_at=timezone.now())
    _seed_pr_in_august()
    client.force_login(lead_user)
    response = client.get(reverse("dashboards:overview") + f"?{PERIOD_QS}")
    content = response.content.decode()
    assert 'data-testid="period-empty-state"' not in content


@pytest.mark.django_db
def test_projects_index_shows_the_same_empty_state(client, lead_user):
    client.force_login(lead_user)
    response = client.get(reverse("dashboards:projects_index") + f"?{PERIOD_QS}")
    assert 'data-testid="period-empty-state"' in response.content.decode()


@pytest.mark.django_db
def test_reviews_page_shows_the_same_empty_state(client, lead_user):
    client.force_login(lead_user)
    response = client.get(reverse("dashboards:reviews") + f"?{PERIOD_QS}")
    assert 'data-testid="period-empty-state"' in response.content.decode()


@pytest.mark.django_db
def test_restricted_lead_gets_an_explanation_not_a_403_or_blank_page(client, lead_user):
    """A restricted lead whose only project has no data in scope reads as empty, exactly like an
    unrestricted lead with no data — never a permission error and never a silently blank page
    (plan's case taxonomy: permissions paired with a positive case)."""
    SyncRunFactory(status=SyncRun.Status.SUCCESS, finished_at=timezone.now())
    _seed_pr_in_august()  # all data lives in a project the restricted lead cannot see
    own_project = ProjectFactory()
    UserProjectAccessFactory(user=lead_user, project=own_project)
    client.force_login(lead_user)
    response = client.get(reverse("dashboards:overview") + f"?{PERIOD_QS}")
    assert response.status_code == 200
    content = response.content.decode()
    assert 'data-testid="period-empty-state"' in content
    assert "No pull requests match this period and filter." in content


@pytest.mark.django_db
def test_pr_created_before_period_but_merged_inside_it_counts_as_present(client, lead_user):
    """Round 1 review MINOR: KPIs narrow by `merged_at`, so a PR created before the window but
    merged inside it must suppress the empty-period banner even though `created_at` misses."""
    SyncRunFactory(status=SyncRun.Status.SUCCESS, finished_at=timezone.now())
    repository = RepositoryFactory()
    identity = IdentityFactory(person=PersonFactory())
    PullRequestFactory(
        repository=repository,
        author=identity,
        state="merged",
        created_at=datetime.datetime(2026, 7, 20, tzinfo=datetime.UTC),
        merged_at=datetime.datetime(2026, 8, 10, tzinfo=datetime.UTC),
        last_activity_at=datetime.datetime(2026, 8, 10, tzinfo=datetime.UTC),
    )
    rebuild(datetime.date(2026, 8, 1), datetime.date(2026, 8, 31))
    bump_data_version()
    client.force_login(lead_user)
    response = client.get(reverse("dashboards:overview") + f"?{PERIOD_QS}")
    assert 'data-testid="period-empty-state"' not in response.content.decode()


@pytest.mark.django_db
def test_cohort_filter_matching_nothing_shows_the_widen_filter_explanation(client, lead_user):
    """Round 1 review MINOR: a cohort/filter combination that matches nothing must show the empty
    banner even though PRs exist in scope for the period under a different cohort."""
    SyncRunFactory(status=SyncRun.Status.SUCCESS, finished_at=timezone.now())
    _seed_pr_in_august()  # seeded PR has the default (non-AI) ai_status
    client.force_login(lead_user)
    response = client.get(reverse("dashboards:overview") + f"?{PERIOD_QS}&cohort=ai")
    content = response.content.decode()
    assert 'data-testid="period-empty-state"' in content
    assert "No pull requests match this period and filter." in content


@pytest.mark.django_db
def test_day_mode_is_unaffected_by_the_period_empty_state(client, lead_user):
    """Day mode already has its own explained empty states (`day_lists.html`); the new
    period-level check must not also fire there."""
    client.force_login(lead_user)
    response = client.get(reverse("dashboards:overview"), {"mode": "day", "day": "2020-01-01"})
    assert 'data-testid="period-empty-state"' not in response.content.decode()
