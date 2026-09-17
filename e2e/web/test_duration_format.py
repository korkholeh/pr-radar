"""[qa:auth-i18n-theme:duration-formatter-matches-fixture]"""

from pathlib import Path

from playwright.sync_api import Page, expect

from e2e.support.auth import log_in
from e2e.support.personas import USER_LEAD

FORMATTING_JS = Path(__file__).parent.parent.parent / "static" / "js" / "formatting.js"


def _load_formatter(page: Page) -> None:
    # formatting.js is not wired into any page yet (no chart consumes it until phase 8); load the
    # real, committed file plus the /jsi18n/ catalog it depends on for gettext/ngettext/interpolate,
    # resolved for whatever language the page is currently in.
    page.add_script_tag(url="/jsi18n/")
    page.add_script_tag(content=FORMATTING_JS.read_text())


def test_js_formatter_matches_fixture_in_both_locales(page: Page, duration_cases: list[dict]) -> None:
    log_in(page, USER_LEAD)
    _load_formatter(page)
    for case in duration_cases:
        result = page.evaluate("(seconds) => window.prRadar.formatDuration(seconds)", case["seconds"])
        assert result == case["en"], f"seconds={case['seconds']} locale=en: got {result!r}"

    page.locator("#language-select").select_option("uk")
    expect(page.get_by_role("heading", name="Огляд")).to_be_visible()
    _load_formatter(page)
    for case in duration_cases:
        result = page.evaluate("(seconds) => window.prRadar.formatDuration(seconds)", case["seconds"])
        assert result == case["uk"], f"seconds={case['seconds']} locale=uk: got {result!r}"
