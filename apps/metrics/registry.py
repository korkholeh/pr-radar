"""The metric registry (spec §8.1, ADR 0007): one `MetricDef` per metric, keyed by its
calculator's strategy type so a distribution can never reach `DailyRollup` by accident
(`rollups.py` only ever iterates `CounterCalc`/`RatioCalc` defs — RISKS row 7)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from django.utils.functional import Promise

from apps.metrics.calculators.base import (
    BatchFunction,
    DayContext,
    DayContextMany,
    PeriodContext,
    PeriodContextMany,
)
from apps.metrics.models import ScopeType
from apps.metrics.types import BreakdownItem, MetricValue

UNITS = frozenset({"count", "ratio", "duration", "lines", "breakdown"})
DIRECTIONS = frozenset({"higher_is_better", "lower_is_better", "neutral"})


@dataclass(frozen=True)
class CounterCalc:
    daily: Callable[[DayContext], MetricValue]
    batch: BatchFunction
    """Computes this counter for every scope a day's rows touch in O(1) queries — what
    `rollups.rebuild()` calls instead of `daily()` once per scope (RISKS row 10)."""


@dataclass(frozen=True)
class RatioCalc:
    numerator: Callable[[DayContext], MetricValue]
    denominator: Callable[[DayContext], MetricValue]
    numerator_batch: BatchFunction
    denominator_batch: BatchFunction


@dataclass(frozen=True)
class DistributionCalc:
    period: Callable[[PeriodContext], MetricValue]
    breakdown: Callable[[PeriodContext], tuple[BreakdownItem, ...]] | None = None
    # Optional: computes this metric for every person in `PeriodContextMany.person_ids` in O(1)
    # queries instead of one `period()` call per person — what `compute_many()` uses for the
    # PERSON-level table builders (`rows.py::people_rows()`) when available (T11 continuation).
    period_by_person: Callable[[PeriodContextMany], dict[int, MetricValue]] | None = None


@dataclass(frozen=True)
class StateCalc:
    at_date: Callable[[DayContext], MetricValue]
    # The `StateCalc` counterpart of `DistributionCalc.period_by_person`.
    at_date_by_person: Callable[[DayContextMany], dict[int, MetricValue]] | None = None


Calculator = CounterCalc | RatioCalc | DistributionCalc | StateCalc

_KIND_BY_CALCULATOR_TYPE: dict[type, str] = {
    CounterCalc: "counter",
    RatioCalc: "ratio",
    DistributionCalc: "distribution",
    StateCalc: "state",
}


@dataclass(frozen=True)
class MetricDef:
    key: str
    title: Promise
    description: Promise
    unit: str
    direction: str
    kind: str
    levels: frozenset[str]
    supports_cohorts: bool
    calculator: Calculator
    formula: str
    params: Mapping[str, object] = field(default_factory=dict)


class UnknownMetricError(ValueError):
    pass


class MetricNotAvailableAtLevel(ValueError):
    pass


_REGISTRY: dict[str, MetricDef] = {}
REGISTRY: Mapping[str, MetricDef] = _REGISTRY


def _register(metric_def: MetricDef) -> MetricDef:
    if metric_def.key in _REGISTRY:
        raise ValueError(f"Duplicate metric key {metric_def.key!r}.")
    expected_kind = _KIND_BY_CALCULATOR_TYPE.get(type(metric_def.calculator))
    if expected_kind is None:
        raise ValueError(
            f"Metric {metric_def.key!r} has an unrecognised calculator type "
            f"{type(metric_def.calculator).__name__}."
        )
    if metric_def.kind != expected_kind:
        raise ValueError(
            f"Metric {metric_def.key!r} declares kind={metric_def.kind!r} but its calculator is a "
            f"{type(metric_def.calculator).__name__} (kind={expected_kind!r})."
        )
    if not metric_def.levels:
        raise ValueError(f"Metric {metric_def.key!r} has an empty levels set.")
    if not metric_def.levels <= frozenset(ScopeType.values):
        raise ValueError(f"Metric {metric_def.key!r} has an unknown scope level in {metric_def.levels!r}.")
    if not isinstance(metric_def.title, Promise):
        raise ValueError(f"Metric {metric_def.key!r} title must be a lazy translation (gettext_lazy).")
    if not isinstance(metric_def.description, Promise):
        raise ValueError(f"Metric {metric_def.key!r} description must be a lazy translation (gettext_lazy).")
    if metric_def.unit not in UNITS:
        raise ValueError(f"Metric {metric_def.key!r} has an unknown unit {metric_def.unit!r}.")
    if metric_def.direction not in DIRECTIONS:
        raise ValueError(f"Metric {metric_def.key!r} has an unknown direction {metric_def.direction!r}.")
    _REGISTRY[metric_def.key] = metric_def
    return metric_def


def get_metric(key: str) -> MetricDef:
    try:
        return _REGISTRY[key]
    except KeyError:
        raise UnknownMetricError(key) from None


def metrics_for_level(scope_type: str) -> tuple[MetricDef, ...]:
    return tuple(metric_def for metric_def in _REGISTRY.values() if scope_type in metric_def.levels)


def require_available_at_level(metric_def: MetricDef, scope_type: str) -> None:
    if scope_type not in metric_def.levels:
        raise MetricNotAvailableAtLevel(
            f"Metric {metric_def.key!r} is not available at scope level {scope_type!r}."
        )


def _load_calculators() -> None:
    """Imported for side effect: every `calculators/*.py` module registers its metrics at import
    time. Deferred to the bottom of this module (rather than a top-level import) because each
    calculator module imports `MetricDef`/`_register`/the strategy dataclasses from here."""
    from apps.metrics.calculators import (  # noqa: F401
        adoption,
        compliance,
        flow,
        quality,
        state,
    )


_load_calculators()
