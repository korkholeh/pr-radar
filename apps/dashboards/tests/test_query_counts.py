"""T18: `assertNumQueries` for every list view (acceptance criterion #2). Each page test pins an
exact query count for a minimal fixture; a companion test at the row-builder level doubles the row
count and asserts the growth matches the documented per-row cost — `metrics.compute_many()`
batches counter/ratio metrics into one widened `DailyRollup` query regardless of entity count
(plan §6), but falls back to the per-scope path for distribution/state metrics (`lead_time_p50`,
`violations_open`, `pr_size_p50`, `reviewer_response_p50`, `churn_21d`), which is a real, accepted
per-row cost, not an N+1 to eliminate here — `recent_prs` is the only table with zero growth since
it is a single ORM queryset, never per-row `compute()` calls."""

from __future__ import annotations

import datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.selectors import scope_for_user
from apps.activity.factories import PullRequestFactory
from apps.catalog.factories import IdentityFactory, PersonFactory, ProjectFactory, RepositoryFactory
from apps.catalog.services import get_int
from apps.dashboards import rows
from apps.dashboards.params import DashboardParams
from apps.metrics.models import ScopeType
from apps.metrics.rollups import rebuild
from apps.metrics.services import bump_data_version
from apps.metrics.types import Scope

DATE_FROM = datetime.date(2026, 8, 1)
DATE_TO = datetime.date(2026, 8, 31)
PERIOD_QS = "preset=custom&from=2026-08-01&to=2026-08-31&granularity=week"


@pytest.fixture(autouse=True)
def _warm_settings_cache():
    """`apps/catalog/services.py`'s `AppSetting` cache is process-wide and lazily populated; warm
    it here so every test below pins the query cost of *rendering*, not of a one-time settings
    read that would otherwise land arbitrarily in whichever test runs first."""
    get_int("MIN_SAMPLE")


def _params(**overrides: object) -> DashboardParams:
    base = dict(
        mode="period",
        preset="custom",
        date_from=DATE_FROM,
        date_to=DATE_TO,
        day=DATE_TO,
        granularity="week",
        granularity_is_auto=False,
        cohort="all",
        project_ids=(),
        repository_ids=(),
        q="",
        sort="",
        page=1,
        table="",
    )
    base.update(overrides)
    return DashboardParams(**base)


def _seed_project(n_people: int = 1) -> tuple[ProjectFactory, RepositoryFactory]:
    repository = RepositoryFactory()
    project = ProjectFactory()
    project.repositories.add(repository)
    for _ in range(n_people):
        identity = IdentityFactory(person=PersonFactory())
        PullRequestFactory(
            repository=repository,
            author=identity,
            state="merged",
            created_at=datetime.datetime(2026, 8, 5, tzinfo=datetime.UTC),
            merged_at=datetime.datetime(2026, 8, 10, tzinfo=datetime.UTC),
            last_activity_at=datetime.datetime(2026, 8, 10, tzinfo=datetime.UTC),
        )
    rebuild(DATE_FROM, DATE_TO)
    bump_data_version()
    return project, repository


# -- whole-page pins -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_overview_query_count(client, lead_user, django_assert_num_queries):
    client.force_login(lead_user)
    _seed_project(1)
    with django_assert_num_queries(274):
        client.get(reverse("dashboards:overview") + f"?{PERIOD_QS}")


@pytest.mark.django_db
def test_project_query_count(client, lead_user, django_assert_num_queries):
    client.force_login(lead_user)
    project, _repository = _seed_project(1)
    with django_assert_num_queries(275):
        client.get(reverse("dashboards:project", args=[project.pk]) + f"?{PERIOD_QS}")


@pytest.mark.django_db
def test_repository_query_count(client, lead_user, django_assert_num_queries):
    client.force_login(lead_user)
    _project, repository = _seed_project(1)
    with django_assert_num_queries(257):
        client.get(reverse("dashboards:repository", args=[repository.pk]) + f"?{PERIOD_QS}")


@pytest.mark.django_db
def test_day_query_count(client, lead_user, django_assert_num_queries):
    client.force_login(lead_user)
    _seed_project(1)
    with django_assert_num_queries(24):
        client.get(reverse("dashboards:overview") + "?mode=day&day=2026-08-05")


@pytest.mark.django_db
def test_projects_index_query_count(client, lead_user, django_assert_num_queries):
    client.force_login(lead_user)
    _seed_project(1)
    with django_assert_num_queries(24):
        client.get(reverse("dashboards:projects_index"))


@pytest.mark.django_db
def test_repositories_index_query_count(client, lead_user, django_assert_num_queries):
    client.force_login(lead_user)
    _seed_project(1)
    with django_assert_num_queries(24):
        client.get(reverse("dashboards:repositories_index"))


def _add_people(repository, n_people: int) -> None:
    """Grows only the person count against an existing repository/project — unlike
    `_seed_project()`, which also adds a repository and a project (and would confound a
    person-only growth pin with the `projects`/`repositories` tables' own per-row cost)."""
    for _ in range(n_people):
        identity = IdentityFactory(person=PersonFactory())
        PullRequestFactory(
            repository=repository,
            author=identity,
            state="merged",
            created_at=datetime.datetime(2026, 8, 5, tzinfo=datetime.UTC),
            merged_at=datetime.datetime(2026, 8, 10, tzinfo=datetime.UTC),
            last_activity_at=datetime.datetime(2026, 8, 10, tzinfo=datetime.UTC),
        )
    rebuild(DATE_FROM, DATE_TO)
    bump_data_version()


