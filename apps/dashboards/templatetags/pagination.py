"""The one pager every paginated surface renders (dashboard tables, the identity queue, the
policy console): Digg-style — first and last page always reachable, a window of pages around the
current one, an ellipsis for each gap — so a reader on page 14 of 30 can jump rather than click
`Next` sixteen times.

The tag builds every href itself from `base_url` + `query` (the page's own query string minus
`page`), which keeps the three call sites down to one line each and makes the markup, the
`aria-current` and the small-screen behaviour impossible to drift apart."""

from __future__ import annotations

from django import template
from django.core.paginator import Page, Paginator

register = template.Library()

PAGES_ON_EACH_SIDE = 2
PAGES_ON_ENDS = 1


def _page_url(base_url: str, query: str, number: int) -> str:
    parts = [part for part in (query, f"page={number}") if part]
    return f"{base_url}?{'&'.join(parts)}"


@register.inclusion_tag("partials/pager.html")
def digg_pager(
    page: Page,
    base_url: str = "",
    query: str = "",
    testid: str = "",
    hx_target: str = "",
) -> dict[str, object]:
    """`page` is a Django `Page`; `query` is an already url-encoded query string without `page`.
    A single-page paginator renders nothing at all."""
    paginator = page.paginator
    if paginator.num_pages <= 1:
        return {"page": None}
    items = []
    for number in paginator.get_elided_page_range(
        page.number, on_each_side=PAGES_ON_EACH_SIDE, on_ends=PAGES_ON_ENDS
    ):
        if number == Paginator.ELLIPSIS:
            items.append({"ellipsis": True})
            continue
        items.append(
            {
                "number": number,
                "url": _page_url(base_url, query, int(number)),
                "current": number == page.number,
            }
        )
    return {
        "page": page,
        "items": items,
        "previous_url": _page_url(base_url, query, page.previous_page_number())
        if page.has_previous()
        else "",
        "next_url": _page_url(base_url, query, page.next_page_number()) if page.has_next() else "",
        "testid": testid,
        "hx_target": hx_target,
    }
