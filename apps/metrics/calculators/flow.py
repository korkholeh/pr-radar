"""Flow metrics (spec §8.2): throughput counters and lead/cycle-time/size/review-load
distributions. Distributions are attributed to the PR's *merge* day/period (spec §8.3), so a PR
still open at the end of a period contributes to none of them yet."""

from __future__ import annotations

import datetime
from collections import Counter
from dataclasses import replace

from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from apps.activity.models import PullRequest, Review
from apps.catalog.services import get_int, get_str
from apps.metrics.calculators.base import (
    GLOBAL_SCOPE,
    DayContext,
    PeriodContext,
    PeriodContextMany,
    batch_count,
    breakdown_value,
    count_value,
    duration_seconds,
    percentile,
)
from apps.metrics.models import ScopeType
from apps.metrics.registry import CounterCalc, DistributionCalc, MetricDef, _register
from apps.metrics.selectors import scoped_excluded_pull_requests, scoped_pull_requests, scoped_reviews
from apps.metrics.timeframe import day_end_exclusive, day_start
from apps.metrics.types import BreakdownItem, MetricValue, Scope

_ALL_LEVELS = frozenset(ScopeType.values)


def _reviews_given_population(cohort: str) -> QuerySet[Review]:
    """Reviews *given* at `GLOBAL_SCOPE`, i.e. before any repository/project/person narrowing —
    the base `batch_count()` groups by `reviewer__person_id` instead of the default
    `author__person_id` (see `_reviews_given_in_scope`'s docstring)."""
    return scoped_reviews(GLOBAL_SCOPE, cohort)


def _reviews_given_in_scope(scope: Scope, cohort: str) -> QuerySet[Review]:
    """Reviews *given*, scoped by the reviewer's identity at PERSON level. `scoped_reviews()`
    narrows PERSON scope by the reviewed PR's *author* (right for "reviews received on this
    person's PRs"), which is the wrong population for "reviews this person gave" — so at PERSON
    level this drops the author narrowing and filters by `reviewer__person_id` instead."""
    if scope.scope_type == ScopeType.PERSON:
        assert scope.scope_id is not None
        population_scope = replace(scope, scope_type=ScopeType.GLOBAL, scope_id=None)
        return scoped_reviews(population_scope, cohort).filter(reviewer__person_id=scope.scope_id)
    return scoped_reviews(scope, cohort)


def _prs_opened_population(cohort: str, date: datetime.date) -> QuerySet[PullRequest]:
    return scoped_pull_requests(GLOBAL_SCOPE, cohort).filter(
        created_at__gte=day_start(date), created_at__lt=day_end_exclusive(date)
    )


def _prs_opened_daily(ctx: DayContext) -> MetricValue:
    count = (
        scoped_pull_requests(ctx.scope, ctx.cohort)
        .filter(created_at__gte=day_start(ctx.date), created_at__lt=day_end_exclusive(ctx.date))
        .count()
    )
    return count_value(count)


_register(
    MetricDef(
        key="prs_opened",
        title=_("PRs opened"),
        description=_("Pull requests created on a day."),
        unit="count",
        direction="neutral",
        kind="counter",
        levels=_ALL_LEVELS,
        supports_cohorts=True,
        calculator=CounterCalc(daily=_prs_opened_daily, batch=batch_count(_prs_opened_population)),
        formula="count of PRs with created_at on the day",
    )
)


def _prs_merged_population(cohort: str, date: datetime.date) -> QuerySet[PullRequest]:
    return scoped_pull_requests(GLOBAL_SCOPE, cohort).filter(
        merged_at__gte=day_start(date), merged_at__lt=day_end_exclusive(date)
    )


def _prs_merged_daily(ctx: DayContext) -> MetricValue:
    count = (
        scoped_pull_requests(ctx.scope, ctx.cohort)
        .filter(merged_at__gte=day_start(ctx.date), merged_at__lt=day_end_exclusive(ctx.date))
        .count()
    )
    return count_value(count)


_register(
    MetricDef(
        key="prs_merged",
        title=_("PRs merged"),
        description=_("Pull requests merged on a day — the flow throughput counter."),
        unit="count",
        direction="higher_is_better",
        kind="counter",
        levels=_ALL_LEVELS,
        supports_cohorts=True,
        calculator=CounterCalc(daily=_prs_merged_daily, batch=batch_count(_prs_merged_population)),
        formula="count of PRs with merged_at on the day",
    )
)


def _prs_closed_unmerged_population(cohort: str, date: datetime.date) -> QuerySet[PullRequest]:
    return scoped_pull_requests(GLOBAL_SCOPE, cohort).filter(
        state=PullRequest.State.CLOSED,
        closed_at__gte=day_start(date),
        closed_at__lt=day_end_exclusive(date),
    )


