"""T7: the dashboard view, template and URLs (plan §2, acceptance criterion #4's view half)."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.catalog.factories import ProjectFactory, RepositoryFactory


@pytest.mark.django_db
def test_overview_returns_200(client, lead_user):
    client.force_login(lead_user)
    response = client.get(reverse("dashboards:overview"))
    assert response.status_code == 200
    assert "Overview" in response.content.decode()


@pytest.mark.django_db
def test_project_page_returns_200(client, lead_user):
    client.force_login(lead_user)
    project = ProjectFactory()
    response = client.get(reverse("dashboards:project", args=[project.pk]))
    assert response.status_code == 200
    assert project.name in response.content.decode()


@pytest.mark.django_db
def test_repository_page_returns_200(client, lead_user):
    client.force_login(lead_user)
    repository = RepositoryFactory()
    response = client.get(reverse("dashboards:repository", args=[repository.pk]))
    assert response.status_code == 200
    assert repository.full_name in response.content.decode()


@pytest.mark.django_db
def test_unknown_project_pk_is_404(client, lead_user):
    client.force_login(lead_user)
    response = client.get(reverse("dashboards:project", args=[999999]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_htmx_request_returns_fragment_normal_request_returns_full_page(client, lead_user):
    client.force_login(lead_user)
    url = reverse("dashboards:overview")

    full_page = client.get(url)
    fragment = client.get(url, HTTP_HX_REQUEST="true")

    assert full_page.status_code == fragment.status_code == 200
    assert b"<html" in full_page.content
    assert b"<html" not in fragment.content
    assert b'data-testid="period-kpis"' in fragment.content


@pytest.mark.django_db
def test_filter_bar_reflects_current_query_string(client, lead_user):
    client.force_login(lead_user)
    response = client.get(reverse("dashboards:overview"), {"preset": "90d", "cohort": "ai"})
    body = response.content.decode()
    assert '<option value="90d" selected>' in body
    assert '<option value="ai" selected>' in body


def test_anonymous_user_is_redirected():
    response = Client().get(reverse("dashboards:overview"))
    assert response.status_code == 302


# -- the repository page's AI tooling section (phase 12, stage 3) --------------------------------


@pytest.mark.django_db
def test_repository_page_lists_the_agent_configuration_it_carries(client, lead_user):
    client.force_login(lead_user)
    repository = RepositoryFactory(
        ai_tooling_paths=[".agents", "AGENTS.md"], ai_tooling_checked_at=timezone.now()
    )

    response = client.get(reverse("dashboards:repository", args=[repository.pk]))

    content = response.content.decode()
    assert 'data-testid="repository-ai-tooling"' in content
    assert ".agents" in content
    assert "AGENTS.md" in content


@pytest.mark.django_db
def test_repository_page_separates_never_probed_from_probed_and_empty(client, lead_user):
    """`[]` and `None` must not read the same: one says the repository carries no agent
    configuration, the other says nobody has looked."""
    client.force_login(lead_user)
    never_probed = RepositoryFactory()
    probed_empty = RepositoryFactory(ai_tooling_paths=[], ai_tooling_checked_at=timezone.now())

    never = client.get(reverse("dashboards:repository", args=[never_probed.pk])).content.decode()
    empty = client.get(reverse("dashboards:repository", args=[probed_empty.pk])).content.decode()

    assert "Not checked yet" in never
    assert "Not checked yet" not in empty
    assert "No agent configuration was found" in empty


@pytest.mark.django_db
def test_the_overview_has_no_repository_tooling_section(client, lead_user):
    """It is a property of one repository; there is nothing for it to say globally."""
    client.force_login(lead_user)
    RepositoryFactory(ai_tooling_paths=["CLAUDE.md"], ai_tooling_checked_at=timezone.now())

    response = client.get(reverse("dashboards:overview"))

    assert 'data-testid="repository-ai-tooling"' not in response.content.decode()
