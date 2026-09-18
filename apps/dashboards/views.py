from __future__ import annotations

import logging

from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render

from apps.accounts.selectors import scope_for_user
from apps.activity.selectors import pull_requests_in_scope
from apps.ai_detection.models import Tool
from apps.ai_detection.selectors import signals_for_pull_request
from apps.catalog.models import Project, Repository
from apps.catalog.selectors import projects_in_scope, repositories_in_scope
from apps.catalog.services import get_int
from apps.dashboards import params as params_module
from apps.dashboards.charts import CHART_REGISTRY, chart_available_at_level
from apps.dashboards.exports.columns import TABLE_SPECS, TableSpec, export_columns
from apps.dashboards.exports.csv import stream_csv
from apps.dashboards.exports.xlsx import write_xlsx
from apps.dashboards.params import DashboardParams
from apps.dashboards.services import build_dashboard
from apps.dashboards.tables import build_table_context, full_rows
from apps.metrics.models import ScopeType
from apps.metrics.services import scope_for
from apps.metrics.types import Scope
from config.htmx import is_htmx

logger = logging.getLogger(__name__)


def dashboard(
    request: HttpRequest, scope_type: str = ScopeType.GLOBAL, pk: int | None = None
) -> HttpResponse:
    """One view for the Overview/Project/Repository pages, both Period and Day mode (plan §2). An
    out-of-scope or unknown id **in the path** is a 404 (`get_object_or_404` on a scoped
    queryset) — unlike the same id in the query string, which `DashboardParams.parse()` drops
    silently (RISKS row 3)."""
    access = scope_for_user(request.user)
    scope_object: Project | Repository | None = None
    if pk is not None:
        if scope_type == ScopeType.PROJECT:
            scope_object = get_object_or_404(projects_in_scope(access), pk=pk)
        elif scope_type == ScopeType.REPO:
            scope_object = get_object_or_404(repositories_in_scope(access), pk=pk)

    scope = scope_for(request.user, scope_type, pk)
    params = params_module.parse(
        request.GET, projects=projects_in_scope(access), repositories=repositories_in_scope(access)
    )
    scope = params_module.narrow_scope(scope, params)
    context = build_dashboard(scope, params)
    context.update(
        {
            "params": params,
            "scope_type": scope_type,
            "scope_id": pk,
            "scope_object": scope_object,
            "projects": projects_in_scope(access) if scope_type == ScopeType.GLOBAL else None,
            "repositories": repositories_in_scope(access) if scope_type == ScopeType.GLOBAL else None,
        }
    )

    if is_htmx(request):
        return render(request, "dashboards/partials/dashboard_content.html", context)

    template = "dashboards/day.html" if params.mode == "day" else "dashboards/dashboard.html"
    return render(request, template, context)


def chart_json(request: HttpRequest, chart_key: str) -> HttpResponse:
    """`GET /api/charts/<chart_key>/?scope_type=&scope_id=&<the same filter query string>`. Scope
    travels in the query string here (this endpoint has no scope in its path), so an unknown or
    out-of-scope `scope_id` is dropped back to global the same way `scope_for()` already drops one
    for the page views (RISKS row 3) — never a 404 for that case. An unknown `chart_key`, or one
    unavailable at the requested level, is a 404 (plan §4)."""
    spec = CHART_REGISTRY.get(chart_key)
    if spec is None:
        raise Http404(f"Unknown chart {chart_key!r}.")

    scope_type = request.GET.get("scope_type") or ScopeType.GLOBAL
    if scope_type not in ScopeType.values:
        raise Http404(f"Unknown scope_type {scope_type!r}.")
    if not chart_available_at_level(spec, scope_type):
        raise Http404(f"Chart {chart_key!r} is not available at level {scope_type!r}.")

    raw_scope_id = request.GET.get("scope_id")
    scope_id = int(raw_scope_id) if raw_scope_id and raw_scope_id.isdigit() else None
    scope = scope_for(request.user, scope_type, scope_id)

    access = scope_for_user(request.user)
    params = params_module.parse(
        request.GET, projects=projects_in_scope(access), repositories=repositories_in_scope(access)
    )
    scope = params_module.narrow_scope(scope, params)
    payload = spec.build(scope, params)
    return JsonResponse(payload.to_dict())


def _index_context(request: HttpRequest, table_key: str) -> dict[str, object]:
    access = scope_for_user(request.user)
    scope = scope_for(request.user, ScopeType.GLOBAL, None)
    params = params_module.parse(
        request.GET, projects=projects_in_scope(access), repositories=repositories_in_scope(access)
    )
    scope = params_module.narrow_scope(scope, params)
    return {
        "params": params,
        "table_ctx": build_table_context(table_key, scope, params),
        "scope_type": ScopeType.GLOBAL,
        "scope_id": None,
        "scope_object": None,
        "projects": projects_in_scope(access),
        "repositories": repositories_in_scope(access),
    }


