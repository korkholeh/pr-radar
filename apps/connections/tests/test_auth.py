import pytest

from apps.connections.auth import ConnectionNotUsableError, auth_for_connection
from apps.connections.factories import GitHubConnectionFactory
from apps.connections.services import set_token

TOKEN = "ghp_secrettokenvalue0123456789"


@pytest.mark.django_db
def test_headers_carry_bearer_and_graphql_accept():
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    auth = auth_for_connection(connection)
    headers = auth.get_headers()
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["Accept"] == "application/vnd.github+json"


@pytest.mark.django_db
def test_rate_limit_key_differs_per_connection_and_is_stable():
    connection_a = GitHubConnectionFactory()
    set_token(connection_a, TOKEN)
    connection_b = GitHubConnectionFactory()
    set_token(connection_b, TOKEN)

    auth_a = auth_for_connection(connection_a)
    auth_b = auth_for_connection(connection_b)

    assert auth_a.rate_limit_key != auth_b.rate_limit_key
    assert auth_a.rate_limit_key == auth_for_connection(connection_a).rate_limit_key


@pytest.mark.django_db
def test_get_git_credentials():
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    auth = auth_for_connection(connection)
    assert auth.get_git_credentials() == ("x-access-token", TOKEN)


@pytest.mark.django_db
def test_repr_and_str_hide_the_token():
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    auth = auth_for_connection(connection)
    assert TOKEN not in repr(auth)
    assert TOKEN not in str(auth)
    assert repr(auth) == f"PATAuth(connection_id={connection.pk})"


@pytest.mark.django_db
def test_inactive_connection_raises():
    connection = GitHubConnectionFactory(is_active=False)
    set_token(connection, TOKEN)
    with pytest.raises(ConnectionNotUsableError):
        auth_for_connection(connection)


@pytest.mark.django_db
def test_tokenless_connection_raises():
    connection = GitHubConnectionFactory()
    with pytest.raises(ConnectionNotUsableError):
        auth_for_connection(connection)


@pytest.mark.django_db
def test_pytest_failure_rendering_of_auth_local_has_no_token():
    # Guard against a traceback rendering the token via the dataclass's default repr:
    # assert the object's repr, which is what a captured local variable would show, hides it.
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    auth = auth_for_connection(connection)
    rendered = f"{auth!r}"
    assert TOKEN not in rendered
