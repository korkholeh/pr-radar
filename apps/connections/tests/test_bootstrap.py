import httpx
import pytest
import respx
from django.conf import settings
from django.core.management import call_command

from apps.connections.crypto import token_last4
from apps.connections.factories import GitHubConnectionFactory
from apps.connections.models import GitHubConnection

TOKEN = "ghp_bootstraptoken0123456789"


def _mock_user(status_code=200, body=None):
    return respx.get(f"{settings.GITHUB_API_BASE_URL}/user").mock(
        return_value=httpx.Response(status_code, json=body or {"login": "octocat"})
    )


def _mock_graphql(*bodies):
    return respx.post(settings.GITHUB_GRAPHQL_URL).mock(
        side_effect=[httpx.Response(200, json=b) for b in bodies]
    )


@pytest.mark.django_db
def test_no_env_var_and_no_connections_exits_cleanly(capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    call_command("bootstrap_connection")

    assert GitHubConnection.objects.count() == 0
    out = capsys.readouterr().out
    assert "GITHUB_TOKEN is not set" in out


@pytest.mark.django_db
def test_creates_default_connection_from_env_var(capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)

    call_command("bootstrap_connection")

    connection = GitHubConnection.objects.get()
    assert connection.name == "Default (.env)"
    assert connection.kind == "fine_grained_pat"
    assert connection.status == GitHubConnection.Status.UNVERIFIED
    assert connection.token_last4 == token_last4(TOKEN)
    assert connection.token_encrypted is not None
    out = capsys.readouterr().out
    assert TOKEN not in out


@pytest.mark.django_db
def test_existing_connection_blocks_bootstrap_and_warns(capsys, monkeypatch, caplog):
    GitHubConnectionFactory()
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)

    call_command("bootstrap_connection")

    assert GitHubConnection.objects.count() == 1
    out = capsys.readouterr().out
    assert "already exists" in out
    assert "GITHUB_TOKEN" in out
    assert TOKEN not in out


@pytest.mark.django_db
def test_existing_connection_without_env_var_is_a_noop(capsys, monkeypatch):
    GitHubConnectionFactory()
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    call_command("bootstrap_connection")

    assert GitHubConnection.objects.count() == 1
    out = capsys.readouterr().out
    assert "Nothing to do" in out


@pytest.mark.django_db
def test_verify_flag_calls_client_only_against_respx(capsys, monkeypatch, github_fixture):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    _mock_user()
    _mock_graphql(github_fixture("viewer_repositories"), github_fixture("rate_limit"))

    call_command("bootstrap_connection", "--verify")

    connection = GitHubConnection.objects.get()
    assert connection.status == GitHubConnection.Status.OK
    out = capsys.readouterr().out
    assert TOKEN not in out
