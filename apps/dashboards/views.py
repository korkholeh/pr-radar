from __future__ import annotations

from django.http import FileResponse, Http404, HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.functional import Promise
from django.utils.translation import get_language, ngettext
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.accounts.selectors import scope_for_user
from apps.accounts.services import record_audit
from apps.activity.models import AIStatus, PullRequest, SizeBucket
from apps.activity.selectors import pull_requests_in_scope
from apps.ai_detection.models import Tool
from apps.ai_detection.selectors import signals_for_pull_request
from apps.catalog.models import Person, Project, Repository
from apps.catalog.selectors import people_in_scope, projects_in_scope, repositories_in_scope
from apps.catalog.services import get_int
from apps.churn.models import ChurnResult
from apps.dashboards import params as params_module
from apps.dashboards import pr_detail, reviews, tasks
from apps.dashboards.charts import CHART_REGISTRY, REVIEWS_CHART_KEYS, chart_available_at_level
from apps.dashboards.exports.columns import TABLE_SPECS, absolutize_urls, export_columns
from apps.dashboards.exports.csv import stream_csv
from apps.dashboards.exports.reports import build_report, report_filename
from apps.dashboards.exports.xlsx import write_xlsx
from apps.dashboards.kpis import KpiSpec, build_kpi_row
from apps.dashboards.models import ExportJob
from apps.dashboards.person import build_comparison
from apps.dashboards.selectors import (
    no_repositories_configured,
    nothing_ever_synced,
    period_has_pull_requests,
)
from apps.dashboards.services import (
    Export,
    build_chart_cards,
    build_dashboard,
    create_export_job,
    export_filename,
    record_export_audit,
    report_row_count,
)
from apps.dashboards.tables import build_table_context, full_row_count, full_rows
from apps.metrics.models import ScopeType
from apps.metrics.registry import get_metric
from apps.metrics.services import scope_for
from apps.policy.forms import BulkViolationActionForm
from apps.policy.models import PolicyViolation
from apps.policy.selectors import violations_for_pull_request
from apps.policy.services import BulkStatusChangeResult, apply_bulk_status_change
from config.htmx import is_htmx

_XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_CHURN_ERROR_REASONS = {
    "no_snapshot": _("No commit was found on the default branch before the churn window ended."),
    "fetch_failed": _("The repository could not be fetched."),
    "blame_failed": _("Measuring surviving lines with git blame failed."),
    "no_pr_commits": _("No commits could be attributed to this pull request."),
    "connection_unusable": _("The GitHub connection for this repository is not usable."),
    "timeout": _("The churn computation timed out."),
    "git_error": _("A git error occurred while measuring churn."),
}
_CHURN_DEFAULT_ERROR_REASON = _CHURN_ERROR_REASONS["git_error"]


