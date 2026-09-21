import logging

import httpx
import pytest
import respx

from apps.github_sync.client import GitHubClient
from apps.github_sync.errors import (
    GitHubAuthError,
    GitHubSchemaError,
    GitHubServerError,
    GitHubSSOError,
    SecondaryRateLimitError,
)
from apps.github_sync.queries import (
    PR_COMMITS_QUERY,
    PR_REVIEWS_QUERY,
    PULL_REQUESTS_QUERY,
    RATE_LIMIT_QUERY,
)
from apps.github_sync.rate_limit import RateBudget
from apps.github_sync.tests.conftest import mock_graphql_responses, mock_graphql_sequence


class FakeAuth:
    rate_limit_key = "connection:fake"

    def get_headers(self):
        return {"Authorization": "Bearer fake-token", "Accept": "application/vnd.github+json"}

    def get_git_credentials(self):
        return ("x-access-token", "fake-token")


def make_client(sleep=None):
    return GitHubClient(FakeAuth(), RateBudget(key="connection:fake"), sleep=sleep or (lambda s: None))


def make_reviews_page(start, count, has_next, end_cursor=None):
    nodes = [
        {
            "id": f"PRR_{i}",
            "author": {"login": f"reviewer-{i}"},
            "state": "APPROVED",
            "submittedAt": "2026-01-02T10:00:00Z",
            "body": "ok",
            "comments": {"totalCount": 0},
        }
        for i in range(start, start + count)
    ]
    page_info = {"hasNextPage": has_next, "endCursor": end_cursor}
    return {
        "data": {
            "node": {"reviews": {"pageInfo": page_info, "nodes": nodes}},
            "rateLimit": {"remaining": 4900, "resetAt": "2026-01-01T01:00:00Z", "cost": 1},
        }
    }


@pytest.mark.django_db
def test_graphql_sends_auth_header_and_feeds_rate_limit_to_budget(github_fixture):
    route = mock_graphql_sequence(github_fixture("rate_limit"))
    client = make_client()
    data = client.graphql(RATE_LIMIT_QUERY, {})
    assert data["rateLimit"]["remaining"] == 4980
    assert client.budget.remaining == 4980
    assert route.calls.last.request.headers["Authorization"] == "Bearer fake-token"
    assert route.calls.last.request.headers["Accept"] == "application/vnd.github+json"


@pytest.mark.django_db
def test_paginate_follows_page_info_across_two_pages(github_fixture):
    mock_graphql_sequence(github_fixture("pull_requests_page1"), github_fixture("pull_requests_page2"))
    client = make_client()
    nodes = list(
        client.paginate(
            PULL_REQUESTS_QUERY,
            {"owner": "acme", "name": "widget"},
            page_path="repository.pullRequests",
            page_size=50,
        )
    )
    assert len(nodes) == 3
    assert [n["number"] for n in nodes] == [101, 102, 100]


@pytest.mark.django_db
def test_nested_reviews_are_paginated_past_one_hundred(github_fixture):
    page1 = make_reviews_page(0, 100, has_next=True, end_cursor="cursor-1")
    page2 = github_fixture("pr_reviews_page2")
    route = mock_graphql_sequence(page1, page2)
    client = make_client()
    nodes = list(client.paginate(PR_REVIEWS_QUERY, {"id": "PR_x"}, page_path="node.reviews", page_size=100))
    assert len(nodes) == 150
    assert route.call_count == 2


@pytest.mark.django_db
def test_truncated_nested_page_raises():
    malformed_page = {
        "data": {
            "node": {"reviews": {"pageInfo": {"hasNextPage": True}, "nodes": []}},
            "rateLimit": {"remaining": 4900, "resetAt": "2026-01-01T01:00:00Z", "cost": 1},
        }
    }
    mock_graphql_sequence(malformed_page)
    client = make_client()
    with pytest.raises(GitHubSchemaError):
        list(client.paginate(PR_REVIEWS_QUERY, {"id": "PR_x"}, page_path="node.reviews", page_size=100))


@pytest.mark.django_db
def test_502_then_200_succeeds_after_one_retry(github_fixture):
    responses = [
        httpx.Response(502, json=github_fixture("server_error_502")),
        httpx.Response(200, json=github_fixture("rate_limit")),
    ]
    mock_graphql_responses(*responses)
    sleeps = []
    client = make_client(sleep=sleeps.append)
    data = client.graphql(RATE_LIMIT_QUERY, {})
    assert data["rateLimit"]["remaining"] == 4980
    assert len(sleeps) == 1


@pytest.mark.django_db
def test_502_exhausts_retries_and_raises(github_fixture):
    responses = [httpx.Response(502, json=github_fixture("server_error_502")) for _ in range(6)]
    mock_graphql_responses(*responses)
    client = make_client()
    with pytest.raises(GitHubServerError):
        client.graphql(RATE_LIMIT_QUERY, {})


