"""[qa:ai_policy_settings:*] Settings > AI policy (/settings/ai-policy/), admin-only, driven as an
admin/lead would see it. See e2e/plans/ai_policy_settings.plan.yaml for the oracle."""

from playwright.sync_api import Page, expect

from e2e.support.auth import log_in
from e2e.support.personas import USER_ADMIN, USER_LEAD


def _history_row_count(page: Page) -> int:
    return page.locator("#policy-history-table tbody tr").count()


def test_admin_sees_settings_lead_403(page: Page) -> None:
    log_in(page, USER_LEAD)
    page.goto("/")
    expect(page.get_by_role("link", name="AI policy")).to_have_count(0)

    page.goto("/settings/ai-policy/")
    expect(page.get_by_role("heading", name="You do not have access to this page.")).to_be_visible()

    log_in(page, USER_ADMIN)
    page.goto("/settings/ai-policy/")
    expect(page.locator("#policy-history-table")).to_contain_text("Jan. 1, 2020")


def test_saving_creates_new_version_and_history_grows(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/ai-policy/")
    count_before = _history_row_count(page)

    page.get_by_label("Require human approval").check()
    page.get_by_label("Minimum human approvals").fill("2")
    page.get_by_label("Maximum effective lines for an AI PR").fill("500")
    page.get_by_role("button", name="Save new version").click()

    expect(page.locator("#policy-history-table")).to_be_visible()
    assert _history_row_count(page) == count_before + 1

    newest_row = page.locator("#policy-history-table tbody tr").first
    cells = newest_row.locator("td")
    expect(cells.nth(2)).to_have_text("Yes")
    expect(cells.nth(3)).to_have_text("2")
    expect(cells.nth(5)).to_have_text("500")

    oldest_row = page.locator("#policy-history-table tbody tr").last
    expect(oldest_row).to_contain_text("Jan. 1, 2020")


def test_invalid_minimum_approvals_shows_visible_error_and_creates_nothing(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/ai-policy/")
    count_before = _history_row_count(page)

    page.get_by_label("Minimum human approvals").fill("-1")
    page.get_by_role("button", name="Save new version").click()

    expect(page.locator("#policy-settings-page").get_by_role("alert")).to_be_visible()
    assert page.url.endswith("/settings/ai-policy/")
    assert _history_row_count(page) == count_before
