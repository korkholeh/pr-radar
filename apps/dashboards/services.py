"""Assembles the page context `views.dashboard()` renders — KPI rows, chart cards and per-level
table lists, composed from `metrics.compute()`/`kpis.build_kpi_row()`/`charts.CHART_REGISTRY` and
`apps/dashboards/selectors.py` — plus the background export lifecycle (plan §6/§7):
`create_export_job()`/`run_export_job()`/`cleanup_exports()` and the one `record_export_audit()`
call every path that produces an export file makes."""

from __future__ import annotations

import dataclasses
import datetime
import logging

from django.core.files.base import ContentFile
from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone, translation

from apps.accounts.selectors import scope_for_user
from apps.accounts.services import record_audit
from apps.catalog.selectors import people_in_scope, projects_in_scope, repositories_in_scope
from apps.dashboards import kpis, selectors
from apps.dashboards import params as params_module
from apps.dashboards.charts import CHART_REGISTRY, DASHBOARD_CHART_KEYS, chart_available_at_level
from apps.dashboards.exports.columns import TABLE_SPECS, absolutize_urls, export_columns
from apps.dashboards.exports.csv import render_csv_bytes
from apps.dashboards.exports.xlsx import write_xlsx
from apps.dashboards.models import ExportJob, compute_expires_at
from apps.dashboards.params import DashboardParams
from apps.dashboards.tables import TableContext, build_table_context, full_rows
from apps.metrics.models import ScopeType
from apps.metrics.selectors import scoped_pull_requests
from apps.metrics.services import scope_for
from apps.metrics.timeframe import day_end_exclusive, day_start
from apps.metrics.types import Scope

logger = logging.getLogger(__name__)

_STUCK_JOB_AGE = datetime.timedelta(hours=1)

TABLE_KEYS_BY_LEVEL: dict[str, tuple[str, ...]] = {
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


def build_chart_cards(
    scope: Scope, params: DashboardParams, chart_keys: tuple[str, ...] = DASHBOARD_CHART_KEYS
) -> list[dict[str, object]]:
    """Renders exactly `chart_keys`, in that order, skipping any spec unavailable at this scope's
    level — the Reviews page (T15) calls this with `REVIEWS_CHART_KEYS` so `reviewer_load` shows
    up there and nowhere else, while a dashboard page keeps the default."""
    cards = []
    for key in chart_keys:
        spec = CHART_REGISTRY[key]
        if not chart_available_at_level(spec, scope.scope_type):
            continue
        payload = spec.build(scope, params)
        cards.append(
            {
                "key": spec.key,
                "title": spec.title,
                "description": spec.description,
                "payload": payload,
                "url": _chart_url(scope, params, spec.key),
            }
        )
    return cards


def _build_tables(scope: Scope, params: DashboardParams) -> list[TableContext]:
    table_keys = TABLE_KEYS_BY_LEVEL.get(scope.scope_type, ())
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
        "charts": build_chart_cards(scope, params),
        "tables": _build_tables(scope, params),
    }


# --- Export filenames, background jobs and audit (plan §6/§7) --------------------------------


class Export:
    """A minimal `record_audit()` subject for a synchronous export, which writes no `ExportJob`
    row: `object_type` becomes `"Export"`, `object_id` the table key or `"report"`. A background
    job instead passes its own `ExportJob` instance as the subject."""

    def __init__(self, pk: str) -> None:
        self.pk = pk


def export_filename(scope: Scope, params: DashboardParams, slug: str, ext: str) -> str:
    """`pr-radar_<table>_<scope-slug>_<from>_<to>.<ext>` (plan §7), ASCII-only regardless of UI
    language — the scope slug is the scope's type/id, never a translatable name; Day mode uses
    `_<date>` instead of a range. Shared by the synchronous export views and the background job
    (T22) so a queued export's filename matches what the same request would have streamed."""
    scope_slug = scope.scope_type if scope.scope_id is None else f"{scope.scope_type}-{scope.scope_id}"
    period = (
        params.day.isoformat()
        if params.mode == "day"
        else f"{params.date_from.isoformat()}_{params.date_to.isoformat()}"
    )
    return f"pr-radar_{slug}_{scope_slug}_{period}.{ext}"


def report_row_count(scope: Scope, params: DashboardParams) -> int:
    """The row count `views.export_report`/`run_export_job` gate `EXPORT_SYNC_MAX_ROWS` on: the
    PRs sheet is the report's largest, so its count is the report's own size proxy (plan §6)."""
    return (
        scoped_pull_requests(scope)
        .filter(created_at__gte=day_start(params.date_from), created_at__lt=day_end_exclusive(params.date_to))
        .count()
    )


