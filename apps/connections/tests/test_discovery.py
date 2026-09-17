import datetime
from html.parser import HTMLParser

import httpx
import pytest
import respx
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import AuditEntry
from apps.activity.factories import PullRequestFactory
from apps.catalog.factories import OrganizationFactory, ProjectFactory, RepositoryFactory
from apps.catalog.models import Organization, Repository
from apps.catalog.services import get_int
from apps.connections.factories import GitHubConnectionFactory
from apps.connections.models import GitHubConnection
from apps.connections.services import set_token

TOKEN = "ghp_discoverytoken0123456789"


def _connection(**kwargs) -> GitHubConnection:
    connection = GitHubConnectionFactory(**kwargs)
    set_token(connection, TOKEN)
    return connection


def _owner_node(node_id: str, name: str, *, is_archived: bool = False, is_private: bool = False) -> dict:
    return {
        "id": node_id,
        "name": name,
        "nameWithOwner": f"acme/{name}",
        "isPrivate": is_private,
        "isArchived": is_archived,
        "defaultBranchRef": {"name": "main"},
        "owner": {"id": "O_acme", "login": "acme", "__typename": "Organization"},
    }


def _owner_page(nodes: list[dict], *, has_next: bool = False) -> dict:
    return {
        "data": {
            "repositoryOwner": {
                "repositories": {
                    "totalCount": len(nodes),
                    "pageInfo": {"hasNextPage": has_next, "endCursor": "next" if has_next else None},
                    "nodes": nodes,
                }
            },
            "rateLimit": {"remaining": 4970, "resetAt": "2026-01-01T01:00:00Z", "cost": 1},
        }
    }


def _mock_owner_pages(*pages: dict) -> respx.Route:
    return respx.post(settings.GITHUB_GRAPHQL_URL).mock(
        side_effect=[httpx.Response(200, json=page) for page in pages]
    )


@pytest.mark.django_db
def test_discovery_groups_by_owner_hides_archived_and_marks_bound_elsewhere(client, admin_user):
    connection = _connection(owner_login="acme")
    other_connection = GitHubConnectionFactory(owner_login="acme")
    organization = OrganizationFactory(login="acme")
    RepositoryFactory(
        organization=organization,
        connection=other_connection,
        name="gadget",
        full_name="acme/gadget",
        github_id="R_gadget",
    )
    page1 = _owner_page([_owner_node("R_widget", "widget")], has_next=True)
    page2 = _owner_page(
        [_owner_node("R_gadget", "gadget"), _owner_node("R_archived", "archived-tool", is_archived=True)]
    )
    _mock_owner_pages(page1, page2)
    client.force_login(admin_user)

    response = client.get(reverse("connections:discover"), {"connection": connection.pk})

    assert response.status_code == 200
    content = response.content.decode()
    assert "acme/widget" in content
    assert "acme/gadget" in content
    assert "acme/archived-tool" not in content
    assert other_connection.name in content


@pytest.mark.django_db
def test_show_archived_reveals_archived_repositories(client, admin_user):
    connection = _connection(owner_login="acme")
    page = _owner_page([_owner_node("R_archived", "archived-tool", is_archived=True)])
    _mock_owner_pages(page)
    client.force_login(admin_user)

    response = client.get(
        reverse("connections:discover"), {"connection": connection.pk, "show_archived": "1"}
    )

    assert "acme/archived-tool" in response.content.decode()


@pytest.mark.django_db
def test_submitting_selection_creates_repository_with_sync_since_and_project(client, admin_user):
    connection = _connection(owner_login="acme")
    project = ProjectFactory()
    page = _owner_page([_owner_node("R_widget", "widget")])
    _mock_owner_pages(page)
    client.force_login(admin_user)

    response = client.post(
        reverse("connections:discover"),
        {"connection": connection.pk, "repo": ["R_widget"], "project": project.slug},
    )

    assert response.status_code == 200
    repository = Repository.objects.get(github_id="R_widget")
    assert repository.full_name == "acme/widget"
    assert repository.connection == connection
    assert repository.sync_since == timezone.localdate() - datetime.timedelta(days=get_int("BACKFILL_DAYS"))
    assert project in repository.projects.all()
    assert Organization.objects.filter(login="acme").exists()


