"""[qa:sensitive_paths_settings:*] Settings > Sensitive paths (/settings/sensitive-paths/),
admin-only, driven as an admin/lead would see it. See
e2e/plans/sensitive_paths_settings.plan.yaml for the oracle."""

from playwright.sync_api import Page, expect

from e2e.support.auth import log_in
from e2e.support.personas import USER_ADMIN, USER_LEAD


def _rule_row(page: Page, glob: str):
    return page.locator("#sensitive-paths-table tr", has_text=glob)


def test_admin_sees_list_lead_403(page: Page) -> None:
    log_in(page, USER_LEAD)
    page.goto("/")
    expect(page.get_by_role("link", name="Sensitive paths")).to_have_count(0)

    page.goto("/settings/sensitive-paths/")
    expect(page.get_by_role("heading", name="You do not have access to this page.")).to_be_visible()

    log_in(page, USER_ADMIN)
    page.goto("/settings/sensitive-paths/")
    row = _rule_row(page, "e2e-editable/**")
    expect(row).to_contain_text("Needs extra review")
    expect(row.locator("td").nth(4)).to_have_text("Yes")


def test_create_global_rule_persists_and_appears_in_list(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/sensitive-paths/")
    page.get_by_role("link", name="New rule").click()

    page.get_by_label("Path glob").fill("**/secrets/**")
    page.get_by_label("AI mode").select_option(label="Forbidden")
    page.get_by_label("Description").fill("e2e seed rule -- UI created global")
    page.get_by_role("button", name="Save").click()

    row = _rule_row(page, "**/secrets/**")
    expect(row.locator("td").nth(0)).to_have_text("Global")
    expect(row).to_contain_text("Forbidden")
    expect(row.locator("td").nth(4)).to_have_text("Yes")


def test_create_project_scoped_rule_shows_its_project_name(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/sensitive-paths/new/")

    page.get_by_label("Project").select_option(label="E2E Widget Project")
    page.get_by_label("Path glob").fill("infra/**")
    page.get_by_label("AI mode").select_option(label="Needs extra review")
    page.get_by_label("Description").fill("e2e seed rule -- UI created project-scoped")
    page.get_by_role("button", name="Save").click()

    row = _rule_row(page, "infra/**")
    expect(row.locator("td").nth(0)).to_have_text("E2E Widget Project")


def test_whitespace_only_glob_shows_visible_error_and_creates_nothing(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/sensitive-paths/new/")

    page.get_by_label("Path glob").fill("   ")
    page.get_by_label("AI mode").select_option(label="Forbidden")
    page.get_by_role("button", name="Save").click()

    expect(page.locator("#sensitive-path-form-page").get_by_role("alert").first).to_be_visible()
    expect(page.locator("#sensitive-path-form-page")).to_contain_text("A path glob is required.")
    assert page.url.endswith("/settings/sensitive-paths/new/")


def test_edit_rule_persists_and_appears_in_list(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/sensitive-paths/")
    _rule_row(page, "e2e-editable/**").get_by_role("link", name="Edit").click()

    page.get_by_label("Description").fill("Edited via e2e")
    page.get_by_role("button", name="Save").click()

    expect(_rule_row(page, "e2e-editable/**")).to_contain_text("Edited via e2e")


def test_toggle_rule_deactivates_and_reactivates_via_htmx(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/sensitive-paths/")

    row = _rule_row(page, "e2e-toggle/**")
    expect(row.locator("td").nth(4)).to_have_text("Yes")

    row.get_by_role("button", name="Deactivate").click()
    row = _rule_row(page, "e2e-toggle/**")
    expect(row.locator("td").nth(4)).to_have_text("No")
    expect(row.get_by_role("button", name="Activate")).to_be_visible()

    row.get_by_role("button", name="Activate").click()
    row = _rule_row(page, "e2e-toggle/**")
    expect(row.locator("td").nth(4)).to_have_text("Yes")
