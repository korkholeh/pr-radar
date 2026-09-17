"""[qa:ai_detection:*] Settings > Detection rules and a PR's AI section, driven as an admin/lead
would see them. No case here triggers detection itself (no browser action re-runs
`detect_pull_request`); every PR here is seeded already-detected by manage.py seed_e2e -- see
e2e/plans/ai_detection.plan.yaml's deferred_not_authored."""

from playwright.sync_api import Page, expect

from e2e.support.auth import log_in
from e2e.support.personas import USER_ADMIN, USER_LEAD


def _rule_row(page: Page, name: str):
    return page.locator("#rules-table tr", has_text=name)


def _signal_row_count(page: Page) -> int:
    return page.locator("#ai-signals-table tbody tr").count()


def test_admin_sees_rules_list_lead_403(page: Page) -> None:
    log_in(page, USER_LEAD)
    page.goto("/")
    expect(page.get_by_role("link", name="Detection rules")).to_have_count(0)

    page.goto("/settings/detection-rules/")
    expect(page.get_by_role("heading", name="You do not have access to this page.")).to_be_visible()

    log_in(page, USER_ADMIN)
    page.goto("/settings/detection-rules/")
    expect(_rule_row(page, "E2E footer rule")).to_be_visible()


def test_create_rule_persists_and_appears_in_list(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/detection-rules/")
    page.get_by_role("link", name="New rule").click()

    page.get_by_label("Name").fill("E2E UI created rule")
    page.get_by_label("Detector").select_option(label="Label")
    page.get_by_label("Pattern").fill("^e2e-ui-created$")
    page.get_by_label("Tool").select_option(label="Windsurf")
    page.get_by_label("Confidence").select_option(label="Medium")
    page.get_by_role("button", name="Save").click()

    row = _rule_row(page, "E2E UI created rule")
    expect(row).to_contain_text("Label")
    expect(row).to_contain_text("Windsurf")
    expect(row).to_contain_text("Medium")
    expect(row.locator("td").nth(4)).to_have_text("Yes")


def test_invalid_pattern_shows_visible_error_in_browser(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/detection-rules/new/")

    page.get_by_label("Name").fill("E2E invalid pattern rule")
    page.get_by_label("Detector").select_option(label="Label")
    page.get_by_label("Pattern").fill("[unclosed")
    page.get_by_label("Tool").select_option(label="Windsurf")
    page.get_by_label("Confidence").select_option(label="Medium")
    page.get_by_role("button", name="Save").click()

    expect(page.locator("#rule-form-page").get_by_role("alert")).to_be_visible()
    assert page.url.endswith("/settings/detection-rules/new/")

    page.goto("/settings/detection-rules/")
    expect(_rule_row(page, "E2E invalid pattern rule")).to_have_count(0)


def test_edit_rule_updates_and_persists(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/detection-rules/")
    _rule_row(page, "E2E editable rule").get_by_role("link", name="Edit").click()

    page.get_by_label("Notes").fill("Edited via e2e")
    page.get_by_role("button", name="Save").click()

    expect(_rule_row(page, "E2E editable rule")).to_contain_text("Edited via e2e")


def test_toggle_rule_deactivates_and_reactivates(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/detection-rules/")

    row = _rule_row(page, "E2E toggle rule")
    expect(row.locator("td").nth(4)).to_have_text("Yes")

    row.get_by_role("button", name="Deactivate").click()
    row = _rule_row(page, "E2E toggle rule")
    expect(row.locator("td").nth(4)).to_have_text("No")

    row.get_by_role("button", name="Activate").click()
    row = _rule_row(page, "E2E toggle rule")
    expect(row.locator("td").nth(4)).to_have_text("Yes")


def test_dry_run_reports_matches_without_writing_signals(page: Page, seed_ids: dict) -> None:
    pk = seed_ids["ai_detection_signal_pr_pk"]

    log_in(page, USER_ADMIN)
    page.goto(f"/prs/{pk}/")
    signals_before = _signal_row_count(page)
    status_before = page.locator("#ai-section dd").nth(0).inner_text()

    page.goto("/settings/detection-rules/")
    page.locator("#dry-run form").get_by_label("Detector").select_option(label="PR body footer")
    page.locator("#dry-run form").get_by_label("Pattern").fill("e2e-dryrun-target")
    page.locator("#dry-run form").get_by_label("Tool").select_option(label="Codex")
    page.locator("#dry-run form").get_by_label("Confidence").select_option(label="Low")
    page.get_by_role("button", name="Run").click()

    result = page.locator("#dry-run-result")
    expect(result).to_contain_text("1 match in the last")
    expect(result).to_contain_text("e2e-org/widget#910")
    expect(result).to_contain_text("e2e-dryrun-target")

    page.goto(f"/prs/{pk}/")
    assert _signal_row_count(page) == signals_before
    assert page.locator("#ai-section dd").nth(0).inner_text() == status_before


def test_dry_run_zero_matches_shows_empty_state(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/detection-rules/")

    page.locator("#dry-run form").get_by_label("Detector").select_option(label="PR body footer")
    page.locator("#dry-run form").get_by_label("Pattern").fill("e2e-nonexistent-marker-zzz")
    page.locator("#dry-run form").get_by_label("Tool").select_option(label="Codex")
    page.locator("#dry-run form").get_by_label("Confidence").select_option(label="Low")
    page.get_by_role("button", name="Run").click()

    result = page.locator("#dry-run-result")
    expect(result).to_contain_text("0 matches in the last")
    expect(result).to_contain_text("No matches.")


def test_dry_run_invalid_pattern_shows_visible_error(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/detection-rules/")

    page.locator("#dry-run form").get_by_label("Detector").select_option(label="PR body footer")
    page.locator("#dry-run form").get_by_label("Pattern").fill("[unclosed")
    page.locator("#dry-run form").get_by_label("Tool").select_option(label="Codex")
    page.locator("#dry-run form").get_by_label("Confidence").select_option(label="Low")
    page.get_by_role("button", name="Run").click()

    result = page.locator("#dry-run-result")
    expect(result.get_by_role("alert")).to_be_visible()
    expect(result).not_to_contain_text("match")


def test_pr_detail_shows_signal_evidence_status_disclosure_tools(page: Page, seed_ids: dict) -> None:
    pk = seed_ids["ai_detection_signal_pr_pk"]

    log_in(page, USER_ADMIN)
    page.goto(f"/prs/{pk}/")

    dd = page.locator("#ai-section dd")
    expect(dd.nth(0)).to_have_text("AI explicit")
    expect(dd.nth(1)).to_have_text("Substantial")
    expect(dd.nth(2)).to_contain_text("Claude Code")

    row = page.locator("#ai-signals-table tbody tr").first
    expect(row).to_contain_text("PR body footer")
    expect(row).to_contain_text("Claude Code")
    expect(row).to_contain_text("High")

    evidence = row.locator("td").nth(3).inner_text()
    assert evidence, "evidence cell must not be empty"
    assert len(evidence) <= 200
    assert "Generated with E2E Bot" in evidence


def test_pr_detail_empty_state_for_pr_without_signals(page: Page, seed_ids: dict) -> None:
    pk = seed_ids["ai_detection_no_signal_pr_pk"]

    log_in(page, USER_ADMIN)
    page.goto(f"/prs/{pk}/")

    dd = page.locator("#ai-section dd")
    expect(dd.nth(0)).to_have_text("Unknown")
    expect(dd.nth(1)).to_have_text("Missing")
    expect(dd.nth(2)).to_have_text("None")

    expect(page.locator("#ai-signals-table")).to_contain_text("No AI signals detected.")
    expect(page.locator("#ai-signals-table tbody tr")).to_have_count(1)
