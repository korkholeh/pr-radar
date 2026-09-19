"""Settings → Repositories: the list of repositories already added, and the per-repository
connection change. Discovery can only rebind a repository the target token can already see; this
page moves a repository to any active connection, which is what preparing a replacement token
needs."""

import pytest
from django.db import connection as db_connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import AuditEntry
from apps.activity.factories import PullRequestFactory
from apps.catalog.factories import OrganizationFactory, RepositoryFactory
from apps.connections.factories import GitHubConnectionFactory


@pytest.mark.django_db
def test_repository_list_shows_each_repository_with_its_connection(client, admin_user):
    old_connection = GitHubConnectionFactory(name="Old token")
    RepositoryFactory(connection=old_connection, full_name="acme/widget")
    client.force_login(admin_user)

    response = client.get(reverse("connections:repositories"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "acme/widget" in content
    assert "Old token" in content


@pytest.mark.django_db
def test_repository_list_filters_by_connection(client, admin_user):
    kept = GitHubConnectionFactory(name="Kept")
    other = GitHubConnectionFactory(name="Other")
    RepositoryFactory(connection=kept, full_name="acme/kept")
    RepositoryFactory(connection=other, full_name="acme/other")
    client.force_login(admin_user)

    response = client.get(reverse("connections:repositories"), {"connection": kept.pk})

    content = response.content.decode()
    assert "acme/kept" in content
    assert "acme/other" not in content


@pytest.mark.django_db
def test_repository_list_ignores_a_connection_filter_that_is_not_a_number(client, admin_user):
    RepositoryFactory(full_name="acme/widget")
    client.force_login(admin_user)

    response = client.get(reverse("connections:repositories"), {"connection": "not-a-pk"})

    assert response.status_code == 200
    assert "acme/widget" in response.content.decode()


@pytest.mark.django_db
def test_repository_list_does_not_query_per_row(client, admin_user):
    """`select_related` on connection and organization: adding repositories must not add queries."""
    client.force_login(admin_user)
    RepositoryFactory(full_name="acme/one")
    client.get(reverse("connections:repositories"))  # warm any lazily populated caches

    with CaptureQueriesContext(db_connection) as one_row:
        client.get(reverse("connections:repositories"))

    for index in range(2, 6):
        RepositoryFactory(full_name=f"acme/repo-{index}")
    with CaptureQueriesContext(db_connection) as five_rows:
        client.get(reverse("connections:repositories"))

    assert len(five_rows) == len(one_row)


@pytest.mark.django_db
def test_change_connection_form_offers_every_other_active_connection(client, admin_user):
    current = GitHubConnectionFactory(name="Current")
    target = GitHubConnectionFactory(name="Target")
    inactive = GitHubConnectionFactory(name="Retired", is_active=False)
    repository = RepositoryFactory(connection=current)
    client.force_login(admin_user)

    response = client.get(reverse("connections:repository_connection", args=[repository.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    assert f'value="{target.pk}"' in content
    assert f'value="{current.pk}"' not in content
    assert f'value="{inactive.pk}"' not in content


@pytest.mark.django_db
def test_change_connection_moves_the_repository_keeps_prs_and_writes_an_audit_entry(client, admin_user):
    organization = OrganizationFactory(login="acme")
    current = GitHubConnectionFactory(name="Current")
    target = GitHubConnectionFactory(name="Target")
    repository = RepositoryFactory(organization=organization, connection=current, full_name="acme/widget")
    pull_request = PullRequestFactory(repository=repository)
    client.force_login(admin_user)

    response = client.post(
        reverse("connections:repository_connection", args=[repository.pk]),
        {"connection": target.pk, "confirm": "1"},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("connections:repositories")
    repository.refresh_from_db()
    assert repository.connection == target
    pull_request.refresh_from_db()
    assert pull_request.repository_id == repository.pk
    assert AuditEntry.objects.filter(action="repository.rebind").exists()


@pytest.mark.django_db
def test_change_connection_without_confirm_does_not_move_the_repository(client, admin_user):
    current = GitHubConnectionFactory(name="Current")
    target = GitHubConnectionFactory(name="Target")
    repository = RepositoryFactory(connection=current)
    client.force_login(admin_user)

    response = client.post(
        reverse("connections:repository_connection", args=[repository.pk]), {"connection": target.pk}
    )

    assert response.status_code == 200
    repository.refresh_from_db()
    assert repository.connection == current
    assert 'role="alert"' in response.content.decode()


@pytest.mark.django_db
def test_change_connection_refuses_an_inactive_target(client, admin_user):
    current = GitHubConnectionFactory(name="Current")
    inactive = GitHubConnectionFactory(name="Retired", is_active=False)
    repository = RepositoryFactory(connection=current)
    client.force_login(admin_user)

    response = client.post(
        reverse("connections:repository_connection", args=[repository.pk]),
        {"connection": inactive.pk, "confirm": "1"},
    )

    assert response.status_code == 200
    repository.refresh_from_db()
    assert repository.connection == current


@pytest.mark.django_db
def test_change_connection_explains_itself_when_there_is_no_other_connection(client, admin_user):
    repository = RepositoryFactory()
    client.force_login(admin_user)

    response = client.get(reverse("connections:repository_connection", args=[repository.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'data-testid="empty-state"' in content
    assert "<form" not in content.split('id="repository-connection-page"')[1]


@pytest.mark.django_db
def test_repository_settings_need_the_manage_settings_permission(client, lead_user):
    repository = RepositoryFactory()
    client.force_login(lead_user)

    assert client.get(reverse("connections:repositories")).status_code == 403
    assert client.get(reverse("connections:repository_connection", args=[repository.pk])).status_code == 403
