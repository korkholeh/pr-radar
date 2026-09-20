"""[qa:structural_signals:*] Settings > Structural signals and the structural half of a PR's AI
section, driven as an admin/lead would see them. No case here triggers detection: activating a rule
deliberately does not re-detect anything (that is the nightly batch, not a request), so the seeded
"E2E structurally suspicious PR" is run through the real `detect_pull_request()` by
manage.py seed_e2e instead -- see e2e/plans/structural_signals.plan.yaml's deferred_not_authored."""

from playwright.sync_api import Page, expect

from e2e.support.auth import log_in
from e2e.support.personas import USER_ADMIN, USER_LEAD

SINGLE_COMMIT_KIND = "Whole change in one large commit"


def _rule_row(page: Page, name: str):
    return page.locator("#signal-rules-table tr", has_text=name)


def _signal_row_count(page: Page) -> int:
    return page.locator("#ai-signals-table tbody tr").count()


def test_admin_sees_structural_rules_lead_403(page: Page) -> None:
    log_in(page, USER_LEAD)
    page.goto("/settings/structural-signals/")
    expect(page.get_by_role("heading", name="You do not have access to this page.")).to_be_visible()

    log_in(page, USER_ADMIN)
    page.goto("/settings/structural-signals/")
    expect(_rule_row(page, "E2E structural single-commit rule")).to_be_visible()


def test_a_shipped_rule_starts_deactivated_and_can_be_switched_on(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/structural-signals/")

    row = _rule_row(page, "E2E structural toggle rule")
    expect(row.locator("td").nth(4)).to_have_text("No")

    row.get_by_role("button", name="Activate").click()
    row = _rule_row(page, "E2E structural toggle rule")
    expect(row.locator("td").nth(4)).to_have_text("Yes")

    page.reload()
    expect(_rule_row(page, "E2E structural toggle rule").locator("td").nth(4)).to_have_text("Yes")


def test_high_confidence_is_not_offered_on_the_rule_form(page: Page) -> None:
    """ADR 0009 in the UI: a structural rule can never be high confidence, and the database refuses
    it even if the option were somehow submitted."""
    log_in(page, USER_ADMIN)
    page.goto("/settings/structural-signals/new/")

    options = page.get_by_label("Confidence").locator("option")
    expect(options).to_have_count(2)
    expect(options).to_have_text(["Medium", "Low"])


def test_dry_run_reports_matches_without_writing_signals(page: Page, seed_ids: dict) -> None:
    pk = seed_ids["structural_pr_pk"]

    log_in(page, USER_ADMIN)
    page.goto(f"/prs/{pk}/")
    signals_before = _signal_row_count(page)
    status_before = page.locator("#ai-section dd").nth(0).inner_text()

    page.goto("/settings/structural-signals/")
    dry_run = page.locator("#signal-dry-run form")
    dry_run.get_by_label("Kind").select_option(label=SINGLE_COMMIT_KIND)
    dry_run.get_by_label("Parameters").fill('{"min_lines": 300, "min_files": 5}')
    dry_run.get_by_label("Tool").select_option(label="Other")
    dry_run.get_by_label("Confidence").select_option(label="Low")
    page.get_by_role("button", name="Run").click()

    result = page.locator("#signal-dry-run-result")
    expect(result).to_contain_text("e2e-org/widget#920")
    expect(result).to_contain_text("900 lines across 12 files")

    page.goto(f"/prs/{pk}/")
    assert _signal_row_count(page) == signals_before
    assert page.locator("#ai-section dd").nth(0).inner_text() == status_before


def test_dry_run_of_a_baseline_kind_explains_it_cannot_be_previewed(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/structural-signals/")

    dry_run = page.locator("#signal-dry-run form")
    dry_run.get_by_label("Kind").select_option(label="Throughput jumped against the author's own history")
    dry_run.get_by_label("Tool").select_option(label="Other")
    dry_run.get_by_label("Confidence").select_option(label="Low")
    page.get_by_role("button", name="Run").click()

    expect(page.locator("#signal-dry-run-result")).to_contain_text("whole history")


def test_two_structural_kinds_make_a_pr_ai_suspected_with_its_arithmetic_shown(
    page: Page, seed_ids: dict
) -> None:
    pk = seed_ids["structural_pr_pk"]

    log_in(page, USER_ADMIN)
    page.goto(f"/prs/{pk}/")

    expect(page.locator("#ai-section dd").nth(0)).to_have_text("AI suspected")

    table = page.locator("#ai-signals-table")
    expect(table).to_contain_text(SINGLE_COMMIT_KIND)
    expect(table).to_contain_text("Many files created across many directories")
    # Each structural signal renders a full sentence naming both the measurement and the threshold
    # that produced it -- never a bare code, and never the stored English of a generated message.
    expect(table).to_contain_text("900 lines across 12 files")
    expect(table).to_contain_text("12 new files were added across 4 directories")
    expect(table).not_to_contain_text("single_large_commit")
    expect(table).not_to_contain_text("High")
