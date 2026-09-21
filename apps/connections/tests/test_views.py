import httpx
import pytest
import respx
from django.conf import settings
from django.urls import reverse

from apps.accounts.models import AuditEntry
from apps.connections.factories import GitHubConnectionFactory
from apps.connections.models import GitHubConnection
from apps.connections.services import set_token
from apps.connections.tests.rest_mocks import mock_repository_listing

TOKEN = "ghp_secrettokenvalue0123456789"
URL_NAMES = [
    "connections:list",
    "connections:create",
]


def _mock_user(headers=None, status_code=200, body=None):
    return respx.get(f"{settings.GITHUB_API_BASE_URL}/user").mock(
        return_value=httpx.Response(status_code, json=body or {"login": "octocat"}, headers=headers or {})
    )


def _mock_graphql(*bodies):
    return respx.post(settings.GITHUB_GRAPHQL_URL).mock(
        side_effect=[httpx.Response(200, json=b) for b in bodies]
    )


@pytest.mark.django_db
@pytest.mark.parametrize("name", URL_NAMES)
def test_lead_gets_403(client, lead_user, name):
    client.force_login(lead_user)
    response = client.get(reverse(name))
    assert response.status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("name", URL_NAMES)
def test_admin_gets_200(client, admin_user, name):
    client.force_login(admin_user)
    response = client.get(reverse(name))
    assert response.status_code == 200


@pytest.mark.django_db
def test_edit_lead_gets_403_admin_gets_200(client, lead_user, admin_user):
    connection = GitHubConnectionFactory()

    client.force_login(lead_user)
    assert client.get(reverse("connections:edit", args=[connection.pk])).status_code == 403

    client.force_login(admin_user)
    assert client.get(reverse("connections:edit", args=[connection.pk])).status_code == 200


@pytest.mark.django_db
def test_create_form_verifies_before_saving(client, admin_user, github_fixture):
    client.force_login(admin_user)
    _mock_user()
    mock_repository_listing(github_fixture("rest_user_repos_page1"))
    _mock_graphql(github_fixture("rate_limit"))

    response = client.post(
        reverse("connections:create"),
        {
            "name": "Default",
            "kind": GitHubConnection.Kind.FINE_GRAINED_PAT,
            "owner_login": "",
            "token": TOKEN,
        },
    )

    assert response.status_code == 302
    connection = GitHubConnection.objects.get(name="Default")
    assert connection.status == GitHubConnection.Status.OK
    assert connection.token_login == "octocat"
    assert AuditEntry.objects.filter(action="connection.create").exists()


