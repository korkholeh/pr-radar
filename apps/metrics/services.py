"""`compute()` and its supporting pieces (spec §8.1) — the single read entry point dashboards,
charts, tables and exports call. `apps/metrics/registry.py` and `rollups.py` hold the rules; this
module holds the read path."""

from __future__ import annotations

import datetime
import hashlib
from collections.abc import Mapping, Sequence

from django.core.cache import cache
from django.db.models import F
from django.utils import timezone

from apps.accounts.selectors import ScopeFilter, scope_for_user
from apps.activity.models import PullRequest, Review
from apps.catalog.models import Repository
from apps.catalog.services import get_int
from apps.metrics.calculators.base import (
    DayContext,
    DayContextMany,
    PeriodContext,
    PeriodContextMany,
    ratio_value,
)
from apps.metrics.models import Cohort, DailyRollup, DataVersion, DirtyDay, ScopeType
from apps.metrics.registry import (
    CounterCalc,
    DistributionCalc,
    MetricDef,
    RatioCalc,
    StateCalc,
    get_metric,
    require_available_at_level,
)
from apps.metrics.rollups import DENOMINATOR_SUFFIX, NUMERATOR_SUFFIX
from apps.metrics.timeframe import bucket_ranges, date_range, day_of, previous_period
from apps.metrics.types import BreakdownItem, MetricResult, MetricResultSet, MetricValue, Scope, SeriesPoint

_DATA_VERSION_ROW_ID = 1
_DATA_VERSION_CACHE_KEY = "metrics:data-version"


def _id_in_scope(scope_type: str, scope_id: int, access: ScopeFilter) -> bool:
    # Only checks project_ids, never repository_ids: `access` here is always the caller's
    # `scope_for_user()` grant (the projects axis), and `repository_ids` is the dashboard filter
    # bar's UI-level narrowing (apps/accounts/selectors.py), never an access grant on its own.
    project_ids = access.project_ids or frozenset()
    if scope_type == ScopeType.PROJECT:
        return scope_id in project_ids
    if scope_type == ScopeType.REPO:
        return Repository.objects.filter(id=scope_id, projects__id__in=project_ids).exists()
    if scope_type == ScopeType.PERSON:
        return PullRequest.objects.filter(
            author__person_id=scope_id, repository__projects__id__in=project_ids
        ).exists()
    return True  # ScopeType.GLOBAL carries no id.


def scope_for(user: object, scope_type: str, scope_id: int | None) -> Scope:
    """Builds a `Scope`: resolves the caller's `ScopeFilter` and silently drops an out-of-scope id
    back to the global scope (RISKS row 3) so no view has to decide what an invalid id means."""
    access = scope_for_user(user)
    if scope_id is None or access.unrestricted:
        return Scope(scope_type=scope_type, scope_id=scope_id, access=access)
    if not _id_in_scope(scope_type, scope_id, access):
        return Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=access)
    return Scope(scope_type=scope_type, scope_id=scope_id, access=access)


def data_version() -> int:
    """The version stamped into `compute()`'s cache key. Read-through cached under a fixed key in
    the same `FileBasedCache` `compute()` itself uses (not just the DB row): a cache hit on a
    second identical `compute()` call must cost zero queries (T15's acceptance criterion), and this
    is the one DB read that would otherwise happen on every call. `bump_data_version()` keeps the
    cache in step; if the cache is ever wiped, the next read falls back to the DB row."""
    cached_version = cache.get(_DATA_VERSION_CACHE_KEY)
    if cached_version is not None:
        return cached_version
    row, _created = DataVersion.objects.get_or_create(id=_DATA_VERSION_ROW_ID, defaults={"version": 1})
    cache.set(_DATA_VERSION_CACHE_KEY, row.version, timeout=None)
    return row.version


def bump_data_version() -> int:
    DataVersion.objects.get_or_create(id=_DATA_VERSION_ROW_ID, defaults={"version": 1})
    DataVersion.objects.filter(id=_DATA_VERSION_ROW_ID).update(version=F("version") + 1)
    version = DataVersion.objects.get(id=_DATA_VERSION_ROW_ID).version
    cache.set(_DATA_VERSION_CACHE_KEY, version, timeout=None)
    return version