def dashboard(
    request: HttpRequest, scope_type: str = ScopeType.GLOBAL, pk: int | None = None
) -> HttpResponse:
    """One view for the Overview/Project/Repository pages, both Period and Day mode (plan §2). An
    out-of-scope or unknown id **in the path** is a 404 (`get_object_or_404` on a scoped
    queryset) — unlike the same id in the query string, which `DashboardParams.parse()` drops
    silently (RISKS row 3)."""
    access = scope_for_user(request.user)
    scope_object: Project | Repository | Person | None = None
    if pk is not None:
        if scope_type == ScopeType.PROJECT:
            scope_object = get_object_or_404(projects_in_scope(access), pk=pk)
        elif scope_type == ScopeType.REPO:
            scope_object = get_object_or_404(repositories_in_scope(access), pk=pk)
        elif scope_type == ScopeType.PERSON:
            scope_object = get_object_or_404(people_in_scope(access), pk=pk)

    scope = scope_for(request.user, scope_type, pk)
    params = params_module.parse(
        request.GET,
        projects=projects_in_scope(access),
        repositories=repositories_in_scope(access),
        people=people_in_scope(access),
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
            "show_report_link": True,
        }
    )
    if params.mode != "day":
        period_is_empty = not period_has_pull_requests(scope, params)
        context["period_is_empty"] = period_is_empty
        no_repositories = period_is_empty and no_repositories_configured(access)
        context["no_repositories"] = no_repositories
        # The template picks the first of the three that is true, so the sync check is only worth
        # a query when something is actually configured — this keeps the pinned query counts flat.
        context["nothing_synced"] = period_is_empty and not no_repositories and nothing_ever_synced()
    if scope_type == ScopeType.PERSON and scope_object is not None:
        context["comparison"] = [
            {"definition": get_metric(row.metric), "row": row}
            for row in build_comparison(scope, params, scope_object)
        ]

    if is_htmx(request):
        return render(request, "dashboards/partials/dashboard_content.html", context)

    if scope_type == ScopeType.PERSON:
        template = "dashboards/person.html"
    elif params.mode == "day":
        template = "dashboards/day.html"
    else:
        template = "dashboards/dashboard.html"
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
        request.GET,
        projects=projects_in_scope(access),
        repositories=repositories_in_scope(access),
        people=people_in_scope(access),
    )
    scope = params_module.narrow_scope(scope, params)
    payload = spec.build(scope, params)
    return JsonResponse(payload.to_dict())


REVIEWS_ROW: tuple[KpiSpec, ...] = (KpiSpec("review_load_share"),)


def reviews_page(request: HttpRequest) -> HttpResponse:
    """`GET /reviews/`: reviewer workload (table + chart), the author×reviewer heat map and the
    waiting-for-review list (plan §4/T15). Scope travels in the query string, the same way
    `chart_json` reads it (this page has no scope in its path) — an out-of-scope or unknown
    `scope_id` is dropped back to global (RISKS row 3)."""
    scope_type = request.GET.get("scope_type") or ScopeType.GLOBAL
    if scope_type not in ScopeType.values:
        raise Http404(f"Unknown scope_type {scope_type!r}.")
    raw_scope_id = request.GET.get("scope_id")
    scope_id = int(raw_scope_id) if raw_scope_id and raw_scope_id.isdigit() else None
    scope = scope_for(request.user, scope_type, scope_id)

    access = scope_for_user(request.user)
    params = params_module.parse(
        request.GET,
        projects=projects_in_scope(access),
        repositories=repositories_in_scope(access),
        people=people_in_scope(access),
    )
    scope = params_module.narrow_scope(scope, params)
    heat_map = reviews.author_reviewer_matrix(scope, params)
    period_is_empty = not period_has_pull_requests(scope, params)
    no_repositories = period_is_empty and no_repositories_configured(access)

    context = {
        "params": params,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "scope_object": None,
        "projects": projects_in_scope(access) if scope_type == ScopeType.GLOBAL else None,
        "repositories": repositories_in_scope(access) if scope_type == ScopeType.GLOBAL else None,
        "period_is_empty": period_is_empty,
        "no_repositories": no_repositories,
        "nothing_synced": period_is_empty and not no_repositories and nothing_ever_synced(),
        "kpi_rows": [
            build_kpi_row(
                scope, REVIEWS_ROW, params.date_from, params.date_to, params.granularity, params.cohort
            )
        ],
        "charts": build_chart_cards(scope, params, REVIEWS_CHART_KEYS),
        "heat_map": heat_map,
        "heat_grid": reviews.heat_map_grid(heat_map),
        "waiting": reviews.prs_waiting_for_review(scope, params),
        "table_ctx": build_table_context("reviewer_load", scope, params),
    }
    if is_htmx(request):
        return render(request, "dashboards/partials/reviews_content.html", context)
    return render(request, "dashboards/reviews.html", context)