def _prs_closed_unmerged_daily(ctx: DayContext) -> MetricValue:
    count = (
        scoped_pull_requests(ctx.scope, ctx.cohort)
        .filter(
            state=PullRequest.State.CLOSED,
            closed_at__gte=day_start(ctx.date),
            closed_at__lt=day_end_exclusive(ctx.date),
        )
        .count()
    )
    return count_value(count)


_register(
    MetricDef(
        key="prs_closed_unmerged",
        title=_("PRs closed without merging"),
        description=_("Pull requests closed on a day without being merged."),
        unit="count",
        direction="lower_is_better",
        kind="counter",
        levels=_ALL_LEVELS,
        supports_cohorts=True,
        calculator=CounterCalc(
            daily=_prs_closed_unmerged_daily, batch=batch_count(_prs_closed_unmerged_population)
        ),
        formula="count of PRs with state=closed and closed_at on the day",
    )
)


def _reviews_given_daily_population(cohort: str, date: datetime.date) -> QuerySet[Review]:
    return _reviews_given_population(cohort).filter(
        submitted_at__gte=day_start(date), submitted_at__lt=day_end_exclusive(date)
    )


def _reviews_given_daily(ctx: DayContext) -> MetricValue:
    count = (
        _reviews_given_in_scope(ctx.scope, ctx.cohort)
        .filter(submitted_at__gte=day_start(ctx.date), submitted_at__lt=day_end_exclusive(ctx.date))
        .count()
    )
    return count_value(count)


_register(
    MetricDef(
        key="reviews_given",
        title=_("Reviews given"),
        description=_("Reviews submitted on a day."),
        unit="count",
        direction="neutral",
        kind="counter",
        levels=_ALL_LEVELS,
        supports_cohorts=True,
        calculator=CounterCalc(
            daily=_reviews_given_daily,
            batch=batch_count(
                _reviews_given_daily_population,
                repo_field="pull_request__repository_id",
                person_field="reviewer__person_id",
            ),
        ),
        formula="count of reviews with submitted_at on the day",
    )
)


def _prs_excluded_from_metrics_population(cohort: str, date: datetime.date) -> QuerySet[PullRequest]:
    return scoped_excluded_pull_requests(GLOBAL_SCOPE).filter(
        created_at__gte=day_start(date), created_at__lt=day_end_exclusive(date)
    )


def _prs_excluded_from_metrics_daily(ctx: DayContext) -> MetricValue:
    count = (
        scoped_excluded_pull_requests(ctx.scope)
        .filter(created_at__gte=day_start(ctx.date), created_at__lt=day_end_exclusive(ctx.date))
        .count()
    )
    return count_value(count)


_register(
    MetricDef(
        key="prs_excluded_from_metrics",
        title=_("PRs excluded from metrics"),
        description=_(
            "Pull requests created on a day by a bot or a person excluded from metrics — the "
            "population every other metric leaves out, surfaced rather than silently dropped."
        ),
        unit="count",
        direction="neutral",
        kind="counter",
        levels=_ALL_LEVELS,
        supports_cohorts=False,
        calculator=CounterCalc(
            daily=_prs_excluded_from_metrics_daily, batch=batch_count(_prs_excluded_from_metrics_population)
        ),
        formula="count of bot/exclude_from_metrics PRs with created_at on the day",
    )
)


def _merged_population(ctx: PeriodContext) -> QuerySet:
    return scoped_pull_requests(ctx.scope, ctx.cohort).filter(
        merged_at__gte=day_start(ctx.date_from), merged_at__lt=day_end_exclusive(ctx.date_to)
    )


def _durations(queryset: QuerySet, start_field: str, end_field: str) -> list[float | None]:
    # `get_str("DURATION_MODE")` read once for the whole population, not once per row (T11
    # continuation: profiling on `--scale large` found `duration_seconds()`'s per-row default
    # `mode=None` was reading this setting up to once per merged PR, ~129,000 settings reads for
    # one Overview render — by far the largest remaining cost once the people table's own N+1 was
    # fixed).
    mode = get_str("DURATION_MODE")
    return [
        duration_seconds(start, end, mode=mode) for start, end in queryset.values_list(start_field, end_field)
    ]


