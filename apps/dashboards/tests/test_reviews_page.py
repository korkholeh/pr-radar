"""T15: the Reviews page — view, chart JSON endpoint, heat-map cells (plan §4)."""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse

from apps.activity.factories import PullRequestFactory, ReviewFactory
from apps.catalog.factories import IdentityFactory, PersonFactory, RepositoryFactory

SUBMITTED = datetime.datetime(2026, 6, 15, tzinfo=datetime.UTC)
PERIOD_QS = "preset=custom&from=2026-06-01&to=2026-06-30"


@pytest.mark.django_db
def test_reviews_page_renders(client, lead_user):
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:reviews") + f"?{PERIOD_QS}")

    assert response.status_code == 200
    assert b'data-testid="reviewer-load"' in response.content
    assert b'data-testid="reviewer-heat-map"' in response.content
    assert b'data-testid="waiting-for-review"' in response.content


@pytest.mark.django_db
def test_reviews_page_htmx_fragment(client, lead_user):
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:reviews") + f"?{PERIOD_QS}", HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    assert b"<html" not in response.content


@pytest.mark.django_db
def test_reviewer_load_chart_json_endpoint(client, lead_user):
    repository = RepositoryFactory()
    author = IdentityFactory()
    reviewer = IdentityFactory(person=PersonFactory(display_name="Rae"))
    pr = PullRequestFactory(repository=repository, author=author, created_at=SUBMITTED)
    ReviewFactory(pull_request=pr, reviewer=reviewer, submitted_at=SUBMITTED)
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:chart_json", args=["reviewer_load"]) + f"?{PERIOD_QS}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["key"] == "reviewer_load"
    assert "Rae" in payload["labels"]


@pytest.mark.django_db
def test_heat_map_cells_carry_text_and_colour(client, lead_user):
    repository = RepositoryFactory()
    author = IdentityFactory(person=PersonFactory(display_name="Aria"))
    reviewer = IdentityFactory(person=PersonFactory(display_name="Rae"))
    pr = PullRequestFactory(repository=repository, author=author, created_at=SUBMITTED)
    ReviewFactory(pull_request=pr, reviewer=reviewer, submitted_at=SUBMITTED)
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:reviews") + f"?{PERIOD_QS}")
    content = response.content.decode()

    assert 'data-heat-level="4"' in content
    assert "background-color: var(--heat-4)" in content


@pytest.mark.django_db
def test_reviewer_load_table_defaults_to_workload_order(client, lead_user):
    repository = RepositoryFactory()
    heavy = IdentityFactory(person=PersonFactory(display_name="Heavy"))
    light = IdentityFactory(person=PersonFactory(display_name="Light"))
    author = IdentityFactory(person=PersonFactory(display_name="Aria"))
    for reviewer, count in ((heavy, 3), (light, 1)):
        for _ in range(count):
            pr = PullRequestFactory(repository=repository, author=author, created_at=SUBMITTED)
            ReviewFactory(pull_request=pr, reviewer=reviewer, submitted_at=SUBMITTED)
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:reviews") + f"?{PERIOD_QS}")
    content = response.content.decode()
    table_start = content.index('data-testid="table-reviewer_load"')

    assert content.index("Heavy", table_start) < content.index("Light", table_start)
