"""[qa:auth-i18n-theme:language-switch-persists]"""

from playwright.sync_api import Page, expect

from e2e.support.auth import log_in
from e2e.support.personas import USER_LEAD


def test_language_switch_persists_across_reload(page: Page) -> None:
    log_in(page, USER_LEAD)
    expect(page.get_by_role("heading", name="Overview")).to_be_visible()

    page.locator("#language-select").select_option("uk")
    expect(page.get_by_role("heading", name="Огляд")).to_be_visible()

    page.reload()
    expect(page.get_by_role("heading", name="Огляд")).to_be_visible()
