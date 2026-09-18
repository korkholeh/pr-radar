"""T20: the projects and repositories index pages — thin views over the `projects`/`repositories`
`TABLE_SPECS` tables (nav parity with spec §10.1)."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.catalog.factories import ProjectFactory, RepositoryFactory


@pytest.mark.django_db
def test_projects_index_returns_200_and_renders_the_table(client, lead_user):
    client.force_login(lead_user)
    project = ProjectFactory(name="Radar Core")
    response = client.get(reverse("dashboards:projects_index"))
    assert response.status_code == 200
    assert project.name in response.content.decode()


@pytest.mark.django_db
def test_repositories_index_returns_200_and_renders_the_table(client, lead_user):
    client.force_login(lead_user)
    repository = RepositoryFactory()
    response = client.get(reverse("dashboards:repositories_index"))
    assert response.status_code == 200
    assert repository.full_name in response.content.decode()


@pytest.mark.django_db
def test_projects_index_export_link_carries_the_current_query_string(client, lead_user):
    client.force_login(lead_user)
    response = client.get(reverse("dashboards:projects_index"), {"preset": "90d"})
    body = response.content.decode()
    assert "preset=90d" in body


@pytest.mark.django_db
def test_repositories_index_htmx_request_returns_a_fragment(client, lead_user):
    client.force_login(lead_user)
    response = client.get(reverse("dashboards:repositories_index"), HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert b"<html" not in response.content


def test_anonymous_user_is_redirected_from_both_index_pages():
    from django.test import Client

    for name in ("dashboards:projects_index", "dashboards:repositories_index"):
        response = Client().get(reverse(name))
        assert response.status_code == 302
