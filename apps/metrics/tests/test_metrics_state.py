import datetime

import pytest

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory
from apps.catalog.services import set_setting
from apps.metrics.calculators.base import DayContext
from apps.metrics.models import Cohort, ScopeType
from apps.metrics.registry import get_metric
from apps.metrics.timeframe import day_end_exclusive
from apps.metrics.types import MetricValue, Scope

pytestmark = pytest.mark.django_db

UNRESTRICTED = ScopeFilter(unrestricted=True, project_ids=None)
UTC = datetime.UTC


def _scope() -> Scope:
    return Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=UNRESTRICTED)


def _day_ctx(date: datetime.date) -> DayContext:
    return DayContext(scope=_scope(), cohort=Cohort.ALL, date=date)


def test_open_at_a_past_date_reconstructs_from_dates():
    # Created D-3, merged D-1: open on D-2, not open on D.
    day_d = datetime.date(2026, 6, 15)
    created_at = datetime.datetime(2026, 6, 12, 10, tzinfo=UTC)
    merged_at = datetime.datetime(2026, 6, 14, 10, tzinfo=UTC)
    PullRequestFactory(created_at=created_at, merged_at=merged_at, state="merged")

    metric_def = get_metric("open_prs")
    assert metric_def.calculator.at_date(_day_ctx(day_d - datetime.timedelta(days=2))) == MetricValue(1.0, 1)
    assert metric_def.calculator.at_date(_day_ctx(day_d)) == MetricValue.empty()


def test_draft_pr_is_out_of_stale_prs_and_wip_per_person():
    day = datetime.date(2026, 6, 15)
    old_activity = datetime.datetime(2026, 6, 1, tzinfo=UTC)
    PullRequestFactory(created_at=old_activity, last_activity_at=old_activity, is_draft=True, state="open")

    assert get_metric("stale_prs").calculator.at_date(_day_ctx(day)) == MetricValue.empty()
    assert get_metric("wip_per_person").calculator.at_date(_day_ctx(day)) == MetricValue.empty()


def test_stale_prs_boundary_at_exactly_stale_days_and_one_second_past():
    set_setting("STALE_DAYS", 5)
    day = datetime.date(2026, 6, 15)
    at_end_of_day = day_end_exclusive(day)

    exactly_at_threshold = at_end_of_day - datetime.timedelta(days=5)
    PullRequestFactory(
        created_at=exactly_at_threshold,
        last_activity_at=exactly_at_threshold,
        is_draft=False,
        state="open",
    )
    assert get_metric("stale_prs").calculator.at_date(_day_ctx(day)) == MetricValue.empty()

    one_second_past = exactly_at_threshold - datetime.timedelta(seconds=1)
    PullRequestFactory(
        created_at=one_second_past, last_activity_at=one_second_past, is_draft=False, state="open"
    )
    assert get_metric("stale_prs").calculator.at_date(_day_ctx(day)) == MetricValue(1.0, 1)


def test_waiting_review_24h_boundary_at_exactly_the_threshold_and_one_second_past():
    set_setting("WAITING_REVIEW_HOURS", 24)
    day = datetime.date(2026, 6, 15)
    at_end_of_day = day_end_exclusive(day)

    exactly_at_threshold = at_end_of_day - datetime.timedelta(hours=24)
    PullRequestFactory(
        created_at=exactly_at_threshold - datetime.timedelta(days=1),
        ready_for_review_at=exactly_at_threshold,
        state="open",
    )
    assert get_metric("waiting_review_24h").calculator.at_date(_day_ctx(day)) == MetricValue.empty()

    one_second_past = exactly_at_threshold - datetime.timedelta(seconds=1)
    PullRequestFactory(
        created_at=one_second_past - datetime.timedelta(days=1),
        ready_for_review_at=one_second_past,
        state="open",
    )
    assert get_metric("waiting_review_24h").calculator.at_date(_day_ctx(day)) == MetricValue(1.0, 1)


def test_wip_per_person_without_open_prs_is_none():
    day = datetime.date(2026, 6, 15)
    assert get_metric("wip_per_person").calculator.at_date(_day_ctx(day)) == MetricValue.empty()
