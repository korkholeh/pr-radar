"""T6: `kpis.py`'s declared rows and `services.build_dashboard()`."""

from __future__ import annotations

import datetime

import pytest

from apps.accounts.selectors import ScopeFilter
from apps.dashboards import kpis
from apps.dashboards.params import DashboardParams
from apps.dashboards.services import build_dashboard
from apps.metrics.models import ScopeType
from apps.metrics.registry import get_metric, require_available_at_level
from apps.metrics.services import compute
from apps.metrics.types import Scope

ALL_ROWS = kpis.PERIOD_ROWS + kpis.DAY_ROWS


@pytest.mark.parametrize("row", ALL_ROWS)
@pytest.mark.parametrize("level", [ScopeType.GLOBAL, ScopeType.PROJECT, ScopeType.REPO])
def test_row_metric_keys_are_registered_and_available_at_every_rendered_level(row, level):
    for spec in row:
        definition = get_metric(spec.metric_key)
        require_available_at_level(definition, level)
        if spec.secondary_key:
            require_available_at_level(get_metric(spec.secondary_key), level)


@pytest.mark.django_db
def test_compare_row_matches_direct_compute_calls(django_user_model):
    access = ScopeFilter(unrestricted=True)
    scope = Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=access)
    date_from = datetime.date(2026, 8, 1)
    date_to = datetime.date(2026, 8, 31)

    cards = kpis.build_kpi_row(scope, kpis.QUALITY_ROW, date_from, date_to, "day", "all")

    for spec, card in zip(kpis.QUALITY_ROW, cards, strict=True):
        assert card.compare is not None
        expected_all = compute([spec.metric_key], scope, date_from, date_to, cohort="all", granularity="day")
        expected_ai = compute([spec.metric_key], scope, date_from, date_to, cohort="ai", granularity="day")
        expected_non_ai = compute(
            [spec.metric_key], scope, date_from, date_to, cohort="non_ai", granularity="day"
        )
        # Round-1 review: a compare card's headline value/delta/sparkline (spec §10.2's mandatory
        # anatomy) must come from the cohort-ALL result, not silently be the AI cohort's.
        assert card.result.value == expected_all[spec.metric_key].value
        assert card.compare["ai"].value == expected_ai[spec.metric_key].value
        assert card.compare["non_ai"].value == expected_non_ai[spec.metric_key].value


@pytest.mark.django_db
def test_build_dashboard_period_mode_returns_the_three_rows():
    access = ScopeFilter(unrestricted=True)
    scope = Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=access)
    params = DashboardParams(
        mode="period",
        preset="30d",
        date_from=datetime.date(2026, 8, 1),
        date_to=datetime.date(2026, 8, 31),
        day=datetime.date(2026, 8, 31),
        granularity="day",
        granularity_is_auto=True,
        cohort="all",
        project_ids=(),
        repository_ids=(),
        q="",
        sort="",
        page=1,
        table="",
    )
    context = build_dashboard(scope, params)
    assert context["mode"] == "period"
    assert len(context["kpi_rows"]) == 3


@pytest.mark.django_db
def test_build_dashboard_day_mode_returns_the_two_rows():
    access = ScopeFilter(unrestricted=True)
    scope = Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=access)
    params = DashboardParams(
        mode="day",
        preset="30d",
        date_from=datetime.date(2026, 8, 31),
        date_to=datetime.date(2026, 8, 31),
        day=datetime.date(2026, 8, 31),
        granularity="day",
        granularity_is_auto=True,
        cohort="all",
        project_ids=(),
        repository_ids=(),
        q="",
        sort="",
        page=1,
        table="",
    )
    context = build_dashboard(scope, params)
    assert context["mode"] == "day"
    assert len(context["kpi_rows"]) == 2