@pytest.mark.django_db
def test_overview_query_count_grows_by_the_known_per_person_cost_end_to_end(client, lead_user):
    """MINOR (round-1 review): the whole-page pins above all use a 1-person/1-repo/1-project
    fixture, so they can't tell a template-level N+1 apart from the documented, accepted per-row
    `compute_many()` fallback cost `test_people_table_query_count_grows_by_the_known_per_person_cost`
    pins at the row-builder level — a page render is dominated by fixed KPI/chart cost. This closes
    that gap by rendering the real Overview page (2 people, then 6) and asserting the growth is
    exactly that documented per-person cost, so a future template-level N+1 in the people table is
    attributable instead of silently absorbed into "the page got slower"."""
    client.force_login(lead_user)
    _project, repository = _seed_project(1)
    _add_people(repository, 1)  # 2 people total
    with CaptureQueriesContext(connection) as small:
        client.get(reverse("dashboards:overview") + f"?{PERIOD_QS}")

    _add_people(repository, 4)  # 6 people total
    with CaptureQueriesContext(connection) as large:
        client.get(reverse("dashboards:overview") + f"?{PERIOD_QS}")

    assert len(large) - len(small) == 40 * 4


# -- per-row growth, isolated at the row-builder level -------------------------------------------


@pytest.mark.django_db
def test_people_table_query_count_grows_by_the_known_per_person_cost():
    """`compute_many()` batches `PEOPLE_METRIC_KEYS`'s counter/ratio metrics into one query, but
    falls back to a per-person `compute()` for its five distribution/state metrics
    (`violations_open`, `lead_time_p50`, `pr_size_p50`, `reviewer_response_p50`, `churn_21d`) —
    each costing one query per bucket for value, previous and the series. Measured at 40
    queries/person for this fixture's date range and granularity; asserted as an exact multiple so
    a change to that cost (for better or worse) is visible, not silently absorbed."""
    scope = Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=scope_for_user(None))
    params = _params()

    PersonFactory.create_batch(2)
    bump_data_version()
    with CaptureQueriesContext(connection) as small:
        rows.people_rows(scope, params)

    PersonFactory.create_batch(4)
    bump_data_version()
    with CaptureQueriesContext(connection) as large:
        rows.people_rows(scope, params)

    assert len(large) - len(small) == 40 * 4


@pytest.mark.django_db
def test_repositories_table_query_count_grows_by_the_known_per_repository_cost():
    """`PROJECT_REPOSITORY_METRIC_KEYS` includes two distribution/state metrics
    (`violations_open`, `lead_time_p50`) alongside four batched counter/ratio ones — measured at 16
    queries/repository for this fixture's date range and granularity."""
    access = scope_for_user(None)
    params = _params()

    RepositoryFactory.create_batch(2)
    bump_data_version()
    with CaptureQueriesContext(connection) as small:
        rows.repository_rows(access, params)

    RepositoryFactory.create_batch(4)
    bump_data_version()
    with CaptureQueriesContext(connection) as large:
        rows.repository_rows(access, params)

    assert len(large) - len(small) == 16 * 4


@pytest.mark.django_db
def test_projects_table_query_count_grows_by_the_known_per_project_cost():
    access = scope_for_user(None)
    params = _params()

    ProjectFactory.create_batch(2)
    bump_data_version()
    with CaptureQueriesContext(connection) as small:
        rows.project_rows(access, params)

    ProjectFactory.create_batch(4)
    bump_data_version()
    with CaptureQueriesContext(connection) as large:
        rows.project_rows(access, params)

    assert len(large) - len(small) == 16 * 4


@pytest.mark.django_db
def test_recent_prs_table_query_count_does_not_grow_with_row_count():
    """The one table backed by a real queryset, not `compute()` — a single annotated query
    regardless of how many PRs match, so doubling the row count costs nothing extra."""
    repository = RepositoryFactory()
    scope = Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=scope_for_user(None))
    params = _params()

    def _add_prs(n: int) -> None:
        for _ in range(n):
            identity = IdentityFactory(person=PersonFactory())
            PullRequestFactory(
                repository=repository,
                author=identity,
                state="merged",
                created_at=datetime.datetime(2026, 8, 5, tzinfo=datetime.UTC),
                merged_at=datetime.datetime(2026, 8, 10, tzinfo=datetime.UTC),
                last_activity_at=datetime.datetime(2026, 8, 10, tzinfo=datetime.UTC),
            )

    _add_prs(2)
    with CaptureQueriesContext(connection) as small:
        small_rows = rows.recent_pr_rows(scope, params)

    _add_prs(4)
    with CaptureQueriesContext(connection) as large:
        large_rows = rows.recent_pr_rows(scope, params)

    assert len(small_rows) == 2
    assert len(large_rows) == 6
    assert len(large) == len(small)
