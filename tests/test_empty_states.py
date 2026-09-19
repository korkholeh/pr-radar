"""T3: every named page-level URL, visited on a completely empty database, explains itself
(plan §1 level 1/3) rather than rendering a bare shell of `—`/empty tables. PK-addressed pages
(`dashboards:person`, `pull_request_detail`, edit forms, …) are out of scope here — there is no
object to address on an empty database, and their own tests already cover the object-not-found
path. Only GET, HTML-rendering, list-shaped pages are swept."""

from __future__ import annotations

import pytest
from django.urls import reverse

NAMED_PAGE_URLS: list[str] = [
    "dashboards:overview",
    "dashboards:projects_index",
    "dashboards:repositories_index",
    "dashboards:people_index",
    "dashboards:pull_requests_index",
    "dashboards:reviews",
    "dashboards:exports_index",
    "catalog:people",
    "catalog:identity_queue",
    "ai_detection:rules",
    "connections:list",
    "connections:discover",
    "github_sync:sync",
    "policy:console",
    "policy:policy_settings",
    "policy:sensitive_paths",
]


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", NAMED_PAGE_URLS)
def test_every_page_on_an_empty_database_explains_itself(client, admin_user, url_name):
    client.force_login(admin_user)

    response = client.get(reverse(url_name))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'data-testid="empty-state"' in content, f"{url_name} has no empty-state explanation"
    assert "<canvas" not in content, f"{url_name} renders a chart canvas on an empty database"
    assert ">None<" not in content, f"{url_name} leaks a raw None into rendered HTML"
    assert "NaN" not in content, f"{url_name} leaks NaN into rendered HTML"
