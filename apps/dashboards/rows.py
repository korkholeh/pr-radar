"""Row builders: entities -> plain dicts, via `metrics.compute_many()` for the three metric-backed
tables and the ORM directly for `recent_prs` (plan §5). One column description
(`exports/columns.py::ExportColumn`) drives django-tables2, CSV and XLSX from these same dicts, so
the three renderers cannot drift apart."""

from __future__ import annotations

from django.db.models import Count, OuterRef, Q, Subquery
from django.urls import reverse

from apps.accounts.selectors import ScopeFilter
from apps.catalog.selectors import people_in_scope, projects_in_scope, repositories_in_scope
from apps.catalog.services import get_int
from apps.churn.models import ChurnResult
from apps.dashboards.params import DashboardParams
from apps.metrics.models import Cohort, ScopeType
from apps.metrics.selectors import scoped_pull_requests, scoped_reviews
from apps.metrics.services import compute_many
from apps.metrics.timeframe import day_end_exclusive, day_start
from apps.metrics.types import MetricResultSet, Scope
from apps.policy.models import PolicyViolation

PROJECT_REPOSITORY_METRIC_KEYS: tuple[str, ...] = (
    "prs_merged",
    "ai_pr_share",
    "violations_open",
    "lead_time_p50",
    "rework_rate",
)

PEOPLE_METRIC_KEYS: tuple[str, ...] = (
    "prs_merged",
    "ai_pr_share",
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
        row[f"{key}__low"] = result.below_min_sample if result is not None else False
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
        include_series=False,
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
    if params.ai_tooling == "yes":
        queryset = queryset.exclude(ai_tooling_paths=[])
    elif params.ai_tooling == "no":
        # `checked_at__isnull=False` on purpose: a repository nobody has probed yet is not
        # evidence of an absence, so it belongs in neither cohort.
        queryset = queryset.filter(ai_tooling_paths=[], ai_tooling_checked_at__isnull=False)
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
        include_series=False,
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
        include_series=False,
    )
    return [
        _metric_row(
            person.id,
            person.display_name,
            reverse("dashboards:person", args=[person.id]),
            PEOPLE_METRIC_KEYS,
            results.get(person.id),
        )
        for person in people
    ]


_PR_ROW_ONLY_FIELDS = (
    "id",
    "number",
    "title",
    "state",
    "created_at",
    "merged_at",
    "size_bucket",
    "ai_status",
    "ai_tools",
    "repository__full_name",
    "author__person__display_name",
)
"""`PullRequest` carries several large columns `_pr_row()` never renders (`raw`, `body`, `labels`
— T11, RISKS row 10): profiling on `seed_demo --scale large` found the default `SELECT *` behind
the Overview page's `prs` table the single slowest statement on the page. Both row builders below
`.only()` down to this set (plus whatever each adds of its own)."""