def mark_dirty(
    pull_request_id: int,
    previous_reverts_pr_id: int | None = None,
    extra_pull_request_ids: set[int] | None = None,
) -> None:
    """Records, in `DirtyDay`, every Kyiv day a synced PR can affect: `created_at`, `merged_at`,
    `closed_at`, each of its reviews' `submitted_at` day (spec: dirty-days set, ADR 0007), and —
    for `revert_rate`, whose numerator is a property of the *reverted* PR's own merge day, not the
    reverting PR's — the merge day of the PR its `reverts_pr` points at. Violations need no day of
    their own: the violation metrics are dated by the PR's `created_at`, already nominated.
    `previous_reverts_pr_id` lets a caller that captured the PR's `reverts_pr_id` *before* this
    sync's `derive_pull_request()` ran also dirty the previously targeted PR's merge day, in case
    detection repointed or cleared the link. `extra_pull_request_ids` dirties the merge day of
    every PR whose `has_followup_fix` flag `update_followup_fixes()` just changed — that flag is
    a property of the *original* PR's merge day, not this sync's PR. Appended to
    `github_sync/pipeline.py::process_pull_request`."""
    row = (
        PullRequest.objects.filter(id=pull_request_id)
        .values_list("created_at", "merged_at", "closed_at", "reverts_pr_id")
        .first()
    )
    if row is None:
        return
    created_at, merged_at, closed_at, reverts_pr_id = row
    days = {day_of(instant) for instant in (created_at, merged_at, closed_at) if instant is not None}
    days.update(
        day_of(submitted_at)
        for submitted_at in Review.objects.filter(
            pull_request_id=pull_request_id, submitted_at__isnull=False
        ).values_list("submitted_at", flat=True)
        if submitted_at is not None
    )
    reverted_pr_ids = {pr_id for pr_id in (reverts_pr_id, previous_reverts_pr_id) if pr_id is not None}
    days.update(
        day_of(merged_at)
        for merged_at in PullRequest.objects.filter(
            id__in=reverted_pr_ids, merged_at__isnull=False
        ).values_list("merged_at", flat=True)
        if merged_at is not None
    )
    if extra_pull_request_ids:
        days.update(
            day_of(merged_at)
            for merged_at in PullRequest.objects.filter(
                id__in=extra_pull_request_ids, merged_at__isnull=False
            ).values_list("merged_at", flat=True)
            if merged_at is not None
        )
    now = timezone.now()
    for day in days:
        # `marked_at` is bumped on every call, including one that finds the row already dirty:
        # `rollups.rebuild_dirty()` snapshots it before recomputing a day and only deletes the
        # marker if it hasn't moved since, so a mark_dirty() concurrent with a rebuild survives to
        # be retried instead of being silently deleted alongside the day it flagged.
        DirtyDay.objects.update_or_create(date=day, defaults={"marked_at": now})


def _rollup_cohort(metric_def: MetricDef, cohort: str) -> str:
    """The cohort a rollup row was actually written under: `cohort` itself for a metric that
    supports the cohort dimension, `Cohort.ALL` for one that doesn't (it hardcodes its own
    population, e.g. `ai_pr_count`) — the read-side mirror of `rollups._cohorts_for()`."""
    return cohort if metric_def.supports_cohorts else Cohort.ALL


