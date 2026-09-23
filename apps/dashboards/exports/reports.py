"""The seven-sheet dashboard report (plan §5, spec §10.6): Summary, Trends (with native Excel
charts), the level's own tables (Projects/Repositories/People), PRs, Violations, Metrics and
Parameters — one workbook `build_report()` produces from the same `Scope`/`DashboardParams` a
dashboard page renders, so a number in the report can never disagree with the page it came from.

`build_report()` is pure (`scope`, `params`, `user`, `language` -> bytes): no request, so the huey
background task (T22) can call it with `translation.override(job.language)` and nothing else.
`base_url`, when given, turns an in-app row link (a project/PR/person page) into an absolute URL a
spreadsheet can open outside the browser session that generated it; without one the cell still
carries its display text, just no clickable hyperlink (`exports/xlsx.py`'s `url` column already
renders `href=None` that way)."""

from __future__ import annotations

import datetime
import tomllib
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import translation
from django.utils.timezone import localtime, now
from django.utils.translation import gettext as _

from apps.catalog.services import get_setting
from apps.dashboards import kpis
from apps.dashboards.charts import CHART_REGISTRY
from apps.dashboards.exports.columns import (
    PEOPLE_COLUMNS,
    PROJECTS_COLUMNS,
    PULL_REQUESTS_COLUMNS,
    REPOSITORIES_COLUMNS,
    ExportColumn,
    absolutize_urls,
)
from apps.dashboards.exports.xlsx import open_workbook, write_sheet
from apps.dashboards.params import DashboardParams
from apps.dashboards.services import TABLE_KEYS_BY_LEVEL
from apps.dashboards.tables import TABLE_SPECS
from apps.github_sync.models import SyncRun
from apps.metrics.models import Cohort, ScopeType
from apps.metrics.registry import get_metric
from apps.metrics.selectors import scoped_violations
from apps.metrics.services import compute
from apps.metrics.timeframe import day_end_exclusive, day_start
from apps.metrics.types import Scope
from apps.policy.messages import rule_label

_TRENDS_CHART_KEYS: tuple[str, ...] = ("throughput", "ai_adoption", "latency")

_TABLE_SHEET_TITLES = {
    "projects": ("Projects", PROJECTS_COLUMNS),
    "repositories": ("Repositories", REPOSITORIES_COLUMNS),
    "people": ("People", PEOPLE_COLUMNS),
}


def _sheet_title(english: str) -> str:
    """Translates a fixed sheet-name literal into the report's own language — deliberately not
    `gettext_lazy` at module scope, since the same process renders reports in several languages."""
    return _(english)


# --- Summary -----------------------------------------------------------------------------------

_SUMMARY_COLUMNS = (
    ExportColumn(key="title", title="Metric", type="text", width=32),
    ExportColumn(key="value", title="Value", type="float", width=14),
    ExportColumn(key="previous_value", title="Previous value", type="float", width=14),
    ExportColumn(key="delta", title="Delta", type="float", width=14),
    ExportColumn(key="ai_value", title="AI cohort", type="float", width=14),
    ExportColumn(key="non_ai_value", title="Non-AI cohort", type="float", width=14),
    ExportColumn(key="unit", title="Unit", type="text", width=10),
    ExportColumn(key="sample_size", title="Sample size", type="int", width=10),
    ExportColumn(key="below_min_sample", title="Below minimum sample", type="text", width=12),
)


def _summary_metric_keys(scope: Scope, params: DashboardParams) -> list[str]:
    rows = kpis.PERIOD_ROWS if params.mode == "period" else kpis.DAY_ROWS
    keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for spec in row:
            for key in (spec.metric_key, spec.secondary_key):
                if key and key not in seen and scope.scope_type in get_metric(key).levels:
                    seen.add(key)
                    keys.append(key)
    return keys