@pytest.mark.django_db
def test_secondary_rate_limit_honours_retry_after_then_succeeds(github_fixture):
    responses = [
        httpx.Response(403, json=github_fixture("secondary_rate_limit"), headers={"Retry-After": "30"}),
        httpx.Response(200, json=github_fixture("rate_limit")),
    ]
    mock_graphql_responses(*responses)
    sleeps = []
    client = make_client(sleep=sleeps.append)
    data = client.graphql(RATE_LIMIT_QUERY, {})
    assert data["rateLimit"]["remaining"] == 4980
    assert sleeps == [30.0]


@pytest.mark.django_db
def test_secondary_rate_limit_exhausts_retries_and_raises(github_fixture):
    responses = [
        httpx.Response(403, json=github_fixture("secondary_rate_limit"), headers={"Retry-After": "0"})
        for _ in range(6)
    ]
    mock_graphql_responses(*responses)
    client = make_client()
    with pytest.raises(SecondaryRateLimitError):
        client.graphql(RATE_LIMIT_QUERY, {})


@pytest.mark.django_db
def test_401_raises_without_retrying(github_fixture):
    route = mock_graphql_responses(httpx.Response(401, json={"message": "Bad credentials"}))
    client = make_client()
    with pytest.raises(GitHubAuthError):
        client.graphql(RATE_LIMIT_QUERY, {})
    assert route.call_count == 1


@pytest.mark.django_db
def test_403_sso_raises_with_org_and_url():
    sso_url = "https://github.com/orgs/acme/sso?authorization_request=abc"
    sso_header = f"required; url={sso_url}"
    route = mock_graphql_responses(
        httpx.Response(403, json={"message": "SSO required"}, headers={"X-GitHub-SSO": sso_header})
    )
    client = make_client()
    with pytest.raises(GitHubSSOError) as exc_info:
        client.graphql(RATE_LIMIT_QUERY, {})
    assert exc_info.value.org == "acme"
    assert exc_info.value.url == sso_url
    assert route.call_count == 1


@pytest.mark.django_db
def test_graphql_errors_array_unauthorized_raises_auth_error(github_fixture):
    mock_graphql_responses(httpx.Response(200, json=github_fixture("graphql_errors_unauthorized")))
    client = make_client()
    with pytest.raises(GitHubAuthError):
        client.graphql(RATE_LIMIT_QUERY, {})


@pytest.mark.django_db
def test_graphql_denial_of_a_root_field_raises_auth_error_naming_the_path(github_fixture):
    """`repository` itself refused means the token may not read this repository at all, which is
    worth quarantining the connection for — and the message has to say which field, or
    SyncRun.error_log reads "authentication failed" with nothing to act on."""
    mock_graphql_responses(httpx.Response(200, json=github_fixture("graphql_errors_repository_forbidden")))
    client = make_client()
    with pytest.raises(GitHubAuthError) as exc_info:
        client.graphql(PULL_REQUESTS_QUERY, {"owner": "acme", "name": "widgets", "first": 1})
    message = str(exc_info.value)
    assert "FORBIDDEN at repository" in message
    assert "Resource not accessible by personal access token" in message


@pytest.mark.django_db
def test_graphql_denial_of_a_nested_field_keeps_the_data_and_the_connection(github_fixture, caplog):
    """A fine-grained token without **Checks** gets a FORBIDDEN on `statusCheckRollup` alone while
    the rest of the document resolves. Treating that as a credential failure marked the whole
    connection invalid over an optional permission; the resolved commits must come back instead,
    with the denial logged once."""
    mock_graphql_responses(httpx.Response(200, json=github_fixture("graphql_errors_field_forbidden")))
    client = make_client()
    with caplog.at_level(logging.WARNING, logger="apps.github_sync.client"):
        data = client.graphql(PR_COMMITS_QUERY, {"id": "PR_1", "first": 1})

    commits = data["node"]["commits"]["nodes"]
    assert [commit["commit"]["oid"] for commit in commits] == ["commit0001"]
    assert commits[0]["commit"]["statusCheckRollup"] is None
    assert "statusCheckRollup" in caplog.text


@pytest.mark.django_db
def test_missing_end_cursor_on_a_further_page_raises_schema_error():
    """paginate() itself depends on pageInfo.endCursor whenever hasNextPage is true — unlike
    author.login, which the client never reads (mappers.py treats a ghost author as optional).
    A page missing it must raise GitHubSchemaError naming the path, not a bare KeyError."""
    page = {
        "data": {
            "repository": {
                "pullRequests": {
                    "pageInfo": {"hasNextPage": True},
                    "nodes": [],
                }
            },
            "rateLimit": {"remaining": 4900, "resetAt": "2026-01-01T01:00:00Z", "cost": 1},
        }
    }
    mock_graphql_sequence(page)
    client = make_client()

    with pytest.raises(GitHubSchemaError) as exc_info:
        list(
            client.paginate(
                PULL_REQUESTS_QUERY,
                {"owner": "acme", "name": "widget"},
                page_path="repository.pullRequests",
                page_size=50,
            )
        )

    assert exc_info.value.path == "repository.pullRequests.pageInfo.endCursor"


@pytest.mark.django_db
def test_unrouted_host_is_refused():
    client = make_client()
    with pytest.raises(respx.models.AllMockedAssertionError):
        client.graphql(RATE_LIMIT_QUERY, {})
