"""T11: `/prs/` — the lead-facing PR list, `views.pull_requests_index`, and its filtered CSV
export (acceptance: the export of a filtered list contains every matching row and only those)."""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse

from apps.activity.factories import PullRequestFactory
from apps.catalog.factories import IdentityFactory, RepositoryFactory

CREATED = datetime.datetime(2026, 6, 15, tzinfo=datetime.UTC)
PERIOD_QS = "preset=custom&from=2026-06-01&to=2026-06-30"


@pytest.mark.django_db
def test_pull_requests_index_returns_200_and_lists_prs(client, lead_user):
    repository = RepositoryFactory()
    identity = IdentityFactory()
    pull_request = PullRequestFactory(
        repository=repository, author=identity, title="Fix the flaky test", created_at=CREATED
    )
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:pull_requests_index") + f"?{PERIOD_QS}")

    assert response.status_code == 200
    assert pull_request.title in response.content.decode()


@pytest.mark.django_db
def test_pull_requests_index_htmx_request_returns_a_fragment(client, lead_user):
    client.force_login(lead_user)
    response = client.get(reverse("dashboards:pull_requests_index") + f"?{PERIOD_QS}", HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert b"<html" not in response.content


@pytest.mark.django_db
def test_csv_export_of_filtered_list_contains_only_matching_rows(client, lead_user):
    repository = RepositoryFactory()
    identity = IdentityFactory()
    open_pr = PullRequestFactory(
        repository=repository,
        author=identity,
        title="Open one",
        state="open",
        created_at=CREATED,
    )
    PullRequestFactory(
        repository=repository,
        author=identity,
        title="Merged one",
        state="merged",
        created_at=CREATED,
    )
    client.force_login(lead_user)

    response = client.get(
        reverse("dashboards:export", args=["pull_requests", "csv"]) + f"?{PERIOD_QS}&state=open"
    )

    assert response.status_code == 200
    body = b"".join(response.streaming_content).decode()
    assert open_pr.title in body
    assert "Merged one" not in body


def test_anonymous_user_is_redirected_from_pull_requests_index():
    from django.test import Client

    response = Client().get(reverse("dashboards:pull_requests_index"))
    assert response.status_code == 302
