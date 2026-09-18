"""Row builders: entities -> plain dicts, via `metrics.compute_many()` for the three metric-backed
tables and the ORM directly for `recent_prs` (plan §5). One column description
(`exports/columns.py::ExportColumn`) drives django-tables2, CSV and XLSX from these same dicts, so
the three renderers cannot drift apart."""

from __future__ import annotations

from django.db.models import Count
from django.urls import reverse

from apps.accounts.selectors import ScopeFilter
from apps.catalog.selectors import people_in_scope, projects_in_scope, repositories_in_scope
from apps.dashboards.params import DashboardParams
from apps.metrics.models import Cohort, ScopeType
from apps.metrics.selectors import scoped_pull_requests, scoped_reviews
from apps.metrics.services import compute_many
from apps.metrics.timeframe import day_end_exclusive, day_start
from apps.metrics.types import MetricResultSet, Scope

PROJECT_REPOSITORY_METRIC_KEYS: tuple[str, ...] = (
    "prs_merged",
    "ai_pr_share",
    "disclosure_rate",
    "violations_open",
    "lead_time_p50",
    "rework_rate",
)

PEOPLE_METRIC_KEYS: tuple[str, ...] = (
    "prs_merged",
    "ai_pr_share",
    "disclosure_rate",
    "violations_open",
    "lead_time_p50",
    "pr_size_p50",
    "rework_rate",
    "reviews_given",
    "reviewer_response_p50",
    "churn_21d",
)

RECENT_PR_COLUMN_KEYS: tuple[str, ...] = (
    "repository",
    "number",
    "title",
    "author",
    "state",
    "created_at",
    "merged_at",
    "size_bucket",
    "ai_status",
    "ai_tools",
    "violations_count",
)


def _resolve_table_cohort(params: DashboardParams) -> str:
    """`cohort=compare` renders both cohorts side by side on the KPI row and the charts (plan §3),
    which a single metric-table cell cannot do — it falls back to `Cohort.ALL` rather than
    silently picking one side. `cohort=ai`/`non_ai` narrows the table the same way it already
    narrows `recent_pr_rows()`, so switching the filter bar's cohort changes every part of the
    page together instead of leaving the projects/repositories/people tables on all PRs while the
    KPIs and charts move (round 2 review MINOR)."""
    return params.cohort if params.cohort in (Cohort.AI, Cohort.NON_AI) else Cohort.ALL


def _metric_row(
    entity_id: int,
    name: str,
    url: str | None,
    metric_keys: tuple[str, ...],
    result_set: MetricResultSet | None,
) -> dict[str, object]:
    row: dict[str, object] = {"id": entity_id, "name": name, "url": url}
    for key in metric_keys:
        result = result_set[key] if result_set is not None else None
        row[key] = result.value if result is not None else None
        row[f"{key}__delta"] = result.delta if result is not None else None
    return row


def project_rows(access: ScopeFilter, params: DashboardParams) -> list[dict[str, object]]:
    projects = list(projects_in_scope(access))
    results = compute_many(
        list(PROJECT_REPOSITORY_METRIC_KEYS),
        ScopeType.PROJECT,
        [project.id for project in projects],
        access,
        params.date_from,
        params.date_to,
        cohort=_resolve_table_cohort(params),
        granularity=params.granularity,
    )
    return [
        _metric_row(
            project.id,
            project.name,
            reverse("dashboards:project", args=[project.id]),
            PROJECT_REPOSITORY_METRIC_KEYS,
            results.get(project.id),
        )
        for project in projects
    ]


def repository_rows(
    access: ScopeFilter, params: DashboardParams, project_id: int | None = None
) -> list[dict[str, object]]:
    queryset = repositories_in_scope(access)
    if project_id is not None:
        queryset = queryset.filter(projects__id=project_id)
    repositories = list(queryset)
    results = compute_many(
        list(PROJECT_REPOSITORY_METRIC_KEYS),
        ScopeType.REPO,
        [repository.id for repository in repositories],
        access,
        params.date_from,
        params.date_to,
        cohort=_resolve_table_cohort(params),
        granularity=params.granularity,
    )
    return [
        _metric_row(
            repository.id,
            repository.full_name,
            reverse("dashboards:repository", args=[repository.id]),
            PROJECT_REPOSITORY_METRIC_KEYS,
            results.get(repository.id),
        )
        for repository in repositories
    ]


