"""`ExportColumn`, the type vocabulary and `TABLE_SPECS` (plan §5/§7): one column description per
table, shared by django-tables2, CSV and XLSX so the three renderers cannot drift apart. A column's
`type` is what `exports/csv.py`/`exports/xlsx.py` (and `tables.py`) key their formatting off of."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin

from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _

from apps.dashboards import rows
from apps.dashboards.params import DashboardParams
from apps.metrics.models import ScopeType
from apps.metrics.registry import get_metric
from apps.metrics.types import Scope

COLUMN_TYPES = frozenset(
    {"text", "int", "float", "percent", "duration", "datetime", "date", "url", "badge_list"}
)

_UNIT_TO_COLUMN_TYPE = {
    "count": "int",
    "lines": "float",
    "ratio": "percent",
    "duration": "duration",
}


@dataclass(frozen=True)
class ExportColumn:
    key: str
    title: Promise | str
    type: str
    width: int = 15
    link_key: str | None = None
    # `MetricDef.direction` ("higher_is_better" | "lower_is_better" | "neutral"), set only on a
    # delta column — what `exports/xlsx.py` colours a delta cell green/red by (spec §10.6).
    direction: str | None = None

    def __post_init__(self) -> None:
        if self.type not in COLUMN_TYPES:
            raise ValueError(f"Unknown column type {self.type!r} for column {self.key!r}.")


@dataclass(frozen=True)
class TableSpec:
    columns: tuple[ExportColumn, ...]
    row_builder: Callable[[Scope, DashboardParams], list[dict[str, object]]]
    filename_slug: str
    metric_keys: tuple[str, ...] = ()
    # A cheap `.count()` query mirroring `row_builder`'s row set, used only when no text search is
    # active (`tables.full_row_count()`) so `views.export_table` can decide the sync-vs-background
    # threshold without materialising and discarding every row dict first (round 2 review MINOR).
    count_builder: Callable[[Scope, DashboardParams], int] | None = None


def _metric_columns(
    metric_keys: tuple[str, ...], *, title_suffix: Callable[[Promise], Promise | str] | None = None
) -> list[ExportColumn]:
    columns = []
    for key in metric_keys:
        definition = get_metric(key)
        value_type = _UNIT_TO_COLUMN_TYPE.get(definition.unit, "text")
        title = title_suffix(definition.title) if title_suffix else definition.title
        columns.append(ExportColumn(key=key, title=title, type=value_type, width=14))
        delta_title = _("%(title)s (Δ)") % {"title": title}
        # The delta cell carries `MetricResult.delta` (RISKS row 1) — the absolute difference in
        # the metric's own unit, e.g. +5 PRs or -3600 seconds — never a ratio, so it renders with
        # the same column type as the value cell rather than always as "percent" (a +5 PR delta is
        # not "500%"). `MetricResult.delta_ratio` is the ratio and has no column here.
        columns.append(
            ExportColumn(
                key=f"{key}__delta",
                title=delta_title,
                type=value_type,
                width=10,
                direction=definition.direction,
            )
        )
    return columns


def _org_wide_title(title: Promise) -> Promise | str:
    return _("%(title)s (org-wide)") % {"title": title}


def _name_column(title: Promise) -> ExportColumn:
    return ExportColumn(key="name", title=title, type="url", link_key="url", width=30)


def _people_columns(*, org_wide: bool) -> tuple[ExportColumn, ...]:
    """Round 2 audit MAJOR: the "(org-wide)" marker must appear only where it is true — see
    `rows.people_values_are_org_wide()` — not unconditionally, since the values it describes are
    narrowed the moment a project/repository filter is applied. `TABLE_SPECS["people"].columns`
    (used by the HTML table, which never showed the suffix in a column header anyway, only in
    `partials/table.html`'s separate note) keeps the plain titles; `export_columns()` below builds
    the marked variant per request for CSV/XLSX."""
    title_suffix = _org_wide_title if org_wide else None
    return (
        ExportColumn(key="name", title=_("Person"), type="url", link_key="url", width=30),
        *_metric_columns(rows.PEOPLE_METRIC_KEYS, title_suffix=title_suffix),
    )


PROJECTS_COLUMNS = (_name_column(_("Project")), *_metric_columns(rows.PROJECT_REPOSITORY_METRIC_KEYS))
REPOSITORIES_COLUMNS = (
    _name_column(_("Repository")),
    *_metric_columns(rows.PROJECT_REPOSITORY_METRIC_KEYS),
)
PEOPLE_COLUMNS = _people_columns(org_wide=False)
RECENT_PRS_COLUMNS = (
    ExportColumn("repository", _("Repository"), "text", width=25),
    ExportColumn("number", _("PR"), "url", width=10, link_key="url"),
    ExportColumn("title", _("Title"), "text", width=40),
    ExportColumn("author", _("Author"), "text", width=20),
    ExportColumn("state", _("State"), "text", width=10),
    ExportColumn("created_at", _("Created"), "datetime", width=18),
    ExportColumn("merged_at", _("Merged"), "datetime", width=18),
    ExportColumn("size_bucket", _("Size"), "text", width=8),
    ExportColumn("ai_status", _("AI status"), "text", width=14),
    ExportColumn("ai_tools", _("AI tools"), "badge_list", width=20),
    ExportColumn("violations_count", _("Violations"), "int", width=10),
)
PULL_REQUESTS_COLUMNS = (
    *RECENT_PRS_COLUMNS,
    ExportColumn("disclosure", _("Disclosure"), "text", width=14),
    ExportColumn("review_rounds", _("Review rounds"), "int", width=10),
    ExportColumn("churn_ratio", _("Churn"), "percent", width=10),
)
REVIEWER_LOAD_COLUMNS = (
    ExportColumn(key="name", title=_("Reviewer"), type="url", link_key="url", width=30),
    ExportColumn(key="reviews_given", title=_("Reviews given"), type="int", width=14),
)


def _project_rows(scope: Scope, params: DashboardParams) -> list[dict[str, object]]:
    return rows.project_rows(scope.access, params)


def _repository_rows(scope: Scope, params: DashboardParams) -> list[dict[str, object]]:
    project_id = scope.scope_id if scope.scope_type == ScopeType.PROJECT else None
    return rows.repository_rows(scope.access, params, project_id=project_id)


def _people_rows(scope: Scope, params: DashboardParams) -> list[dict[str, object]]:
    return rows.people_rows(scope, params)


def extract_value(column: ExportColumn, row: dict[str, object]) -> object:
    """The raw Python value a column reads out of a row dict. A `url` column returns
    `(display_text, href)` since it needs both; every other type returns the plain value, `None`
    for a missing key rather than `0` or `""` (CLAUDE.md: a missing value is `None`, never `0`)."""
    if column.type == "url":
        return row.get(column.key), row.get(column.link_key or column.key)
    return row.get(column.key)


def absolutize_urls(
    rows: list[dict[str, object]], columns: tuple[ExportColumn, ...], base_url: str
) -> list[dict[str, object]]:
    """XLSX hyperlinks need an absolute URL (a relative `/projects/1/` is correct for an in-app
    table or a CSV, which never writes an href, only display text, but `xlsxwriter.write_url()`
    rejects an app-relative href outright). Shared by the synchronous export views
    (`request.build_absolute_uri("/")`), `exports/reports.py::build_report()` and the background
    job (`services.run_export_job()`, round 2 review MINOR) so all three produce the same
    clickable-or-not result for the same `base_url`."""
    link_keys = {column.link_key or column.key for column in columns if column.type == "url"}
    if not base_url or not link_keys:
        return rows
    return [
        {
            **row,
            **{
                key: urljoin(base_url, str(row[key]))
                for key in link_keys
                if row.get(key) and str(row[key]).startswith("/")
            },
        }
        for row in rows
    ]


TABLE_SPECS: dict[str, TableSpec] = {
    "projects": TableSpec(PROJECTS_COLUMNS, _project_rows, "projects", rows.PROJECT_REPOSITORY_METRIC_KEYS),
    "repositories": TableSpec(
        REPOSITORIES_COLUMNS, _repository_rows, "repositories", rows.PROJECT_REPOSITORY_METRIC_KEYS
    ),
    "people": TableSpec(PEOPLE_COLUMNS, _people_rows, "people", rows.PEOPLE_METRIC_KEYS),
    "recent_prs": TableSpec(
        RECENT_PRS_COLUMNS, rows.recent_pr_rows, "pull_requests", count_builder=rows.recent_pr_row_count
    ),
    "pull_requests": TableSpec(
        PULL_REQUESTS_COLUMNS,
        rows.pull_request_rows,
        "pull_requests",
        count_builder=rows.pull_request_row_count,
    ),
    "reviewer_load": TableSpec(REVIEWER_LOAD_COLUMNS, rows.reviewer_load_rows, "reviewer_load"),
}


def export_columns(table_key: str, scope: Scope, params: DashboardParams) -> tuple[ExportColumn, ...]:
    """The column list `views.export_table()` renders with: identical to
    `TABLE_SPECS[table_key].columns` except for `people`, whose metric titles gain the
    "(org-wide)" marker exactly when `rows.people_values_are_org_wide()` says the values are
    actually organization-wide for this request (round 2 audit MAJOR)."""
    if table_key == "people":
        return _people_columns(org_wide=rows.people_values_are_org_wide(scope, params))
    return TABLE_SPECS[table_key].columns
