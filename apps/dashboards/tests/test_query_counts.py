"""T18: `assertNumQueries` for every list view (acceptance criterion #2). Each page test pins an
exact query count for a minimal fixture; a companion test at the row-builder level doubles the row
count and asserts the growth matches the documented per-row cost — `metrics.compute_many()`
batches counter/ratio metrics into one widened `DailyRollup` query regardless of entity count
(plan §6), but falls back to the per-scope path for distribution/state metrics (`lead_time_p50`,
`violations_open`, `pr_size_p50`, `reviewer_response_p50`, `churn_21d`) at PROJECT/REPO scope,
which is a real, accepted per-row cost there, not an N+1 to eliminate — `recent_prs` is the only
table with zero growth since it is a single ORM queryset, never per-row `compute()` calls.

T11 (RISKS row 10, profiling on `seed_demo --scale large`) cut that per-row cost further:
`project_rows()`/`repository_rows()`/`people_rows()` now call `compute_many(...,
include_series=False)`, so a distribution/state metric's fallback pays only its value + previous
query, never the per-bucket series a table row never reads — the numbers below reflect that, not a
regression in coverage.

T11 continuation: at PERSON scope specifically, every one of `PEOPLE_METRIC_KEYS`'s five
distribution/state metrics now also has a batched `period_by_person`/`at_date_by_person`
implementation (`apps/metrics/calculators/{flow,quality,adoption}.py`), so `people_rows()`'s
distribution/state slice costs a fixed number of queries regardless of how many people are in
scope — the per-person growth tests below assert 0, not a per-row multiple, for that table only."""

from __future__ import annotations

import datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory
from apps.catalog.factories import IdentityFactory, PersonFactory, ProjectFactory, RepositoryFactory
from apps.catalog.models import Person
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
    # +1 vs pre-T2 for `period_has_pull_requests()`'s `EXISTS` query (plan T2); down from 276
    # (T11 round 1): `rows.py`'s `project_rows()`/`repository_rows()`/`people_rows()` now call
    # `compute_many(..., include_series=False)`, skipping the per-bucket series query for their
    # distribution/state metrics, and `policy.selectors.violations_in_scope()` skips a redundant
    # `pull_request__in=<unfiltered subquery>` for an unrestricted caller — both profiling-driven
    # (`scripts/profile_dashboard.py` against `seed_demo --scale large`, DECISIONS p11/implement).
    # Down again from 234 (T11 round 2): `kpis.build_kpi_row()`'s `ai_set`/`non_ai_set`/
    # `secondary_set` and `charts.py`'s `_build_pr_size_distribution`/`_build_churn_rework`/
    # `_build_violations_by_rule` (the last one ~31x, once per outer bucket) all now pass
    # `include_series=False` too — none of them ever read `.series`, only `.value`/`.breakdown`.
    # +1 again (T11 continuation): `tables.build_table_context()` now paginates `recent_prs` at
    # the SQL level (a `COUNT(*)` via `recent_pr_row_count()` plus a `LIMIT`/`OFFSET` select)
    # instead of materializing every matching PR just to show page 1 — a net win on wall time
    # despite the extra query (profiling: ~15,000 fewer `PullRequest` rows built per render at
    # `--scale large`), see DECISIONS p11/implement for the measured before/after.
    with django_assert_num_queries(162):
        client.get(reverse("dashboards:overview") + f"?{PERIOD_QS}")


@pytest.mark.django_db
def test_project_query_count(client, lead_user, django_assert_num_queries):
    client.force_login(lead_user)
    project, _repository = _seed_project(1)
    # See test_overview_query_count for both the +1s and the T11 reductions.
    with django_assert_num_queries(163):
        client.get(reverse("dashboards:project", args=[project.pk]) + f"?{PERIOD_QS}")


@pytest.mark.django_db
def test_repository_query_count(client, lead_user, django_assert_num_queries):
    client.force_login(lead_user)
    _project, repository = _seed_project(1)
    # See test_overview_query_count for both the +1s and the T11 reductions.
    with django_assert_num_queries(157):
        client.get(reverse("dashboards:repository", args=[repository.pk]) + f"?{PERIOD_QS}")