def _fetch_counter_ratio_rows(
    metric_defs: Sequence[MetricDef],
    scope: Scope,
    cohort: str,
    date_from: datetime.date,
    date_to: datetime.date,
) -> dict[tuple[str, str], list[tuple[datetime.date, float | None, int]]]:
    """One query covering every counter/ratio metric requested, for the whole span a `compute()`
    call needs (current period, previous period and every series bucket are all subranges of
    it) — grouped by `(metric_key, cohort)` so two metrics resolving to different cohorts (one
    supporting the dimension, one not) never mix their rows."""
    keys: set[str] = set()
    cohorts: set[str] = set()
    for metric_def in metric_defs:
        resolved_cohort = _rollup_cohort(metric_def, cohort)
        cohorts.add(resolved_cohort)
        if isinstance(metric_def.calculator, CounterCalc):
            keys.add(metric_def.key)
        else:
            keys.add(metric_def.key + NUMERATOR_SUFFIX)
            keys.add(metric_def.key + DENOMINATOR_SUFFIX)
    if not keys:
        return {}
    rows = DailyRollup.objects.filter(
        scope_type=scope.scope_type,
        scope_id=scope.scope_id,
        cohort__in=cohorts,
        metric_key__in=keys,
        date__gte=date_from,
        date__lte=date_to,
    ).values_list("metric_key", "cohort", "date", "value", "sample_size")
    by_key: dict[tuple[str, str], list[tuple[datetime.date, float | None, int]]] = {}
    for metric_key, row_cohort, date, value, sample_size in rows:
        by_key.setdefault((metric_key, row_cohort), []).append((date, value, sample_size))
    return by_key


def _sum_rows(
    rows: Sequence[tuple[datetime.date, float | None, int]], date_from: datetime.date, date_to: datetime.date
) -> MetricValue:
    total_value = 0.0
    total_sample = 0
    for date, value, sample_size in rows:
        if date_from <= date <= date_to:
            if value is not None:
                total_value += value
            total_sample += sample_size
    if total_sample == 0:
        return MetricValue.empty()
    return MetricValue(total_value, total_sample)


def _period_value(
    metric_def: MetricDef,
    rows_by_key: dict[tuple[str, str], list[tuple[datetime.date, float | None, int]]],
    cohort: str,
    date_from: datetime.date,
    date_to: datetime.date,
) -> MetricValue:
    resolved_cohort = _rollup_cohort(metric_def, cohort)
    if isinstance(metric_def.calculator, CounterCalc):
        rows = rows_by_key.get((metric_def.key, resolved_cohort), [])
        return _sum_rows(rows, date_from, date_to)
    numerator_rows = rows_by_key.get((metric_def.key + NUMERATOR_SUFFIX, resolved_cohort), [])
    denominator_rows = rows_by_key.get((metric_def.key + DENOMINATOR_SUFFIX, resolved_cohort), [])
    numerator = _sum_rows(numerator_rows, date_from, date_to)
    denominator = _sum_rows(denominator_rows, date_from, date_to)
    return ratio_value(numerator, denominator)


def _sum_daily(values: Sequence[MetricValue]) -> MetricValue:
    total_value = 0.0
    total_sample = 0
    for value in values:
        if value.sample_size:
            total_value += value.value or 0.0
            total_sample += value.sample_size
    if total_sample == 0:
        return MetricValue.empty()
    return MetricValue(total_value, total_sample)


def _counter_ratio_value_from_raw(
    metric_def: MetricDef, scope: Scope, cohort: str, date_from: datetime.date, date_to: datetime.date
) -> MetricValue:
    """Computed straight from the calculator, one day at a time, instead of `DailyRollup` — the
    path for a restricted `ScopeFilter`. Rollup rows are written once under a hardcoded
    unrestricted access (`calculators.base.GLOBAL_SCOPE`), so reading them back for a narrowed caller would
    silently return the unrestricted number (RISKS row 3); the calculator itself composes on
    `scope.access` via `selectors.py`, so calling it directly is correct at the cost of one query
    per day instead of one query for the whole range."""
    resolved_cohort = _rollup_cohort(metric_def, cohort)
    calculator = metric_def.calculator
    if isinstance(calculator, CounterCalc):
        daily_values = [
            calculator.daily(DayContext(scope=scope, cohort=resolved_cohort, date=day))
            for day in date_range(date_from, date_to)
        ]
        return _sum_daily(daily_values)
    assert isinstance(calculator, RatioCalc)
    numerator_values = []
    denominator_values = []
    for day in date_range(date_from, date_to):
        ctx = DayContext(scope=scope, cohort=resolved_cohort, date=day)
        numerator_values.append(calculator.numerator(ctx))
        denominator_values.append(calculator.denominator(ctx))
    return ratio_value(_sum_daily(numerator_values), _sum_daily(denominator_values))


