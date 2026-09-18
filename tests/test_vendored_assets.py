"""T12: `make vendor` fetches Chart.js 4 UMD to `static/vendor/chart.umd.js`, committed alongside
`htmx.min.js` so neither tests nor a run need the network (plan §4)."""

from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CHART_JS = BASE_DIR / "static" / "vendor" / "chart.umd.js"


def test_chart_js_is_vendored_and_non_empty():
    assert CHART_JS.is_file()
    assert CHART_JS.stat().st_size > 1000


def test_base_html_references_the_vendored_chart_js():
    text = (BASE_DIR / "templates" / "base.html").read_text(encoding="utf-8")
    assert "vendor/chart.umd.js" in text


def test_makefile_vendor_target_fetches_chart_js():
    text = (BASE_DIR / "Makefile").read_text(encoding="utf-8")
    assert "chart.umd.js" in text