def _people_active_in_scope(scope: Scope, params: DashboardParams) -> set[int]:
    """Person ids with any authored PR or submitted review in `scope` during the period —
    `compute()` has no compound person-within-project/repository scope yet (that's phase 9), so
    `people_rows()` cannot narrow the *metric values* to the page's scope, only the *row set*: a
    Project/Repository people table would otherwise list every person in the caller's access with
    their org-wide numbers, which reads as if those numbers belonged to that project/repository."""
    start, end = day_start(params.date_from), day_end_exclusive(params.date_to)
    authored = scoped_pull_requests(scope).filter(last_activity_at__gte=start, last_activity_at__lt=end)
    reviewed = scoped_reviews(scope).filter(submitted_at__gte=start, submitted_at__lt=end)
    return set(authored.values_list("author__person_id", flat=True)) | set(
        reviewed.values_list("reviewer__person_id", flat=True)
    )


def people_values_are_org_wide(scope: Scope, params: DashboardParams) -> bool:
    """True only where the people table's row set is narrowed to a project/repository
    (`scope.scope_type != GLOBAL`) but the metric *values* are not: `compute_many(ScopeType.PERSON,
    scope.access, ...)` reads `scope.access` directly, so the values ARE narrowed the moment a
    project/repository filter is applied via `params.narrow_scope()` — round 2 audit MAJOR. The
    page's own `scope_type`/`scope_id` (e.g. visiting `/projects/<pk>/`) does **not** by itself
    narrow `scope.access`, only the filter bar's `project=`/`repository=` query params do."""
    return scope.scope_type != ScopeType.GLOBAL and not params.project_ids and not params.repository_ids


def people_rows(scope: Scope, params: DashboardParams) -> list[dict[str, object]]:
    """Default ordering is by name (RISKS row 1: no ranking column) — `people_in_scope()` already
    orders by `display_name`, so this preserves it rather than sorting by any metric. At
    Project/Repository level the row set is narrowed to people active in that scope during the
    period (see `_people_active_in_scope`); the metric values stay organization-wide only when
    `people_values_are_org_wide()` says so — `partials/table.html` and the CSV/XLSX export label
    the table accordingly. Logged in DECISIONS."""
    access = scope.access
    people = list(people_in_scope(access))
    if scope.scope_type != ScopeType.GLOBAL:
        active_ids = _people_active_in_scope(scope, params)
        people = [person for person in people if person.id in active_ids]
    results = compute_many(
        list(PEOPLE_METRIC_KEYS),
        ScopeType.PERSON,
        [person.id for person in people],
        access,
        params.date_from,
        params.date_to,
        cohort=_resolve_table_cohort(params),
        granularity=params.granularity,
    )
    return [
        _metric_row(person.id, person.display_name, None, PEOPLE_METRIC_KEYS, results.get(person.id))
        for person in people
    ]


def recent_pr_rows(scope: Scope, params: DashboardParams) -> list[dict[str, object]]:
    """A real queryset (`select_related`/`annotate`), not `compute()` — PRs touched in the period,
    ordered most-recent-first."""
    cohort = _resolve_table_cohort(params)
    queryset = (
        scoped_pull_requests(scope, cohort)
        .filter(
            last_activity_at__gte=day_start(params.date_from),
            last_activity_at__lt=day_end_exclusive(params.date_to),
        )
        .select_related("repository", "author__person")
        .annotate(violations_count=Count("violations"))
        .order_by("-last_activity_at")
    )
    return [
        {
            "id": pull_request.id,
            "repository": pull_request.repository.full_name,
            "number": pull_request.number,
            "url": reverse("dashboards:pull_request_detail", args=[pull_request.pk]),
            "title": pull_request.title,
            "author": (
                pull_request.author.person.display_name
                if pull_request.author and pull_request.author.person
                else ""
            ),
            "state": pull_request.state,
            "created_at": pull_request.created_at,
            "merged_at": pull_request.merged_at,
            "size_bucket": pull_request.size_bucket or "",
            "ai_status": pull_request.ai_status,
            "ai_tools": pull_request.ai_tools,
            "violations_count": pull_request.violations_count,
        }
        for pull_request in queryset
    ]