def _counter_ratio_value(
    metric_def: MetricDef,
    scope: Scope,
    rows_by_key: dict[tuple[str, str], list[tuple[datetime.date, float | None, int]]],
    cohort: str,
    date_from: datetime.date,
    date_to: datetime.date,
) -> MetricValue:
    if scope.access.unrestricted:
        return _period_value(metric_def, rows_by_key, cohort, date_from, date_to)
    return _counter_ratio_value_from_raw(metric_def, scope, cohort, date_from, date_to)


def _delta(value_mv: MetricValue, previous_mv: MetricValue) -> tuple[float | None, float | None]:
    if value_mv.value is None or previous_mv.value is None:
        return None, None
    delta = value_mv.value - previous_mv.value
    delta_ratio = delta / previous_mv.value if previous_mv.value != 0 else None
    return delta, delta_ratio


def _counter_ratio_result(
    metric_def: MetricDef,
    scope: Scope,
    rows_by_key: dict[tuple[str, str], list[tuple[datetime.date, float | None, int]]],
    cohort: str,
    date_from: datetime.date,
    date_to: datetime.date,
    previous_from: datetime.date,
    previous_to: datetime.date,
    buckets: Sequence[tuple[datetime.date, datetime.date]],
    min_sample: int,
    include_series: bool = True,
) -> MetricResult:
    """Builds one metric's full `MetricResult` (value, previous, delta, series) from
    already-fetched rollup rows — the code both `_compute_uncached()` (one scope) and
    `compute_many()` (many scopes sharing one widened query) share, so the two paths can never
    silently disagree on what a counter/ratio result looks like. `include_series=False` (T11: a
    table row that only ever reads `.value`/`.delta`/`.below_min_sample`, never `.series`) skips
    the per-bucket loop."""
    value_mv = _counter_ratio_value(metric_def, scope, rows_by_key, cohort, date_from, date_to)
    previous_mv = _counter_ratio_value(metric_def, scope, rows_by_key, cohort, previous_from, previous_to)
    series = (
        tuple(
            SeriesPoint(
                b_from, b_to, *_counter_ratio_value(metric_def, scope, rows_by_key, cohort, b_from, b_to)
            )
            for b_from, b_to in buckets
        )
        if include_series
        else ()
    )
    delta, delta_ratio = _delta(value_mv, previous_mv)
    return MetricResult(
        key=metric_def.key,
        definition=metric_def,
        value=value_mv.value,
        previous_value=previous_mv.value,
        delta=delta,
        delta_ratio=delta_ratio,
        sample_size=value_mv.sample_size,
        previous_sample_size=previous_mv.sample_size,
        below_min_sample=value_mv.sample_size < min_sample,
        series=series,
        breakdown=(),
    )


def _distribution_or_state_value(
    metric_def: MetricDef, scope: Scope, cohort: str, date_from: datetime.date, date_to: datetime.date
) -> MetricValue:
    calculator = metric_def.calculator
    if isinstance(calculator, DistributionCalc):
        period_ctx = PeriodContext(scope=scope, cohort=cohort, date_from=date_from, date_to=date_to)
        return calculator.period(period_ctx)
    assert isinstance(calculator, StateCalc)
    return calculator.at_date(DayContext(scope=scope, cohort=cohort, date=date_to))


def _axis_fingerprint(ids: frozenset[int] | None) -> str:
    """`None` ("no restriction on this axis") and an explicit empty `frozenset` ("restricted to
    nothing on this axis") are different `ScopeFilter` states (`narrow_scope()` produces both) and
    must render differently here, or a repository-only filter and an intersection that resolved to
    "no projects visible" would collide on the same cache key (round 2 audit MINOR)."""
    if ids is None:
        return "*"
    if not ids:
        return "-"
    return ",".join(str(value) for value in sorted(ids))


