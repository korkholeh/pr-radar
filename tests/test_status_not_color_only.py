"""T8: status is never conveyed by colour alone (spec §10.5) — every KPI delta carries a glyph
and a number, every heat cell carries its count as text, and every small-sample marker carries a
`title`/`aria-label`. Plus a grep guard: no form control (`<input>`/`<select>`/`<textarea>`/
`<button>`) uses the decorative `--border` token alone; interactive boundaries use
`--border-strong`."""

from __future__ import annotations

import datetime
import re
from pathlib import Path

from django.template.loader import render_to_string

from apps.dashboards.reviews import HeatMapAxis, HeatMapCell, HeatMapRow
from apps.dashboards.templatetags.dashboards import small_sample_note
from apps.metrics.registry import get_metric
from apps.metrics.types import MetricResult, SeriesPoint

BASE_DIR = Path(__file__).resolve().parent.parent


def _kpi_result(**overrides: object) -> MetricResult:
    key = "prs_merged"
    definition = get_metric(key)
    defaults: dict[str, object] = {
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
        ),
        "breakdown": (),
    }
    defaults.update(overrides)
    return MetricResult(**defaults)


def test_kpi_delta_carries_both_a_glyph_and_a_number():
    result = _kpi_result(delta=2.0, delta_ratio=0.25)
    html = render_to_string("dashboards/partials/kpi_card.html", {"result": result})
    assert 'data-testid="kpi-delta"' in html
    assert "▲" in html
    assert "25.0%" in html


def test_kpi_small_sample_marker_is_a_full_sentence_not_a_bare_colour():
    result = _kpi_result(below_min_sample=True)
    html = render_to_string("dashboards/partials/kpi_card.html", {"result": result})
    assert 'data-testid="kpi-small-sample"' in html
    assert "Small sample" in html


def test_small_sample_note_tag_carries_text_content():
    html = str(small_sample_note())
    assert 'data-testid="small-sample-note"' in html
    assert "Small sample" in html


def test_heat_cell_carries_its_count_as_visible_text():
    author = HeatMapAxis(id=1, label="Alice")
    reviewer = HeatMapAxis(id=2, label="Bob")
    rows = [HeatMapRow(author=author, cells=[HeatMapCell(reviewer=reviewer, value=7, level=4)])]
    html = render_to_string(
        "dashboards/partials/reviewer_heat_map.html",
        {"heat_grid": rows, "heat_map": type("HM", (), {"reviewers": [reviewer]})()},
    )
    assert 'data-testid="heat-cell"' in html
    assert re.search(r"data-heat-level=\"4\"\s*>\s*7\s*<", html)
    assert "text-[var(--on-heat)]" in html


def test_heat_cell_below_level_three_uses_plain_text_token():
    author = HeatMapAxis(id=1, label="Alice")
    reviewer = HeatMapAxis(id=2, label="Bob")
    rows = [HeatMapRow(author=author, cells=[HeatMapCell(reviewer=reviewer, value=1, level=1)])]
    html = render_to_string(
        "dashboards/partials/reviewer_heat_map.html",
        {"heat_grid": rows, "heat_map": type("HM", (), {"reviewers": [reviewer]})()},
    )
    assert "text-[var(--text)]" in html
    assert "text-[var(--on-heat)]" not in html


FORM_CONTROL_RE = re.compile(r"<(input|select|textarea|button)\b[^>]*>", re.IGNORECASE)
BARE_BORDER_RE = re.compile(r"border-\[var\(--border\)\]")

# Row/section dividers on non-control elements legitimately keep the decorative `--border` token
# (T6/T7's own exemption reasoning: a divider is not a UI-component boundary or state indicator).
SCAN_DIRS = ["templates", "apps"]


def test_no_form_control_uses_the_decorative_border_token_alone():
    violations = []
    for scan_dir in SCAN_DIRS:
        root = BASE_DIR / scan_dir
        for path in root.rglob("*.html"):
            if "migrations" in path.parts or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for match in FORM_CONTROL_RE.finditer(text):
                tag = match.group(0)
                if BARE_BORDER_RE.search(tag):
                    violations.append(f"{path.relative_to(BASE_DIR)}: {tag[:80]}")
    assert not violations, "form controls must use --border-strong, not --border:\n" + "\n".join(violations)
