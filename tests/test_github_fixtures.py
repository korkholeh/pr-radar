"""Fixture corpus sanity check: a malformed fixture must fail loudly here, not mid-client."""

import pytest

GRAPHQL_ENVELOPE_FIXTURES = [
    "rate_limit",
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


@pytest.mark.parametrize(
    "name,path",
    [
        ("graphql_errors_repository_forbidden", ["repository"]),
        (
            "graphql_errors_field_forbidden",
            ["node", "commits", "nodes", 0, "commit", "statusCheckRollup"],
        ),
    ],
)
def test_graphql_denial_fixtures_carry_data_and_the_path_that_was_refused(github_fixture, name, path):
    """Both denials answer 200 with a resolved `data` block — that is what tells a root-field
    refusal apart from a single field the token may not read, so a fixture that dropped `data`
    or `path` would stop exercising the distinction."""
    body = github_fixture(name)
    assert body["data"]["rateLimit"]["remaining"] > 0
    assert body["errors"][0]["type"] == "FORBIDDEN"
    assert body["errors"][0]["path"] == path


def test_rest_user_fixture_has_a_login(github_fixture):
    body = github_fixture("rest_user")
    assert "login" in body


REST_REPOSITORY_LIST_FIXTURES = [
    "rest_user_repos_page1",
    "rest_user_repos_page2",
    "rest_org_repos",
]


@pytest.mark.parametrize("name", REST_REPOSITORY_LIST_FIXTURES)
def test_rest_repository_list_fixture_carries_what_discovery_reads(github_fixture, name):
    """Discovery reshapes these into GraphQL-style nodes, so a fixture missing node_id or owner
    would fail deep inside repository_node() rather than here."""
    body = github_fixture(name)
    assert isinstance(body, list) and body
    for repository in body:
        assert {"node_id", "name", "full_name", "private", "owner"} <= repository.keys()
        assert {"node_id", "login", "type"} <= repository["owner"].keys()


def test_rest_user_orgs_fixture_carries_a_login(github_fixture):
    body = github_fixture("rest_user_orgs")
    assert isinstance(body, list) and body
    assert "login" in body[0]


def test_error_body_fixtures_carry_a_message(github_fixture):
    for name in ("secondary_rate_limit", "server_error_502"):
        body = github_fixture(name)
        assert "message" in body
