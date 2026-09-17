"""Fixture corpus sanity check: a malformed fixture must fail loudly here, not mid-client."""

import pytest

GRAPHQL_ENVELOPE_FIXTURES = [
    "rate_limit",
    "discovery_repos_page1",
    "discovery_repos_page2",
    "viewer_repositories",
    "pull_requests_page1",
    "pull_requests_page2",
    "pr_reviews_page2",
    "pr_commits_page2",
    "pr_files_page2",
    "pr_missing_author_login",
]


@pytest.mark.parametrize("name", GRAPHQL_ENVELOPE_FIXTURES)
def test_graphql_fixture_matches_envelope_shape(github_fixture, name):
    body = github_fixture(name)
    assert "data" in body
    rate_limit = body["data"].get("rateLimit")
    assert rate_limit is not None, f"{name} is missing the rateLimit block every GraphQL document requests"
    assert {"remaining", "resetAt", "cost"} <= rate_limit.keys()


def test_graphql_error_fixture_has_no_data_and_names_a_type(github_fixture):
    body = github_fixture("graphql_errors_unauthorized")
    assert body["data"] is None
    assert body["errors"][0]["type"] == "UNAUTHORIZED"


def test_rest_user_fixture_has_a_login(github_fixture):
    body = github_fixture("rest_user")
    assert "login" in body


def test_error_body_fixtures_carry_a_message(github_fixture):
    for name in ("secondary_rate_limit", "server_error_502"):
        body = github_fixture(name)
        assert "message" in body
