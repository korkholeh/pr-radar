"""T10: `rows.pull_request_rows()` — filters narrow correctly, the violations count annotation
counts only open violations, and the list pays no N+1 (CLAUDE.md `assertNumQueries`)."""

from __future__ import annotations

import datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory
from apps.activity.models import AIStatus
from apps.catalog.factories import IdentityFactory, PersonFactory, RepositoryFactory
from apps.dashboards.params import DashboardParams
from apps.dashboards.pr_filters import PRFilters
from apps.metrics.models import ScopeType
from apps.metrics.types import Scope
from apps.policy.factories import PolicyViolationFactory
from apps.policy.models import PolicyViolation

DATE_FROM = datetime.date(2026, 6, 1)
DATE_TO = datetime.date(2026, 6, 30)
IN_PERIOD = datetime.datetime(2026, 6, 15, tzinfo=datetime.UTC)
OUT_OF_PERIOD = datetime.datetime(2026, 5, 1, tzinfo=datetime.UTC)


def _scope() -> Scope:
    return Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=ScopeFilter(unrestricted=True))


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
        pr_filters=PRFilters(),
    )
    base.update(overrides)
    return DashboardParams(**base)


@pytest.mark.django_db
def test_narrows_by_period_on_created_at():
    repository = RepositoryFactory()
    identity = IdentityFactory()
    in_period = PullRequestFactory(repository=repository, author=identity, created_at=IN_PERIOD)
    PullRequestFactory(repository=repository, author=identity, created_at=OUT_OF_PERIOD)

    from apps.dashboards.rows import pull_request_rows

    row_ids = {row["id"] for row in pull_request_rows(_scope(), _params())}
    assert row_ids == {in_period.id}


@pytest.mark.django_db
def test_narrows_by_state():
    repository = RepositoryFactory()
    identity = IdentityFactory()
    open_pr = PullRequestFactory(repository=repository, author=identity, created_at=IN_PERIOD, state="open")
    PullRequestFactory(repository=repository, author=identity, created_at=IN_PERIOD, state="merged")

    from apps.dashboards.rows import pull_request_rows

    rows = pull_request_rows(_scope(), _params(pr_filters=PRFilters(states=("open",))))
    assert {row["id"] for row in rows} == {open_pr.id}


@pytest.mark.django_db
def test_narrows_by_ai_status():
    repository = RepositoryFactory()
    identity = IdentityFactory()
    ai_pr = PullRequestFactory(
        repository=repository, author=identity, created_at=IN_PERIOD, ai_status=AIStatus.AI_EXPLICIT
    )
    PullRequestFactory(repository=repository, author=identity, created_at=IN_PERIOD, ai_status=AIStatus.NO_AI)

    from apps.dashboards.rows import pull_request_rows

    rows = pull_request_rows(_scope(), _params(pr_filters=PRFilters(ai_statuses=(AIStatus.AI_EXPLICIT,))))
    assert {row["id"] for row in rows} == {ai_pr.id}


@pytest.mark.django_db
def test_narrows_by_tool():
    repository = RepositoryFactory()
    identity = IdentityFactory()
    with_tool = PullRequestFactory(
        repository=repository, author=identity, created_at=IN_PERIOD, ai_tools=["cursor"]
    )
    PullRequestFactory(repository=repository, author=identity, created_at=IN_PERIOD, ai_tools=["copilot"])

    from apps.dashboards.rows import pull_request_rows

    rows = pull_request_rows(_scope(), _params(pr_filters=PRFilters(tools=("cursor",))))
    assert {row["id"] for row in rows} == {with_tool.id}


@pytest.mark.django_db
def test_narrows_by_size_bucket():
    repository = RepositoryFactory()
    identity = IdentityFactory()
    small = PullRequestFactory(repository=repository, author=identity, created_at=IN_PERIOD, size_bucket="S")
    PullRequestFactory(repository=repository, author=identity, created_at=IN_PERIOD, size_bucket="L")

    from apps.dashboards.rows import pull_request_rows

    rows = pull_request_rows(_scope(), _params(pr_filters=PRFilters(size_buckets=("S",))))
    assert {row["id"] for row in rows} == {small.id}


@pytest.mark.django_db
def test_narrows_by_author():
    repository = RepositoryFactory()
    person = PersonFactory()
    identity = IdentityFactory(person=person)
    mine = PullRequestFactory(repository=repository, author=identity, created_at=IN_PERIOD)
    PullRequestFactory(repository=repository, author=IdentityFactory(), created_at=IN_PERIOD)

    from apps.dashboards.rows import pull_request_rows

    rows = pull_request_rows(_scope(), _params(pr_filters=PRFilters(author_ids=(person.pk,))))
    assert {row["id"] for row in rows} == {mine.id}


