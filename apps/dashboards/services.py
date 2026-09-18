"""Assembles the page context `views.dashboard()` renders: KPI rows (plan T6) and chart cards
(plan T10/T11) so far; table instances join this module in a later task of this phase. Holds no
query of its own beyond `metrics.compute()`/`kpis.build_kpi_row()`/`charts.CHART_REGISTRY` and
`apps/dashboards/selectors.py`."""

from __future__ import annotations

from django.urls import reverse

from apps.dashboards import kpis, selectors
from apps.dashboards.charts import CHART_REGISTRY, chart_available_at_level
from apps.dashboards.params import DashboardParams
from apps.dashboards.tables import TableContext, build_table_context
from apps.metrics.models import ScopeType
from apps.metrics.types import Scope

_TABLE_KEYS_BY_LEVEL: dict[str, tuple[str, ...]] = {
    ScopeType.GLOBAL: ("projects", "people", "recent_prs"),
    ScopeType.PROJECT: ("repositories", "people", "recent_prs"),
    ScopeType.REPO: ("people", "recent_prs"),
}


def _chart_url(scope: Scope, params: DashboardParams, chart_key: str) -> str:
    """The URL `charts.js` fetches for one chart card: the page's own query string, plus the
    scope this page renders — `/api/charts/<key>/` carries no scope in its path (plan §4), so the
    page has to hand it one explicitly."""
    query = params.to_query_dict().copy()
    query["scope_type"] = scope.scope_type
    if scope.scope_id is not None:
        query["scope_id"] = str(scope.scope_id)
    return f"{reverse('dashboards:chart_json', args=[chart_key])}?{query.urlencode()}"


def _build_chart_cards(scope: Scope, params: DashboardParams) -> list[dict[str, object]]:
    cards = []
    for spec in CHART_REGISTRY.values():
        if not chart_available_at_level(spec, scope.scope_type):
            continue
        payload = spec.build(scope, params)
        cards.append(
            {
                "key": spec.key,
                "title": spec.title,
                "payload": payload,
                "url": _chart_url(scope, params, spec.key),
            }
        )
    return cards


def _build_tables(scope: Scope, params: DashboardParams) -> list[TableContext]:
    table_keys = _TABLE_KEYS_BY_LEVEL.get(scope.scope_type, ())
    return [build_table_context(table_key, scope, params) for table_key in table_keys]


def build_dashboard(scope: Scope, params: DashboardParams) -> dict[str, object]:
    if params.mode == "day":
        kpi_rows = [
            kpis.build_kpi_row(scope, row, params.day, params.day, "day", params.cohort)
            for row in kpis.DAY_ROWS
        ]
        return {
            "mode": "day",
            "kpi_rows": kpi_rows,
            "prs_opened": list(selectors.prs_opened_on_day(scope, params.day)),
            "prs_merged": list(selectors.prs_merged_on_day(scope, params.day)),
            "person_activity": selectors.person_activity_for_day(scope, params.day),
        }

    kpi_rows = [
        kpis.build_kpi_row(scope, row, params.date_from, params.date_to, params.granularity, params.cohort)
        for row in kpis.PERIOD_ROWS
    ]
    return {
        "mode": "period",
        "kpi_rows": kpi_rows,
        "charts": _build_chart_cards(scope, params),
        "tables": _build_tables(scope, params),
    }
