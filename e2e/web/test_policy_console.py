"""[qa:policy_console:*] The Policy console (/policy/) driven as a lead/admin would see it: KPIs,
the by-rule chart, the disclosure-mismatch list, filters, and bulk acknowledge/waive. See
e2e/plans/policy_console.plan.yaml for the oracle and what is deliberately deferred."""

from playwright.sync_api import Page, expect

from e2e.support.auth import log_in
from e2e.support.personas import USER_ADMIN, USER_LEAD


def _violation_row(page: Page, pr_number: int):
    return page.locator("#violations-table tbody tr", has_text=f"#{pr_number}")


def test_console_shows_seeded_violations_with_rendered_messages(page: Page) -> None:
    log_in(page, USER_LEAD)
    page.goto("/")
    expect(page.get_by_role("link", name="Policy")).to_have_count(1)

    page.goto("/policy/")

    missing_row = _violation_row(page, 940)
    expect(missing_row).to_contain_text("Disclosure missing")
    expect(missing_row).to_contain_text("Medium")
    expect(missing_row).to_contain_text("Open")
    expect(missing_row).not_to_contain_text("DISCLOSURE_MISSING")

    tool_row = _violation_row(page, 942)
    expect(tool_row).to_contain_text("Tool not allowed")
    expect(tool_row).to_contain_text("High")
    expect(tool_row).to_contain_text("cursor")


