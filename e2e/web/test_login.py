"""[qa:auth-i18n-theme:login-reaches-overview]"""

from urllib.parse import urlparse

from playwright.sync_api import Page, expect

from e2e.support.auth import log_in
from e2e.support.personas import USER_LEAD


def test_persona_logs_in_and_reaches_overview(page: Page) -> None:
    log_in(page, USER_LEAD)
    assert urlparse(page.url).path == "/"
    expect(page.get_by_role("heading", name="Overview")).to_be_visible()