def _access_fingerprint(access: ScopeFilter) -> str:
    """`"*"` for an unrestricted caller, else its project and repository axis fingerprints — part
    of the cache key so a restricted lead and an admin (or two leads/dashboard filters with
    different project or repository sets) can never share a cached entry (RISKS row 3), once phase
    9 turns per-user narrowing on, and *today* for the dashboard filter bar's own project/
    repository multi-select, which reuses this same `ScopeFilter` narrowing."""
    if access.unrestricted:
        return "*"
    return f"{_axis_fingerprint(access.project_ids)}|{_axis_fingerprint(access.repository_ids)}"


def _cache_key(
    metric_keys: Sequence[str],
    scope: Scope,
    cohort: str,
    granularity: str,
    date_from: datetime.date,
    date_to: datetime.date,
    include_series: bool = True,
) -> str:
    keys_digest = hashlib.sha1(",".join(sorted(set(metric_keys))).encode()).hexdigest()
    series_flag = "series" if include_series else "noseries"
    return (
        f"metrics:v3:{data_version()}:{scope.scope_type}:{scope.scope_id}:"
        f"{_access_fingerprint(scope.access)}:{cohort}:{granularity}:"
        f"{date_from.isoformat()}:{date_to.isoformat()}:{keys_digest}:{series_flag}"
    )


def _serialize_result(result: MetricResult) -> dict[str, object]:
    return {
        "value": result.value,
        "previous_value": result.previous_value,
        "delta": result.delta,
        "delta_ratio": result.delta_ratio,
        "sample_size": result.sample_size,
        "previous_sample_size": result.previous_sample_size,
        "below_min_sample": result.below_min_sample,
        "series": [(sp.date_from, sp.date_to, sp.value, sp.sample_size) for sp in result.series],
        "breakdown": [(item.label, item.value, item.sample_size) for item in result.breakdown],
    }


def _deserialize_result(key: str, payload: dict) -> MetricResult:
    """The counterpart of `_serialize_result()`. `definition` is *not* part of the cached payload
    — a `MetricDef` can hold a `lambda` calculator (not picklable) — so it's re-attached from the
    live, already-imported `REGISTRY` on every read instead."""
    return MetricResult(
        key=key,
        definition=get_metric(key),
        value=payload["value"],
        previous_value=payload["previous_value"],
        delta=payload["delta"],
        delta_ratio=payload["delta_ratio"],
        sample_size=payload["sample_size"],
        previous_sample_size=payload["previous_sample_size"],
        below_min_sample=payload["below_min_sample"],
        series=tuple(SeriesPoint(*item) for item in payload["series"]),
        breakdown=tuple(BreakdownItem(*item) for item in payload["breakdown"]),
    )


def compute(
    metric_keys: Sequence[str],
    scope: Scope,
    date_from: datetime.date,
    date_to: datetime.date,
    cohort: str = Cohort.ALL,
    granularity: str = "day",
    include_series: bool = True,
) -> MetricResultSet:
    """The single read entry point every dashboard, chart, table and export calls. Counters and
    ratios are read from `DailyRollup`; distributions and state metrics are computed straight from
    raw rows for the requested window — never from rollups, so a distribution can never be
    silently wrong because of a stale or missing rollup row. Read-through cached (T15): a second
    identical call is served from `FileBasedCache` at zero queries, and a bumped `data_version()`
    (a sync or a recompute) changes the cache key, so a stale entry is never served.
    `include_series=False` (T11, RISKS row 10) skips the per-bucket series for every metric —
    for a distribution/state metric that means skipping the per-scope, per-bucket raw-row query
    entirely, the dominant cost profiling found on the projects/repositories/people tables, whose
    rows never read `.series`. `include_series` is part of the cache key so the two shapes never
    collide."""
    cache_key = _cache_key(metric_keys, scope, cohort, granularity, date_from, date_to, include_series)
    cached_payload = cache.get(cache_key)
    if cached_payload is not None:
        return MetricResultSet(
            {key: _deserialize_result(key, payload) for key, payload in cached_payload.items()}
        )

    result_set = _compute_uncached(
        metric_keys, scope, date_from, date_to, cohort, granularity, include_series
    )

    ttl = get_int("METRICS_CACHE_TTL_SECONDS")
    cache.set(cache_key, {key: _serialize_result(result) for key, result in result_set.results.items()}, ttl)
    return result_set