def _make_duration_distribution(
    key: str,
    title,
    description,
    start_field: str,
    end_field: str,
    pct: float,
    formula: str,
) -> None:
    def _period(ctx: PeriodContext) -> MetricValue:
        return percentile(_durations(_merged_population(ctx), start_field, end_field), pct)

    def _period_by_person(ctx: PeriodContextMany) -> dict[int, MetricValue]:
        population = scoped_pull_requests(GLOBAL_SCOPE, ctx.cohort).filter(
            merged_at__gte=day_start(ctx.date_from),
            merged_at__lt=day_end_exclusive(ctx.date_to),
            author__person_id__in=ctx.person_ids,
        )
        mode = get_str("DURATION_MODE")
        durations_by_person: dict[int, list[float | None]] = {}
        for person_id, start, end in population.values_list("author__person_id", start_field, end_field):
            durations_by_person.setdefault(person_id, []).append(duration_seconds(start, end, mode=mode))
        return {person_id: percentile(values, pct) for person_id, values in durations_by_person.items()}

    _register(
        MetricDef(
            key=key,
            title=title,
            description=description,
            unit="duration",
            direction="lower_is_better",
            kind="distribution",
            levels=_ALL_LEVELS,
            supports_cohorts=True,
            calculator=DistributionCalc(period=_period, period_by_person=_period_by_person),
            formula=formula,
        )
    )


_make_duration_distribution(
    "lead_time_p50",
    _("Lead time (median)"),
    _("From ready for review to merge, for PRs merged in the period."),
    "ready_for_review_at",
    "merged_at",
    50,
    "median(merged_at - ready_for_review_at) over PRs merged in the period",
)
_make_duration_distribution(
    "lead_time_p90",
    _("Lead time (p90)"),
    _("From ready for review to merge, for PRs merged in the period (90th percentile)."),
    "ready_for_review_at",
    "merged_at",
    90,
    "p90(merged_at - ready_for_review_at) over PRs merged in the period",
)
_make_duration_distribution(
    "cycle_time_p50",
    _("Cycle time (median)"),
    _("From first commit to merge, for PRs merged in the period."),
    "first_commit_at",
    "merged_at",
    50,
    "median(merged_at - first_commit_at) over PRs merged in the period",
)
_make_duration_distribution(
    "time_to_first_review_p50",
    _("Time to first review (median)"),
    _("From ready for review to the first review, for PRs merged in the period."),
    "ready_for_review_at",
    "first_review_at",
    50,
    "median(first_review_at - ready_for_review_at) over PRs merged in the period",
)
_make_duration_distribution(
    "time_to_first_review_p90",
    _("Time to first review (p90)"),
    _("From ready for review to the first review, for PRs merged in the period (90th percentile)."),
    "ready_for_review_at",
    "first_review_at",
    90,
    "p90(first_review_at - ready_for_review_at) over PRs merged in the period",
)


def _pr_sizes(ctx: PeriodContext) -> list[float | None]:
    sizes: list[float | None] = []
    rows = _merged_population(ctx).values_list("effective_additions", "effective_deletions")
    for additions, deletions in rows:
        sizes.append(None if additions is None or deletions is None else float(additions + deletions))
    return sizes


def _pr_size_p50_period(ctx: PeriodContext) -> MetricValue:
    return percentile(_pr_sizes(ctx), 50)


def _pr_size_p50_period_by_person(ctx: PeriodContextMany) -> dict[int, MetricValue]:
    population = scoped_pull_requests(GLOBAL_SCOPE, ctx.cohort).filter(
        merged_at__gte=day_start(ctx.date_from),
        merged_at__lt=day_end_exclusive(ctx.date_to),
        author__person_id__in=ctx.person_ids,
    )
    sizes_by_person: dict[int, list[float | None]] = {}
    for person_id, additions, deletions in population.values_list(
        "author__person_id", "effective_additions", "effective_deletions"
    ):
        size = None if additions is None or deletions is None else float(additions + deletions)
        sizes_by_person.setdefault(person_id, []).append(size)
    return {person_id: percentile(values, 50) for person_id, values in sizes_by_person.items()}


_register(
    MetricDef(
        key="pr_size_p50",
        title=_("PR size (median)"),
        description=_("Median effective lines changed (additions + deletions) for PRs merged in the period."),
        unit="lines",
        # Smaller is better: the PLANEKS standards cap PR size, and a small PR is reviewed properly.
        direction="lower_is_better",
        kind="distribution",
        levels=_ALL_LEVELS,
        supports_cohorts=True,
        calculator=DistributionCalc(
            period=_pr_size_p50_period, period_by_person=_pr_size_p50_period_by_person
        ),
        formula="median(effective_additions + effective_deletions) over PRs merged in the period",
    )
)


def _pr_size_bucket_counts(ctx: PeriodContext) -> Counter[str]:
    values = _merged_population(ctx).exclude(size_bucket__isnull=True).values_list("size_bucket", flat=True)
    return Counter(values)


def _pr_size_buckets_period(ctx: PeriodContext) -> MetricValue:
    return count_value(sum(_pr_size_bucket_counts(ctx).values()))


def _pr_size_buckets_items(ctx: PeriodContext) -> tuple[BreakdownItem, ...]:
    return breakdown_value(_pr_size_bucket_counts(ctx))