@pytest.mark.django_db
def test_create_refuses_to_save_an_unverified_connection_without_opt_in(client, admin_user):
    client.force_login(admin_user)
    _mock_user(status_code=401, body={"message": "Bad credentials"})

    response = client.post(
        reverse("connections:create"),
        {
            "name": "Default",
            "kind": GitHubConnection.Kind.FINE_GRAINED_PAT,
            "owner_login": "",
            "token": TOKEN,
        },
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert 'role="alert"' in content
    assert not GitHubConnection.objects.filter(name="Default").exists()


@pytest.mark.django_db
def test_create_with_recoverable_github_error_renders_visible_form_error_not_500(client, admin_user):
    """A GitHub error verify_connection() doesn't itself swallow (unlike 401/SSO, which it turns
    into a stored check) must not 500 the create view — it re-renders the form with a visible
    error and creates nothing (CLAUDE.md: errors return a visible fragment, never an empty body)."""
    client.force_login(admin_user)
    _mock_user(status_code=500, body={"message": "Internal Server Error"})

    response = client.post(
        reverse("connections:create"),
        {
            "name": "Default",
            "kind": GitHubConnection.Kind.FINE_GRAINED_PAT,
            "owner_login": "",
            "token": TOKEN,
        },
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert 'role="alert"' in content
    assert not GitHubConnection.objects.filter(name="Default").exists()


@pytest.mark.django_db
def test_edit_with_recoverable_github_error_renders_visible_form_error_not_500(client, admin_user):
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    client.force_login(admin_user)
    _mock_user(status_code=500, body={"message": "Internal Server Error"})

    response = client.post(
        reverse("connections:edit", args=[connection.pk]),
        {
            "name": connection.name,
            "kind": connection.kind,
            "owner_login": "",
            "token": TOKEN,
        },
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert 'role="alert"' in content


@pytest.mark.django_db
def test_create_with_save_unverified_opt_in_skips_verification(client, admin_user):
    client.force_login(admin_user)

    response = client.post(
        reverse("connections:create"),
        {
            "name": "Default",
            "kind": GitHubConnection.Kind.FINE_GRAINED_PAT,
            "owner_login": "",
            "token": TOKEN,
            "save_unverified": "on",
        },
    )

    assert response.status_code == 302
    connection = GitHubConnection.objects.get(name="Default")
    assert connection.status == GitHubConnection.Status.UNVERIFIED


@pytest.mark.django_db
def test_list_and_edit_render_last4_and_no_token_fragment(client, admin_user):
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    client.force_login(admin_user)

    list_content = client.get(reverse("connections:list")).content.decode()
    edit_content = client.get(reverse("connections:edit", args=[connection.pk])).content.decode()

    for content in (list_content, edit_content):
        assert connection.token_last4 in content
        assert TOKEN not in content
        assert TOKEN[:-4] not in content
    assert "reveal" not in edit_content.lower()
    assert 'type="password"' in edit_content


@pytest.mark.django_db
def test_check_button_returns_fragment_for_hx_request_and_full_page_otherwise(
    client, admin_user, github_fixture
):
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    client.force_login(admin_user)
    _mock_user()
    mock_repository_listing(github_fixture("rest_user_repos_page1"))
    _mock_graphql(github_fixture("rate_limit"))

    response = client.post(reverse("connections:check", args=[connection.pk]), HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert "<html" not in response.content.decode().lower()

    _mock_user()
    mock_repository_listing(github_fixture("rest_user_repos_page1"))
    _mock_graphql(github_fixture("rate_limit"))
    response = client.post(reverse("connections:check", args=[connection.pk]))
    assert response.status_code == 302


@pytest.mark.django_db
def test_invalid_token_check_renders_visible_error_not_empty_400(client, admin_user):
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    client.force_login(admin_user)
    _mock_user(status_code=401, body={"message": "Bad credentials"})

    response = client.post(reverse("connections:check", args=[connection.pk]), HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    content = response.content.decode()
    assert content.strip() != ""
    connection.refresh_from_db()
    assert connection.status == GitHubConnection.Status.INVALID


@pytest.mark.django_db
def test_check_on_an_inactive_connection_renders_visible_error_not_500(client, admin_user):
    """The list page offers "Check" on an inactive row, and auth_for_connection() refuses to
    produce credentials for one (ConnectionNotUsableError) — that must render a visible
    alert, not the unhandled-exception 500 this used to be (CLAUDE.md: never an empty 400/500
    body from an htmx endpoint)."""
    connection = GitHubConnectionFactory(is_active=False)
    set_token(connection, TOKEN)
    client.force_login(admin_user)

    response = client.post(reverse("connections:check", args=[connection.pk]), HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    content = response.content.decode()
    assert 'role="alert"' in content


@pytest.mark.django_db
def test_audit_entries_contain_no_token(client, admin_user, github_fixture):
    client.force_login(admin_user)
    _mock_user()
    mock_repository_listing(github_fixture("rest_user_repos_page1"))
    _mock_graphql(github_fixture("rate_limit"))

    client.post(
        reverse("connections:create"),
        {
            "name": "Default",
            "kind": GitHubConnection.Kind.FINE_GRAINED_PAT,
            "owner_login": "",
            "token": TOKEN,
        },
    )

    for entry in AuditEntry.objects.all():
        assert TOKEN not in str(entry.changes)


CANARY_ENGLISH_STRINGS = [
    ">Connections<",
    "New connection",
    "Edit connection",
    "Discover repositories",
    ">Save<",
    ">Edit<",
    ">Check<",
    ">Delete<",
    ">Activate<",
    ">Deactivate<",
    ">Name<",
    ">Kind<",
    ">Token<",
    ">Status<",
    ">Actions<",
    "No GitHub connections yet.",
]


@pytest.mark.django_db
def test_uk_render_has_no_canary_english(client, admin_user):
    client.force_login(admin_user)
    client.post(reverse("accounts:set_language"), {"language": "uk", "next": "/"})
    GitHubConnectionFactory()

    for name in ["connections:list", "connections:create", "connections:discover"]:
        response = client.get(reverse(name))
        content = response.content.decode()
        for canary in CANARY_ENGLISH_STRINGS:
            assert canary not in content, (
                f"untranslated English string {canary!r} leaked into uk render of {name}"
            )