def _compute_uncached(
    metric_keys: Sequence[str],
    scope: Scope,
    date_from: datetime.date,
    date_to: datetime.date,
    cohort: str,
    granularity: str,
    include_series: bool = True,
) -> MetricResultSet:
    metric_defs = [get_metric(key) for key in metric_keys]
    for metric_def in metric_defs:
        require_available_at_level(metric_def, scope.scope_type)

    min_sample = get_int("MIN_SAMPLE")
    previous_from, previous_to = previous_period(date_from, date_to)
    buckets = bucket_ranges(date_from, date_to, granularity)

    counter_ratio_defs = [
        metric_def
        for metric_def in metric_defs
        if isinstance(metric_def.calculator, (CounterCalc, RatioCalc))
    ]
    rows_by_key = (
        _fetch_counter_ratio_rows(counter_ratio_defs, scope, cohort, previous_from, date_to)
        if scope.access.unrestricted
        else {}
    )

    results: dict[str, MetricResult] = {}
    for metric_def in metric_defs:
        if isinstance(metric_def.calculator, (CounterCalc, RatioCalc)):
            results[metric_def.key] = _counter_ratio_result(
                metric_def,
                scope,
                rows_by_key,
                cohort,
                date_from,
                date_to,
                previous_from,
                previous_to,
                buckets,
                min_sample,
                include_series,
            )
            continue

        breakdown: tuple[BreakdownItem, ...] = ()
        value_mv = _distribution_or_state_value(metric_def, scope, cohort, date_from, date_to)
        previous_mv = _distribution_or_state_value(metric_def, scope, cohort, previous_from, previous_to)
        series = (
            tuple(
                SeriesPoint(
                    b_from, b_to, *_distribution_or_state_value(metric_def, scope, cohort, b_from, b_to)
                )
                for b_from, b_to in buckets
            )
            if include_series
            else ()
        )
        calculator = metric_def.calculator
        if isinstance(calculator, DistributionCalc) and calculator.breakdown is not None:
            period_ctx = PeriodContext(scope=scope, cohort=cohort, date_from=date_from, date_to=date_to)
            breakdown = calculator.breakdown(period_ctx)

        delta, delta_ratio = _delta(value_mv, previous_mv)
        results[metric_def.key] = MetricResult(
            key=metric_def.key,
            definition=metric_def,
            value=value_mv.value,
            previous_value=previous_mv.value,
            delta=delta,
            delta_ratio=delta_ratio,
            sample_size=value_mv.sample_size,
            previous_sample_size=previous_mv.sample_size,
            below_min_sample=value_mv.sample_size < min_sample,
            series=series,
            breakdown=breakdown,
        )
    return MetricResultSet(results)