@pytest.mark.django_db
def test_day_query_count(client, lead_user, django_assert_num_queries):
    client.force_login(lead_user)
    _seed_project(1)
    with django_assert_num_queries(25):
        client.get(reverse("dashboards:overview") + "?mode=day&day=2026-08-05")


@pytest.mark.django_db
def test_projects_index_query_count(client, lead_user, django_assert_num_queries):
    client.force_login(lead_user)
    _seed_project(1)
    # +2 vs pre-T2: this fixture's PRs sit on a fixed August date, so the default 30-day preset
    # (anchored on the real "today") sees a genuinely empty period — both
    # `period_has_pull_requests()` and `nothing_ever_synced()` run (plan T2), unlike the pages
    # pinned with `PERIOD_QS`, which land on the seeded month and only pay the first query. Down
    # from 27 (T11): see test_overview_query_count for the `include_series=False` and
    # `violations_in_scope()` reductions, which apply here too (`projects_index` renders
    # `project_rows()`).
    with django_assert_num_queries(17):
        client.get(reverse("dashboards:projects_index"))


@pytest.mark.django_db
def test_repositories_index_query_count(client, lead_user, django_assert_num_queries):
    client.force_login(lead_user)
    _seed_project(1)
    # See test_projects_index_query_count, including the T11 reduction.
    with django_assert_num_queries(17):
        client.get(reverse("dashboards:repositories_index"))


@pytest.mark.django_db
def test_pull_requests_index_query_count(client, lead_user, django_assert_num_queries):
    """Round 1 review MINOR: `/prs/` had no page-level bound (only its row builder,
    `test_pr_rows.py`, did), so a regression in the page assembly around it would not fail
    the build."""
    client.force_login(lead_user)
    _seed_project(1)
    # +1: see test_overview_query_count.
    with django_assert_num_queries(12):
        client.get(reverse("dashboards:pull_requests_index") + f"?{PERIOD_QS}")


@pytest.mark.django_db
def test_reviews_page_query_count(client, lead_user, django_assert_num_queries):
    client.force_login(lead_user)
    _seed_project(1)
    # +1: see test_overview_query_count.
    with django_assert_num_queries(22):
        client.get(reverse("dashboards:reviews") + f"?{PERIOD_QS}")


