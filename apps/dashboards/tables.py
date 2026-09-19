"""django-tables2 tables generated from `exports.columns.ExportColumn` lists (plan §5), plus the
search/sort/pagination that runs entirely in Python over the row dicts `TableSpec.row_builder`
returns — the same dicts CSV and XLSX render, so a table and its exports can never drift apart.
Only the `TableSpec` named by `params.table` is sorted/searched/paginated by the current query
string; every other table on the same page renders in the order `row_builder` produced (RISKS
row 1: the people table's default is name order, never a ranking)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from zoneinfo import ZoneInfo

import django_tables2 as tables
from django.conf import settings
from django.core.paginator import Page, Paginator
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.utils.timezone import localtime
from django.utils.translation import gettext

from apps.catalog.services import get_int
from apps.dashboards import rows as row_builders
from apps.dashboards.exports.columns import TABLE_SPECS, ExportColumn, TableSpec, extract_value
from apps.dashboards.formatting import EM_DASH, format_duration
from apps.dashboards.params import DashboardParams
from apps.dashboards.templatetags.dashboards import format_count, percent
from apps.metrics.types import Scope

SEARCH_KEYS: dict[str, tuple[str, ...]] = {
    "projects": ("name",),
    "repositories": ("name",),
    "people": ("name",),
    "recent_prs": ("title", "author", "repository"),
    "pull_requests": ("title", "author", "repository"),
    "reviewer_load": ("name",),
}


def _cell_html(column: ExportColumn, value: object) -> str | SafeString:
    if column.type == "url":
        text, href = cast("tuple[object, object]", value)
        if text is None:
            return EM_DASH
        if not href:
            return str(text)
        return format_html('<a href="{}" class="text-[var(--accent)] hover:underline">{}</a>', href, text)
    if value is None:
        return EM_DASH
    if column.type == "percent":
        return percent(cast(float, value))
    if column.type == "duration":
        return format_duration(cast(float, value))
    if column.type == "int":
        return format_count(cast(float, value))
    if column.type == "float":
        return str(value)
    if column.type in ("datetime", "date"):
        localized = localtime(cast("object", value), timezone=ZoneInfo(settings.REPORT_TIMEZONE))
        fmt = "%Y-%m-%d %H:%M" if column.type == "datetime" else "%Y-%m-%d"
        return localized.strftime(fmt)
    if column.type == "badge_list":
        items = cast("list[object]", value)
        return ", ".join(str(item) for item in items) if items else EM_DASH
    return str(value)


def _make_render(column: ExportColumn):
    def render(self: tables.Table, record: dict[str, object]) -> str | SafeString:  # noqa: ARG001
        html = _cell_html(column, extract_value(column, record))
        if not record.get(f"{column.key}__low"):
            return html
        return format_html(
            '<span class="text-[var(--text-muted)]">{}</span> '
            '<span class="text-[var(--warning)]" title="{}" aria-label="{}" '
            'data-testid="cell-small-sample">≈</span>',
            html,
            gettext("Small sample"),
            gettext("Small sample"),
        )

    return render


def _make_table_class(table_key: str, spec: TableSpec) -> type[tables.Table]:
    attrs: dict[str, object] = {}
    for column in spec.columns:
        attrs[column.key] = tables.Column(verbose_name=column.title, orderable=False, empty_values=())
    for column in spec.columns:
        attrs[f"render_{column.key}"] = _make_render(column)
    attrs["Meta"] = type("Meta", (), {"attrs": {"class": "w-full text-sm"}})
    class_name = "".join(part.capitalize() for part in table_key.split("_")) + "Table"
    return type(class_name, (tables.Table,), attrs)


_TABLE_CLASSES: dict[str, type[tables.Table]] = {
    key: _make_table_class(key, spec) for key, spec in TABLE_SPECS.items()
}


def sort_rows(
    rows: list[dict[str, object]], sort: str, column_keys: frozenset[str]
) -> list[dict[str, object]]:
    """`sort` is `<key>` or `-<key>`; an unknown or empty key leaves `rows` in the order
    `row_builder` produced."""
    if not sort:
        return rows
    key = sort[1:] if sort.startswith("-") else sort
    if key not in column_keys:
        return rows
    descending = sort.startswith("-")
    return sorted(rows, key=lambda row: (row.get(key) is None, row.get(key)), reverse=descending)


def search_rows(rows: list[dict[str, object]], q: str, keys: tuple[str, ...]) -> list[dict[str, object]]:
    needle = q.strip().lower()
    if not needle:
        return rows
    return [row for row in rows if any(needle in str(row.get(key, "")).lower() for key in keys)]


def paginate_rows(rows: list[dict[str, object]], page: int) -> Page:
    paginator = Paginator(rows, get_int("DASHBOARD_TABLE_PAGE_SIZE"))
    return paginator.get_page(page)


@dataclass(frozen=True)
class TableContext:
    table_key: str
    table: tables.Table
    page: Page
    spec: TableSpec
    q: str
    sort: str
    export_csv_url: str
    export_xlsx_url: str


def _export_query(scope: Scope, params: DashboardParams, table_key: str, sort: str, q: str) -> str:
    query = params.replace(table=table_key, sort=sort, q=q, page=1).to_query_dict().copy()
    query["scope_type"] = scope.scope_type
    if scope.scope_id is not None:
        query["scope_id"] = str(scope.scope_id)
    return query.urlencode()


def build_table_context(table_key: str, scope: Scope, params: DashboardParams) -> TableContext:
    """The one function every dashboard/index view and `views.export_table` calls to get a
    table's current page: `row_builder(scope, params)` — unfiltered, unsorted — then search, sort
    and paginate in Python (plan §5), all driven by the current query string.

    `recent_prs`, unsearched and unsorted, instead paginates at the SQL level (T11 continuation,
    RISKS row 10): profiling on `--scale large` found materializing every matching PR (~15,000 at
    a 90-day GLOBAL scope, each paying a `reverse()` call) just to show the first 25 was the
    largest remaining cost on the Overview page. Safe only for this one table: its natural DB
    order (`-last_activity_at`) already matches the page's display order, and it is the only table
    with no per-row `compute()` cost a reader could ask to sort by — every other table keeps the
    build-then-paginate path so sorting by a metric column stays correct."""
    spec = TABLE_SPECS[table_key]
    active = params.table == table_key
    sort = params.sort if active else ""
    q = params.q if active else ""
    page_number = params.page if active else 1

    if table_key == "recent_prs" and not sort and not q:
        page_size = get_int("DASHBOARD_TABLE_PAGE_SIZE")
        total = row_builders.recent_pr_row_count(scope, params)
        # `range(total)`'s only job is to give `Paginator` a cheap, correctly-sized stand-in to
        # compute `num_pages`/`has_next`/etc. from — its `object_list` is never read; the actual
        # page of rows is fetched separately below, sliced at the database.
        page = Paginator(range(total), page_size).get_page(page_number)
        offset = (page.number - 1) * page_size
        table_instance = _TABLE_CLASSES[table_key](
            row_builders.recent_pr_rows(scope, params, limit=page_size, offset=offset)
        )
        export_query = _export_query(scope, params, table_key, sort, q)
        return TableContext(
            table_key=table_key,
            table=table_instance,
            page=page,
            spec=spec,
            q=q,
            sort=sort,
            export_csv_url=f"{reverse('dashboards:export', args=[table_key, 'csv'])}?{export_query}",
            export_xlsx_url=f"{reverse('dashboards:export', args=[table_key, 'xlsx'])}?{export_query}",
        )

    rows = spec.row_builder(scope, params)
    rows = search_rows(rows, q, SEARCH_KEYS[table_key])
    column_keys = frozenset(column.key for column in spec.columns)
    rows = sort_rows(rows, sort, column_keys)
    page = paginate_rows(rows, page_number)

    table_instance = _TABLE_CLASSES[table_key](list(page.object_list))
    export_query = _export_query(scope, params, table_key, sort, q)
    return TableContext(
        table_key=table_key,
        table=table_instance,
        page=page,
        spec=spec,
        q=q,
        sort=sort,
        export_csv_url=f"{reverse('dashboards:export', args=[table_key, 'csv'])}?{export_query}",
        export_xlsx_url=f"{reverse('dashboards:export', args=[table_key, 'xlsx'])}?{export_query}",
    )


def full_row_count(table_key: str, scope: Scope, params: DashboardParams) -> int:
    """Cheap row count for `views.export_table`'s sync-vs-background cap check, mirroring
    `services.report_row_count()`: uses `TableSpec.count_builder` (a `.count()` query) when the
    table has one and no text search is active — a search narrows rows in Python, so an accurate
    count still needs the row list built; every other case falls back to `len(full_rows(...))`
    (round 2 review MINOR: avoids materialising every row dict just to throw it away when the
    count already exceeds the cap)."""
    spec = TABLE_SPECS[table_key]
    active = params.table == table_key
    q = params.q if active else ""
    if spec.count_builder is not None and not q:
        return spec.count_builder(scope, params)
    return len(full_rows(table_key, scope, params))


def full_rows(table_key: str, scope: Scope, params: DashboardParams) -> list[dict[str, object]]:
    """The complete matching row set for `views.export_table` (plan §7): search and sort applied
    when `params.table` names this table (the export link always sets it), pagination never
    applied — acceptance criterion #5, a filtered export contains every row."""
    spec = TABLE_SPECS[table_key]
    active = params.table == table_key
    sort = params.sort if active else ""
    q = params.q if active else ""
    rows = spec.row_builder(scope, params)
    rows = search_rows(rows, q, SEARCH_KEYS[table_key])
    column_keys = frozenset(column.key for column in spec.columns)
    return sort_rows(rows, sort, column_keys)
