import datetime

import pytest
from django.test import override_settings

from apps.github_sync.errors import GitHubSchemaError
from apps.github_sync.mappers import (
    map_check_status,
    map_commit,
    map_pr_file,
    map_pull_request,
    map_ready_for_review_at,
    map_review,
    map_review_comment,
    map_review_requested_at,
    parse_co_authors,
    parse_trailers,
)


def test_map_pull_request_from_fixture(github_fixture):
    node = github_fixture("pull_requests_page1")["data"]["repository"]["pullRequests"]["nodes"][0]
    fields = map_pull_request(node)
    assert fields["github_id"] == "PR_kwDOA1"
    assert fields["number"] == 101
    assert fields["state"] == "merged"
    assert fields["author_login"] == "octocat"
    assert fields["merged_by_login"] == "octocat"
    assert fields["merge_commit_sha"] == "abc123def456"
    assert fields["labels"] == ["enhancement"]
    assert fields["created_at"] == datetime.datetime(2026, 1, 1, 9, 0, tzinfo=datetime.UTC)
    assert fields["raw"] is None


def test_map_pull_request_open_pr_has_no_merge_fields(github_fixture):
    node = github_fixture("pull_requests_page1")["data"]["repository"]["pullRequests"]["nodes"][1]
    fields = map_pull_request(node)
    assert fields["state"] == "open"
    assert fields["merged_at"] is None
    assert fields["merge_commit_sha"] == ""
    assert fields["merged_by_login"] is None
    assert fields["author_login"] == "hubot"


def test_ghost_author_maps_to_none_not_a_failure(github_fixture):
    node = github_fixture("pr_missing_author_login")["data"]["repository"]["pullRequests"]["nodes"][0]
    fields = map_pull_request(node)
    assert fields["author_login"] is None


def test_missing_required_field_raises_with_path():
    node = {"number": 1, "state": "OPEN", "createdAt": "2026-01-01T00:00:00Z"}
    with pytest.raises(GitHubSchemaError) as exc_info:
        map_pull_request(node)
    assert exc_info.value.path == "id"


@override_settings(STORE_RAW_PAYLOADS=True)
def test_raw_populated_only_when_store_raw_payloads_is_on(github_fixture):
    node = github_fixture("pull_requests_page1")["data"]["repository"]["pullRequests"]["nodes"][0]
    fields = map_pull_request(node)
    assert fields["raw"] == node


def test_map_commit_from_fixture(github_fixture):
    node = github_fixture("pr_commits_page2")["data"]["node"]["commits"]["nodes"][0]
    fields = map_commit(node)
    assert fields["sha"] == "commit0100"
    assert fields["author_login"] == "octocat"
    assert fields["author_email"] == "octocat@example.com"
    assert fields["check_rollup_state"] == "SUCCESS"
    assert fields["trailers"] == {}
    assert fields["co_authors"] == []


def test_parse_trailers_extracts_trailing_key_value_block():
    message = "Subject line\n\nBody text.\n\nCo-authored-by: Jane Doe <jane@example.com>\nRefs: #42"
    trailers = parse_trailers(message)
    assert trailers == {
        "Co-authored-by": ["Jane Doe <jane@example.com>"],
        "Refs": ["#42"],
    }


def test_parse_trailers_ignores_body_lines_that_look_like_urls():
    message = "Subject\n\nSee https://example.com for details."
    assert parse_trailers(message) == {}


def test_parse_co_authors_from_trailers():
    trailers = {"Co-authored-by": ["Jane Doe <jane@example.com>", "not-a-trailer-value"]}
    assert parse_co_authors(trailers) == [{"name": "Jane Doe", "email": "jane@example.com"}]


def test_map_review_from_fixture(github_fixture):
    node = github_fixture("pr_reviews_page2")["data"]["node"]["reviews"]["nodes"][0]
    fields = map_review(node)
    assert fields["github_id"] == "PRR_100"
    assert fields["reviewer_login"] == "reviewer-100"
    assert fields["state"] == "APPROVED"
    assert fields["body_length"] == len("Looks good.")