def _index_context(
    request: HttpRequest, table_key: str, extra: dict[str, object] | None = None
) -> dict[str, object]:
    access = scope_for_user(request.user)
    scope = scope_for(request.user, ScopeType.GLOBAL, None)
    params = params_module.parse(
        request.GET,
        projects=projects_in_scope(access),
        repositories=repositories_in_scope(access),
        people=people_in_scope(access),
    )
    scope = params_module.narrow_scope(scope, params)
    period_is_empty = not period_has_pull_requests(scope, params)
    # Materialised once: the filter bar iterates this list anyway, so asking it whether anything
    # is configured costs nothing on top — an extra `EXISTS` here would move the pinned query
    # counts for every index page (`test_query_counts`).
    repositories = list(repositories_in_scope(access))
    no_repositories = period_is_empty and not repositories
    context: dict[str, object] = {
        "params": params,
        "table_ctx": build_table_context(table_key, scope, params),
        "scope_type": ScopeType.GLOBAL,
        "scope_id": None,
        "scope_object": None,
        "projects": projects_in_scope(access),
        "repositories": repositories,
        "period_is_empty": period_is_empty,
        "no_repositories": no_repositories,
        "nothing_synced": period_is_empty and not no_repositories and nothing_ever_synced(),
    }
    if extra:
        context.update(extra)
    return context


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


def people_index(request: HttpRequest) -> HttpResponse:
    context = _index_context(request, "people")
    if is_htmx(request):
        return render(request, "dashboards/partials/index_content.html", context)
    return render(request, "dashboards/people_index.html", context)


def pull_requests_index(request: HttpRequest) -> HttpResponse:
    access = scope_for_user(request.user)
    context = _index_context(
        request,
        "pull_requests",
        extra={
            "people": people_in_scope(access),
            "state_choices": PullRequest.State.choices,
            "ai_status_choices": AIStatus.choices,
            "tool_choices": Tool.choices,
            "size_choices": SizeBucket.choices,
        },
    )
    if is_htmx(request):
        return render(request, "dashboards/partials/index_content.html", context)
    return render(request, "dashboards/pull_requests.html", context)


def _queued_response(request: HttpRequest, job: ExportJob) -> HttpResponse:
    """>`EXPORT_SYNC_MAX_ROWS` rows (plan §6, acceptance criterion #9): a job is enqueued instead
    of blocking the request, and the caller is sent to "My exports" to watch it finish."""
    if is_htmx(request):
        return render(request, "dashboards/partials/export_queued.html", {"job": job})
    return redirect("dashboards:exports_index")


def export_table(request: HttpRequest, table_key: str, fmt: str) -> HttpResponse:
    """`GET /export/<table_key>.<fmt>?scope_type=&scope_id=&<the current filter/sort/search query
    string>` (plan §7, acceptance criterion #5): the full matching row set, never just the current
    page. Scope travels in the query string, the same way `chart_json` reads it — an out-of-scope
    or unknown `scope_id` is dropped back to global rather than a 404 (RISKS row 3). Above
    `EXPORT_SYNC_MAX_ROWS` the export becomes a background `ExportJob` (plan §6/T22) instead of
    streaming every row in the request."""
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
        request.GET,
        projects=projects_in_scope(access),
        repositories=repositories_in_scope(access),
        people=people_in_scope(access),
    )
    scope = params_module.narrow_scope(scope, params)
    max_rows = get_int("EXPORT_SYNC_MAX_ROWS")
    if full_row_count(table_key, scope, params) > max_rows:
        job = create_export_job(
            request.user,
            kind=ExportJob.Kind.TABLE_CSV if fmt == "csv" else ExportJob.Kind.TABLE_XLSX,
            scope_type=scope_type,
            scope_id=scope_id,
            query_string=request.GET.urlencode(),
            table_key=table_key,
            fmt=fmt,
            language=get_language() or "en",
            base_url=request.build_absolute_uri("/"),
        )
        tasks.export_job_task(job.id)
        return _queued_response(request, job)

    columns = export_columns(table_key, scope, params)
    rows = full_rows(table_key, scope, params)
    filename = export_filename(scope, params, spec.filename_slug, fmt)
    if fmt == "csv":
        response = stream_csv(columns, rows, filename)
    else:
        absolute_rows = absolutize_urls(rows, spec.columns, request.build_absolute_uri("/"))
        xlsx_bytes = write_xlsx(list(columns), absolute_rows, spec.filename_slug)
        response = HttpResponse(xlsx_bytes, content_type=_XLSX_CONTENT_TYPE)
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
    record_export_audit(
        request.user,
        kind=ExportJob.Kind.TABLE_CSV if fmt == "csv" else ExportJob.Kind.TABLE_XLSX,
        fmt=fmt,
        table_key=table_key,
        scope=scope,
        params=params,
        rows_count=len(rows),
        subject=Export(table_key),
    )
    return response


