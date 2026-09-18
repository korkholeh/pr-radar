"""[qa:churn:*] A PR's Churn section on the PR detail page, driven as an admin would see it. No case
here runs git or `compute_churn` -- the e2e stack forbids subprocess/network work, same as every other
plan -- so both `ChurnResult` rows are seeded directly by manage.py seed_e2e, exactly the shape a real
nightly run would leave behind. See e2e/plans/churn.plan.yaml's deferred_not_authored for the two
data-free status branches (too_large, error) and the empty state, which are asserted without a browser
in apps/dashboards/tests/test_pull_request_detail.py instead."""

from playwright.sync_api import Page, expect

from e2e.support.auth import log_in
from e2e.support.personas import USER_ADMIN


def test_pr_detail_shows_a_computed_churn_percentage(page: Page, seed_ids: dict) -> None:
    pk = seed_ids["churn_ok_pr_pk"]

    log_in(page, USER_ADMIN)
    page.goto(f"/prs/{pk}/")

    expect(page.locator("#pr-churn")).to_contain_text(
        "40.0% churn — 6 of 10 lines still present after 21 days."
    )
    expect(page.locator("#pr-churn")).not_to_contain_text("Churn not computed yet.")


def test_pr_detail_shows_unsupported_merge_method_sentence(page: Page, seed_ids: dict) -> None:
    pk = seed_ids["churn_unsupported_pr_pk"]

    log_in(page, USER_ADMIN)
    page.goto(f"/prs/{pk}/")

    expect(page.locator("#pr-churn")).to_contain_text("Churn is not measured for rebase merges.")
    expect(page.locator("#pr-churn")).not_to_contain_text("%")