def test_by_rule_chart_and_data_table_agree(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/policy/")

    bar_list = page.locator("#policy-rule-chart ul")
    expect(bar_list).to_contain_text("Disclosure missing")
    expect(bar_list).to_contain_text("Tool not allowed")

    data_table = page.locator("#policy-rule-chart-table")
    expect(data_table).to_contain_text("Disclosure missing")
    expect(data_table).to_contain_text("Tool not allowed")
    for row in data_table.locator("tbody tr").all():
        count_cell = row.locator("td").nth(1)
        assert int(count_cell.inner_text()) > 0


def test_compliance_kpi_greys_below_min_sample(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/policy/")

    rate_card = page.locator("#policy-kpis [data-low-sample='true']")
    expect(rate_card).to_be_visible()
    expect(rate_card).to_contain_text("%")
    expect(page.locator("#policy-kpis [role='note']")).to_contain_text("5")


def test_mismatch_list_shows_the_disclosure_mismatch_pr(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/policy/")

    expect(page.locator("#policy-mismatch-list")).to_contain_text("e2e-org/widget#945")


def test_filter_by_rule_code_narrows_table_and_survives_back_navigation(page: Page) -> None:
    log_in(page, USER_LEAD)
    page.goto("/policy/")
    expect(_violation_row(page, 940)).to_be_visible()
    expect(_violation_row(page, 942)).to_be_visible()

    page.get_by_label("Rule").select_option(label="Tool not allowed")
    page.locator("#violation-filters").get_by_role("button", name="Filter").click()

    expect(_violation_row(page, 942)).to_be_visible()
    expect(_violation_row(page, 940)).to_have_count(0)
    assert "rule_code=TOOL_NOT_ALLOWED" in page.url

    page.go_back()
    expect(_violation_row(page, 940)).to_be_visible()
    expect(_violation_row(page, 942)).to_be_visible()


def test_filter_by_severity_narrows_table(page: Page) -> None:
    log_in(page, USER_LEAD)
    page.goto("/policy/")

    page.get_by_label("Severity").select_option(label="High")
    page.locator("#violation-filters").get_by_role("button", name="Filter").click()

    expect(_violation_row(page, 942)).to_be_visible()
    expect(_violation_row(page, 945)).to_be_visible()
    expect(_violation_row(page, 940)).to_have_count(0)
    expect(_violation_row(page, 941)).to_have_count(0)


def test_search_by_pr_number_narrows_table(page: Page) -> None:
    log_in(page, USER_LEAD)
    page.goto("/policy/")

    page.get_by_label("Search").fill("941")
    page.locator("#violation-filters").get_by_role("button", name="Filter").click()

    rows = page.locator("#violations-table tbody tr")
    expect(rows).to_have_count(1)
    expect(rows).to_contain_text("#941")


def test_acknowledge_updates_status_decrements_open_kpi_and_writes_audit_entry(page: Page) -> None:
    log_in(page, USER_LEAD)
    page.goto("/policy/")
    open_before = int(page.locator("#policy-kpis [data-testid='policy-kpi-value']").first.inner_text())

    _violation_row(page, 940).get_by_role("checkbox").check()
    page.locator("#violation-bulk-form").get_by_label("Action").select_option(label="Acknowledge")
    page.locator("#violation-bulk-form").get_by_label("Comment").fill(
        "Reviewed with the author, tracked in standup."
    )
    page.locator("#violation-bulk-form").get_by_role("button", name="Apply").click()

    expect(page.locator("[role='status']")).to_contain_text("Updated 1 violation.")
    # The default view filters to open-only (ViolationFilterForm.clean_status), so an
    # acknowledged row leaves it immediately -- itself proof the status actually changed.
    expect(_violation_row(page, 940)).to_have_count(0)
    page.get_by_label("Status").select_option(["acknowledged"])
    page.locator("#violation-filters").get_by_role("button", name="Filter").click()
    expect(_violation_row(page, 940)).to_contain_text("Acknowledged")

    page.goto("/policy/")
    open_after = int(page.locator("#policy-kpis [data-testid='policy-kpi-value']").first.inner_text())
    assert open_after == open_before - 1

    log_in(page, USER_ADMIN)
    page.goto("/admin/accounts/auditentry/?action=policy_violation.acknowledge")
    entry_row = page.locator("#result_list tbody tr").first
    expect(entry_row).to_contain_text("e2e-lead")
    entry_row.locator("th a").click()
    expect(page.locator("#content")).to_contain_text('"before": {"status": "open"}')
    expect(page.locator("#content")).to_contain_text('"status": "acknowledged"')
    expect(page.locator("#content")).to_contain_text("Reviewed with the author")


def test_waive_with_comment_updates_status(page: Page) -> None:
    log_in(page, USER_LEAD)
    page.goto("/policy/")

    _violation_row(page, 942).get_by_role("checkbox").check()
    page.locator("#violation-bulk-form").get_by_label("Action").select_option(label="Waive")
    page.locator("#violation-bulk-form").get_by_label("Comment").fill(
        "Cursor is allowed for this repo per exception."
    )
    page.locator("#violation-bulk-form").get_by_role("button", name="Apply").click()

    # The default view filters to open-only, so a waived row leaves it immediately.
    expect(_violation_row(page, 942)).to_have_count(0)

    page.get_by_label("Status").select_option(["waived"])
    page.locator("#violation-filters").get_by_role("button", name="Filter").click()
    expect(_violation_row(page, 942)).to_contain_text("Waived")


def test_missing_comment_is_rejected_and_changes_nothing(page: Page) -> None:
    log_in(page, USER_LEAD)
    page.goto("/policy/")

    _violation_row(page, 941).get_by_role("checkbox").check()
    page.locator("#violation-bulk-form").get_by_label("Action").select_option(label="Acknowledge")
    page.locator("#violation-bulk-form").get_by_role("button", name="Apply").click()

    expect(page.locator("#violations").get_by_role("alert")).to_be_visible()
    expect(page.locator("[role='status']")).to_have_count(0)
    expect(_violation_row(page, 941)).to_contain_text("Open")


def test_bulk_select_two_rows_updates_both_with_one_notice(page: Page) -> None:
    log_in(page, USER_LEAD)
    page.goto("/policy/")

    _violation_row(page, 943).get_by_role("checkbox").check()
    _violation_row(page, 944).get_by_role("checkbox").check()
    page.locator("#violation-bulk-form").get_by_label("Action").select_option(label="Acknowledge")
    page.locator("#violation-bulk-form").get_by_label("Comment").fill("Bulk-reviewed together.")
    page.locator("#violation-bulk-form").get_by_role("button", name="Apply").click()

    expect(page.locator("[role='status']")).to_contain_text("Updated 2 violations.")
    expect(_violation_row(page, 943)).to_have_count(0)
    expect(_violation_row(page, 944)).to_have_count(0)

    page.get_by_label("Status").select_option(["acknowledged"])
    page.locator("#violation-filters").get_by_role("button", name="Filter").click()
    expect(_violation_row(page, 943)).to_contain_text("Acknowledged")
    expect(_violation_row(page, 944)).to_contain_text("Acknowledged")