def export_report(request: HttpRequest) -> HttpResponse:
    """`GET /report.xlsx?scope_type=&scope_id=&<filters>` (plan §5/§7): the seven-sheet workbook
    for the current Overview/Project/Repository/Person page, built by the same `scope`/`params`
    the page itself renders so a report figure can never disagree with the screen it came from.
    Above `EXPORT_SYNC_MAX_ROWS` PRs the report becomes a background `ExportJob` (plan §6/T22), the
    same threshold `export_table` applies. Not yet wired for the Policy console (plan §5's
    deviation, DECISIONS): Policy filters through `ViolationFilterForm`, not `DashboardParams`,
    and reusing this view for it needs its own `scope`/`params` mapping — deferred rather than
    guessed at here."""
    scope_type = request.GET.get("scope_type") or ScopeType.GLOBAL
    if scope_type not in ScopeType.values:
        raise Http404(f"Unknown scope_type {scope_type!r}.")
    raw_scope_id = request.GET.get("scope_id")
    scope_id = int(raw_scope_id) if raw_scope_id and raw_scope_id.isdigit() else None
    scope = scope_for(request.user, scope_type, scope_id)

    access = scope_for_user(request.user)
    params = params_module.parse(
        request.GET,
        projects=projects_in_scope(access),
        repositories=repositories_in_scope(access),
        people=people_in_scope(access),
    )
    scope = params_module.narrow_scope(scope, params)

    row_count = report_row_count(scope, params)
    max_rows = get_int("EXPORT_SYNC_MAX_ROWS")
    if row_count > max_rows:
        job = create_export_job(
            request.user,
            kind=ExportJob.Kind.REPORT_XLSX,
            scope_type=scope_type,
            scope_id=scope_id,
            query_string=request.GET.urlencode(),
            table_key=None,
            fmt="xlsx",
            language=get_language() or "en",
            base_url=request.build_absolute_uri("/"),
        )
        tasks.export_job_task(job.id)
        return _queued_response(request, job)

    content = build_report(
        scope,
        params,
        user=request.user,
        language=get_language() or "en",
        base_url=request.build_absolute_uri("/"),
    )
    filename = report_filename(scope, params)
    response = HttpResponse(content, content_type=_XLSX_CONTENT_TYPE)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    record_export_audit(
        request.user,
        kind=ExportJob.Kind.REPORT_XLSX,
        fmt="xlsx",
        table_key=None,
        scope=scope,
        params=params,
        rows_count=row_count,
        subject=Export("report"),
    )
    return response


def exports_index(request: HttpRequest) -> HttpResponse:
    """`GET /exports/` — "My exports" (plan §6): the caller's own jobs only, newest first. The
    list fragment keeps polling (`hx-trigger="every 3s"`) while any job is still `PENDING`/
    `RUNNING`, and stops once every job is terminal."""
    jobs = list(ExportJob.objects.filter(user=request.user).order_by("-created_at"))
    context = {
        "jobs": jobs,
        "polling": any(job.status in (ExportJob.Status.PENDING, ExportJob.Status.RUNNING) for job in jobs),
    }
    if is_htmx(request):
        return render(request, "dashboards/partials/exports_list.html", context)
    return render(request, "dashboards/exports.html", context)


