"""The read-only guarantee, pinned. PR Radar reads GitHub and never writes to it; GraphQL carries
both over the same POST, so these tests are what keeps that promise from drifting silently."""

import inspect

import pytest
import respx

from apps.github_sync import queries
from apps.github_sync.client import GitHubClient, assert_read_only
from apps.github_sync.errors import GitHubWriteAttemptError
from apps.github_sync.rate_limit import RateBudget
from apps.github_sync.tests.test_client import FakeAuth

MUTATION_DOCUMENT = """
mutation AddComment($id: ID!, $body: String!) {
  addComment(input: {subjectId: $id, body: $body}) {
    clientMutationId
  }
}
"""


def shipped_documents():
    """Every module-level string in queries.py that holds a GraphQL operation. Reading the module
    rather than a hand-kept list means a document added later is covered without touching this
    test -- which is the point, since the guard exists for the code nobody has written yet."""
    return [
        (name, value)
        for name, value in inspect.getmembers(queries)
        if not name.startswith("_") and isinstance(value, str) and "query " in value
    ]


def test_queries_module_ships_at_least_the_known_documents():
    names = {name for name, _ in shipped_documents()}
    assert {"RATE_LIMIT_QUERY", "PULL_REQUESTS_QUERY", "VIEWER_REPOSITORIES_QUERY"} <= names


@pytest.mark.parametrize("name,document", shipped_documents(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_shipped_document_is_read_only(name, document):
    assert_read_only(document)


@pytest.mark.parametrize("keyword", ["mutation", "subscription"])
def test_assert_read_only_rejects_write_operations(keyword):
    document = MUTATION_DOCUMENT.replace("mutation", keyword, 1)
    with pytest.raises(GitHubWriteAttemptError) as exc_info:
        assert_read_only(document)
    assert exc_info.value.keyword == keyword


def test_assert_read_only_rejects_a_mutation_hidden_after_a_query():
    document = queries.RATE_LIMIT_QUERY + MUTATION_DOCUMENT
    with pytest.raises(GitHubWriteAttemptError):
        assert_read_only(document)


def test_assert_read_only_rejects_an_anonymous_shorthand_document():
    """`{ viewer { login } }` is a legal read, but no shipped document uses the shorthand and
    allowing it would mean the first token no longer identifies the operation. Fail closed."""
    with pytest.raises(GitHubWriteAttemptError):
        assert_read_only("{ viewer { login } }")


def test_assert_read_only_ignores_comments_and_blank_lines():
    assert_read_only("\n# mutation in a comment is not an operation\n\n" + queries.RATE_LIMIT_QUERY)


@pytest.mark.django_db
def test_client_refuses_a_mutation_without_sending_a_request():
    route = respx.post("https://api.github.com/graphql")
    client = GitHubClient(FakeAuth(), RateBudget(key="connection:fake"), sleep=lambda s: None)

    with pytest.raises(GitHubWriteAttemptError):
        client.graphql(MUTATION_DOCUMENT, {"id": "PR_1", "body": "hi"})

    assert not route.called


def test_rest_surface_exposes_no_write_verb():
    """`rest_get()` hardcodes GET. A `rest_post()`/`rest_delete()` added beside it would be a new
    write path that no other test in the suite would notice."""
    members = inspect.getmembers(GitHubClient, inspect.isfunction)
    public = {name for name, _ in members if not name.startswith("_")}
    assert public == {"graphql", "rest_get", "paginate"}
