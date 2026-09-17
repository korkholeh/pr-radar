"""[qa:people:*] Settings > People, the person CRUD forms, the merge form and the unmapped-identity
queue, driven as an admin/lead would see them. No case here exercises a live GitHub sync (see
e2e/plans/people.plan.yaml's deferred_not_authored) -- every row is seeded directly by manage.py seed_e2e."""

from playwright.sync_api import Locator, Page, expect

from e2e.support.auth import log_in
from e2e.support.personas import USER_ADMIN, USER_LEAD


def _identity_row(page: Page, email: str) -> Locator:
    """Locates a queue row by its Value column only. The row-wide text also includes every
    Person's display name, since the assign mini-form's <select> lists them all -- and
    create_person_from_identity() names a new person after the identity's own value, so a
    row-wide text match would (dis)ambiguously match every other row too once that identity's
    action has run once before."""
    return page.locator("#identities-table tr[id^='identity-row-']").filter(
        has=page.locator("td:nth-child(2)", has_text=email)
    )


def test_admin_sees_people_list_with_mapped_and_bot_rows(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/people/")

    ada_row = page.locator("#people-table tr", has_text="E2E Ada")
    expect(ada_row).to_contain_text("e2e-ada")
    expect(ada_row.locator("td").nth(3)).to_have_text("No")
    expect(ada_row.locator("td").nth(5)).to_have_text("1")

    bot_row = page.locator("#people-table tr", has_text="e2e-ci[bot]")
    expect(bot_row.locator("td").nth(3)).to_have_text("Yes")


def test_lead_cannot_reach_people(page: Page) -> None:
    log_in(page, USER_LEAD)
    page.goto("/")
    expect(page.get_by_role("link", name="People")).to_have_count(0)

    page.goto("/settings/people/")
    expect(page.get_by_role("heading", name="You do not have access to this page.")).to_be_visible()


def test_create_person_persists_and_appears_in_list(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/people/")
    page.get_by_role("link", name="New person").click()

    page.get_by_label("Display name").fill("E2E UI Created Person")
    page.get_by_label("Team").fill("Growth")
    page.get_by_role("button", name="Save").click()

    row = page.locator("#people-table tr", has_text="E2E UI Created Person")
    expect(row).to_contain_text("Growth")


def test_edit_person_updates_and_persists(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/people/")
    row = page.locator("#people-table tr", has_text="E2E Editable")
    row.get_by_role("link", name="Edit").click()

    page.get_by_label("Team").fill("Platform Edited")
    page.get_by_role("button", name="Save").click()

    updated_row = page.locator("#people-table tr", has_text="E2E Editable")
    expect(updated_row).to_contain_text("Platform Edited")


def test_invalid_person_form_shows_visible_error_in_browser(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/people/new/")
    page.get_by_role("button", name="Save").click()

    expect(page.locator("#person-form-page").get_by_role("alert")).to_be_visible()
    assert page.url.endswith("/settings/people/new/")


def test_queue_pagination_lists_every_identity_without_loss(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/people/identities/")

    # The 55 seeded "e2e-zzz-queue-item-*" rows always sort after every dedicated action-target
    # identity (see seed_e2e.py), so regardless of which of those a previous spec has since
    # consumed: page 1 always has exactly 50 rows, item 000 is always on it, item 050 never is
    # (it needs at least 51 rows of headroom, which the queue's smallest 4-target case still
    # doesn't reach), and item 054 -- the very last -- is always on page 2, never page 1.
    expect(page.locator('#identities-table tr[id^="identity-row-"]')).to_have_count(50)
    expect(page.get_by_text("e2e-zzz-queue-item-000@example.com")).to_be_visible()
    expect(page.get_by_text("e2e-zzz-queue-item-050@example.com")).to_have_count(0)
    expect(page.get_by_text("e2e-zzz-queue-item-054@example.com")).to_have_count(0)
    expect(page.get_by_text("Page 1 of 2")).to_be_visible()

    page.get_by_role("link", name="Next").click()

    expect(page.get_by_text("e2e-zzz-queue-item-054@example.com")).to_be_visible()
    expect(page.get_by_text("e2e-zzz-queue-item-000@example.com")).to_have_count(0)
    expect(page.get_by_text("Page 2 of 2")).to_be_visible()


def test_assign_identity_attaches_and_leaves_the_queue(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/people/identities/")
    row = _identity_row(page, "e2e-assign-target@example.com")
    row.locator("select[name='person']").select_option(label="E2E Assign Target")
    row.get_by_role("button", name="Assign").click()

    expect(_identity_row(page, "e2e-assign-target@example.com")).to_have_count(0)

    page.goto("/settings/people/")
    target_row = page.locator("#people-table tr", has_text="E2E Assign Target")
    expect(target_row).to_contain_text("e2e-assign-target@example.com")


def test_mark_bot_creates_a_bot_person_visible_in_the_list(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/people/identities/")
    row = _identity_row(page, "e2e-mark-bot-target@example.com")
    row.get_by_role("button", name="Mark as bot").click()

    expect(_identity_row(page, "e2e-mark-bot-target@example.com")).to_have_count(0)

    page.goto("/settings/people/")
    new_row = page.locator("#people-table tr", has_text="e2e-mark-bot-target@example.com")
    expect(new_row.locator("td").nth(3)).to_have_text("Yes")


def test_exclude_sets_exclude_from_metrics_visible_in_the_list(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/people/identities/")
    row = _identity_row(page, "e2e-exclude-target@example.com")
    row.get_by_role("button", name="Exclude").click()

    expect(_identity_row(page, "e2e-exclude-target@example.com")).to_have_count(0)

    page.goto("/settings/people/")
    new_row = page.locator("#people-table tr", has_text="e2e-exclude-target@example.com")
    expect(new_row.locator("td").nth(3)).to_have_text("No")
    expect(new_row.locator("td").nth(4)).to_have_text("Yes")


def test_create_person_from_identity_visible_in_the_list(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/people/identities/")
    row = _identity_row(page, "e2e-create-person-target@example.com")
    row.get_by_role("button", name="New person").click()

    expect(_identity_row(page, "e2e-create-person-target@example.com")).to_have_count(0)

    page.goto("/settings/people/")
    new_row = page.locator("#people-table tr", has_text="e2e-create-person-target@example.com")
    expect(new_row.locator("td").nth(3)).to_have_text("No")
    expect(new_row.locator("td").nth(4)).to_have_text("No")


def test_merge_people_repoints_identity_and_deletes_source(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/people/merge/")
    page.get_by_label("Merge this person").select_option(label="E2E Merge Source")
    page.get_by_label("Into this person").select_option(label="E2E Merge Target")
    page.get_by_role("button", name="Merge").click()

    expect(page.locator("#people-table tr", has_text="E2E Merge Source")).to_have_count(0)
    target_row = page.locator("#people-table tr", has_text="E2E Merge Target")
    expect(target_row).to_contain_text("e2e-merge-source-login")


def test_merge_into_self_shows_visible_error(page: Page) -> None:
    log_in(page, USER_ADMIN)
    page.goto("/settings/people/merge/")
    page.get_by_label("Merge this person").select_option(label="E2E Self Merge Guard")
    page.get_by_label("Into this person").select_option(label="E2E Self Merge Guard")
    page.get_by_role("button", name="Merge").click()

    expect(page.locator("#merge-form-page").get_by_role("alert")).to_contain_text(
        "Cannot merge a person into themselves."
    )
    page.goto("/settings/people/")
    expect(page.locator("#people-table tr", has_text="E2E Self Merge Guard")).to_have_count(1)