def export_download(request: HttpRequest, pk: int) -> HttpResponse:
    """`GET /exports/<pk>/download/` — author-only (plan §6): another user's export is a 403, an
    identity refusal on an object the caller reached by id (unlike an out-of-scope filter value,
    which is dropped silently). A job that is not `DONE`, or whose file was already cleaned up,
    renders a visible message instead of an empty body (CLAUDE.md)."""
    job = get_object_or_404(ExportJob, pk=pk)
    if job.user_id != request.user.id:
        return HttpResponseForbidden("This export belongs to another user.")
    if job.status == ExportJob.Status.DONE and job.file:
        filename = job.file.name.rsplit("/", 1)[-1]
        return FileResponse(job.file.open("rb"), as_attachment=True, filename=filename)
    return render(request, "dashboards/export_unavailable.html", {"job": job}, status=410)


def _violation_notice(result: BulkStatusChangeResult) -> str:
    """Same wording as `policy.views._bulk_notice` (kept as a private duplicate rather than an
    import of a `_`-prefixed name) so the two action bars share one translated sentence."""
    if result.skipped_resolved:
        return ngettext(
            "Updated %(updated)s violation; skipped %(skipped)s resolved violation.",
            "Updated %(updated)s violations; skipped %(skipped)s resolved violations.",
            result.updated + result.skipped_resolved,
        ) % {"updated": result.updated, "skipped": result.skipped_resolved}
    return ngettext("Updated %(updated)s violation.", "Updated %(updated)s violations.", result.updated) % {
        "updated": result.updated
    }


def _pr_violations_context(scope, pk: int, *, notice: str | None = None, action_errors=None) -> dict:
    """A resolved violation's condition is gone, so it is history, not something to act on: it is
    listed apart, without a checkbox, and the main table matches the open count the PR list shows."""
    violations = list(
        violations_for_pull_request(scope, pk).select_related("resolved_by").order_by("-created_at")
    )
    resolved = PolicyViolation.Status.RESOLVED
    return {
        "violations": [violation for violation in violations if violation.status != resolved],
        "resolved_violations": [violation for violation in violations if violation.status == resolved],
        "notice": notice,
        "action_errors": action_errors,
    }


def _pull_request_detail_context(scope, pull_request: PullRequest) -> dict:
    pk = pull_request.pk
    signals = list(
        signals_for_pull_request(scope, pk)
        .select_related("rule", "signal_rule", "commit")
        .order_by("detected_at")
    )
    ai_tools_display = [
        Tool(value).label if value in Tool.values else value for value in pull_request.ai_tools
    ]

    files_limit = get_int("PR_FILES_DISPLAY_LIMIT")
    total_files = pull_request.files.count()
    files = list(pull_request.files.select_related("matched_sensitive_rule").order_by("path")[:files_limit])
    extra_files_count = max(0, total_files - files_limit)

    churn_result = (
        pull_request.churn_results.filter(window_days=get_int("CHURN_WINDOW_DAYS"))
        .order_by("-computed_at")
        .first()
    )

    churn_error_reason: str | Promise = ""
    churn_error_detail = ""
    if churn_result is not None and churn_result.status == ChurnResult.Status.ERROR and churn_result.error:
        code, _sep, detail = churn_result.error.partition(": ")
        churn_error_reason = _CHURN_ERROR_REASONS.get(code, _CHURN_DEFAULT_ERROR_REASON)
        churn_error_detail = detail

    # `too_large`'s `error` field holds the CHURN_MAX_FILES value in force when the row was
    # written -- rendering today's live setting would misstate a terminal row after an operator
    # later changes the limit.
    churn_too_large_limit = get_int("CHURN_MAX_FILES")
    churn_is_too_large = churn_result is not None and churn_result.status == ChurnResult.Status.TOO_LARGE
    if churn_is_too_large and churn_result.error:
        try:
            churn_too_large_limit = int(churn_result.error)
        except ValueError:
            pass

    return {
        "pull_request": pull_request,
        "description": pr_detail.description(pull_request),
        "author": pr_detail.author(scope, pull_request),
        "signals": signals,
        "ai_tools_display": ai_tools_display,
        "timeline": pr_detail.timeline(pull_request),
        "pr_metrics": pr_detail.pr_metrics(pull_request),
        "size_bucket_range": pr_detail.size_bucket_range(pull_request.size_bucket),
        "files": files,
        "extra_files_count": extra_files_count,
        "churn_too_large_limit": churn_too_large_limit,
        "churn_error_reason": churn_error_reason,
        "churn_error_detail": churn_error_detail,
        "churn_result": churn_result,
        **_pr_violations_context(scope, pk),
    }


