import datetime

import pytest

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory
from apps.activity.models import AIStatus, PullRequest
from apps.catalog.factories import ProjectFactory, RepositoryFactory
from apps.catalog.services import set_setting
from apps.metrics.models import DailyRollup, ScopeType
from apps.metrics.rollups import rebuild
from apps.metrics.services import bump_data_version, compute
from apps.metrics.types import Scope

pytestmark = pytest.mark.django_db

UNRESTRICTED = ScopeFilter(unrestricted=True, project_ids=None)
UTC = datetime.UTC


def _scope() -> Scope:
    return Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=UNRESTRICTED)


def _merged(day: datetime.date, **kwargs) -> PullRequest:
    at = datetime.datetime(day.year, day.month, day.day, 10, tzinfo=UTC)
    return PullRequestFactory(state=PullRequest.State.MERGED, merged_at=at, **kwargs)


def test_counter_read_from_rollups_equals_raw_row_count():
    day_from = datetime.date(2026, 6, 10)
    day_to = datetime.date(2026, 6, 16)
    for offset in range(3):
        _merged(day_from + datetime.timedelta(days=offset))
    rebuild(day_from - datetime.timedelta(days=8), day_to)

    result_set = compute(["prs_merged"], _scope(), day_from, day_to)
    result = result_set["prs_merged"]
    assert result.value == 3.0
    assert result.sample_size == 3


def test_restricted_scope_filter_narrows_a_counter_metric_instead_of_reading_the_global_rollup():
    """`DailyRollup` rows are always written under a hardcoded unrestricted access
    (`calculators.base.GLOBAL_SCOPE`), so a restricted `ScopeFilter` must never be answered from them —
    that would silently leak the unrestricted total to a narrowed caller (RISKS row 3)."""
    project_a = ProjectFactory()
    repo_a = RepositoryFactory()
    project_a.repositories.add(repo_a)
    project_b = ProjectFactory()
    repo_b = RepositoryFactory()
    project_b.repositories.add(repo_b)

    day_from = datetime.date(2026, 6, 10)
    day_to = datetime.date(2026, 6, 16)
    _merged(day_from, repository=repo_a)
    _merged(day_from, repository=repo_a)
    _merged(day_from, repository=repo_b)
    rebuild(day_from, day_to)

    restricted = ScopeFilter(unrestricted=False, project_ids=frozenset({project_a.id}))
    scope = Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=restricted)

    result = compute(["prs_merged"], scope, day_from, day_to)["prs_merged"]
    assert result.value == 2.0
    assert result.sample_size == 2


def test_restricted_scope_filter_narrows_a_ratio_metric():
    project_a = ProjectFactory()
    repo_a = RepositoryFactory()
    project_a.repositories.add(repo_a)
    project_b = ProjectFactory()
    repo_b = RepositoryFactory()
    project_b.repositories.add(repo_b)

    day_from = datetime.date(2026, 6, 10)
    day_to = datetime.date(2026, 6, 16)
    _merged(day_from, repository=repo_a, ai_status=AIStatus.AI_EXPLICIT)
    _merged(day_from, repository=repo_a)
    _merged(day_from, repository=repo_b, ai_status=AIStatus.AI_EXPLICIT)
    rebuild(day_from, day_to)

    restricted = ScopeFilter(unrestricted=False, project_ids=frozenset({project_a.id}))
    scope = Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=restricted)

    result = compute(["ai_pr_share"], scope, day_from, day_to)["ai_pr_share"]
    assert result.value == pytest.approx(0.5)
    assert result.sample_size == 2


def test_distribution_ignores_rollups_entirely():
    day_from = datetime.date(2026, 6, 10)
    day_to = datetime.date(2026, 6, 16)
    _merged(day_from, ready_for_review_at=datetime.datetime(2026, 6, 9, 10, tzinfo=UTC))
    rebuild(day_from - datetime.timedelta(days=8), day_to)

    before = compute(["lead_time_p50"], _scope(), day_from, day_to)["lead_time_p50"].value
    DailyRollup.objects.all().delete()
    after = compute(["lead_time_p50"], _scope(), day_from, day_to)["lead_time_p50"].value

    assert before == after
    assert before is not None


def test_previous_period_of_a_7_day_window_is_the_7_days_before_it():
    day_from = datetime.date(2026, 6, 10)
    day_to = datetime.date(2026, 6, 16)
    _merged(day_from - datetime.timedelta(days=1))  # last day of the previous period
    _merged(day_from)  # first day of the current period
    rebuild(day_from - datetime.timedelta(days=8), day_to)

    result = compute(["prs_merged"], _scope(), day_from, day_to)["prs_merged"]
    assert result.value == 1.0
    assert result.previous_value == 1.0


def test_delta_is_none_when_the_previous_period_is_empty():
    day_from = datetime.date(2026, 6, 10)
    day_to = datetime.date(2026, 6, 16)
    _merged(day_from)
    rebuild(day_from - datetime.timedelta(days=8), day_to)

    result = compute(["prs_merged"], _scope(), day_from, day_to)["prs_merged"]
    assert result.previous_value is None
    assert result.delta is None
    assert result.delta_ratio is None


def test_metric_without_data_is_none_and_sample_size_is_zero():
    day_from = datetime.date(2026, 6, 10)
    day_to = datetime.date(2026, 6, 16)

    result = compute(["prs_merged"], _scope(), day_from, day_to)["prs_merged"]
    assert result.value is None
    assert result.sample_size == 0


def test_below_min_sample_flips_at_min_sample():
    set_setting("MIN_SAMPLE", 3)
    day_from = datetime.date(2026, 6, 10)
    day_to = datetime.date(2026, 6, 16)
    for offset in range(2):
        _merged(day_from + datetime.timedelta(days=offset))
    rebuild(day_from, day_to)
    below = compute(["prs_merged"], _scope(), day_from, day_to)["prs_merged"]
    assert below.sample_size == 2
    assert below.below_min_sample is True

    _merged(day_from + datetime.timedelta(days=2))
    rebuild(day_from, day_to)
    bump_data_version()  # a real recompute always bumps, invalidating the cached first read
    at_threshold = compute(["prs_merged"], _scope(), day_from, day_to)["prs_merged"]
    assert at_threshold.sample_size == 3
    assert at_threshold.below_min_sample is False


def test_week_and_month_granularity_bucket_counts():
    day_from = datetime.date(2026, 6, 1)
    day_to = datetime.date(2026, 6, 30)
    rebuild(day_from, day_to)

    week_result = compute(["prs_merged"], _scope(), day_from, day_to, granularity="week")["prs_merged"]
    month_result = compute(["prs_merged"], _scope(), day_from, day_to, granularity="month")["prs_merged"]
    assert len(week_result.series) == 5
    assert len(month_result.series) == 1


def test_compute_query_count_is_bounded_for_a_multi_metric_call(django_assert_num_queries):
    day_from = datetime.date(2026, 6, 10)
    day_to = datetime.date(2026, 6, 16)
    for offset in range(3):
        _merged(day_from + datetime.timedelta(days=offset))
    rebuild(day_from - datetime.timedelta(days=8), day_to)

    keys = ["prs_merged", "prs_opened", "ai_pr_share"]
    # Bound, not an exact count: a cold call still touches the DB for MIN_SAMPLE, the cache TTL
    # setting, DataVersion (get_or_create) and one rollup fetch — independent of how many
    # counter/ratio keys or series buckets are requested, which is the property this guards.
    with django_assert_num_queries(8, exact=False):
        compute(keys, _scope(), day_from, day_to)