def _summary_rows(scope: Scope, params: DashboardParams, metric_keys: list[str]) -> list[dict[str, object]]:
    if not metric_keys:
        return []
    # The Summary sheet row below only reads `.value`/`.previous_value`/`.delta`/`.sample_size`/
    # `.below_min_sample` off each result — never `.series` — so all three calls pass
    # `include_series=False` (T11) to skip their per-bucket series entirely.
    all_set = compute(
        metric_keys,
        scope,
        params.date_from,
        params.date_to,
        cohort=Cohort.ALL,
        granularity=params.granularity,
        include_series=False,
    )
    ai_set = compute(
        metric_keys,
        scope,
        params.date_from,
        params.date_to,
        cohort=Cohort.AI,
        granularity=params.granularity,
        include_series=False,
    )
    non_ai_set = compute(
        metric_keys,
        scope,
        params.date_from,
        params.date_to,
        cohort=Cohort.NON_AI,
        granularity=params.granularity,
        include_series=False,
    )
    rows = []
    for key in metric_keys:
        result = all_set[key]
        definition = result.definition
        rows.append(
            {
                "title": str(definition.title),
                "value": result.value,
                "previous_value": result.previous_value,
                "delta": result.delta,
                "ai_value": ai_set[key].value,
                "non_ai_value": non_ai_set[key].value,
                "unit": definition.unit,
                "sample_size": result.sample_size,
                "below_min_sample": _("Yes") if result.below_min_sample else _("No"),
            }
        )
    return rows


# --- Trends --------------------------------------------------------------------------------------

_TRENDS_COLUMNS = (
    ExportColumn(key="date", title="Date", type="text", width=12),
    ExportColumn(key="ai_merged", title="PRs merged (AI)", type="int", width=14),
    ExportColumn(key="non_ai_merged", title="PRs merged (Non-AI)", type="int", width=16),
    ExportColumn(key="ai_pr_share", title="AI PR share", type="percent", width=12),
    ExportColumn(key="lead_time_p90", title="Lead time (p90)", type="duration", width=14),
    ExportColumn(key="ttfr_p90", title="Time to first review (p90)", type="duration", width=18),
)


def _trends_rows(scope: Scope, params: DashboardParams) -> list[dict[str, object]]:
    """Rebuilds the three time-series chart payloads a dashboard page renders (`CHART_REGISTRY`),
    pivoted into one row per bucket — never a separate `compute()` call, so the Trends sheet's
    numbers can never drift from what the page's own charts show."""
    payloads = {key: CHART_REGISTRY[key].build(scope, params) for key in _TRENDS_CHART_KEYS}
    throughput, ai_adoption, latency = (payloads[key] for key in _TRENDS_CHART_KEYS)
    labels = throughput.labels
    rows = []
    for index, label in enumerate(labels):
        rows.append(
            {
                "date": label,
                "ai_merged": throughput.datasets[0].data[index],
                "non_ai_merged": throughput.datasets[1].data[index],
                "ai_pr_share": ai_adoption.datasets[0].data[index],
                "lead_time_p90": latency.datasets[0].data[index],
                "ttfr_p90": latency.datasets[1].data[index],
            }
        )
    return rows


def _add_trends_charts(workbook, worksheet, sheet_name: str, row_count: int) -> None:
    if row_count == 0:
        return
    last_row = row_count  # header is row 0, data starts at row 1
    categories = [sheet_name, 1, 0, last_row, 0]

    throughput_chart = workbook.add_chart({"type": "column", "subtype": "stacked"})
    throughput_chart.add_series(
        {"name": _("PRs merged (AI)"), "categories": categories, "values": [sheet_name, 1, 1, last_row, 1]}
    )
    throughput_chart.add_series(
        {
            "name": _("PRs merged (Non-AI)"),
            "categories": categories,
            "values": [sheet_name, 1, 2, last_row, 2],
        }
    )
    throughput_chart.set_title({"name": _("Throughput")})
    worksheet.insert_chart(last_row + 3, 0, throughput_chart)

    ai_share_chart = workbook.add_chart({"type": "line"})
    ai_share_chart.add_series(
        {"name": _("AI PR share"), "categories": categories, "values": [sheet_name, 1, 3, last_row, 3]}
    )
    ai_share_chart.set_title({"name": _("AI adoption")})
    worksheet.insert_chart(last_row + 3, 6, ai_share_chart)

    lead_time_chart = workbook.add_chart({"type": "line"})
    for col_index, series_name in ((4, _("Lead time (p90)")), (5, _("Time to first review (p90)"))):
        lead_time_chart.add_series(
            {
                "name": series_name,
                "categories": categories,
                "values": [sheet_name, 1, col_index, last_row, col_index],
            }
        )
    lead_time_chart.set_title({"name": _("Latency")})
    worksheet.insert_chart(last_row + 18, 0, lead_time_chart)


# --- PRs / Violations ------------------------------------------------------------------------


