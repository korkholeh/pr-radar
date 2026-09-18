"""Value types shared by the registry, the calculators and `compute()`. No Django ORM imports here
— everything below is a plain dataclass/NamedTuple so it can be cached and compared cheaply."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Literal, NamedTuple

if TYPE_CHECKING:
    from apps.accounts.selectors import ScopeFilter
    from apps.metrics.registry import MetricDef

Granularity = Literal["day", "week", "month"]


@dataclass(frozen=True)
class Scope:
    """What a `compute()` call is scoped to: a level (`ScopeType` value), an optional id at that
    level, and the caller's `ScopeFilter` — every selector composes on both."""

    scope_type: str
    scope_id: int | None
    access: ScopeFilter


class MetricValue(NamedTuple):
    """What every calculator returns. `value is None` iff `sample_size == 0` — spec §15's "no
    data → `None`, never `0`" is enforced here, once, rather than per calculator."""

    value: float | None
    sample_size: int

    @staticmethod
    def empty() -> MetricValue:
        return MetricValue(None, 0)


@dataclass(frozen=True)
class SeriesPoint:
    date_from: date
    date_to: date
    value: float | None
    sample_size: int


@dataclass(frozen=True)
class BreakdownItem:
    label: str
    value: float | None
    sample_size: int


@dataclass(frozen=True)
class MetricResult:
    key: str
    definition: MetricDef
    value: float | None
    previous_value: float | None
    delta: float | None
    delta_ratio: float | None
    sample_size: int
    previous_sample_size: int
    below_min_sample: bool
    series: tuple[SeriesPoint, ...]
    breakdown: tuple[BreakdownItem, ...] = ()


@dataclass(frozen=True)
class MetricResultSet:
    results: Mapping[str, MetricResult]

    def __getitem__(self, key: str) -> MetricResult:
        return self.results[key]

    def __iter__(self):
        return iter(self.results.values())

    def __len__(self) -> int:
        return len(self.results)
