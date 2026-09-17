import re

import pytest
from django.utils import timezone

from apps.activity.models import Commit, PullRequest, PullRequestCommit
from apps.ai_detection.detectors import EVIDENCE_MAX_LENGTH, evidence_fragment, load_context
from apps.catalog.models import Organization, Repository
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