def test_map_review_comment_marks_review_thread_flag():
    node = {
        "id": "RC_1",
        "author": {"login": "octocat"},
        "createdAt": "2026-01-01T00:00:00Z",
        "bodyText": "hi",
    }
    fields = map_review_comment(node, is_review_thread=True)
    assert fields["is_review_thread"] is True
    assert fields["body_length"] == 2


def test_map_pr_file_from_fixture(github_fixture):
    node = github_fixture("pr_files_page2")["data"]["node"]["files"]["nodes"][0]
    fields = map_pr_file(node)
    assert fields["path"] == "src/file_100.py"
    assert fields["status"] == "modified"


def test_map_check_status_from_commit_node(github_fixture):
    node = github_fixture("pr_commits_page2")["data"]["node"]["commits"]["nodes"][0]
    fields = map_check_status(node)
    assert fields["commit_sha"] == "commit0100"
    assert fields["rollup_state"] == "SUCCESS"


def test_map_check_status_returns_none_without_rollup():
    node = {"commit": {"oid": "abc", "committedDate": "2026-01-01T00:00:00Z"}}
    assert map_check_status(node) is None


def test_map_ready_for_review_at_picks_earliest_event():
    nodes = [
        {"__typename": "ReadyForReviewEvent", "createdAt": "2026-01-02T00:00:00Z"},
        {"__typename": "ReadyForReviewEvent", "createdAt": "2026-01-01T00:00:00Z"},
    ]
    assert map_ready_for_review_at(nodes) == datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)


def test_map_ready_for_review_at_none_without_events():
    assert map_ready_for_review_at([{"__typename": "SomethingElse"}]) is None


# -- phase 12, stage 7: thread resolution and the review-requested event --------------------------


def test_map_review_comment_carries_the_threads_resolution(github_fixture):
    threads = github_fixture("pr_review_threads_resolution")["data"]["node"]["reviewThreads"]["nodes"]
    resolved, unresolved, unknown = (thread["comments"]["nodes"][0] for thread in threads)

    assert (
        map_review_comment(resolved, is_review_thread=True, is_resolved=threads[0].get("isResolved"))[
            "is_resolved"
        ]
        is True
    )
    assert (
        map_review_comment(unresolved, is_review_thread=True, is_resolved=threads[1].get("isResolved"))[
            "is_resolved"
        ]
        is False
    )
    # A server that does not return the field leaves it unknown. `None`, never `False`: "we could
    # not tell" must not be stored as "the author ignored the reviewer".
    assert (
        map_review_comment(unknown, is_review_thread=True, is_resolved=threads[2].get("isResolved"))[
            "is_resolved"
        ]
        is None
    )


def test_map_review_comment_keeps_private_bookkeeping_out_of_raw(settings):
    settings.STORE_RAW_PAYLOADS = True
    node = {
        "id": "RC_1",
        "author": {"login": "octocat"},
        "createdAt": "2026-01-01T00:00:00Z",
        "bodyText": "hi",
        "_thread_is_resolved": False,
    }

    fields = map_review_comment(node, is_review_thread=True, is_resolved=False)

    assert "_thread_is_resolved" not in fields["raw"]
    assert fields["raw"]["id"] == "RC_1"


def test_map_review_requested_at_picks_the_earliest_request(github_fixture):
    """The earliest, not the latest: a second reviewer added two days later does not change when
    the pull request started waiting."""
    nodes = github_fixture("pr_timeline_review_requested")["data"]["node"]["timelineItems"]["nodes"]

    requested_at = map_review_requested_at(nodes)

    assert requested_at == datetime.datetime(2026, 3, 1, 8, 30, tzinfo=datetime.UTC)
    # And the two event types do not bleed into each other.
    assert map_ready_for_review_at(nodes) == datetime.datetime(2026, 3, 1, 8, 0, tzinfo=datetime.UTC)


def test_map_review_requested_at_is_none_when_nobody_was_asked():
    nodes = [{"__typename": "ReadyForReviewEvent", "createdAt": "2026-01-01T00:00:00Z"}]
    assert map_review_requested_at(nodes) is None
