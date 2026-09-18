import datetime
from zoneinfo import ZoneInfo

import pytest

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory
from apps.activity.models import PullRequest
from apps.catalog.factories import IdentityFactory, PersonFactory
from apps.metrics.calculators.base import DayContext
from apps.metrics.models import Cohort, ScopeType
from apps.metrics.registry import get_metric
from apps.metrics.types import MetricValue, Scope

pytestmark = pytest.mark.django_db

UNRESTRICTED = ScopeFilter(unrestricted=True, project_ids=None)
KYIV = ZoneInfo("Europe/Kyiv")


def _scope() -> Scope:
    return Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=UNRESTRICTED)


def _day_ctx(date: datetime.date) -> DayContext:
    return DayContext(scope=_scope(), cohort=Cohort.ALL, date=date)


def test_merge_at_2359_and_0001_kyiv_land_in_different_days():
    day_one = datetime.date(2026, 6, 15)
    day_two = datetime.date(2026, 6, 16)
    late_on_day_one = datetime.datetime(2026, 6, 15, 23, 59, tzinfo=KYIV)
    early_on_day_two = datetime.datetime(2026, 6, 16, 0, 1, tzinfo=KYIV)

    PullRequestFactory(state=PullRequest.State.MERGED, merged_at=late_on_day_one)
    PullRequestFactory(state=PullRequest.State.MERGED, merged_at=early_on_day_two)

    metric_def = get_metric("prs_merged")
    assert metric_def.calculator.daily(_day_ctx(day_one)) == MetricValue(1.0, 1)
    assert metric_def.calculator.daily(_day_ctx(day_two)) == MetricValue(1.0, 1)


def test_day_attribution_across_the_dst_transition():
    # Europe/Kyiv springs forward on 2026-03-29 (EET +2 -> EEST +3).
    before_transition = datetime.date(2026, 3, 29)
    after_transition = datetime.date(2026, 3, 30)
    just_before_midnight = datetime.datetime(2026, 3, 29, 23, 59, tzinfo=KYIV)
    just_after_midnight = datetime.datetime(2026, 3, 30, 0, 1, tzinfo=KYIV)

    PullRequestFactory(state=PullRequest.State.MERGED, merged_at=just_before_midnight)
    PullRequestFactory(state=PullRequest.State.MERGED, merged_at=just_after_midnight)

    metric_def = get_metric("prs_merged")
    assert metric_def.calculator.daily(_day_ctx(before_transition)) == MetricValue(1.0, 1)
    assert metric_def.calculator.daily(_day_ctx(after_transition)) == MetricValue(1.0, 1)


def test_prs_opened_counts_by_created_at_day():
    day = datetime.date(2026, 6, 15)
    PullRequestFactory(created_at=datetime.datetime(2026, 6, 15, 10, tzinfo=datetime.UTC))
    PullRequestFactory(created_at=datetime.datetime(2026, 6, 16, 10, tzinfo=datetime.UTC))

    metric_def = get_metric("prs_opened")
    assert metric_def.calculator.daily(_day_ctx(day)) == MetricValue(1.0, 1)


def test_prs_closed_unmerged_requires_closed_state():
    day = datetime.date(2026, 6, 15)
    closed_at = datetime.datetime(2026, 6, 15, 10, tzinfo=datetime.UTC)
    PullRequestFactory(state=PullRequest.State.CLOSED, closed_at=closed_at)
    # Merged PRs are not "closed unmerged", even with a closed_at on the same day.
    PullRequestFactory(state=PullRequest.State.MERGED, closed_at=closed_at, merged_at=closed_at)

    metric_def = get_metric("prs_closed_unmerged")
    assert metric_def.calculator.daily(_day_ctx(day)) == MetricValue(1.0, 1)


def test_bot_pr_is_excluded_from_every_counter_and_present_in_prs_excluded_from_metrics():
    day = datetime.date(2026, 6, 15)
    created_at = datetime.datetime(2026, 6, 15, 10, tzinfo=datetime.UTC)
    bot_identity = IdentityFactory(person=PersonFactory(is_bot=True))
    PullRequestFactory(author=bot_identity, created_at=created_at)

    assert get_metric("prs_opened").calculator.daily(_day_ctx(day)) == MetricValue.empty()
    assert get_metric("prs_excluded_from_metrics").calculator.daily(_day_ctx(day)) == MetricValue(1.0, 1)


def test_reviews_given_counts_by_submitted_at_day():
    from apps.activity.factories import ReviewFactory

    day = datetime.date(2026, 6, 15)
    ReviewFactory(submitted_at=datetime.datetime(2026, 6, 15, 10, tzinfo=datetime.UTC))
    ReviewFactory(submitted_at=datetime.datetime(2026, 6, 16, 10, tzinfo=datetime.UTC))

    metric_def = get_metric("reviews_given")
    assert metric_def.calculator.daily(_day_ctx(day)) == MetricValue(1.0, 1)