def projects_index(request: HttpRequest) -> HttpResponse:
    context = _index_context(request, "projects")
    if is_htmx(request):
        return render(request, "dashboards/partials/index_content.html", context)
    return render(request, "dashboards/projects_index.html", context)


def repositories_index(request: HttpRequest) -> HttpResponse:
    context = _index_context(request, "repositories")
    if is_htmx(request):
        return render(request, "dashboards/partials/index_content.html", context)
    return render(request, "dashboards/repositories_index.html", context)


def _export_filename(scope: Scope, params: DashboardParams, slug: str, ext: str) -> str:
    """`pr-radar_<table>_<scope-slug>_<from>_<to>.<ext>` (plan §7), ASCII-only regardless of UI
    language — the scope slug is the scope's type/id, never a translatable name; Day mode uses
    `_<date>` instead of a range."""
    scope_slug = scope.scope_type if scope.scope_id is None else f"{scope.scope_type}-{scope.scope_id}"
    period = (
        params.day.isoformat()
        if params.mode == "day"
        else f"{params.date_from.isoformat()}_{params.date_to.isoformat()}"
    )
    return f"pr-radar_{slug}_{scope_slug}_{period}.{ext}"


def _absolute_urls(
    request: HttpRequest, spec: TableSpec, rows: list[dict[str, object]]
) -> list[dict[str, object]]:
    """XLSX hyperlinks need an absolute URL — a relative `/projects/1/` (correct for an in-app
    table or a CSV, which never writes an href, only display text) is not a URL xlsxwriter's
    `write_url` accepts. CSV is untouched since it ignores a `url` column's href entirely."""
    link_keys = {column.link_key or column.key for column in spec.columns if column.type == "url"}
    if not link_keys:
        return rows
    return [
        {**row, **{key: request.build_absolute_uri(row[key]) for key in link_keys if row.get(key)}}
        for row in rows
    ]


def export_table(request: HttpRequest, table_key: str, fmt: str) -> HttpResponse:
    """`GET /export/<table_key>.<fmt>?scope_type=&scope_id=&<the current filter/sort/search query
    string>` (plan §7, acceptance criterion #5): the full matching row set, never just the current
    page. Scope travels in the query string, the same way `chart_json` reads it — an out-of-scope
    or unknown `scope_id` is dropped back to global rather than a 404 (RISKS row 3)."""
    spec = TABLE_SPECS.get(table_key)
    if spec is None:
        raise Http404(f"Unknown table {table_key!r}.")
    if fmt not in ("csv", "xlsx"):
        raise Http404(f"Unknown export format {fmt!r}.")

    scope_type = request.GET.get("scope_type") or ScopeType.GLOBAL
    if scope_type not in ScopeType.values:
        raise Http404(f"Unknown scope_type {scope_type!r}.")
    raw_scope_id = request.GET.get("scope_id")
    scope_id = int(raw_scope_id) if raw_scope_id and raw_scope_id.isdigit() else None
    scope = scope_for(request.user, scope_type, scope_id)

    access = scope_for_user(request.user)
    params = params_module.parse(
        request.GET, projects=projects_in_scope(access), repositories=repositories_in_scope(access)
    )
    scope = params_module.narrow_scope(scope, params)
    columns = export_columns(table_key, scope, params)
    rows = full_rows(table_key, scope, params)
    max_rows = get_int("EXPORT_SYNC_MAX_ROWS")
    if len(rows) > max_rows:
        # No background-job path yet (phase 9's `ExportJob`) — still stream every row rather than
        # silently truncate the export, but record that the configured cap was exceeded so an
        # operator sizing EXPORT_SYNC_MAX_ROWS has a signal to act on.
        logger.warning(
            "Synchronous export of %r exceeded EXPORT_SYNC_MAX_ROWS (%d rows > %d cap).",
            table_key,
            len(rows),
            max_rows,
        )
    filename = _export_filename(scope, params, spec.filename_slug, fmt)
    if fmt == "csv":
        return stream_csv(columns, rows, filename)

    xlsx_bytes = write_xlsx(list(columns), _absolute_urls(request, spec, rows), spec.filename_slug)
    response = HttpResponse(
        xlsx_bytes,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def pull_request_detail(request: HttpRequest, pk: int) -> HttpResponse:
    scope = scope_for_user(request.user)
    pull_request = get_object_or_404(pull_requests_in_scope(scope).select_related("repository"), pk=pk)
    signals = list(
        signals_for_pull_request(scope, pk).select_related("rule", "commit").order_by("detected_at")
    )
    ai_tools_display = [
        Tool(value).label if value in Tool.values else value for value in pull_request.ai_tools
    ]
    return render(
        request,
        "dashboards/pull_request_detail.html",
        {"pull_request": pull_request, "signals": signals, "ai_tools_display": ai_tools_display},
    )
