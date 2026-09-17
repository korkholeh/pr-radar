"""[qa:connections:*] Settings > Connections and the Sync page, driven as an admin/lead would see them.
No case here triggers a sync — the e2e surface has no live GitHub credentials (see the plan's
deferred_not_authored)."""

from playwright.sync_api import Page, expect

from e2e.support.auth import log_in
from e2e.support.personas import USER_ADMIN, USER_LEAD

OK_TOKEN = "ghp_e2eoktokennotreal0123456789"
EXPIRING_TOKEN = "ghp_e2eexpiringtokennotreal012345"


def test_admin_sees_connection_list_with_last4_no_token(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/connections/")

    expect(page.get_by_role("cell", name="e2e-ok", exact=True)).to_be_visible()
    expect(page.get_by_role("cell", name="e2e-expiring", exact=True)).to_be_visible()
    expect(page.get_by_text(OK_TOKEN[-4:])).to_be_visible()

    body = page.content()
    assert OK_TOKEN not in body
    assert EXPIRING_TOKEN not in body


def test_expiry_banner_visible_to_admin(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/")
    expect(page.get_by_role("alert")).to_contain_text("e2e-expiring")


def test_expiry_banner_absent_for_lead(page: Page) -> None:
    log_in(page, USER_LEAD)
    page.goto("/")
    expect(page.get_by_role("alert")).to_have_count(0)
    expect(page.get_by_role("link", name="Connections")).to_have_count(0)


def test_sync_page_lists_seeded_run(page: Page) -> None:
    log_in(page, USER_LEAD)
    page.goto("/sync/")

    runs_table = page.locator("#sync-runs")
    expect(runs_table).to_contain_text("e2e-ok")
    expect(runs_table).to_contain_text("3")


def test_admin_sees_sync_now_button_lead_does_not(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/sync/")
    expect(page.get_by_role("button", name="Sync now")).to_be_visible()

    log_in(page, USER_LEAD)
    page.goto("/sync/")
    expect(page.get_by_role("button", name="Sync now")).to_have_count(0)


def test_deleting_connection_with_repositories_shows_rebind_message(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/connections/")

    row = page.locator("#connections-table tr", has_text="e2e-ok")
    row.get_by_role("button", name="Delete").click()

    connections_page = page.locator("#connections-page")
    expect(connections_page.get_by_role("alert")).to_contain_text("e2e-ok")
    expect(connections_page.get_by_text("e2e-org/widget")).to_be_visible()
    expect(connections_page.get_by_role("cell", name="e2e-ok", exact=True)).to_be_visible()


def test_discovery_page_loads_with_no_connection_chosen(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/repositories/discover/")

    discovery_page = page.locator("#discovery-page")
    expect(discovery_page.locator("#id_discovery_connection")).to_be_visible()
    expect(discovery_page.get_by_role("option", name="e2e-ok")).to_have_count(1)
    expect(discovery_page.locator("table")).to_have_count(0)
    expect(discovery_page.get_by_role("alert")).to_have_count(0)
