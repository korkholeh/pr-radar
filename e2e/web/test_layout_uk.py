"""[qa:layout-uk:*] Ukrainian strings run 40%+ longer than their English source (plan §6) -- this
suite switches to uk and checks that the extra length never grows the page past the viewport, nor
clips a KPI card title or a table header, at a desktop and a narrow-phone width."""

import pytest
from playwright.sync_api import Page

from e2e.support.auth import log_in
from e2e.support.personas import USER_ADMIN, USER_LEAD

DESKTOP = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 844}
VIEWPORTS = [DESKTOP, PHONE]


def _switch_to_ukrainian(page: Page) -> None:
    # The switcher auto-submits a form (`onchange="this.form.requestSubmit()"`) that redirects
    # back to the current page -- waiting for that navigation to settle before the caller's own
    # `page.goto()` avoids a race where the two navigations collide (`net::ERR_ABORTED`).
    with page.expect_navigation():
        page.locator("#language-select").select_option("uk")


def _document_scroll_width_within_viewport(page: Page) -> bool:
    return page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")


def _overflowing_element_count(page: Page, selector: str) -> int:
    return page.evaluate(
        """(selector) => {
            const elements = Array.from(document.querySelectorAll(selector));
            return elements.filter((el) => el.scrollWidth > el.clientWidth + 1).length;
        }""",
        selector,
    )


@pytest.mark.parametrize("viewport", VIEWPORTS, ids=["desktop", "phone"])
@pytest.mark.parametrize(
    "path",
    ["/", "/reviews/", "/people/"],
    ids=["overview", "reviews", "people"],
)
def test_dashboard_page_has_no_horizontal_overflow_in_ukrainian(
    page: Page, path: str, viewport: dict
) -> None:
    page.set_viewport_size(viewport)
    log_in(page, USER_LEAD)
    _switch_to_ukrainian(page)

    page.goto(path)

    assert _document_scroll_width_within_viewport(page), f"{path} overflows the viewport at {viewport}"
    assert _overflowing_element_count(page, '[data-testid="kpi-card"] > div:first-child') == 0
    assert _overflowing_element_count(page, "table th") == 0


@pytest.mark.parametrize("viewport", VIEWPORTS, ids=["desktop", "phone"])
def test_policy_console_has_no_horizontal_overflow_in_ukrainian(page: Page, viewport: dict) -> None:
    page.set_viewport_size(viewport)
    log_in(page, USER_ADMIN)
    _switch_to_ukrainian(page)

    page.goto("/policy/")

    assert _document_scroll_width_within_viewport(page), f"/policy/ overflows the viewport at {viewport}"
    assert _overflowing_element_count(page, "table th") == 0