_register(
    MetricDef(
        key="pr_size_buckets",
        title=_("PR size buckets"),
        description=_(
            "PRs merged in the period, grouped by size bucket (XS/S/M/L/XL, boundaries from PR_SIZE_BUCKETS)."
        ),
        unit="breakdown",
        direction="neutral",
        kind="distribution",
        levels=_ALL_LEVELS,
        supports_cohorts=True,
        calculator=DistributionCalc(period=_pr_size_buckets_period, breakdown=_pr_size_buckets_items),
        formula="count of PRs merged in the period, grouped by size_bucket",
    )
)


def _review_load_counts(ctx: PeriodContext) -> Counter[int]:
    queryset = _reviews_given_in_scope(ctx.scope, ctx.cohort).filter(
        submitted_at__gte=day_start(ctx.date_from), submitted_at__lt=day_end_exclusive(ctx.date_to)
    )
    return Counter(queryset.values_list("reviewer_id", flat=True))


def _review_load_share_period(ctx: PeriodContext) -> MetricValue:
    counts = _review_load_counts(ctx)
    total = sum(counts.values())
    if total == 0:
        return MetricValue.empty()
    top_n = get_int("REVIEW_LOAD_TOP_N")
    top_total = sum(count for _reviewer_id, count in counts.most_common(top_n))
    return MetricValue(top_total / total, total)


_register(
    MetricDef(
        key="review_load_share",
        title=_("Review load share"),
        description=_(
            "Share of reviews given in the period that fall on the top REVIEW_LOAD_TOP_N "
            "reviewers by review count — a bottleneck-risk indicator."
        ),
        unit="ratio",
        direction="lower_is_better",
        kind="distribution",
        levels=frozenset({ScopeType.GLOBAL, ScopeType.PROJECT, ScopeType.REPO}),
        supports_cohorts=True,
        calculator=DistributionCalc(period=_review_load_share_period),
        formula="reviews by the top REVIEW_LOAD_TOP_N reviewers / all reviews given in the period",
    )
)


def _reviewer_response_seconds(ctx: PeriodContext) -> list[float | None]:
    queryset = _reviews_given_in_scope(ctx.scope, ctx.cohort).filter(
        submitted_at__gte=day_start(ctx.date_from), submitted_at__lt=day_end_exclusive(ctx.date_to)
    )
    mode = get_str("DURATION_MODE")
    durations: list[float | None] = []
    for ready_for_review_at, created_at, submitted_at in queryset.values_list(
        "pull_request__ready_for_review_at", "pull_request__created_at", "submitted_at"
    ):
        requested_at = ready_for_review_at if ready_for_review_at is not None else created_at
        durations.append(duration_seconds(requested_at, submitted_at, mode=mode))
    return durations


def _reviewer_response_p50_period(ctx: PeriodContext) -> MetricValue:
    return percentile(_reviewer_response_seconds(ctx), 50)


def _reviewer_response_p50_period_by_person(ctx: PeriodContextMany) -> dict[int, MetricValue]:
    """The batched counterpart of `_reviewer_response_seconds()`, grouped by the *reviewer*
    (`_reviews_given_in_scope`'s own PERSON-level distinction: reviews given, not reviews
    received) — `scoped_reviews` at `GLOBAL_SCOPE` is the population before reviewer narrowing."""
    queryset = scoped_reviews(GLOBAL_SCOPE, ctx.cohort).filter(
        submitted_at__gte=day_start(ctx.date_from),
        submitted_at__lt=day_end_exclusive(ctx.date_to),
        reviewer__person_id__in=ctx.person_ids,
    )
    mode = get_str("DURATION_MODE")
    durations_by_person: dict[int, list[float | None]] = {}
    for reviewer_id, ready_for_review_at, created_at, submitted_at in queryset.values_list(
        "reviewer__person_id", "pull_request__ready_for_review_at", "pull_request__created_at", "submitted_at"
    ):
        requested_at = ready_for_review_at if ready_for_review_at is not None else created_at
        durations_by_person.setdefault(reviewer_id, []).append(
            duration_seconds(requested_at, submitted_at, mode=mode)
        )
    return {person_id: percentile(values, 50) for person_id, values in durations_by_person.items()}


_register(
    MetricDef(
        key="reviewer_response_p50",
        title=_("Reviewer response time (median)"),
        description=_(
            "From the PR becoming ready for review (falling back to its creation) to this "
            "reviewer's review, for reviews given in the period — an approximation: no "
            "review-request timestamp is stored, so this is not measured from the actual request."
        ),
        unit="duration",
        direction="lower_is_better",
        kind="distribution",
        levels=frozenset({ScopeType.PERSON}),
        supports_cohorts=True,
        calculator=DistributionCalc(
            period=_reviewer_response_p50_period, period_by_person=_reviewer_response_p50_period_by_person
        ),
        formula="median(submitted_at - (ready_for_review_at or created_at)) over reviews given in the period",
    )
)