def _fetch_counter_ratio_rows_many(
    metric_defs: Sequence[MetricDef],
    scope_type: str,
    scope_ids: Sequence[int],
    cohort: str,
    date_from: datetime.date,
    date_to: datetime.date,
) -> dict[int, dict[tuple[str, str], list[tuple[datetime.date, float | None, int]]]]:
    """The widened counterpart of `_fetch_counter_ratio_rows()`: one `DailyRollup` query covering
    every requested scope id at once (`scope_id__in=scope_ids`), grouped by scope id and then by
    `(metric_key, cohort)` exactly like the per-scope fetch — so a table of 50 repositories costs
    one query instead of 50 (RISKS row 10, this phase's `compute_many()`)."""
    keys: set[str] = set()
    cohorts: set[str] = set()
    for metric_def in metric_defs:
        resolved_cohort = _rollup_cohort(metric_def, cohort)
        cohorts.add(resolved_cohort)
        if isinstance(metric_def.calculator, CounterCalc):
            keys.add(metric_def.key)
        else:
            keys.add(metric_def.key + NUMERATOR_SUFFIX)
            keys.add(metric_def.key + DENOMINATOR_SUFFIX)

    by_scope: dict[int, dict[tuple[str, str], list[tuple[datetime.date, float | None, int]]]] = {
        scope_id: {} for scope_id in scope_ids
    }
    if not keys or not scope_ids:
        return by_scope

    rows = DailyRollup.objects.filter(
        scope_type=scope_type,
        scope_id__in=scope_ids,
        cohort__in=cohorts,
        metric_key__in=keys,
        date__gte=date_from,
        date__lte=date_to,
    ).values_list("scope_id", "metric_key", "cohort", "date", "value", "sample_size")
    for scope_id, metric_key, row_cohort, date, value, sample_size in rows:
        # `scope_id__in=scope_ids` (all concrete ints) already excludes the null-scope_id GLOBAL
        # rows a plain `DailyRollup` row can otherwise carry.
        assert scope_id is not None
        by_scope[scope_id].setdefault((metric_key, row_cohort), []).append((date, value, sample_size))
    return by_scope


def _has_person_batch(metric_def: MetricDef) -> bool:
    calculator = metric_def.calculator
    if isinstance(calculator, DistributionCalc):
        return calculator.period_by_person is not None
    if isinstance(calculator, StateCalc):
        return calculator.at_date_by_person is not None
    return False


def _other_results_by_person(
    metric_defs: Sequence[MetricDef],
    scope_ids: Sequence[int],
    cohort: str,
    date_from: datetime.date,
    date_to: datetime.date,
    previous_from: datetime.date,
    previous_to: datetime.date,
    min_sample: int,
) -> dict[int, dict[str, MetricResult]]:
    """The batched counterpart of calling `compute(other_keys, ...)` once per scope id (T11
    continuation, RISKS row 10): profiling found this per-scope fallback, not a missing index, was
    the dominant remaining cost of the people table at `--scale large` (60 people x 5
    distribution/state metrics x 2 raw queries = 600 round trips). Each metric here costs exactly
    2 queries (value, previous) regardless of how many person ids are requested. Only called with
    `include_series=False` (`compute_many()` below), so no per-bucket series is built."""
    person_ids = frozenset(scope_ids)
    result: dict[int, dict[str, MetricResult]] = {scope_id: {} for scope_id in scope_ids}
    for metric_def in metric_defs:
        calculator = metric_def.calculator
        if isinstance(calculator, DistributionCalc):
            assert calculator.period_by_person is not None
            value_by_person = calculator.period_by_person(
                PeriodContextMany(cohort=cohort, date_from=date_from, date_to=date_to, person_ids=person_ids)
            )
            previous_by_person = calculator.period_by_person(
                PeriodContextMany(
                    cohort=cohort, date_from=previous_from, date_to=previous_to, person_ids=person_ids
                )
            )
        else:
            assert isinstance(calculator, StateCalc)
            assert calculator.at_date_by_person is not None
            value_by_person = calculator.at_date_by_person(
                DayContextMany(cohort=cohort, date=date_to, person_ids=person_ids)
            )
            previous_by_person = calculator.at_date_by_person(
                DayContextMany(cohort=cohort, date=previous_to, person_ids=person_ids)
            )
        for scope_id in scope_ids:
            value_mv = value_by_person.get(scope_id, MetricValue.empty())
            previous_mv = previous_by_person.get(scope_id, MetricValue.empty())
            delta, delta_ratio = _delta(value_mv, previous_mv)
            result[scope_id][metric_def.key] = MetricResult(
                key=metric_def.key,
                definition=metric_def,
                value=value_mv.value,
                previous_value=previous_mv.value,
                delta=delta,
                delta_ratio=delta_ratio,
                sample_size=value_mv.sample_size,
                previous_sample_size=previous_mv.sample_size,
                below_min_sample=value_mv.sample_size < min_sample,
                series=(),
                breakdown=(),
            )
    return result


