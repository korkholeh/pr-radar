import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.activity.factories import PRFileFactory, PullRequestFactory, ReviewFactory
from apps.activity.models import Commit, PullRequest, PullRequestCommit
from apps.ai_detection.detectors import EVIDENCE_MAX_LENGTH, evidence_fragment, load_context
from apps.catalog.factories import IdentityFactory
from apps.catalog.models import Identity, Organization, Repository
from apps.connections.models import GitHubConnection


def test_short_match_is_returned_untruncated():
    haystack = "Co-Authored-By: Claude <noreply@anthropic.com>"
    match = re.search("Claude", haystack)
    assert evidence_fragment(haystack, match) == haystack


def test_long_match_is_truncated_to_200_chars():
    haystack = ("x" * 3000) + "Co-Authored-By: Claude" + ("y" * 2000)
    match = re.search("Co-Authored-By: Claude", haystack)
    fragment = evidence_fragment(haystack, match)
    assert len(fragment) <= EVIDENCE_MAX_LENGTH
    assert "Co-Authored-By: Claude" in fragment


def test_truncation_is_centred_and_ellipsised():
    haystack = ("a" * 3000) + "MARKER" + ("b" * 3000)
    match = re.search("MARKER", haystack)
    fragment = evidence_fragment(haystack, match)
    assert fragment.startswith("…")
    assert fragment.endswith("…")
    assert "MARKER" in fragment


@pytest.fixture
def repository(db):
    org = Organization.objects.create(login="acme", type=Organization.Type.ORG, github_id="O_1")
    connection = GitHubConnection.objects.create(name="conn", kind=GitHubConnection.Kind.FINE_GRAINED_PAT)
    return Repository.objects.create(
        organization=org, connection=connection, name="repo", full_name="acme/repo", github_id="R_1"
    )


@pytest.mark.django_db
def test_load_context_orders_commits_by_committed_at_then_sha(repository):
    pr = PullRequest.objects.create(
        repository=repository,
        number=1,
        github_id="PR_1",
        state=PullRequest.State.OPEN,
        created_at=timezone.now(),
    )
    now = timezone.now()
    commit_late = Commit.objects.create(repository=repository, sha="c" * 40, committed_at=now)
    commit_early_b = Commit.objects.create(
        repository=repository, sha="bbbb" + "0" * 36, committed_at=now - timezone.timedelta(hours=1)
    )
    commit_early_a = Commit.objects.create(
        repository=repository, sha="aaaa" + "0" * 36, committed_at=now - timezone.timedelta(hours=1)
    )
    commit_none = Commit.objects.create(repository=repository, sha="d" * 40, committed_at=None)
    for position, commit in enumerate([commit_late, commit_none, commit_early_b, commit_early_a]):
        PullRequestCommit.objects.create(pull_request=pr, commit=commit, position=position)

    ctx = load_context(pr.pk)

    assert [c.pk for c in ctx.commits] == [
        commit_early_a.pk,
        commit_early_b.pk,
        commit_late.pk,
        commit_none.pk,
    ]


# --- the fields phase 12 added to the context ---------------------------------------------------


@pytest.mark.django_db
def test_load_context_reads_paths_title_reviewers_and_merger():
    pr = PullRequestFactory(title="Codex: fix the flaky sync test")
    PRFileFactory(pull_request=pr, path="b.py")
    PRFileFactory(pull_request=pr, path="a.py")
    reviewer = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="coderabbitai[bot]")
    merger = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="octocat")
    ReviewFactory(pull_request=pr, reviewer=reviewer, submitted_at=timezone.now())
    pr.merged_by = merger
    pr.save(update_fields=["merged_by"])

    ctx = load_context(pr.pk)

    assert ctx.title == "Codex: fix the flaky sync test"
    assert ctx.file_paths == ("a.py", "b.py")  # ascending, so a rule always reports the same path
    assert ctx.reviewer_values == ("coderabbitai[bot]",)
    assert ctx.merged_by_values == ("octocat",)


@pytest.mark.django_db
def test_load_context_leaves_excluded_files_out():
    """`is_excluded` files are lockfiles, vendored trees and generated output. A rule that fires
    on one of those is describing the repository's tooling, not this pull request."""
    pr = PullRequestFactory()
    PRFileFactory(pull_request=pr, path="apps/sync/client.py")
    PRFileFactory(pull_request=pr, path="vendor/lib.js", is_excluded=True)

    assert load_context(pr.pk).file_paths == ("apps/sync/client.py",)


@pytest.mark.django_db
def test_load_context_orders_reviewers_by_submission_and_skips_reviews_without_one():
    pr = PullRequestFactory()
    now = timezone.now()
    first = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="first-reviewer")
    second = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="second-reviewer")
    ReviewFactory(pull_request=pr, reviewer=second, submitted_at=now)
    ReviewFactory(pull_request=pr, reviewer=first, submitted_at=now - timezone.timedelta(hours=1))
    ReviewFactory(pull_request=pr, reviewer=None, submitted_at=now)

    assert load_context(pr.pk).reviewer_values == ("first-reviewer", "second-reviewer")


@pytest.mark.django_db
def test_load_context_query_budget_does_not_grow_with_files_or_reviews():
    """The `file_path` and `reviewer_identity` detectors read whole collections, so their cost
    must stay in the prefetch, not in a query per row. Asserted as a comparison rather than a
    pinned number: what matters is that forty files cost exactly what two do."""
    small = PullRequestFactory()
    for index in range(2):
        PRFileFactory(pull_request=small, path=f"small-{index}.py")
        ReviewFactory(pull_request=small)

    load_context(small.pk)  # warm anything cached process-wide before counting
    with CaptureQueriesContext(connection) as small_queries:
        small_ctx = load_context(small.pk)

    big = PullRequestFactory()
    for index in range(40):
        PRFileFactory(pull_request=big, path=f"big-{index}.py")
        ReviewFactory(pull_request=big)

    with CaptureQueriesContext(connection) as big_queries:
        big_ctx = load_context(big.pk)

    assert len(small_ctx.file_paths) == 2
    assert len(big_ctx.file_paths) == 40
    assert len(big_queries) == len(small_queries)
    assert len(small_queries) <= 10, [q["sql"] for q in small_queries]
