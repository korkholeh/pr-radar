from pathlib import Path

import pytest
from django.utils.translation import gettext_lazy as _

from apps.metrics import registry as registry_module
from apps.metrics.calculators.base import DayContext
from apps.metrics.models import ScopeType
from apps.metrics.registry import (
    REGISTRY,
    CounterCalc,
    MetricDef,
    _register,
)
from apps.metrics.types import MetricValue


@pytest.fixture(autouse=True)
def _restore_registry():
    """`_register` mutates the module-global `_REGISTRY`; without this, a key registered by one
    test here would leak into every other test in the suite (e.g. `docs/METRICS.md`'s freshness
    check, which iterates the real `REGISTRY`)."""
    keys_before = set(registry_module._REGISTRY)
    yield
    for key in set(registry_module._REGISTRY) - keys_before:
        del registry_module._REGISTRY[key]


def _counter_calc() -> CounterCalc:
    return CounterCalc(daily=lambda ctx: MetricValue.empty(), batch=lambda cohort, date: {})


def _base_kwargs(**overrides: object) -> dict:
    kwargs = dict(
        key="test_metric_key_unique_xyz",
        title=_("Test metric"),
        description=_("A metric used only by test_registry.py."),
        unit="count",
        direction="higher_is_better",
        kind="counter",
        levels=frozenset({ScopeType.GLOBAL}),
        supports_cohorts=False,
        calculator=_counter_calc(),
        formula="count of things",
    )
    kwargs.update(overrides)
    return kwargs


def test_register_accepts_a_well_formed_counter_metric():
    metric_def = _register(MetricDef(**_base_kwargs()))
    assert metric_def.key == "test_metric_key_unique_xyz"


def test_register_rejects_a_duplicate_key():
    _register(MetricDef(**_base_kwargs(key="dup_key_xyz")))
    with pytest.raises(ValueError, match="Duplicate"):
        _register(MetricDef(**_base_kwargs(key="dup_key_xyz")))


def test_register_rejects_a_kind_that_contradicts_its_calculator():
    with pytest.raises(ValueError, match="declares kind"):
        _register(MetricDef(**_base_kwargs(key="wrong_kind_xyz", kind="ratio")))


def test_register_rejects_an_empty_levels_set():
    with pytest.raises(ValueError, match="empty levels"):
        _register(MetricDef(**_base_kwargs(key="empty_levels_xyz", levels=frozenset())))


def test_register_rejects_an_unknown_level():
    with pytest.raises(ValueError, match="unknown scope level"):
        _register(MetricDef(**_base_kwargs(key="unknown_level_xyz", levels=frozenset({"planet"}))))


def test_register_rejects_a_non_lazy_title():
    with pytest.raises(ValueError, match="title must be a lazy translation"):
        _register(MetricDef(**_base_kwargs(key="non_lazy_title_xyz", title="Not lazy")))


def test_register_rejects_a_non_lazy_description():
    with pytest.raises(ValueError, match="description must be a lazy translation"):
        _register(MetricDef(**_base_kwargs(key="non_lazy_desc_xyz", description="Not lazy")))


def test_register_rejects_an_unknown_unit():
    with pytest.raises(ValueError, match="unknown unit"):
        _register(MetricDef(**_base_kwargs(key="unknown_unit_xyz", unit="furlongs")))


def test_register_rejects_an_unknown_direction():
    with pytest.raises(ValueError, match="unknown direction"):
        _register(MetricDef(**_base_kwargs(key="unknown_direction_xyz", direction="sideways")))


def test_register_rejects_an_unrecognised_calculator_type():
    with pytest.raises(ValueError, match="unrecognised calculator"):
        _register(MetricDef(**_base_kwargs(key="bad_calc_xyz", calculator=object())))


def test_metric_value_empty_is_none_and_zero():
    assert MetricValue.empty() == (None, 0)


@pytest.mark.parametrize("key", sorted(REGISTRY), ids=lambda key: key)
def test_every_registered_metric_has_a_test(key):
    """Acceptance criterion 1: every metric has a test over a hand-computed dataset. A metric
    added to a calculator module without a corresponding test case here fails the build instead
    of silently shipping untested (the gap this closes: `lead_time_p90`,
    `time_to_first_review_p90`, `pr_size_p50` and `reviewer_response_p50` shipped with no test at
    all until this gate was added). Scoped to a call passing the key as a quoted argument to
    `get_metric(...)` (or to `test_metrics_quality.py`'s local `_ratio(...)` helper, which
    indirects through `get_metric` for its ratio metrics) — not any mention of the key as a
    string, which a comment, an unrelated list or an import would also satisfy. The module list is
    discovered by glob so a new `test_metrics_*.py` module counts automatically without editing
    this test."""
    tests_dir = Path(__file__).parent
    test_modules = sorted(tests_dir.glob("test_metrics_*.py"))
    assert test_modules, "no test_metrics_*.py modules found"
    combined = "\n".join(module.read_text() for module in test_modules)
    call_sites = (f'get_metric("{key}")', f"get_metric('{key}')", f'_ratio("{key}"', f"_ratio('{key}'")
    assert any(call_site in combined for call_site in call_sites), (
        f"metric {key!r} is not referenced by get_metric(...) in any of {[m.name for m in test_modules]}"
    )


def test_day_context_is_a_frozen_dataclass():
    import dataclasses

    from apps.metrics.types import Scope

    ctx = DayContext(
        scope=Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=None), cohort="all", date=None
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.cohort = "ai"  # type: ignore[misc]