def record_export_audit(
    user,
    *,
    kind: str,
    fmt: str,
    table_key: str | None,
    scope: Scope,
    params: DashboardParams,
    rows_count: int,
    subject: object,
) -> None:
    """The one call every path that *produces a file* makes (plan §7): sync CSV, sync XLSX, sync
    report and a completed background job, each exactly once."""
    action = "export.report" if kind == ExportJob.Kind.REPORT_XLSX else "export.table"
    record_audit(
        user,
        action,
        subject,
        after={
            "kind": kind,
            "format": fmt,
            "table_key": table_key,
            "scope_type": scope.scope_type,
            "scope_id": scope.scope_id,
            "filters": params.to_query_dict().urlencode(),
            "rows": rows_count,
        },
    )


def create_export_job(
    user,
    *,
    kind: str,
    scope_type: str,
    scope_id: int | None,
    query_string: str,
    table_key: str | None,
    fmt: str,
    language: str,
    base_url: str = "",
) -> ExportJob:
    """Enqueues a background export (plan §6): `params` stores enough to rebuild the `Scope` and
    re-resolve `scope_for_user(job.user)` at run time, not at enqueue time, so a grant revoked
    between enqueue and run is honoured (RISKS row 3). `base_url` (the view's own
    `request.build_absolute_uri("/")`) is stored too, since the task itself has no request — without
    it a background XLSX/report would silently lose the clickable hyperlinks the same synchronous
    export has (round 2 review MINOR)."""
    created_at = timezone.now()
    return ExportJob.objects.create(
        user=user,
        kind=kind,
        params={
            "scope_type": scope_type,
            "scope_id": scope_id,
            "query_string": query_string,
            "table_key": table_key,
            "fmt": fmt,
            "base_url": base_url,
        },
        language=language,
        created_at=created_at,
        expires_at=compute_expires_at(created_at),
    )


def _resolve_job_scope(user, job_params: dict) -> tuple[Scope, DashboardParams]:
    access = scope_for_user(user)
    scope = scope_for(user, job_params["scope_type"], job_params["scope_id"])
    query = QueryDict(job_params["query_string"])
    params = params_module.parse(
        query,
        projects=projects_in_scope(access),
        repositories=repositories_in_scope(access),
        people=people_in_scope(access),
    )
    return params_module.narrow_scope(scope, params), params


def run_export_job(job_id: int) -> ExportJob:
    """`PENDING -> RUNNING -> DONE/FAILED` (plan §6): writes the file to `ExportJob.file` and an
    `AuditEntry` on success, `error_code="failed"` on any exception rather than letting it
    propagate — a background task must always leave the job in a terminal state."""
    from apps.dashboards.exports.reports import build_report, report_filename

    job = ExportJob.objects.select_related("user").get(pk=job_id)
    job.status = ExportJob.Status.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])
    try:
        with translation.override(job.language):
            scope, params = _resolve_job_scope(job.user, job.params)
            table_key = job.params.get("table_key")
            fmt = job.params["fmt"]
            base_url = job.params.get("base_url", "")
            if job.kind == ExportJob.Kind.REPORT_XLSX:
                content = build_report(scope, params, user=job.user, language=job.language, base_url=base_url)
                filename = report_filename(scope, params)
                rows_count = report_row_count(scope, params)
            else:
                spec = TABLE_SPECS[table_key]
                columns = export_columns(table_key, scope, params)
                rows = full_rows(table_key, scope, params)
                rows_count = len(rows)
                filename = export_filename(scope, params, spec.filename_slug, fmt)
                content = (
                    render_csv_bytes(columns, rows)
                    if fmt == "csv"
                    else write_xlsx(
                        list(columns), absolutize_urls(rows, spec.columns, base_url), spec.filename_slug
                    )
                )
            job.file.save(filename, ContentFile(content), save=False)
            job.rows = rows_count
            job.status = ExportJob.Status.DONE
            job.finished_at = timezone.now()
            job.save(update_fields=["file", "rows", "status", "finished_at"])
            record_export_audit(
                job.user,
                kind=job.kind,
                fmt=fmt,
                table_key=table_key,
                scope=scope,
                params=params,
                rows_count=rows_count,
                subject=job,
            )
    except Exception:
        logger.exception("Export job %s failed.", job.id)
        job.status = ExportJob.Status.FAILED
        job.error_code = "failed"
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error_code", "finished_at"])
    return job


@dataclasses.dataclass(frozen=True)
class CleanupResult:
    deleted: int
    stuck_swept: int


def cleanup_exports(now: datetime.datetime | None = None) -> CleanupResult:
    """Deletes every expired export's file and row, and sweeps a `RUNNING` job stuck for over an
    hour to `FAILED` (plan §6, RISKS row 13) — so the "My exports" poller never spins forever."""
    at = now or timezone.now()
    stuck_swept = ExportJob.objects.filter(
        status=ExportJob.Status.RUNNING, started_at__lt=at - _STUCK_JOB_AGE
    ).update(status=ExportJob.Status.FAILED, error_code="stuck", finished_at=at)

    expired = ExportJob.objects.filter(expires_at__lt=at).exclude(status=ExportJob.Status.RUNNING)
    deleted = 0
    for job in expired:
        if job.file:
            job.file.delete(save=False)
        job.delete()
        deleted += 1
    return CleanupResult(deleted=deleted, stuck_swept=stuck_swept)
