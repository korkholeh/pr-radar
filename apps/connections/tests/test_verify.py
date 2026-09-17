import datetime

import httpx
import pytest
import respx
from django.conf import settings
from django.utils import timezone

from apps.connections.check_codes import CHECK_CODES
from apps.connections.factories import GitHubConnectionFactory
from apps.connections.models import GitHubConnection
from apps.connections.services import plaintext_token, set_token, verify_connection

TOKEN = "ghp_secrettokenvalue0123456789"


def _mock_user(headers=None, status_code=200, body=None):
    return respx.get(f"{settings.GITHUB_API_BASE_URL}/user").mock(
        return_value=httpx.Response(status_code, json=body or {"login": "octocat"}, headers=headers or {})
    )


def _mock_graphql(*bodies):
    return respx.post(settings.GITHUB_GRAPHQL_URL).mock(
        side_effect=[httpx.Response(200, json=b) for b in bodies]
    )


@pytest.mark.django_db
def test_healthy_connection_is_verified_ok(github_fixture):
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    _mock_user()
    _mock_graphql(github_fixture("viewer_repositories"), github_fixture("rate_limit"))

    verify_connection(connection)

    connection.refresh_from_db()
    assert connection.status == GitHubConnection.Status.OK
    assert connection.token_login == "octocat"
    codes = {c["code"] for c in connection.last_check_result["checks"]}
    assert codes == {
        "TOKEN_USER_OK",
        "REPOS_VISIBLE",
        "PERM_PULL_REQUESTS",
        "PERM_CONTENTS_UNAVAILABLE",
        "RATE_LIMIT",
    }
    perm_contents = next(
        c for c in connection.last_check_result["checks"] if c["code"] == "PERM_CONTENTS_UNAVAILABLE"
    )
    assert perm_contents["outcome"] == "unavailable"


@pytest.mark.django_db
def test_401_stores_invalid_and_auth_failed():
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    _mock_user(status_code=401, body={"message": "Bad credentials"})

    verify_connection(connection)

    connection.refresh_from_db()
    assert connection.status == GitHubConnection.Status.INVALID
    codes = {c["code"] for c in connection.last_check_result["checks"]}
    assert codes == {"AUTH_FAILED"}


@pytest.mark.django_db
def test_classic_pat_with_repo_scope_is_degraded(github_fixture):
    connection = GitHubConnectionFactory(kind=GitHubConnection.Kind.CLASSIC_PAT)
    set_token(connection, TOKEN)
    _mock_user(headers={"X-OAuth-Scopes": "repo, read:org"})
    _mock_graphql(github_fixture("viewer_repositories"), github_fixture("rate_limit"))

    verify_connection(connection)

    connection.refresh_from_db()
    assert connection.status == GitHubConnection.Status.DEGRADED
    write_scope_check = next(
        c for c in connection.last_check_result["checks"] if c["code"] == "CLASSIC_PAT_WRITE_SCOPE"
    )
    assert write_scope_check["outcome"] == "fail"
    assert write_scope_check["params"]["scopes"] == ["repo", "read:org"]


@pytest.mark.django_db
def test_403_sso_stores_degraded_with_org_and_url():
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    _mock_user()
    sso_url = "https://github.com/orgs/acme/sso?authorization_request=abc"
    respx.post(settings.GITHUB_GRAPHQL_URL).mock(
        return_value=httpx.Response(
            403, json={"message": "SSO required"}, headers={"X-GitHub-SSO": f"required; url={sso_url}"}
        )
    )

    verify_connection(connection)

    connection.refresh_from_db()
    assert connection.status == GitHubConnection.Status.DEGRADED
    sso_check = next(
        c for c in connection.last_check_result["checks"] if c["code"] == "SSO_AUTHORIZATION_REQUIRED"
    )
    assert sso_check["outcome"] == "fail"
    assert sso_check["params"] == {"org": "acme", "url": sso_url}


@pytest.mark.django_db
def test_403_sso_on_user_endpoint_stores_degraded_with_org_and_url():
    """A token valid but not SSO-authorized gets 403 + X-GitHub-SSO on REST too (not just
    GraphQL) — this must not be misdiagnosed as a network failure by the view's generic handler."""
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    sso_url = "https://github.com/orgs/acme/sso?authorization_request=abc"
    _mock_user(
        status_code=403,
        body={"message": "SSO required"},
        headers={"X-GitHub-SSO": f"required; url={sso_url}"},
    )

    verify_connection(connection)

    connection.refresh_from_db()
    assert connection.status == GitHubConnection.Status.DEGRADED
    codes = {c["code"] for c in connection.last_check_result["checks"]}
    assert codes == {"SSO_AUTHORIZATION_REQUIRED"}
    sso_check = next(
        c for c in connection.last_check_result["checks"] if c["code"] == "SSO_AUTHORIZATION_REQUIRED"
    )
    assert sso_check["params"] == {"org": "acme", "url": sso_url}


@pytest.mark.django_db
def test_expired_token_header_yields_expired_status(github_fixture):
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    _mock_user(headers={"github-authentication-token-expiration": "2020-01-01 00:00:00 UTC"})
    _mock_graphql(github_fixture("viewer_repositories"), github_fixture("rate_limit"))

    verify_connection(connection)

    connection.refresh_from_db()
    assert connection.status == GitHubConnection.Status.EXPIRED
    assert connection.expires_at == datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)


@pytest.mark.django_db
def test_no_stored_param_contains_a_token_shape(github_fixture):
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    _mock_user()
    _mock_graphql(github_fixture("viewer_repositories"), github_fixture("rate_limit"))

    verify_connection(connection)

    connection.refresh_from_db()
    serialized = str(connection.last_check_result)
    assert TOKEN not in serialized
    assert plaintext_token(connection) not in serialized.replace(TOKEN, "")


@pytest.mark.django_db
def test_every_emitted_code_has_a_check_codes_entry(github_fixture):
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    _mock_user()
    _mock_graphql(github_fixture("viewer_repositories"), github_fixture("rate_limit"))

    verify_connection(connection)

    connection.refresh_from_db()
    for check in connection.last_check_result["checks"]:
        assert check["code"] in CHECK_CODES


def test_perm_contents_message_differs_by_outcome():
    from apps.connections.check_codes import render_message

    rendered = {
        outcome: render_message(f"PERM_CONTENTS_{outcome}", {}) for outcome in ("OK", "DENIED", "UNAVAILABLE")
    }
    assert len(set(rendered.values())) == 3


@pytest.mark.django_db
def test_second_call_within_the_hour_is_throttled(github_fixture):
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    route = _mock_user()
    _mock_graphql(github_fixture("viewer_repositories"), github_fixture("rate_limit"))

    verify_connection(connection)
    assert route.call_count == 1

    verify_connection(connection)
    assert route.call_count == 1


@pytest.mark.django_db
def test_force_bypasses_the_throttle(github_fixture):
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    connection.last_checked_at = timezone.now()
    connection.save(update_fields=["last_checked_at"])
    route = _mock_user()
    _mock_graphql(
        github_fixture("viewer_repositories"),
        github_fixture("rate_limit"),
    )

    verify_connection(connection, force=True)

    assert route.call_count == 1
