"""Rendering helpers for KPI cards, tables and the "data as of" banner (plan T5/T9). Every
formatting rule from spec §10.2/CLAUDE.md lives here, once, so a template never re-derives it:
`value is None` renders an em dash never `0`, a delta's colour follows the metric's own
`direction` (never colour alone — an arrow glyph always accompanies it), and counts go through
Django's locale-aware `number_format` (`USE_THOUSAND_SEPARATOR`)."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from django import template
from django.conf import settings
from django.utils.formats import number_format
from django.utils.html import format_html
from django.utils.safestring import SafeString, mark_safe
from django.utils.timezone import localtime
from django.utils.translation import gettext

from apps.dashboards.charts import ChartPayload
from apps.dashboards.formatting import EM_DASH
from apps.dashboards.formatting import format_duration as _format_duration
from apps.dashboards.params import DashboardParams
from apps.github_sync.models import SyncRun
from apps.metrics.registry import MetricDef
from apps.metrics.types import MetricResult, SeriesPoint

register = template.Library()

NEUTRAL_DELTA_RATIO_THRESHOLD = 0.05
SPARKLINE_WIDTH = 96
SPARKLINE_HEIGHT = 24


@register.filter(name="humanize_duration")
def humanize_duration(seconds: float | None) -> str:
    return _format_duration(seconds)


@register.filter(name="percent")
def percent(value: float | None) -> str:
    """A ratio (0.0..1.0) as `0.0%`; `None` is an em dash, never `0%`."""
    if value is None:
        return EM_DASH
    return f"{value * 100:.1f}%"


@register.filter(name="format_count")
def format_count(value: float | None) -> str:
    if value is None:
        return EM_DASH
    return number_format(round(value), decimal_pos=0, force_grouping=True)


@register.filter(name="format_lines")
def format_lines(value: float | None) -> str:
    if value is None:
        return EM_DASH
    return number_format(round(value), decimal_pos=0, force_grouping=True)


@register.filter(name="metric_value")
def metric_value(value: float | None, unit: str) -> str:
    """Formats a raw number by `MetricDef.unit` — the one place that decides what a `ratio`, a
    `duration` or a plain `count` looks like on a card, a chart tooltip or a table cell."""
    if value is None:
        return EM_DASH
    if unit == "ratio":
        return percent(value)
    if unit == "duration":
        return _format_duration(value)
    if unit in ("count", "lines"):
        return format_count(value)
    return str(value)


@register.filter(name="delta_arrow")
def delta_arrow(delta: float | None) -> str:
    """Spec §10.2: status is never colour-only, so every delta carries an arrow glyph alongside
    its colour class."""
    if delta is None or delta == 0:
        return "–"
    return "▲" if delta > 0 else "▼"


def _direction_sign(definition: MetricDef, delta: float | None, delta_ratio: float | None) -> str:
    """`good` / `bad` / `neutral` — a `neutral`-direction metric, or a delta within
    `NEUTRAL_DELTA_RATIO_THRESHOLD` of the previous value, is always grey regardless of sign."""
    if delta is None:
        return "neutral"
    if delta_ratio is not None and abs(delta_ratio) < NEUTRAL_DELTA_RATIO_THRESHOLD:
        return "neutral"
    if definition.direction == "neutral":
        return "neutral"
    improved = delta > 0 if definition.direction == "higher_is_better" else delta < 0
    return "good" if improved else "bad"


_DIRECTION_CLASSES = {
    "good": "text-[var(--good)]",
    "bad": "text-[var(--bad)]",
    "neutral": "text-[var(--neutral)]",
}


@register.filter(name="direction_class")
def direction_class(result: MetricResult) -> str:
    """Returns one of `_DIRECTION_CLASSES`'s literal values, never a class name assembled at
    runtime by string interpolation — Tailwind's static scanner only sees literal strings in
    source, so a dynamically built class name is invisible to it and silently ships unstyled
    markup for whichever sign no other template happens to spell out literally."""
    sign = _direction_sign(result.definition, result.delta, result.delta_ratio)
    return _DIRECTION_CLASSES[sign]


@register.simple_tag(name="sparkline_svg")
def sparkline_svg(series: tuple[SeriesPoint, ...]) -> SafeString:
    """An inline SVG sparkline built server-side from `MetricResult.series` — no Chart.js, no
    extra request, and it survives an htmx swap intact. One point per `SeriesPoint`; a series with
    fewer than two points with a value renders a flat empty state instead of a degenerate line."""
    points_with_value = [
        (index, point.value) for index, point in enumerate(series) if point.value is not None
    ]
    if len(series) < 2 or len(points_with_value) < 2:
        return mark_safe(
            f'<svg width="{SPARKLINE_WIDTH}" height="{SPARKLINE_HEIGHT}" '
            f'viewBox="0 0 {SPARKLINE_WIDTH} {SPARKLINE_HEIGHT}" role="img" aria-hidden="true" '
            f'class="inline-block"></svg>'
        )
    values = [point.value for point in series if point.value is not None]
    min_value, max_value = min(values), max(values)
    span = max_value - min_value or 1.0
    last_index = len(series) - 1

    def _coords() -> list[tuple[float, float]]:
        coords = []
        for index, point in enumerate(series):
            if point.value is None:
                continue
            x = (index / last_index) * SPARKLINE_WIDTH if last_index else 0.0
            y = SPARKLINE_HEIGHT - ((point.value - min_value) / span) * SPARKLINE_HEIGHT
            coords.append((x, y))
        return coords

    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in _coords())
    return format_html(
        '<svg width="{}" height="{}" viewBox="0 0 {} {}" role="img" aria-hidden="true" class="inline-block">'
        '<polyline points="{}" fill="none" stroke="var(--accent)" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round" /></svg>',
        SPARKLINE_WIDTH,
        SPARKLINE_HEIGHT,
        SPARKLINE_WIDTH,
        SPARKLINE_HEIGHT,
        mark_safe(path),
    )


@register.inclusion_tag("dashboards/partials/data_as_of.html")
def data_as_of_banner() -> dict[str, object]:
    """ "Data as of <last successful sync>" (spec §10.1). An inclusion tag rather than a context
    processor, so it costs one query on dashboard pages only, and a distinct "never synced" state
    when no `SyncRun` has ever succeeded."""
    last_success = (
        SyncRun.objects.filter(status=SyncRun.Status.SUCCESS, finished_at__isnull=False)
        .order_by("-finished_at")
        .first()
    )
    finished_at = (
        localtime(last_success.finished_at, timezone=ZoneInfo(settings.REPORT_TIMEZONE))
        if last_success
        else None
    )
    return {"finished_at": finished_at}


@register.simple_tag(name="metric_title")
def metric_title(definition: MetricDef) -> str:
    return gettext(str(definition.title))


@register.simple_tag(name="table_sort_url", takes_context=True)
def table_sort_url(context: dict, params: DashboardParams, table_key: str, column_key: str) -> str:
    """The href for a sortable table header: toggles ascending/descending on repeated clicks and
    always resets to page 1, without disturbing any other table's state on the same page (only
    the table named by `table=` reads `sort`/`q`/`page` from the query string)."""
    current_sort = params.sort if params.table == table_key else ""
    next_sort = f"-{column_key}" if current_sort == column_key else column_key
    query = params.replace(table=table_key, sort=next_sort, page=1).to_query_dict()
    return f"{context['request'].path}?{query.urlencode()}"


@register.simple_tag(name="table_page_url", takes_context=True)
def table_page_url(context: dict, params: DashboardParams, table_key: str, page_number: int) -> str:
    current_sort = params.sort if params.table == table_key else ""
    current_q = params.q if params.table == table_key else ""
    query = params.replace(table=table_key, sort=current_sort, q=current_q, page=page_number).to_query_dict()
    return f"{context['request'].path}?{query.urlencode()}"


@register.simple_tag(name="hidden_query_fields")
def hidden_query_fields(params: DashboardParams, *exclude: str) -> SafeString:
    """Hidden `<input>`s replaying every current query-string field except `exclude` — what lets a
    table's own search/sort form submit without dropping the page's other filters (spec §10.1,
    acceptance criterion #4: the query string alone restores the view)."""
    query = params.to_query_dict()
    fields = [
        format_html('<input type="hidden" name="{}" value="{}">', key, value)
        for key in query
        if key not in exclude
        for value in query.getlist(key)
    ]
    return mark_safe("".join(fields))


@register.simple_tag(name="chart_rows")
def chart_rows(payload: ChartPayload) -> list[tuple[str, list[float | None]]]:
    """One row per label for a chart's visually hidden data-table twin (plan §4's accessibility
    requirement) — `(label, [dataset_1_value, dataset_2_value, ...])`, built server-side from the
    same `ChartPayload` `charts.js` fetches, so the table needs no JavaScript to be correct."""
    return [
        (label, [dataset.data[index] for dataset in payload.datasets])
        for index, label in enumerate(payload.labels)
    ]
