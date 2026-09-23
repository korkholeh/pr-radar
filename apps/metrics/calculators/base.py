"""Shared context objects and math helpers every calculator module builds on. Calculators read
settings through `apps.catalog.services` directly (same convention as `ai_detection`/`policy`),
so no settings snapshot is threaded through the context — a deliberate simplification over the
plan's "resolved settings" wording, logged in `.autodev/DECISIONS.md`."""

from __future__ import annotations

import datetime
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from django.db.models import Count, QuerySet, Sum

from apps.accounts.selectors import ScopeFilter
from apps.catalog.models import Project
from apps.catalog.services import get_str
from apps.metrics.models import ScopeType
from apps.metrics.types import BreakdownItem, MetricValue, Scope

UNRESTRICTED_ACCESS = ScopeFilter(unrestricted=True, project_ids=None)
GLOBAL_SCOPE = Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=UNRESTRICTED_ACCESS)


@dataclass(frozen=True)
class DayContext:
    """What a `CounterCalc`/`StateCalc` calculator needs for a single day."""

    scope: Scope
    cohort: str
    date: datetime.date


@dataclass(frozen=True)
class PeriodContext:
    """What a `DistributionCalc` calculator needs for a whole (inclusive) period."""

    scope: Scope
    cohort: str
    date_from: datetime.date
    date_to: datetime.date


@dataclass(frozen=True)
class DayContextMany:
    """What `StateCalc.at_date_by_person` needs to compute every requested person's value in one
    query instead of one `at_date()` call per person (T11 continuation: `compute_many()`'s
    per-scope fallback for distribution/state metrics was the dominant remaining cost on the
    people table — 600 raw queries for 60 people × 5 metrics at `--scale large`)."""

    cohort: str
    date: datetime.date
    person_ids: frozenset[int]


@dataclass(frozen=True)
class PeriodContextMany:
    """The `PeriodContext` counterpart of `DayContextMany`, for `DistributionCalc.period_by_person`."""

    cohort: str
    date_from: datetime.date
    date_to: datetime.date
    person_ids: frozenset[int]


