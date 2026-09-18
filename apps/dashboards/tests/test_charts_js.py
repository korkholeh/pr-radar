"""T13: `static/js/charts.js` resolves chart colours from CSS custom properties at draw time and
redraws on `themechange`/`htmx:afterSwap` (plan §4) — `tests/test_no_hardcoded_colors.py` already
proves it holds no literal; this test proves *how* it stays literal-free."""

from __future__ import annotations

from pathlib import Path

CHARTS_JS = Path(__file__).resolve().parents[3] / "static" / "js" / "charts.js"


def test_charts_js_exists_and_is_non_empty():
    assert CHARTS_JS.is_file()
    assert CHARTS_JS.stat().st_size > 0


def test_charts_js_resolves_colors_via_getComputedStyle():
    text = CHARTS_JS.read_text(encoding="utf-8")
    assert "getComputedStyle" in text


def test_charts_js_listens_for_themechange_and_htmx_afterswap():
    text = CHARTS_JS.read_text(encoding="utf-8")
    assert '"themechange"' in text
    assert '"htmx:afterSwap"' in text


def test_base_html_references_charts_js_and_vendored_chart():
    base_html = Path(__file__).resolve().parents[3] / "templates" / "base.html"
    text = base_html.read_text(encoding="utf-8")
    assert "js/charts.js" in text
    assert "vendor/chart.umd.js" in text


def test_charts_js_handles_a_failed_fetch_visibly():
    """A rejected fetch (network error, non-2xx) must not leave a blank canvas with only an
    unhandled promise rejection in the console — it should render a visible, translated error."""
    text = CHARTS_JS.read_text(encoding="utf-8")
    assert ".catch(" in text
    assert "gettext(" in text