def pull_request_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """PR detail page (plan §3/T13): AI signals (existing), a chronological timeline, single-PR
    duration facts, the changed-files list (capped, "+N more"), open violations with an inline
    acknowledge/waive action, and the churn slot — an explicit "not computed yet" state until
    phase 10 fills `ChurnResult` for most PRs, never a `0%` (CLAUDE.md: a missing value is `None`)."""
    scope = scope_for_user(request.user)
    pull_request = get_object_or_404(
        pull_requests_in_scope(scope).select_related("repository", "author__person"), pk=pk
    )
    return render(
        request, "dashboards/pull_request_detail.html", _pull_request_detail_context(scope, pull_request)
    )


@require_POST
def pull_request_violation_action(request: HttpRequest, pk: int) -> HttpResponse:
    """`POST /prs/<pk>/violations/` — the PR page's own acknowledge/waive action bar, scoped to
    this PR's violations only (`violation_ids` bound to `violations_for_pull_request(scope, pk)`,
    so an id outside this PR is a validation error, same rule as the Policy console's bulk form).
    Swaps only the violations fragment; a full-page request re-renders the whole PR page (CLAUDE.md:
    every htmx endpoint's URL also answers a normal request)."""
    scope = scope_for_user(request.user)
    pull_request = get_object_or_404(pull_requests_in_scope(scope), pk=pk)
    action_form = BulkViolationActionForm(
        request.POST, violations_queryset=violations_for_pull_request(scope, pk)
    )

    notice = None
    if action_form.is_valid():
        violations = list(action_form.cleaned_data["violation_ids"])
        result = apply_bulk_status_change(
            request.user, violations, action_form.cleaned_data["action"], action_form.cleaned_data["comment"]
        )
        notice = _violation_notice(result)
    action_errors = None if action_form.is_valid() else action_form.errors

    violations_context = _pr_violations_context(scope, pk, notice=notice, action_errors=action_errors)
    if is_htmx(request):
        return render(
            request,
            "dashboards/partials/pr_violations.html",
            {"pull_request": pull_request, **violations_context},
        )
    context = {**_pull_request_detail_context(scope, pull_request), **violations_context}
    return render(request, "dashboards/pull_request_detail.html", context)


@require_POST
def person_notes(request: HttpRequest, pk: int) -> HttpResponse:
    """`POST /people/<pk>/notes/` — any authenticated lead with the person in scope may edit
    `Person.notes` (spec §11 gives leads the people pages; no per-view permission decorator per
    ARCHITECTURE's rejected "per-view permission check"). The audit entry records only the note's
    length before and after, never its text (CLAUDE.md: no rendered/private text in an audit
    entry)."""
    access = scope_for_user(request.user)
    person = get_object_or_404(people_in_scope(access), pk=pk)
    before_length = len(person.notes)
    person.notes = request.POST.get("notes", "")
    person.save(update_fields=["notes"])
    record_audit(
        request.user,
        "person.notes",
        person,
        before={"length": before_length},
        after={"length": len(person.notes)},
    )
    if is_htmx(request):
        return render(request, "dashboards/partials/person_notes.html", {"scope_object": person})
    return redirect("dashboards:person", pk=pk)