@pytest.mark.django_db
def test_resubmitting_the_same_selection_creates_no_second_row(client, admin_user):
    connection = _connection(owner_login="acme")
    page = _owner_page([_owner_node("R_widget", "widget")])
    _mock_owner_pages(page, page)
    client.force_login(admin_user)

    for _ in range(2):
        client.post(reverse("connections:discover"), {"connection": connection.pk, "repo": ["R_widget"]})

    assert Repository.objects.filter(github_id="R_widget").count() == 1
    assert Organization.objects.filter(login="acme").count() == 1


@pytest.mark.django_db
def test_resubmitting_an_existing_repository_does_not_reset_sync_since(client, admin_user):
    connection = _connection(owner_login="acme")
    page = _owner_page([_owner_node("R_widget", "widget")])
    _mock_owner_pages(page, page)
    client.force_login(admin_user)

    client.post(reverse("connections:discover"), {"connection": connection.pk, "repo": ["R_widget"]})
    repository = Repository.objects.get(github_id="R_widget")
    narrowed_sync_since = timezone.localdate() - datetime.timedelta(days=5)
    repository.sync_since = narrowed_sync_since
    repository.save(update_fields=["sync_since"])

    client.post(reverse("connections:discover"), {"connection": connection.pk, "repo": ["R_widget"]})

    repository.refresh_from_db()
    assert repository.sync_since == narrowed_sync_since


@pytest.mark.django_db
def test_bulk_add_does_not_silently_rebind_a_repository_bound_elsewhere(client, admin_user):
    connection = _connection(owner_login="acme")
    other_connection = GitHubConnectionFactory(owner_login="acme")
    organization = OrganizationFactory(login="acme")
    repository = RepositoryFactory(
        organization=organization,
        connection=other_connection,
        name="gadget",
        full_name="acme/gadget",
        github_id="R_gadget",
    )
    page = _owner_page([_owner_node("R_gadget", "gadget")])
    _mock_owner_pages(page)
    client.force_login(admin_user)

    response = client.post(
        reverse("connections:discover"), {"connection": connection.pk, "repo": ["R_gadget"]}
    )

    assert response.status_code == 200
    repository.refresh_from_db()
    assert repository.connection == other_connection