def percentile(values: Iterable[float | None], pct: float) -> MetricValue:
    """Linear-interpolation percentile (the same method `statistics.median` uses at pct=50),
    dropping `None`s from both the value and the sample size."""
    cleaned = sorted(value for value in values if value is not None)
    n = len(cleaned)
    if n == 0:
        return MetricValue.empty()
    if n == 1:
        return MetricValue(cleaned[0], 1)
    rank = (pct / 100) * (n - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        value = cleaned[int(rank)]
    else:
        weight = rank - lower
        value = cleaned[lower] * (1 - weight) + cleaned[upper] * weight
    return MetricValue(value, n)


def median(values: Iterable[float | None]) -> MetricValue:
    return percentile(values, 50)


def duration_hours(
    start: datetime.datetime | None, end: datetime.datetime | None, *, mode: str | None = None
) -> float | None:
    """Hours between two instants, honouring `DURATION_MODE` (only `calendar_hours` is
    implemented; an unrecognised mode falls back to it). `None` if either bound is missing or the
    computed duration is negative (bad data — e.g. merged before marked ready), so it drops out of
    a caller's sample rather than being counted as zero or a poisoned negative."""
    if start is None or end is None:
        return None
    effective_mode = mode if mode is not None else get_str("DURATION_MODE")
    if effective_mode != "calendar_hours":
        effective_mode = "calendar_hours"
    hours = (end - start).total_seconds() / 3600
    if hours < 0:
        return None
    return hours


def duration_seconds(
    start: datetime.datetime | None, end: datetime.datetime | None, *, mode: str | None = None
) -> float | None:
    """`duration_hours()` in seconds — the unit every `unit="duration"` metric reports in, because
    it is the unit every reader of one assumes: `metric_value`/`format_duration`, `charts.js`,
    and the CSV/XLSX exports (which divide by 3600). A duration metric built on
    `duration_hours()` renders 3 hours as "3 seconds"."""
    hours = duration_hours(start, end, mode=mode)
    return None if hours is None else hours * 3600


def count_value(count: int) -> MetricValue:
    """A plain count as a `MetricValue`: `None` when the count is zero, applying the "no data ->
    None, never 0" rule (CLAUDE.md) uniformly to counters and state snapshots too, not only to
    ratios/distributions — the same rule `rollups.write_rollups()` relies on (a zero-sample day
    writes no row, so a missing row and a verified zero are the same thing on read)."""
    return MetricValue(float(count), count) if count else MetricValue.empty()


def ratio_value(numerator: MetricValue, denominator: MetricValue) -> MetricValue:
    """A ratio's `sample_size` is the denominator's — the population the ratio is measured over.
    `None`, never a division by zero, when the denominator is empty or zero. A `None` numerator is
    treated as zero: under the rollup storage contract a numerator row is written only when its
    own sample is non-empty, so "no numerator rows in this period" and "zero matching rows" are
    the same fact, not a missing measurement."""
    if denominator.sample_size == 0 or not denominator.value:
        return MetricValue(None, denominator.sample_size)
    numerator_value = numerator.value if numerator.value is not None else 0.0
    return MetricValue(numerator_value / denominator.value, denominator.sample_size)


ScopeKey = tuple[str, int | None]


def _grouped_value(
    queryset: QuerySet,
    repo_field: str,
    person_field: str,
    sum_fields: tuple[str, ...] = (),
) -> dict[ScopeKey, float]:
    """One metric's value at every scope a day's matching rows touch, in three queries
    regardless of how many repositories/projects/people are touched — the batched counterpart of
    calling a `CounterCalc`/`RatioCalc` function once per scope (which re-runs the whole query
    every time). `sum_fields` empty means "count of matching rows"; otherwise their sum. PROJECT
    values are derived from the REPO values in Python (a repository in two projects contributes
    its full value to each), never queried directly."""

    def _group_by(field: str) -> dict[int, float]:
        grouped = queryset.exclude(**{f"{field}__isnull": True}).values(field)
        if sum_fields:
            grouped = grouped.annotate(**{f"s{i}": Sum(name) for i, name in enumerate(sum_fields)})
            rows = grouped.values_list(field, *[f"s{i}" for i in range(len(sum_fields))])
            return {row[0]: float(sum(part or 0 for part in row[1:])) for row in rows}
        grouped = grouped.annotate(n=Count("pk", distinct=True))
        return {row[0]: float(row[1]) for row in grouped.values_list(field, "n")}

    if sum_fields:
        totals = queryset.aggregate(**{f"s{i}": Sum(name) for i, name in enumerate(sum_fields)})
        global_value = float(sum((totals[f"s{i}"] or 0) for i in range(len(sum_fields))))
    else:
        global_value = float(queryset.count())

    values: dict[ScopeKey, float] = {(ScopeType.GLOBAL, None): global_value}
    repo_values = _group_by(repo_field)
    for repo_id, value in repo_values.items():
        values[(ScopeType.REPO, repo_id)] = value
    for person_id, value in _group_by(person_field).items():
        values[(ScopeType.PERSON, person_id)] = value
    if repo_values:
        project_values: dict[int, float] = {}
        for project_id, repo_id in Project.objects.filter(
            repositories__id__in=repo_values.keys()
        ).values_list("id", "repositories__id"):
            project_values[project_id] = project_values.get(project_id, 0.0) + repo_values[repo_id]
        for project_id, value in project_values.items():
            values[(ScopeType.PROJECT, project_id)] = value
    return values


BatchFunction = Callable[[str, datetime.date], dict[ScopeKey, MetricValue]]


def batch_count(
    queryset_factory: Callable[[str, datetime.date], QuerySet],
    repo_field: str = "repository_id",
    person_field: str = "author__person_id",
) -> BatchFunction:
    """The batched counterpart of a `CounterCalc`/`RatioCalc` function whose value is a plain
    count of matching rows — `queryset_factory(cohort, date)` must return the metric's matching
    rows at `GLOBAL_SCOPE` (i.e. before any repository/project/person narrowing), exactly what
    the per-scope function filters further before calling `.count()`."""

    def _batch(cohort: str, date: datetime.date) -> dict[ScopeKey, MetricValue]:
        values = _grouped_value(queryset_factory(cohort, date), repo_field, person_field)
        return {key: MetricValue(value, int(value)) for key, value in values.items() if value}

    return _batch


def batch_sum(
    queryset_factory: Callable[[str, datetime.date], QuerySet],
    sum_fields: tuple[str, ...],
    repo_field: str = "repository_id",
    person_field: str = "author__person_id",
) -> BatchFunction:
    """The batched counterpart of a `RatioCalc` component whose value is the sum of one or more
    fields *and* whose sample_size is the row count of that same (already filtered) queryset —
    e.g. `review_rounds_avg`'s numerator."""

    def _batch(cohort: str, date: datetime.date) -> dict[ScopeKey, MetricValue]:
        queryset = queryset_factory(cohort, date)
        sums = _grouped_value(queryset, repo_field, person_field, sum_fields=sum_fields)
        counts = _grouped_value(queryset, repo_field, person_field)
        return {key: MetricValue(sums.get(key, 0.0), int(n)) for key, n in counts.items() if n}

    return _batch


def batch_value_over_population(
    population_factory: Callable[[str, datetime.date], QuerySet],
    join_factory: Callable[[str, datetime.date], QuerySet],
    repo_field: str,
    person_field: str,
    join_repo_field: str,
    join_person_field: str,
    sum_fields: tuple[str, ...] = (),
    scale: float = 1.0,
) -> BatchFunction:
    """The batched counterpart of a `RatioCalc` component whose sample_size comes from a PR
    population (`population_factory`) but whose value is aggregated over a *different*, joined
    queryset (`join_factory`, e.g. `PRFile`/`ReviewComment`/`CheckStatus` filtered by
    `pull_request__in=population`) — e.g. `test_lines_ratio`'s numerator, whose sample_size is
    the number of merged PRs, not the number of file rows."""

    def _batch(cohort: str, date: datetime.date) -> dict[ScopeKey, MetricValue]:
        counts = _grouped_value(population_factory(cohort, date), repo_field, person_field)
        join_values = _grouped_value(
            join_factory(cohort, date), join_repo_field, join_person_field, sum_fields=sum_fields
        )
        return {key: MetricValue(join_values.get(key, 0.0) * scale, int(n)) for key, n in counts.items() if n}

    return _batch


def breakdown_value(counter: Mapping[str, int]) -> tuple[BreakdownItem, ...]:
    """A count-per-label breakdown, most-populous label first, zero-count labels dropped (a PR
    with `ai_tools=[]` must be absent from `ai_tool_breakdown`, not a zero row)."""
    total = sum(counter.values())
    return tuple(
        BreakdownItem(label=label, value=float(count), sample_size=total)
        for label, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
        if count > 0
    )