@pytest.mark.django_db
def test_narrows_by_has_violations():
    repository = RepositoryFactory()
    identity = IdentityFactory()
    with_violation = PullRequestFactory(repository=repository, author=identity, created_at=IN_PERIOD)
    PolicyViolationFactory(pull_request=with_violation, status=PolicyViolation.Status.OPEN)
    PullRequestFactory(repository=repository, author=identity, created_at=IN_PERIOD)

    from apps.dashboards.rows import pull_request_rows

    rows = pull_request_rows(_scope(), _params(pr_filters=PRFilters(has_violations="yes")))
    assert {row["id"] for row in rows} == {with_violation.id}

    rows = pull_request_rows(_scope(), _params(pr_filters=PRFilters(has_violations="no")))
    assert with_violation.id not in {row["id"] for row in rows}


@pytest.mark.django_db
def test_violations_count_counts_only_open():
    repository = RepositoryFactory()
    identity = IdentityFactory()
    pull_request = PullRequestFactory(repository=repository, author=identity, created_at=IN_PERIOD)
    PolicyViolationFactory(pull_request=pull_request, status=PolicyViolation.Status.OPEN)
    PolicyViolationFactory(pull_request=pull_request, status=PolicyViolation.Status.OPEN)
    PolicyViolationFactory(pull_request=pull_request, status=PolicyViolation.Status.WAIVED)

    from apps.dashboards.rows import pull_request_rows

    rows = pull_request_rows(_scope(), _params())
    row = next(row for row in rows if row["id"] == pull_request.id)
    assert row["violations_count"] == 2


@pytest.mark.django_db
def test_churn_ratio_is_none_not_zero_when_unmeasured():
    repository = RepositoryFactory()
    identity = IdentityFactory()
    PullRequestFactory(repository=repository, author=identity, created_at=IN_PERIOD)

    from apps.dashboards.rows import pull_request_rows

    rows = pull_request_rows(_scope(), _params())
    assert rows[0]["churn_ratio"] is None


@pytest.mark.django_db
def test_churn_ratio_reflects_the_settled_churn_result():
    from apps.catalog.services import get_int
    from apps.churn.factories import ChurnResultFactory
    from apps.churn.models import ChurnResult
    from apps.dashboards.rows import pull_request_rows

    repository = RepositoryFactory()
    identity = IdentityFactory()
    pull_request = PullRequestFactory(repository=repository, author=identity, created_at=IN_PERIOD)
    ChurnResultFactory(
        pull_request=pull_request,
        window_days=get_int("CHURN_WINDOW_DAYS"),
        status=ChurnResult.Status.OK,
        churn_ratio=0.42,
    )
    # An error row for a different window must never leak into the export column.
    ChurnResultFactory(
        pull_request=pull_request,
        window_days=get_int("CHURN_WINDOW_DAYS") + 1,
        status=ChurnResult.Status.ERROR,
        churn_ratio=None,
    )

    rows = pull_request_rows(_scope(), _params())
    assert rows[0]["churn_ratio"] == pytest.approx(0.42)


@pytest.mark.django_db
def test_no_n_plus_one():
    repository = RepositoryFactory()
    for _index in range(5):
        pull_request = PullRequestFactory(
            repository=repository, author=IdentityFactory(), created_at=IN_PERIOD
        )
        PolicyViolationFactory(pull_request=pull_request, status=PolicyViolation.Status.OPEN)

    from apps.dashboards.rows import pull_request_rows

    # Warm the process-wide AppSetting cache (`catalog.services._all_setting_rows()`) before
    # either capture: `churn_ratio`'s Subquery reads CHURN_WINDOW_DAYS through it, and an
    # uncached read costs one query exactly once per cache generation, not once per row -- not
    # an N+1, but it would otherwise make the two captures differ by exactly one query
    # regardless of how many PRs are added between them.
    pull_request_rows(_scope(), _params())

    with CaptureQueriesContext(connection) as first:
        pull_request_rows(_scope(), _params())

    for _index in range(5):
        pull_request = PullRequestFactory(
            repository=repository, author=IdentityFactory(), created_at=IN_PERIOD
        )
        PolicyViolationFactory(pull_request=pull_request, status=PolicyViolation.Status.OPEN)

    with CaptureQueriesContext(connection) as second:
        pull_request_rows(_scope(), _params())

    assert len(second.captured_queries) == len(first.captured_queries)