def _pr_rows(scope: Scope, params: DashboardParams, base_url: str) -> list[dict[str, object]]:
    rows = TABLE_SPECS["pull_requests"].row_builder(scope, params)
    return absolutize_urls(rows, PULL_REQUESTS_COLUMNS, base_url)


_VIOLATIONS_COLUMNS = (
    ExportColumn(key="repository", title="Repository", type="text", width=25),
    ExportColumn(key="pr_number", title="PR", type="url", link_key="pr_url", width=10),
    ExportColumn(key="rule", title="Rule", type="text", width=28),
    ExportColumn(key="severity", title="Severity", type="text", width=10),
    ExportColumn(key="status", title="Status", type="text", width=14),
    ExportColumn(key="pr_created_at", title="PR opened", type="datetime", width=18),
    ExportColumn(key="created_at", title="Recorded", type="datetime", width=18),
    ExportColumn(key="resolved_by", title="Resolved by", type="text", width=20),
)


def _violation_rows(scope: Scope, params: DashboardParams, base_url: str) -> list[dict[str, object]]:
    queryset = (
        scoped_violations(scope)
        .filter(
            pull_request__created_at__gte=day_start(params.date_from),
            pull_request__created_at__lt=day_end_exclusive(params.date_to),
        )
        .select_related("pull_request", "pull_request__repository", "resolved_by")
        .order_by("-pull_request__created_at", "-pk")
    )
    rows = [
        {
            "repository": violation.pull_request.repository.full_name,
            "pr_number": violation.pull_request.number,
            "pr_url": reverse("dashboards:pull_request_detail", args=[violation.pull_request_id]),
            "rule": rule_label(violation.rule_code),
            "severity": violation.get_severity_display(),
            "status": violation.get_status_display(),
            "pr_created_at": violation.pull_request.created_at,
            "created_at": violation.created_at,
            "resolved_by": violation.resolved_by.get_username() if violation.resolved_by else "",
        }
        for violation in queryset
    ]
    return absolutize_urls(rows, _VIOLATIONS_COLUMNS, base_url)


# --- Metrics / Parameters ----------------------------------------------------------------------

_METRICS_COLUMNS = (
    ExportColumn(key="key", title="Key", type="text", width=26),
    ExportColumn(key="title", title="Title", type="text", width=28),
    ExportColumn(key="description", title="Description", type="text", width=50),
    ExportColumn(key="formula", title="Formula", type="text", width=40),
    ExportColumn(key="unit", title="Unit", type="text", width=10),
    ExportColumn(key="direction", title="Direction", type="text", width=16),
    ExportColumn(key="levels", title="Levels", type="text", width=20),
    ExportColumn(key="cohorts", title="Cohorts", type="text", width=16),
)

_PARAMETERS_COLUMNS = (
    ExportColumn(key="name", title="Parameter", type="text", width=24),
    ExportColumn(key="value", title="Value", type="text", width=50),
)


def _metrics_used_on_page(summary_keys: list[str], table_keys: tuple[str, ...]) -> list[str]:
    used: set[str] = set(summary_keys)
    for key in _TRENDS_CHART_KEYS:
        used.update(CHART_REGISTRY[key].metric_keys)
    for table_key in table_keys:
        used.update(TABLE_SPECS[table_key].metric_keys)
    return sorted(used)


def _metrics_rows(metric_keys: list[str]) -> list[dict[str, object]]:
    rows = []
    for key in metric_keys:
        definition = get_metric(key)
        rows.append(
            {
                "key": definition.key,
                "title": str(definition.title),
                "description": str(definition.description),
                "formula": definition.formula,
                "unit": definition.unit,
                "direction": definition.direction,
                "levels": ", ".join(level for level in ScopeType.values if level in definition.levels),
                "cohorts": _("all, ai, non_ai") if definition.supports_cohorts else _("all only"),
            }
        )
    return rows


def _tool_version() -> str:
    pyproject_path = settings.BASE_DIR / "pyproject.toml"
    with pyproject_path.open("rb") as handle:
        data = tomllib.load(handle)
    return str(data.get("project", {}).get("version", "unknown"))


def _last_successful_sync() -> datetime.datetime | None:
    run = SyncRun.objects.filter(status=SyncRun.Status.SUCCESS).order_by("-finished_at").first()
    return run.finished_at if run else None


