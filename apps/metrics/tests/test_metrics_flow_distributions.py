import datetime

import pytest

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory, ReviewFactory
from apps.activity.models import PullRequest, SizeBucket
from apps.catalog.factories import IdentityFactory, PersonFactory
from apps.catalog.services import set_setting
from apps.dashboards.formatting import format_duration
from apps.metrics.calculators.base import PeriodContext
from apps.metrics.models import Cohort, ScopeType
from apps.metrics.registry import get_metric
from apps.metrics.types import MetricValue, Scope

pytestmark = pytest.mark.django_db

UNRESTRICTED = ScopeFilter(unrestricted=True, project_ids=None)
PERIOD_START = datetime.date(2026, 6, 1)
PERIOD_END = datetime.date(2026, 6, 30)
UTC = datetime.UTC
# Every `unit="duration"` metric reports seconds — the unit `format_duration`, `charts.js` and the
# exports all read it in. The oracles below are written in hours and scaled by this.
HOUR = 3600.0


def _scope(scope_type: str = ScopeType.GLOBAL, scope_id: int | None = None) -> Scope:
    return Scope(scope_type=scope_type, scope_id=scope_id, access=UNRESTRICTED)


def _period_ctx(scope: Scope | None = None) -> PeriodContext:
    return PeriodContext(
        scope=scope or _scope(), cohort=Cohort.ALL, date_from=PERIOD_START, date_to=PERIOD_END
    )


def _merged_pr(**kwargs):
    kwargs.setdefault("state", PullRequest.State.MERGED)
    kwargs.setdefault("merged_at", datetime.datetime(2026, 6, 15, tzinfo=UTC))
    return PullRequestFactory(**kwargs)


def test_lead_time_p50_is_the_hand_computed_median():
    # ready -> merged: 10h and 30h -> median 20h.
    _merged_pr(
        ready_for_review_at=datetime.datetime(2026, 6, 10, 0, tzinfo=UTC),
        merged_at=datetime.datetime(2026, 6, 10, 10, tzinfo=UTC),
    )
    _merged_pr(
        ready_for_review_at=datetime.datetime(2026, 6, 11, 0, tzinfo=UTC),
        merged_at=datetime.datetime(2026, 6, 12, 6, tzinfo=UTC),
    )

    metric_def = get_metric("lead_time_p50")
    assert metric_def.calculator.period(_period_ctx()) == MetricValue(20.0 * HOUR, 2)


def test_lead_time_p90_is_the_hand_computed_p90_with_interpolation():
    # ready -> merged: 10h, 20h, 30h, 40h, 50h. p90 rank = 0.9 * (5-1) = 3.6 -> interpolate between
    # the 4th (40h) and 5th (50h) sorted values: 40*0.4 + 50*0.6 = 46h.
    for hours in (10, 20, 30, 40, 50):
        _merged_pr(
            ready_for_review_at=datetime.datetime(2026, 6, 10, 0, tzinfo=UTC),
            merged_at=datetime.datetime(2026, 6, 10, 0, tzinfo=UTC) + datetime.timedelta(hours=hours),
        )

    metric_def = get_metric("lead_time_p90")
    assert metric_def.calculator.period(_period_ctx()) == MetricValue(46.0 * HOUR, 5)


def test_lead_time_drops_prs_with_no_ready_for_review_at_and_is_none_when_all_lack_it():
    _merged_pr(ready_for_review_at=None)

    metric_def = get_metric("lead_time_p50")
    assert metric_def.calculator.period(_period_ctx()) == MetricValue.empty()


def test_cycle_time_p50_from_first_commit_to_merge():
    _merged_pr(
        first_commit_at=datetime.datetime(2026, 6, 10, 0, tzinfo=UTC),
        merged_at=datetime.datetime(2026, 6, 11, 0, tzinfo=UTC),
    )

    metric_def = get_metric("cycle_time_p50")
    assert metric_def.calculator.period(_period_ctx()) == MetricValue(24.0 * HOUR, 1)


def test_time_to_first_review_p50_from_ready_to_first_review():
    _merged_pr(
        ready_for_review_at=datetime.datetime(2026, 6, 10, 0, tzinfo=UTC),
        first_review_at=datetime.datetime(2026, 6, 10, 5, tzinfo=UTC),
    )

    metric_def = get_metric("time_to_first_review_p50")
    assert metric_def.calculator.period(_period_ctx()) == MetricValue(5.0 * HOUR, 1)


def test_time_to_first_review_p90_is_the_hand_computed_p90_with_interpolation():
    # Same shape as lead_time_p90's oracle: 10h, 20h, 30h, 40h, 50h -> p90 = 46h.
    for hours in (10, 20, 30, 40, 50):
        _merged_pr(
            ready_for_review_at=datetime.datetime(2026, 6, 10, 0, tzinfo=UTC),
            first_review_at=datetime.datetime(2026, 6, 10, 0, tzinfo=UTC) + datetime.timedelta(hours=hours),
        )

    metric_def = get_metric("time_to_first_review_p90")
    assert metric_def.calculator.period(_period_ctx()) == MetricValue(46.0 * HOUR, 5)