class _FormNestingChecker(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.nested = False

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            if self.depth > 0:
                self.nested = True
            self.depth += 1

    def handle_endtag(self, tag):
        if tag == "form":
            self.depth = max(self.depth - 1, 0)


def _has_nested_form(html: str) -> bool:
    checker = _FormNestingChecker()
    checker.feed(html)
    return checker.nested


@pytest.mark.django_db
def test_discovery_page_has_no_nested_forms(client, admin_user):
    connection = _connection(owner_login="acme")
    other_connection = GitHubConnectionFactory(owner_login="acme")
    organization = OrganizationFactory(login="acme")
    RepositoryFactory(
        organization=organization,
        connection=other_connection,
        name="gadget",
        full_name="acme/gadget",
        github_id="R_gadget",
    )
    page = _owner_page([_owner_node("R_gadget", "gadget")])
    _mock_owner_pages(page)
    client.force_login(admin_user)

    response = client.get(reverse("connections:discover"), {"connection": connection.pk})

    assert response.status_code == 200
    assert _has_nested_form(response.content.decode()) is False


@pytest.mark.django_db
def test_rebind_moves_connection_keeps_prs_and_writes_audit_entry(client, admin_user):
    organization = OrganizationFactory(login="acme")
    old_connection = GitHubConnectionFactory(owner_login="acme")
    new_connection = _connection(owner_login="acme")
    repository = RepositoryFactory(
        organization=organization, connection=old_connection, github_id="R_widget", full_name="acme/widget"
    )
    pr = PullRequestFactory(repository=repository)
    page = _owner_page([_owner_node("R_widget", "widget")])
    _mock_owner_pages(page)
    client.force_login(admin_user)

    response = client.post(
        reverse("connections:rebind", args=[repository.pk]),
        {"connection": new_connection.pk, "confirm": "1"},
    )

    assert response.status_code == 200
    repository.refresh_from_db()
    assert repository.connection == new_connection
    pr.refresh_from_db()
    assert pr.repository_id == repository.pk
    assert AuditEntry.objects.filter(action="repository.rebind").exists()


@pytest.mark.django_db
def test_rebind_without_confirm_does_not_move_repository(client, admin_user):
    organization = OrganizationFactory(login="acme")
    old_connection = GitHubConnectionFactory(owner_login="acme")
    new_connection = _connection(owner_login="acme")
    repository = RepositoryFactory(organization=organization, connection=old_connection)
    page = _owner_page([])
    _mock_owner_pages(page)
    client.force_login(admin_user)

    response = client.post(
        reverse("connections:rebind", args=[repository.pk]), {"connection": new_connection.pk}
    )

    assert response.status_code == 200
    repository.refresh_from_db()
    assert repository.connection == old_connection
    assert "Confirm" in response.content.decode() or "confirm" in response.content.decode().lower()


@pytest.mark.django_db
def test_rebind_with_recoverable_github_error_renders_visible_error_not_500(client, admin_user):
    """The rebind view re-fetches the discovery list to re-render the page after a successful
    rebind — a flaky GitHub during that re-fetch must not 500 (CLAUDE.md: errors return a visible
    fragment, never an empty 400/500 body)."""
    organization = OrganizationFactory(login="acme")
    old_connection = GitHubConnectionFactory(owner_login="acme")
    new_connection = _connection(owner_login="acme")
    repository = RepositoryFactory(
        organization=organization, connection=old_connection, github_id="R_widget", full_name="acme/widget"
    )
    respx.post(settings.GITHUB_GRAPHQL_URL).mock(
        return_value=httpx.Response(500, json={"message": "Internal Server Error"})
    )
    client.force_login(admin_user)

    response = client.post(
        reverse("connections:rebind", args=[repository.pk]),
        {"connection": new_connection.pk, "confirm": "1"},
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert 'role="alert"' in content
    # The rebind itself must still have gone through — only the re-fetch for rendering failed.
    repository.refresh_from_db()
    assert repository.connection == new_connection


@pytest.mark.django_db
def test_deleting_connection_with_repositories_shows_message_and_survives(client, admin_user):
    connection = GitHubConnectionFactory()
    repository = RepositoryFactory(connection=connection, full_name="acme/widget")

    client.force_login(admin_user)
    response = client.post(reverse("connections:delete", args=[connection.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "acme/widget" in content
    assert connection.__class__.objects.filter(pk=connection.pk).exists()
    assert repository.__class__.objects.filter(pk=repository.pk).exists()


@pytest.mark.django_db
def test_deleting_unused_connection_succeeds(client, admin_user):
    connection = GitHubConnectionFactory()

    client.force_login(admin_user)
    response = client.post(reverse("connections:delete", args=[connection.pk]))

    assert response.status_code == 200
    assert connection.__class__.objects.filter(pk=connection.pk).exists() is False


@pytest.mark.django_db
def test_discover_on_an_inactive_connection_renders_visible_error_not_500(client, admin_user):
    """auth_for_connection() refuses an inactive connection before any HTTP call; discovering
    against one (reachable by editing the `connection` query param) must render a visible alert
    rather than crash (CLAUDE.md: never an empty 400/500 body from an htmx endpoint)."""
    connection = _connection(owner_login="acme")
    connection.is_active = False
    connection.save(update_fields=["is_active"])
    client.force_login(admin_user)

    response = client.get(reverse("connections:discover"), {"connection": connection.pk})

    assert response.status_code == 200
    content = response.content.decode()
    assert 'role="alert"' in content


@pytest.mark.django_db
def test_lead_gets_403_on_discovery_and_rebind(client, lead_user):
    connection = GitHubConnectionFactory()
    repository = RepositoryFactory(connection=connection)
    client.force_login(lead_user)

    assert client.get(reverse("connections:discover")).status_code == 403
    assert client.post(reverse("connections:rebind", args=[repository.pk]), {}).status_code == 403
    assert client.post(reverse("connections:delete", args=[connection.pk])).status_code == 403
