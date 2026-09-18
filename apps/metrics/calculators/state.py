"""State metrics (spec §8.2): point-in-time reconstructions from stored timestamps, never a
snapshot table — "open at a past date" is answered by comparing that date's end-of-day instant
against `created_at`/`merged_at`/`closed_at`, not by a `state` column that only holds *today's*
value."""

from __future__ import annotations

import datetime

from django.db.models import Count, Q, QuerySet
from django.db.models.functions import Coalesce
from django.utils.translation import gettext_lazy as _

from apps.catalog.services import get_int
from apps.metrics.calculators.base import DayContext, count_value
from apps.metrics.models import ScopeType
from apps.metrics.registry import MetricDef, StateCalc, _register
from apps.metrics.selectors import scoped_pull_requests
from apps.metrics.timeframe import day_end_exclusive
from apps.metrics.types import MetricValue, Scope

_ALL_LEVELS = frozenset(ScopeType.values)


def _as_of(date: datetime.date) -> datetime.datetime:
    """The instant "end of `date`" — everything created on or before `date` and not yet
    merged/closed by then was still open at that point."""
    return day_end_exclusive(date)


def _open_at(scope: Scope, cohort: str, at: datetime.datetime) -> QuerySet:
    return (
        scoped_pull_requests(scope, cohort)
        .filter(created_at__lte=at)
        .filter(Q(merged_at__isnull=True) | Q(merged_at__gt=at))
        .filter(Q(closed_at__isnull=True) | Q(closed_at__gt=at))
    )


def _open_prs_at_date(ctx: DayContext) -> MetricValue:
    count = _open_at(ctx.scope, ctx.cohort, _as_of(ctx.date)).count()
    return count_value(count)


_register(
    MetricDef(
        key="open_prs",
        title=_("Open PRs"),
        description=_("Pull requests open at the end of a day, reconstructed from their dates."),
        unit="count",
        direction="neutral",
        kind="state",
        levels=_ALL_LEVELS,
        supports_cohorts=True,
        calculator=StateCalc(at_date=_open_prs_at_date),
        formula="open PRs (created <= end of day, not yet merged/closed by then)",
    )
)


def _stale_prs_at_date(ctx: DayContext) -> MetricValue:
    at = _as_of(ctx.date)
    threshold = at - datetime.timedelta(days=get_int("STALE_DAYS"))
    count = (
        _open_at(ctx.scope, ctx.cohort, at)
        .filter(is_draft=False)
        .annotate(last_activity=Coalesce("last_activity_at", "created_at"))
        .filter(last_activity__lt=threshold)
        .count()
    )
    return count_value(count)


_register(
    MetricDef(
        key="stale_prs",
        title=_("Stale PRs"),
        description=_(
            "Open, non-draft pull requests with no activity for more than STALE_DAYS, as of the end of a day."
        ),
        unit="count",
        direction="lower_is_better",
        kind="state",
        levels=_ALL_LEVELS,
        supports_cohorts=True,
        calculator=StateCalc(at_date=_stale_prs_at_date),
        formula=(
            "open, non-draft PRs with last_activity_at (or created_at) "
            "older than STALE_DAYS before end of day"
        ),
    )
)


def _waiting_review_24h_at_date(ctx: DayContext) -> MetricValue:
    at = _as_of(ctx.date)
    threshold = at - datetime.timedelta(hours=get_int("WAITING_REVIEW_HOURS"))
    count = (
        _open_at(ctx.scope, ctx.cohort, at)
        .filter(ready_for_review_at__isnull=False, ready_for_review_at__lt=threshold)
        .filter(Q(first_review_at__isnull=True) | Q(first_review_at__gt=at))
        .count()
    )
    return count_value(count)


_register(
    MetricDef(
        key="waiting_review_24h",
        title=_("Waiting for review > 24h"),
        description=_(
            "Pull requests ready for review with no review yet, waiting for more than "
            "WAITING_REVIEW_HOURS, as of the end of a day."
        ),
        unit="count",
        direction="lower_is_better",
        kind="state",
        levels=_ALL_LEVELS,
        supports_cohorts=True,
        calculator=StateCalc(at_date=_waiting_review_24h_at_date),
        formula=(
            "open PRs with ready_for_review_at older than WAITING_REVIEW_HOURS "
            "and no review yet, at end of day"
        ),
    )
)


def _wip_per_person_at_date(ctx: DayContext) -> MetricValue:
    at = _as_of(ctx.date)
    rows = (
        _open_at(ctx.scope, ctx.cohort, at)
        .filter(is_draft=False, author__person__isnull=False)
        .values("author__person_id")
        .annotate(open_count=Count("id"))
    )
    counts = [row["open_count"] for row in rows]
    authors = len(counts)
    if authors == 0:
        return MetricValue.empty()
    return MetricValue(sum(counts) / authors, authors)


_register(
    MetricDef(
        key="wip_per_person",
        title=_("WIP per person"),
        description=_(
            "Open, non-draft pull requests divided by the number of distinct authors holding "
            "one, as of the end of a day."
        ),
        unit="count",
        direction="lower_is_better",
        kind="state",
        levels=_ALL_LEVELS,
        supports_cohorts=True,
        calculator=StateCalc(at_date=_wip_per_person_at_date),
        formula="open, non-draft PRs / distinct authors with at least one, at end of day",
    )
)
