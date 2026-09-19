import datetime

import httpx
import pytest
import respx
from django.conf import settings
from django.utils import timezone

from apps.catalog.factories import RepositoryFactory
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
        "PERM_PULL_REQUESTS_UNAVAILABLE",
        "PERM_CONTENTS_UNAVAILABLE",
        "RATE_LIMIT",
    }
    perm_contents = next(
        c for c in connection.last_check_result["checks"] if c["code"] == "PERM_CONTENTS_UNAVAILABLE"
    )
    assert perm_contents["outcome"] == "unavailable"


@pytest.mark.django_db
def test_token_that_sees_no_repository_is_degraded_not_ok(github_fixture):
    """A token with zero visible repositories used to verify as OK, leaving an empty discovery page
    as the only symptom."""
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    _mock_user()
    empty = {
        "data": {
            "viewer": {
                "repositories": {
                    "totalCount": 0,
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [],
                }
            },
            "rateLimit": {"remaining": 4970, "resetAt": "2026-01-01T01:00:00Z", "cost": 1},
        }
    }
    _mock_graphql(empty, github_fixture("rate_limit"))

    verify_connection(connection)

    connection.refresh_from_db()
    assert connection.status == GitHubConnection.Status.DEGRADED
    codes = {c["code"] for c in connection.last_check_result["checks"]}
    assert "REPOS_VISIBLE_NONE" in codes
    assert "REPOS_VISIBLE" not in codes


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


def _mock_repo_rest(full_name, *, pulls_status=200, contents_status=200):
    """The two probes verify_connection() runs against one of the connection's repositories."""
    pulls = respx.get(f"{settings.GITHUB_API_BASE_URL}/repos/{full_name}/pulls").mock(
        return_value=httpx.Response(
            pulls_status, json=[] if pulls_status == 200 else {"message": "Not Found"}
        )
    )
    contents = respx.get(f"{settings.GITHUB_API_BASE_URL}/repos/{full_name}/contents/").mock(
        return_value=httpx.Response(
            contents_status, json=[] if contents_status == 200 else {"message": "Not Found"}
        )
    )
    return pulls, contents


@pytest.mark.django_db
def test_pull_request_access_is_probed_not_assumed(github_fixture):
    """PERM_PULL_REQUESTS used to be appended as ok without a single request behind it."""
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    repository = RepositoryFactory(connection=connection, full_name="acme/widget")
    _mock_user()
    _mock_graphql(github_fixture("viewer_repositories"), github_fixture("rate_limit"))
    pulls, _contents = _mock_repo_rest(repository.full_name)

    verify_connection(connection)

    connection.refresh_from_db()
    assert pulls.call_count == 1
    assert connection.status == GitHubConnection.Status.OK
    check = next(c for c in connection.last_check_result["checks"] if c["code"] == "PERM_PULL_REQUESTS")
    assert check["outcome"] == "ok"
    assert check["params"] == {"repository": "acme/widget"}


@pytest.mark.django_db
def test_a_404_on_pull_requests_is_denied_access_not_a_crash(github_fixture):
    """GitHub answers 404, not 403, for a private repository the token may not read."""
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    repository = RepositoryFactory(connection=connection, full_name="acme/private", is_private=True)
    _mock_user()
    _mock_graphql(github_fixture("viewer_repositories"), github_fixture("rate_limit"))
    _mock_repo_rest(repository.full_name, pulls_status=404, contents_status=404)

    verify_connection(connection)

    connection.refresh_from_db()
    assert connection.status == GitHubConnection.Status.DEGRADED
    codes = {c["code"] for c in connection.last_check_result["checks"]}
    assert "PERM_PULL_REQUESTS_DENIED" in codes
    assert "PERM_CONTENTS_DENIED" in codes
    denied = next(
        c for c in connection.last_check_result["checks"] if c["code"] == "PERM_PULL_REQUESTS_DENIED"
    )
    assert denied["params"] == {"repository": "acme/private"}


@pytest.mark.django_db
def test_a_403_on_pull_requests_is_denied_access_too(github_fixture):
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    repository = RepositoryFactory(connection=connection, full_name="acme/forbidden")
    _mock_user()
    _mock_graphql(github_fixture("viewer_repositories"), github_fixture("rate_limit"))
    _mock_repo_rest(repository.full_name, pulls_status=403, contents_status=403)

    verify_connection(connection)

    connection.refresh_from_db()
    codes = {c["code"] for c in connection.last_check_result["checks"]}
    assert "PERM_PULL_REQUESTS_DENIED" in codes


@pytest.mark.django_db
def test_classic_pat_without_repo_scope_is_degraded_when_a_private_repository_is_tracked(
    github_fixture,
):
    """'public_repo' cannot read a private repository, and GitHub says so with silence: the sync
    reports success with zero pull requests."""
    connection = GitHubConnectionFactory(kind=GitHubConnection.Kind.CLASSIC_PAT)
    set_token(connection, TOKEN)
    repository = RepositoryFactory(connection=connection, full_name="acme/private", is_private=True)
    _mock_user(headers={"X-OAuth-Scopes": "public_repo, read:org"})
    _mock_graphql(github_fixture("viewer_repositories"), github_fixture("rate_limit"))
    _mock_repo_rest(repository.full_name, pulls_status=404, contents_status=404)

    verify_connection(connection)

    connection.refresh_from_db()
    assert connection.status == GitHubConnection.Status.DEGRADED
    check = next(
        c for c in connection.last_check_result["checks"] if c["code"] == "CLASSIC_PAT_NO_PRIVATE_SCOPE"
    )
    assert check["outcome"] == "fail"
    assert check["params"]["scopes"] == ["public_repo", "read:org"]


@pytest.mark.django_db
def test_classic_pat_without_repo_scope_is_fine_when_every_repository_is_public(github_fixture):
    connection = GitHubConnectionFactory(kind=GitHubConnection.Kind.CLASSIC_PAT)
    set_token(connection, TOKEN)
    repository = RepositoryFactory(connection=connection, full_name="acme/public", is_private=False)
    _mock_user(headers={"X-OAuth-Scopes": "public_repo, read:org"})
    _mock_graphql(github_fixture("viewer_repositories"), github_fixture("rate_limit"))
    _mock_repo_rest(repository.full_name)

    verify_connection(connection)

    connection.refresh_from_db()
    assert connection.status == GitHubConnection.Status.OK
    codes = {c["code"] for c in connection.last_check_result["checks"]}
    assert "CLASSIC_PAT_NO_PRIVATE_SCOPE" not in codes


def test_perm_pull_requests_message_differs_by_outcome():
    from apps.connections.check_codes import render_message

    params = {"repository": "acme/widget"}
    rendered = {
        "ok": render_message("PERM_PULL_REQUESTS", params),
        "denied": render_message("PERM_PULL_REQUESTS_DENIED", params),
        "unavailable": render_message("PERM_PULL_REQUESTS_UNAVAILABLE", {}),
    }
    assert len(set(rendered.values())) == 3
