"""[qa:auth-i18n-theme:theme-switch-persists]"""

from playwright.sync_api import Browser, Page, expect

from e2e.support.auth import log_in
from e2e.support.personas import USER_LEAD


def _theme_attribute(page: Page) -> str:
    return page.evaluate("document.documentElement.getAttribute('data-theme')")


def test_theme_switch_persists_across_reload_and_new_context(
    page: Page, browser: Browser, base_url: str
) -> None:
    log_in(page, USER_LEAD)
    # Theme is the one persona property this spec owns; force a known baseline first so the test
    # is independent of whatever a previous run left behind, then restore it in `finally`.
    page.locator("#theme-select").select_option("system")
    expect(page.locator("html")).to_have_attribute("data-theme", "light")

    try:
        page.locator("#theme-select").select_option("dark")
        expect(page.locator("html")).to_have_attribute("data-theme", "dark")

        # Server renders data-theme in the initial HTML, so it must already be correct at
        # domcontentloaded — before the pre-paint script or any stylesheet runs.
        page.goto(base_url + "/", wait_until="domcontentloaded")
        assert _theme_attribute(page) == "dark"

        page.reload()
        expect(page.locator("html")).to_have_attribute("data-theme", "dark")

        fresh_context = browser.new_context(base_url=base_url)
        try:
            fresh_page = fresh_context.new_page()
            log_in(fresh_page, USER_LEAD)
            expect(fresh_page.locator("html")).to_have_attribute("data-theme", "dark")
        finally:
            fresh_context.close()
    finally:
        page.locator("#theme-select").select_option("system")
        expect(page.locator("html")).to_have_attribute("data-theme", "light")
