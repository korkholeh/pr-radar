"""T5: KPI formatting helpers and `partials/kpi_card.html`, spec §10.2/CLAUDE.md's "a missing
value is `None`, never `0`"."""

from __future__ import annotations

import datetime
from typing import Any

from django.template.loader import render_to_string

from apps.dashboards.templatetags.dashboards import (
    delta_arrow,
    direction_class,
    metric_value,
    percent,
    sparkline_svg,
)
from apps.metrics.registry import get_metric
from apps.metrics.types import MetricResult, SeriesPoint


def _result(key: str, **overrides: object) -> MetricResult:
    definition = get_metric(key)
    defaults: dict[str, Any] = {
        "key": key,
        "definition": definition,
        "value": 10.0,
        "previous_value": 8.0,
        "delta": 2.0,
        "delta_ratio": 0.25,
        "sample_size": 10,
        "previous_sample_size": 8,
        "below_min_sample": False,
        "series": (
            SeriesPoint(
                date_from=datetime.date(2026, 1, 1),
                date_to=datetime.date(2026, 1, 1),
                value=1.0,
                sample_size=1,
            ),
            SeriesPoint(
                date_from=datetime.date(2026, 1, 2),
                date_to=datetime.date(2026, 1, 2),
                value=3.0,
                sample_size=1,
            ),
        ),
        "breakdown": (),
    }
    defaults.update(overrides)
    return MetricResult(**defaults)


def test_metric_value_none_renders_em_dash_never_zero():
    assert metric_value(None, "count") == "—"
    assert metric_value(None, "ratio") == "—"
    assert metric_value(None, "duration") == "—"


def test_percent_none_is_em_dash():
    assert percent(None) == "—"
    assert percent(0.256) == "25.6%"


def test_below_min_sample_shows_badge_and_grey_class():
    result = _result("prs_merged", below_min_sample=True)
    html = render_to_string("dashboards/partials/kpi_card.html", {"result": result})
    assert 'data-testid="kpi-small-sample"' in html
    assert "opacity-60" in html


def test_lower_is_better_negative_delta_is_good_higher_is_better_is_bad():
    lower_better = _result("reviewer_response_p50", delta=-2.0, delta_ratio=-0.3)
    higher_better = _result("prs_merged", delta=-2.0, delta_ratio=-0.3)
    assert direction_class(lower_better) == "text-[var(--good)]"
    assert direction_class(higher_better) == "text-[var(--bad)]"


def test_small_delta_ratio_is_neutral():
    result = _result("prs_merged", delta=1.0, delta_ratio=0.01)
    assert direction_class(result) == "text-[var(--neutral)]"


def test_delta_arrow_glyph_present_for_up_down_and_flat():
    assert delta_arrow(3.0) == "▲"
    assert delta_arrow(-3.0) == "▼"
    assert delta_arrow(0.0) == "–"
    assert delta_arrow(None) == "–"


def test_sparkline_has_one_point_per_series_point():
    result = _result("prs_merged")
    svg = sparkline_svg(result.series)
    polyline_points = str(svg).split('points="')[1].split('"')[0]
    assert len(polyline_points.split()) == len(result.series)
