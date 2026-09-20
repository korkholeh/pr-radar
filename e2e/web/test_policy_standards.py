"""[qa:policy_standards:*] The PLANEKS standards as checks, end to end: the AI-policy form's
switch groups, and one enabled check's violation rendered on the console and waived. No case here
makes a violation appear by clicking a switch -- a new policy version governs pull requests created
after it, and evaluation runs on sync, not on save -- so the seeded policy has
`forbid_ai_only_approval` on and manage.py seed_e2e evaluates the offending pull request through
the real `evaluate_pull_request()`. See e2e/plans/policy_standards.plan.yaml."""

from playwright.sync_api import Page, expect

from e2e.support.auth import log_in
from e2e.support.personas import USER_ADMIN, USER_LEAD

BOT_APPROVAL_PR = 946


def _violation_row(page: Page, pr_number: int):
    return page.locator("#violations-table tbody tr", has_text=f"#{pr_number}")


def _open_filters(page: Page) -> None:
    page.locator("#policy-console").get_by_role("button", name="Filters").click()
    expect(page.locator("#violation-filters")).to_be_visible()


def _choose_tag(page: Page, field: str, option_label: str) -> None:
    page.locator(f"[data-testid='tagselect-{field}']").click()
    page.locator(".tagselect-menu").get_by_role("option", name=option_label, exact=True).click()
    page.keyboard.press("Escape")


def test_policy_form_groups_the_standards_switches(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/ai-policy/")

    form = page.locator("#policy-settings-page")
    for legend in (
        "Disclosure and tools",
        "Human review",
        "AI review",
        "What a description must state",
        "Quality gates and tests",
        "Size and scope",
    ):
        expect(form).to_contain_text(legend)

    expect(page.get_by_label("Forbid merging with only bot approvals")).to_be_checked()


def test_an_enabled_standards_check_renders_its_violation_in_the_readers_language(page: Page) -> None:
    log_in(page, USER_LEAD)
    page.goto("/policy/")

    row = _violation_row(page, BOT_APPROVAL_PR)
    expect(row).to_contain_text("Approved only by AI")
    expect(row).to_contain_text("High")
    expect(row).to_contain_text("Open")
    # The code itself is never shown to a reader, and the message is rendered from its stored
    # parameters rather than stored as a sentence.
    expect(row).not_to_contain_text("AI_ONLY_APPROVAL")

    row.get_by_role("link", name=f"#{BOT_APPROVAL_PR}").click()
    expect(page.locator("#pr-violations")).to_contain_text("none from a person")


def test_a_standards_violation_can_be_waived_with_a_reason(page: Page) -> None:
    log_in(page, USER_LEAD)
    page.goto("/policy/")

    _violation_row(page, BOT_APPROVAL_PR).get_by_role("checkbox").check()
    bulk = page.locator("#violation-bulk-form")
    bulk.get_by_label("Action").select_option(label="Waive")
    bulk.get_by_label("Comment").fill("Dependency bump, release engineer signed off out of band.")
    bulk.get_by_role("button", name="Apply").click()

    expect(page.locator("[role='status']")).to_contain_text("Updated 1 violation.")
    # The console defaults to open-only, so a waived row leaves the default view immediately.
    expect(_violation_row(page, BOT_APPROVAL_PR)).to_have_count(0)

    _open_filters(page)
    _choose_tag(page, "status", "Waived")
    page.locator("#violation-filters").get_by_role("button", name="Filter").click()
    expect(_violation_row(page, BOT_APPROVAL_PR)).to_contain_text("Waived")


def test_compliance_kpis_are_on_the_dashboard_for_a_lead(page: Page) -> None:
    log_in(page, USER_LEAD)
    page.goto("/")

    for key in (
        "ai_only_approval_rate",
        "quality_gate_bypass_rate",
        "ai_review_coverage",
        "high_risk_ai_pr_rate",
    ):
        expect(page.locator(f"[data-metric-key='{key}']")).to_have_count(1)

    # Nobody has named an AI reviewer in the seeded policy, so coverage is not measurable and
    # renders as a missing value, never as 0%.
    coverage = page.locator("[data-metric-key='ai_review_coverage'] [data-testid='kpi-value']")
    expect(coverage).not_to_contain_text("%")
