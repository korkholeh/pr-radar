"""Rollup writer, restricted to additive (counter/ratio) metrics — CLAUDE.md: "`DailyRollup` stores
only additive metrics." A ratio's numerator and denominator are stored as two rollup rows under
`"<key>__num"`/`"<key>__den"` and divided at read time (`services.compute()`); the ratio itself is
never stored. `rebuild()` computes every metric's `batch()` function once per day per cohort — a
fixed number of grouped-aggregate queries regardless of how many repositories, projects or people
were touched that day — rather than calling the metric once per scope (RISKS row 10)."""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from django.db import transaction

from apps.metrics.models import Cohort, DailyRollup, DirtyDay
from apps.metrics.registry import REGISTRY, CounterCalc, MetricDef, RatioCalc
from apps.metrics.timeframe import date_range
from apps.metrics.types import MetricValue

NUMERATOR_SUFFIX = "__num"
DENOMINATOR_SUFFIX = "__den"


class NonAdditiveMetricError(ValueError):
    pass


@dataclass(frozen=True)
class RebuildResult:
    days: int
    rows: int


def rollup_metric_keys() -> frozenset[str]:
    """Every metric key `write_rollups()` accepts: the bare key for a counter, `<key>__num`/
    `<key>__den` for a ratio. A distribution/state key never appears here — the structural guard
    against RISKS row 7 (a distribution rolled up by accident)."""
    keys: set[str] = set()
    for metric_def in REGISTRY.values():
        if isinstance(metric_def.calculator, CounterCalc):
            keys.add(metric_def.key)
        elif isinstance(metric_def.calculator, RatioCalc):
            keys.add(metric_def.key + NUMERATOR_SUFFIX)
            keys.add(metric_def.key + DENOMINATOR_SUFFIX)
    return frozenset(keys)


def write_rollups(rows: list[DailyRollup]) -> int:
    valid_keys = rollup_metric_keys()
    for row in rows:
        if row.metric_key not in valid_keys:
            raise NonAdditiveMetricError(f"{row.metric_key!r} is not an additive (counter/ratio) rollup key.")
    if rows:
        DailyRollup.objects.bulk_create(rows)
    return len(rows)


def _cohorts_for(metric_def: MetricDef) -> tuple[str, ...]:
    if metric_def.supports_cohorts:
        return (Cohort.ALL, Cohort.AI, Cohort.NON_AI)
    return (Cohort.ALL,)


def _rollup_row(
    date: datetime.date,
    scope_type: str,
    scope_id: int | None,
    cohort: str,
    metric_key: str,
    value: MetricValue,
) -> DailyRollup:
    return DailyRollup(
        date=date,
        scope_type=scope_type,
        scope_id=scope_id,
        cohort=cohort,
        metric_key=metric_key,
        value=value.value,
        sample_size=value.sample_size,
    )


def _rows_from_batch(
    date: datetime.date, cohort: str, metric_key: str, batch: dict[tuple[str, int | None], MetricValue]
) -> list[DailyRollup]:
    return [
        _rollup_row(date, scope_type, scope_id, cohort, metric_key, value)
        for (scope_type, scope_id), value in batch.items()
        if value.sample_size > 0
    ]


def _build_rows_for_day(date: datetime.date) -> list[DailyRollup]:
    """One `batch()` call per metric per cohort — a fixed number of grouped-aggregate queries
    that discover every scope a day's rows touch directly from the data, instead of one query per
    (metric, scope, cohort) combination (RISKS row 10)."""
    rows: list[DailyRollup] = []
    for metric_def in REGISTRY.values():
        calculator = metric_def.calculator
        if isinstance(calculator, CounterCalc):
            for cohort in _cohorts_for(metric_def):
                rows += _rows_from_batch(date, cohort, metric_def.key, calculator.batch(cohort, date))
        elif isinstance(calculator, RatioCalc):
            for cohort in _cohorts_for(metric_def):
                rows += _rows_from_batch(
                    date, cohort, metric_def.key + NUMERATOR_SUFFIX, calculator.numerator_batch(cohort, date)
                )
                rows += _rows_from_batch(
                    date,
                    cohort,
                    metric_def.key + DENOMINATOR_SUFFIX,
                    calculator.denominator_batch(cohort, date),
                )
    return rows


def _rebuild_days(days: list[datetime.date]) -> RebuildResult:
    total_rows = 0
    for date in days:
        rows = _build_rows_for_day(date)
        with transaction.atomic():
            DailyRollup.objects.filter(date=date).delete()
            write_rollups(rows)
        total_rows += len(rows)
    return RebuildResult(days=len(days), rows=total_rows)


def rebuild(date_from: datetime.date, date_to: datetime.date) -> RebuildResult:
    """Deletes and re-inserts exactly the `(date, ...)` rows in `[date_from, date_to]`, one
    transaction per day, so a rebuild is idempotent and a crash leaves at most one day
    half-written."""
    return _rebuild_days(date_range(date_from, date_to))


def rebuild_dirty() -> RebuildResult:
    """Rebuilds exactly the days `services.mark_dirty()` recorded, and consumes (deletes) each
    `DirtyDay` row once its rollups are written — an incremental sync rewrites the days it touched,
    not the whole history. Each marker's `id`/`marked_at` is captured *before* `_build_rows_for_day`
    reads that day's data, and the delete inside the transaction only removes a marker that still
    has that exact `marked_at` — `mark_dirty()` bumps `marked_at` on every call, so a PR committed
    for this day concurrently with this rebuild leaves a marker that survives to be retried, instead
    of being silently deleted alongside the day it flagged."""
    dirty_markers = list(DirtyDay.objects.order_by("date").values_list("date", "id", "marked_at"))
    total_rows = 0
    for date, dirty_id, marked_at in dirty_markers:
        rows = _build_rows_for_day(date)
        with transaction.atomic():
            DailyRollup.objects.filter(date=date).delete()
            write_rollups(rows)
            DirtyDay.objects.filter(id=dirty_id, marked_at=marked_at).delete()
        total_rows += len(rows)
    return RebuildResult(days=len(dirty_markers), rows=total_rows)