def _pr_row(pull_request) -> dict[str, object]:
    return {
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


def pull_request_rows(scope: Scope, params: DashboardParams) -> list[dict[str, object]]:
    """The PR list's own row set (plan §3): every PR in `scope` created in the period, narrowed by
    `params.pr_filters` — a superset of `recent_pr_rows()`'s dashboard-card list, which stays on
    `last_activity_at` and has no filter form. `violations_count` counts only *open* violations
    (matches the badge the row renders), one annotation so the list pays no N+1. `disclosure`/
    `review_rounds` reuse the row directly; `churn_ratio` is a one-row `Subquery` against the
    settled `ChurnResult` for `CHURN_WINDOW_DAYS`, so it stays `None` (never `0`, CLAUDE.md) for a
    PR that has no `status=ok` row yet, and adds no per-row query (round 2 review MAJOR)."""
    churn_subquery = ChurnResult.objects.filter(
        pull_request=OuterRef("pk"),
        window_days=get_int("CHURN_WINDOW_DAYS"),
        status=ChurnResult.Status.OK,
    ).values("churn_ratio")[:1]
    queryset = scoped_pull_requests(scope).filter(
        created_at__gte=day_start(params.date_from),
        created_at__lt=day_end_exclusive(params.date_to),
    )
    queryset = params.pr_filters.apply(queryset)
    queryset = (
        queryset.select_related("repository", "author__person")
        .only(*_PR_ROW_ONLY_FIELDS, "ai_disclosure", "review_rounds")
        .annotate(
            violations_count=Count("violations", filter=Q(violations__status=PolicyViolation.Status.OPEN)),
            churn_ratio=Subquery(churn_subquery),
        )
        .order_by("-created_at")
    )
    return [
        {
            **_pr_row(pull_request),
            "disclosure": pull_request.get_ai_disclosure_display(),
            "review_rounds": pull_request.review_rounds,
            "churn_ratio": pull_request.churn_ratio,
        }
        for pull_request in queryset
    ]


def pull_request_row_count(scope: Scope, params: DashboardParams) -> int:
    """`.count()` twin of `pull_request_rows()` (round 2 review MINOR): the PR list is the one
    table realistically large enough to hit `EXPORT_SYNC_MAX_ROWS`, so `views.export_table` counts
    this instead of building and discarding every row dict first."""
    queryset = scoped_pull_requests(scope).filter(
        created_at__gte=day_start(params.date_from),
        created_at__lt=day_end_exclusive(params.date_to),
    )
    return params.pr_filters.apply(queryset).count()


def recent_pr_row_count(scope: Scope, params: DashboardParams) -> int:
    """`.count()` twin of `recent_pr_rows()` (round 2 review MINOR)."""
    cohort = _resolve_table_cohort(params)
    return (
        scoped_pull_requests(scope, cohort)
        .filter(
            last_activity_at__gte=day_start(params.date_from),
            last_activity_at__lt=day_end_exclusive(params.date_to),
        )
        .count()
    )


def reviewer_load_rows(scope: Scope, params: DashboardParams) -> list[dict[str, object]]:
    """The Reviews page's own table: `reviews.reviewer_load()`'s `ReviewerLoad` list, already
    descending by reviews given — a workload view, not a people ranking (RISKS row 1), so this
    preserves that order rather than re-sorting by name. It carries the same two counts as the
    page's chart (reviews given, and the distinct pull requests they fall on) so the table, the
    chart and the exports cannot disagree."""
    from apps.dashboards.reviews import reviewer_load

    return [
        {
            "id": load.person.id,
            "name": load.person.display_name,
            "url": reverse("dashboards:person", args=[load.person.id]),
            "reviews_given": load.reviews_given,
            "pull_requests_reviewed": load.pull_requests_reviewed,
        }
        for load in reviewer_load(scope, params)
    ]


def recent_pr_rows(
    scope: Scope, params: DashboardParams, *, limit: int | None = None, offset: int = 0
) -> list[dict[str, object]]:
    """A real queryset (`select_related`/`annotate`), not `compute()` — PRs touched in the period,
    ordered most-recent-first. `limit`/`offset` (T11 continuation, RISKS row 10: profiling on
    `--scale large` found this the single largest remaining cost on the Overview page — ~15,000
    `PullRequest` rows, each paying a `reverse()` call in `_pr_row()`, materialized just to show
    the first page of 25) let `tables.build_table_context()` paginate at the SQL level instead of
    building every row: safe only because this table's default order already matches the page's
    display order, unlike a `compute()`-backed table a reader can sort by any metric column."""
    cohort = _resolve_table_cohort(params)
    queryset = (
        scoped_pull_requests(scope, cohort)
        .filter(
            last_activity_at__gte=day_start(params.date_from),
            last_activity_at__lt=day_end_exclusive(params.date_to),
        )
        .select_related("repository", "author__person")
        .only(*_PR_ROW_ONLY_FIELDS)
        .annotate(
            violations_count=Count("violations", filter=Q(violations__status=PolicyViolation.Status.OPEN))
        )
        .order_by("-last_activity_at")
    )
    if limit is not None:
        queryset = queryset[offset : offset + limit]
    return [_pr_row(pull_request) for pull_request in queryset]