@pytest.mark.django_db
def test_person_page_query_count(client, lead_user, django_assert_num_queries):
    client.force_login(lead_user)
    _seed_project(1)
    person = Person.objects.get()
    # +1 vs pre-T2, then the T11 round-2 reduction: see test_overview_query_count. Also
    # `person.py::build_comparison()`'s `person_set`/`project_set`/`org_set` now pass
    # `include_series=False` — `ComparisonRow` only reads `.value`/`.sample_size`/
    # `.previous_value`/`.below_min_sample`, never `.series`.
    with django_assert_num_queries(171):
        client.get(reverse("dashboards:person", args=[person.id]) + f"?{PERIOD_QS}")


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
    fixture, so they can't tell a template-level N+1 apart from the row-builder level. This closes
    that gap by rendering the real Overview page (2 people, then 6) and asserting the growth
    matches `test_people_table_query_count_grows_by_the_known_per_person_cost`'s own number, so a
    future template-level N+1 in the people table is attributable instead of silently absorbed
    into "the page got slower".

    T11 continuation: `compute_many()`'s distribution/state metrics used to fall back to one
    `compute()` call per person (10 queries/person, `include_series=False` already down from a
    per-bucket series); every metric `people_rows()` needs now has a `period_by_person`/
    `at_date_by_person` batched implementation (`apps/metrics/calculators/{flow,quality,
    adoption}.py`), so the whole distribution/state slice costs a fixed number of queries
    regardless of how many people are in scope — the growth from 2 to 6 people is 0, not 40."""
    client.force_login(lead_user)
    _project, repository = _seed_project(1)
    _add_people(repository, 1)  # 2 people total
    with CaptureQueriesContext(connection) as small:
        client.get(reverse("dashboards:overview") + f"?{PERIOD_QS}")

    _add_people(repository, 4)  # 6 people total
    with CaptureQueriesContext(connection) as large:
        client.get(reverse("dashboards:overview") + f"?{PERIOD_QS}")

    assert len(large) - len(small) == 0


# -- per-row growth, isolated at the row-builder level -------------------------------------------


@pytest.mark.django_db
def test_people_table_query_count_grows_by_the_known_per_person_cost():
    """`compute_many()` batches `PEOPLE_METRIC_KEYS`'s counter/ratio metrics into one query. Its
    five distribution/state metrics (`violations_open`, `lead_time_p50`, `pr_size_p50`,
    `reviewer_response_p50`, `churn_21d`) used to fall back to a per-person `compute()` call, 10
    queries/person (T11: `include_series=False` had already cut it from a per-bucket series down
    to just value + previous) — a real, `assertNumQueries`-pinned cost that scaled with the person
    count.

    T11 continuation (RISKS row 10, session 6's profiling on `--scale large`: this was the
    dominant remaining cost on the Overview page, ~9s of ~14s): each of those five metrics now has
    a `period_by_person`/`at_date_by_person` batched implementation
    (`apps/metrics/calculators/{flow,quality,adoption}.py`) that computes every requested person's
    value in 2 queries total (value, previous) regardless of person count —
    `services._other_results_by_person()` uses it whenever `scope_type == PERSON`,
    `include_series=False` and every requested metric has one, which `PEOPLE_METRIC_KEYS` does.
    The row count therefore no longer moves this query count at all: asserted as exactly 0 growth,
    not a shrunk multiple, so a metric added to `PEOPLE_METRIC_KEYS` without a batched
    implementation (silently falling back to the old per-person cost) fails this test instead of
    reappearing unnoticed."""
    scope = Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=ScopeFilter(unrestricted=True))
    params = _params()

    PersonFactory.create_batch(2)
    bump_data_version()
    with CaptureQueriesContext(connection) as small:
        rows.people_rows(scope, params)

    PersonFactory.create_batch(4)
    bump_data_version()
    with CaptureQueriesContext(connection) as large:
        rows.people_rows(scope, params)

    assert len(large) - len(small) == 0


@pytest.mark.django_db
def test_repositories_table_query_count_grows_by_the_known_per_repository_cost():
    """`PROJECT_REPOSITORY_METRIC_KEYS` includes two distribution/state metrics
    (`violations_open`, `lead_time_p50`) alongside four batched counter/ratio ones — measured at 4
    queries/repository for this fixture's date range and granularity (T11: down from 16 once
    `compute_many(..., include_series=False)` drops the per-bucket series for the two
    distribution/state metrics)."""
    access = ScopeFilter(unrestricted=True)
    params = _params()

    RepositoryFactory.create_batch(2)
    bump_data_version()
    with CaptureQueriesContext(connection) as small:
        rows.repository_rows(access, params)

    RepositoryFactory.create_batch(4)
    bump_data_version()
    with CaptureQueriesContext(connection) as large:
        rows.repository_rows(access, params)

    assert len(large) - len(small) == 4 * 4


@pytest.mark.django_db
def test_projects_table_query_count_grows_by_the_known_per_project_cost():
    """See test_repositories_table_query_count_grows_by_the_known_per_repository_cost — same
    metric set, same T11 reduction from 16 to 4 queries/project."""
    access = ScopeFilter(unrestricted=True)
    params = _params()

    ProjectFactory.create_batch(2)
    bump_data_version()
    with CaptureQueriesContext(connection) as small:
        rows.project_rows(access, params)

    ProjectFactory.create_batch(4)
    bump_data_version()
    with CaptureQueriesContext(connection) as large:
        rows.project_rows(access, params)

    assert len(large) - len(small) == 4 * 4


@pytest.mark.django_db
def test_recent_prs_table_query_count_does_not_grow_with_row_count():
    """The one table backed by a real queryset, not `compute()` — a single annotated query
    regardless of how many PRs match, so doubling the row count costs nothing extra."""
    repository = RepositoryFactory()
    scope = Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=ScopeFilter(unrestricted=True))
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