def _parameters_rows(
    scope: Scope, params: DashboardParams, *, user: User, language: str
) -> list[dict[str, object]]:
    tzinfo = ZoneInfo(settings.REPORT_TIMEZONE)
    rows: list[dict[str, object]] = []
    rows.append({"name": _("Filter: %(key)s") % {"key": "scope_type"}, "value": scope.scope_type})
    if scope.scope_id is not None:
        rows.append({"name": _("Filter: %(key)s") % {"key": "scope_id"}, "value": str(scope.scope_id)})
    for key, values in params.to_query_dict().lists():
        rows.append({"name": _("Filter: %(key)s") % {"key": key}, "value": ", ".join(values)})

    last_sync = _last_successful_sync()
    rows.extend(
        [
            {"name": _("User"), "value": user.get_username()},
            {
                "name": _("Generated at"),
                "value": localtime(now(), timezone=tzinfo).strftime("%Y-%m-%d %H:%M"),
            },
            {
                "name": _("Last successful sync"),
                "value": localtime(last_sync, timezone=tzinfo).strftime("%Y-%m-%d %H:%M")
                if last_sync
                else _("Never"),
            },
            {"name": _("Tool version"), "value": _tool_version()},
            {
                "name": _("AI cohort includes suspected"),
                "value": _("Yes") if get_setting("AI_COHORT_INCLUDE_SUSPECTED") else _("No"),
            },
            {"name": _("Report timezone"), "value": settings.REPORT_TIMEZONE},
            {"name": _("Report language"), "value": language},
        ]
    )
    return rows


# --- Entry point ---------------------------------------------------------------------------------


def report_filename(scope: Scope, params: DashboardParams) -> str:
    scope_slug = scope.scope_type if scope.scope_id is None else f"{scope.scope_type}-{scope.scope_id}"
    period = (
        params.day.isoformat()
        if params.mode == "day"
        else f"{params.date_from.isoformat()}_{params.date_to.isoformat()}"
    )
    return f"pr-radar_report_{scope_slug}_{period}.xlsx"


def build_report(
    scope: Scope, params: DashboardParams, *, user: User, language: str, base_url: str = ""
) -> bytes:
    """The full seven-sheet workbook (plan §5). Opened `constant_memory=False` (DECISIONS): native
    charts and several sheets need every sheet to stay addressable, and a report is bounded by
    `EXPORT_SYNC_MAX_ROWS` or runs in the background (T22) — the memory cost is paid where it is
    affordable."""
    with translation.override(language):
        workbook, buffer, formats = open_workbook(constant_memory=False)

        summary_keys = _summary_metric_keys(scope, params)
        summary_rows = _summary_rows(scope, params, summary_keys)
        write_sheet(workbook, formats, _sheet_title("Summary"), list(_SUMMARY_COLUMNS), summary_rows)

        trends_rows = _trends_rows(scope, params)
        trends_sheet_name = _sheet_title("Trends")
        trends_worksheet = write_sheet(
            workbook, formats, trends_sheet_name, list(_TRENDS_COLUMNS), trends_rows
        )
        _add_trends_charts(workbook, trends_worksheet, trends_sheet_name, len(trends_rows))

        table_keys = tuple(
            key for key in TABLE_KEYS_BY_LEVEL.get(scope.scope_type, ()) if key in _TABLE_SHEET_TITLES
        )
        for table_key in table_keys:
            title, columns = _TABLE_SHEET_TITLES[table_key]
            spec = TABLE_SPECS[table_key]
            rows = absolutize_urls(spec.row_builder(scope, params), columns, base_url)
            write_sheet(workbook, formats, _sheet_title(title), list(columns), rows)

        write_sheet(
            workbook,
            formats,
            _sheet_title("PRs"),
            list(PULL_REQUESTS_COLUMNS),
            _pr_rows(scope, params, base_url),
        )
        write_sheet(
            workbook,
            formats,
            _sheet_title("Violations"),
            list(_VIOLATIONS_COLUMNS),
            _violation_rows(scope, params, base_url),
        )

        metric_keys = _metrics_used_on_page(summary_keys, table_keys)
        write_sheet(
            workbook, formats, _sheet_title("Metrics"), list(_METRICS_COLUMNS), _metrics_rows(metric_keys)
        )

        write_sheet(
            workbook,
            formats,
            _sheet_title("Parameters"),
            list(_PARAMETERS_COLUMNS),
            _parameters_rows(scope, params, user=user, language=language),
        )

        workbook.close()
        return buffer.getvalue()