def compute_many(
    metric_keys: Sequence[str],
    scope_type: str,
    scope_ids: Sequence[int],
    access: ScopeFilter,
    date_from: datetime.date,
    date_to: datetime.date,
    cohort: str = Cohort.ALL,
    granularity: str = "day",
    include_series: bool = True,
) -> dict[int, MetricResultSet]:
    """Batches `compute()` over many scope ids of the same level (a table of repositories, of
    projects, of people) so it costs one `DailyRollup` query for their counter/ratio metrics
    instead of one query per row. Distribution/state metrics, and *everything* for a restricted
    `ScopeFilter`, fall back to the plain per-scope `compute()` call: rollup rows are written once
    under an unrestricted access (`calculators.base.GLOBAL_SCOPE`), so batch-reading them back for
    a narrowed caller would silently return the unrestricted number (RISKS row 3) — this is honest
    rather than clever, per the plan's deviation note; phase 10 owns profiling that path if it
    becomes the bottleneck. An empty `scope_ids` returns `{}` without touching the database.
    `include_series=False` (T11, RISKS row 10) skips the per-bucket series everywhere below —
    the caller is a table row that only reads `.value`/`.delta`/`.below_min_sample`."""
    if not scope_ids:
        return {}

    metric_defs = [get_metric(key) for key in metric_keys]
    for metric_def in metric_defs:
        require_available_at_level(metric_def, scope_type)

    if not access.unrestricted:
        return {
            scope_id: compute(
                metric_keys,
                Scope(scope_type, scope_id, access),
                date_from,
                date_to,
                cohort,
                granularity,
                include_series,
            )
            for scope_id in scope_ids
        }

    counter_ratio_defs = [
        metric_def
        for metric_def in metric_defs
        if isinstance(metric_def.calculator, (CounterCalc, RatioCalc))
    ]
    other_defs = [metric_def for metric_def in metric_defs if metric_def not in counter_ratio_defs]
    other_keys = [metric_def.key for metric_def in other_defs]

    min_sample = get_int("MIN_SAMPLE")
    previous_from, previous_to = previous_period(date_from, date_to)
    buckets = bucket_ranges(date_from, date_to, granularity)
    rows_by_scope = _fetch_counter_ratio_rows_many(
        counter_ratio_defs, scope_type, scope_ids, cohort, previous_from, date_to
    )

    # T11 continuation: when every distribution/state metric requested has a batched
    # implementation (`_has_person_batch`) and no series is needed, compute them all at once
    # across every scope id instead of falling back to one `compute()` call per scope below —
    # the fix for the people table's dominant remaining query-round-trip cost at `--scale large`.
    other_by_scope = (
        _other_results_by_person(
            other_defs, scope_ids, cohort, date_from, date_to, previous_from, previous_to, min_sample
        )
        if other_defs
        and not include_series
        and scope_type == ScopeType.PERSON
        and all(_has_person_batch(metric_def) for metric_def in other_defs)
        else None
    )

    result_sets: dict[int, MetricResultSet] = {}
    for scope_id in scope_ids:
        scope = Scope(scope_type=scope_type, scope_id=scope_id, access=access)
        rows_by_key = rows_by_scope.get(scope_id, {})
        batched = {
            metric_def.key: _counter_ratio_result(
                metric_def,
                scope,
                rows_by_key,
                cohort,
                date_from,
                date_to,
                previous_from,
                previous_to,
                buckets,
                min_sample,
                include_series,
            )
            for metric_def in counter_ratio_defs
        }
        other: Mapping[str, MetricResult]
        if other_by_scope is not None:
            other = other_by_scope.get(scope_id, {})
        else:
            other = (
                compute(other_keys, scope, date_from, date_to, cohort, granularity, include_series).results
                if other_keys
                else {}
            )
        result_sets[scope_id] = MetricResultSet({**batched, **other})
    return result_sets
