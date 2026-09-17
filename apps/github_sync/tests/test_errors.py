import pytest

from apps.github_sync.errors import GitHubSchemaError, optional, require

PAYLOAD = {
    "repository": {
        "pullRequests": {
            "nodes": [
                {"author": {"login": "octocat"}},
            ]
        }
    },
    "secretLooking": "ghp_abcdef0123456789",
}


def test_require_returns_value_when_present():
    assert require(PAYLOAD, "repository.pullRequests.nodes[0].author.login") == "octocat"


def test_missing_leaf_names_the_full_path():
    with pytest.raises(GitHubSchemaError) as exc_info:
        require(PAYLOAD, "repository.pullRequests.nodes[0].author.email")
    assert exc_info.value.path == "repository.pullRequests.nodes[0].author.email"
    assert "repository.pullRequests.nodes[0].author.email" in str(exc_info.value)


def test_missing_intermediate_node_names_the_full_path():
    with pytest.raises(GitHubSchemaError) as exc_info:
        require(PAYLOAD, "repository.pullRequests.nodes[0].reviews.nodes[0].login")
    assert exc_info.value.path == "repository.pullRequests.nodes[0].reviews"


def test_short_list_index_raises():
    with pytest.raises(GitHubSchemaError) as exc_info:
        require(PAYLOAD, "repository.pullRequests.nodes[5].author.login")
    assert exc_info.value.path == "repository.pullRequests.nodes[5]"


def test_optional_missing_numeric_field_returns_none_not_zero():
    assert optional(PAYLOAD, "repository.pullRequests.nodes[0].additions") is None


def test_optional_present_field_returns_value():
    assert optional(PAYLOAD, "repository.pullRequests.nodes[0].author.login") == "octocat"


def test_error_message_never_contains_the_payload():
    with pytest.raises(GitHubSchemaError) as exc_info:
        require(PAYLOAD, "repository.pullRequests.nodes[0].author.email")
    assert "ghp_abcdef0123456789" not in str(exc_info.value)