def test_pr_size_p50_is_the_hand_computed_median_of_effective_lines():
    # effective_additions + effective_deletions: 10, 20, 30 -> median 20.
    _merged_pr(effective_additions=10, effective_deletions=0)
    _merged_pr(effective_additions=15, effective_deletions=5)
    _merged_pr(effective_additions=20, effective_deletions=10)

    metric_def = get_metric("pr_size_p50")
    assert metric_def.calculator.period(_period_ctx()) == MetricValue(20.0, 3)


def test_pr_size_buckets_match_boundaries_at_exactly_10_100_400_1000():
    # PR_SIZE_BUCKETS = {"XS": 10, "S": 100, "M": 400, "L": 1000}; a size bucket boundary is the
    # first bucket whose limit the PR's line count is strictly below (pr_radar's own
    # `_size_bucket` rule, mirrored here rather than imported since it is a private helper).
    boundary_sizes_and_buckets = [
        (9, SizeBucket.XS),
        (10, SizeBucket.S),
        (99, SizeBucket.S),
        (100, SizeBucket.M),
        (399, SizeBucket.M),
        (400, SizeBucket.L),
        (999, SizeBucket.L),
        (1000, SizeBucket.XL),
    ]
    for lines, expected_bucket in boundary_sizes_and_buckets:
        _merged_pr(
            effective_additions=lines,
            effective_deletions=0,
            size_bucket=expected_bucket,
        )

    metric_def = get_metric("pr_size_buckets")
    items = {item.label: int(item.value) for item in metric_def.calculator.breakdown(_period_ctx())}
    assert items == {SizeBucket.XS: 1, SizeBucket.S: 2, SizeBucket.M: 2, SizeBucket.L: 2, SizeBucket.XL: 1}


def test_reviewer_response_p50_from_ready_for_review_at_to_the_reviewer_first_review():
    reviewer = IdentityFactory(person=PersonFactory())
    pr = _merged_pr(ready_for_review_at=datetime.datetime(2026, 6, 10, 0, tzinfo=UTC))
    ReviewFactory(
        pull_request=pr, reviewer=reviewer, submitted_at=datetime.datetime(2026, 6, 10, 8, tzinfo=UTC)
    )

    metric_def = get_metric("reviewer_response_p50")
    person_scope = _scope(ScopeType.PERSON, reviewer.person_id)
    assert metric_def.calculator.period(_period_ctx(person_scope)) == MetricValue(8.0 * HOUR, 1)


def test_reviewer_response_p50_falls_back_to_created_at_when_ready_for_review_at_is_missing():
    reviewer = IdentityFactory(person=PersonFactory())
    pr = _merged_pr(ready_for_review_at=None, created_at=datetime.datetime(2026, 6, 10, 0, tzinfo=UTC))
    ReviewFactory(
        pull_request=pr, reviewer=reviewer, submitted_at=datetime.datetime(2026, 6, 10, 6, tzinfo=UTC)
    )

    metric_def = get_metric("reviewer_response_p50")
    person_scope = _scope(ScopeType.PERSON, reviewer.person_id)
    assert metric_def.calculator.period(_period_ctx(person_scope)) == MetricValue(6.0 * HOUR, 1)


def test_reviewer_response_p50_is_person_level_only():
    from apps.metrics.registry import MetricNotAvailableAtLevel, require_available_at_level

    metric_def = get_metric("reviewer_response_p50")
    with pytest.raises(MetricNotAvailableAtLevel):
        require_available_at_level(metric_def, ScopeType.GLOBAL)


def test_review_load_share_with_three_reviewers_and_top_n_two():
    set_setting("REVIEW_LOAD_TOP_N", 2)
    reviewer_a = IdentityFactory(person=PersonFactory())
    reviewer_b = IdentityFactory(person=PersonFactory())
    reviewer_c = IdentityFactory(person=PersonFactory())
    submitted_at = datetime.datetime(2026, 6, 10, tzinfo=UTC)
    for reviewer, count in ((reviewer_a, 5), (reviewer_b, 3), (reviewer_c, 2)):
        for _ in range(count):
            ReviewFactory(reviewer=reviewer, submitted_at=submitted_at)

    metric_def = get_metric("review_load_share")
    # top 2 (a=5, b=3) of 10 total -> 0.8
    assert metric_def.calculator.period(_period_ctx()) == MetricValue(0.8, 10)


def test_duration_metrics_render_as_the_hours_they_measure():
    """Regression: the calculators returned hours while every reader treats a duration as
    seconds, so a 3-hour median lead time rendered as "3 seconds"."""
    _merged_pr(
        ready_for_review_at=datetime.datetime(2026, 6, 10, 0, tzinfo=UTC),
        first_review_at=datetime.datetime(2026, 6, 10, 3, tzinfo=UTC),
        merged_at=datetime.datetime(2026, 6, 10, 5, 30, tzinfo=UTC),
    )

    lead_time = get_metric("lead_time_p50").calculator.period(_period_ctx())
    first_review = get_metric("time_to_first_review_p50").calculator.period(_period_ctx())

    assert format_duration(lead_time.value, locale="en") == "5h 30m"
    assert format_duration(first_review.value, locale="en") == "3h 0m"
