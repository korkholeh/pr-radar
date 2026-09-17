import re

import pytest

from apps.activity.factories import CommitFactory
from apps.ai_detection.detectors import DETECTORS, DetectionContext
from apps.ai_detection.models import Detector
from apps.catalog.factories import IdentityFactory
from apps.catalog.models import Identity


def _pattern(text: str) -> re.Pattern[str]:
    return re.compile(text, re.IGNORECASE | re.MULTILINE)


def _ctx(**overrides) -> DetectionContext:
    defaults = dict(
        pull_request=None,
        commits=(),
        author_values=(),
        labels=(),
        head_ref="",
        body="",
    )
    defaults.update(overrides)
    return DetectionContext(**defaults)


def test_registry_covers_every_detector_choice():
    assert set(DETECTORS.keys()) == set(Detector.values)


# -- commit_trailer -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_commit_trailer_matches():
    commit = CommitFactory(trailers={"Co-Authored-By": ["Claude <noreply@anthropic.com>"]})
    ctx = _ctx(commits=(commit,))
    matches = list(DETECTORS[Detector.COMMIT_TRAILER](_pattern("Co-Authored-By: Claude"), ctx))
    assert len(matches) == 1
    assert matches[0].commit_id == commit.pk
    assert "Claude" in matches[0].evidence


@pytest.mark.django_db
def test_commit_trailer_does_not_match():
    commit = CommitFactory(trailers={"Signed-off-by": ["Someone <someone@example.com>"]})
    ctx = _ctx(commits=(commit,))
    matches = list(DETECTORS[Detector.COMMIT_TRAILER](_pattern("Co-Authored-By: Claude"), ctx))
    assert matches == []


# -- commit_author --------------------------------------------------------------------------------


@pytest.mark.django_db
def test_commit_author_matches():
    identity = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="claude[bot]")
    commit = CommitFactory(author_identity=identity)
    ctx = _ctx(commits=(commit,))
    matches = list(DETECTORS[Detector.COMMIT_AUTHOR](_pattern(r"claude\[bot\]"), ctx))
    assert len(matches) == 1
    assert matches[0].commit_id == commit.pk


@pytest.mark.django_db
def test_commit_author_does_not_match():
    identity = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="octocat")
    commit = CommitFactory(author_identity=identity)
    ctx = _ctx(commits=(commit,))
    matches = list(DETECTORS[Detector.COMMIT_AUTHOR](_pattern(r"claude\[bot\]"), ctx))
    assert matches == []


@pytest.mark.django_db
def test_commit_author_matches_a_co_author_email():
    commit = CommitFactory(co_authors=[{"name": "Claude", "email": "noreply@anthropic.com"}])
    ctx = _ctx(commits=(commit,))
    matches = list(DETECTORS[Detector.COMMIT_AUTHOR](_pattern("noreply@anthropic.com"), ctx))
    assert len(matches) == 1
    assert matches[0].commit_id == commit.pk


# -- pr_author --------------------------------------------------------------------------------


def test_pr_author_matches():
    ctx = _ctx(author_values=("claude[bot]",))
    matches = list(DETECTORS[Detector.PR_AUTHOR](_pattern(r"claude\[bot\]"), ctx))
    assert len(matches) == 1
    assert matches[0].commit_id is None


def test_pr_author_does_not_match():
    ctx = _ctx(author_values=("octocat",))
    matches = list(DETECTORS[Detector.PR_AUTHOR](_pattern(r"claude\[bot\]"), ctx))
    assert matches == []


# -- pr_body_footer --------------------------------------------------------------------------------


def test_pr_body_footer_matches():
    ctx = _ctx(body="Fixes #12\n\n🤖 Generated with Claude Code")
    matches = list(DETECTORS[Detector.PR_BODY_FOOTER](_pattern("Generated with Claude Code"), ctx))
    assert len(matches) == 1


def test_pr_body_footer_does_not_match():
    ctx = _ctx(body="Fixes #12, no AI mentioned here")
    matches = list(DETECTORS[Detector.PR_BODY_FOOTER](_pattern("Generated with Claude Code"), ctx))
    assert matches == []


def test_pr_body_footer_does_not_match_inside_an_html_comment():
    ctx = _ctx(body="Fixes #12\n<!-- Generated with Claude Code -->")
    matches = list(DETECTORS[Detector.PR_BODY_FOOTER](_pattern("Generated with Claude Code"), ctx))
    assert matches == []


# -- html_comment --------------------------------------------------------------------------------


def test_html_comment_matches():
    ctx = _ctx(body="Fixes #12\n<!-- ai-tool: claude-code -->")
    matches = list(DETECTORS[Detector.HTML_COMMENT](_pattern("ai-tool: claude-code"), ctx))
    assert len(matches) == 1


def test_html_comment_does_not_match():
    ctx = _ctx(body="Fixes #12\n<!-- nothing to see here -->")
    matches = list(DETECTORS[Detector.HTML_COMMENT](_pattern("ai-tool: claude-code"), ctx))
    assert matches == []


def test_html_comment_does_not_match_the_same_marker_in_visible_body_text():
    ctx = _ctx(body="ai-tool: claude-code is mentioned right here in the visible text")
    matches = list(DETECTORS[Detector.HTML_COMMENT](_pattern("ai-tool: claude-code"), ctx))
    assert matches == []


# -- label --------------------------------------------------------------------------------


def test_label_matches():
    ctx = _ctx(labels=("ai-generated", "bug"))
    matches = list(DETECTORS[Detector.LABEL](_pattern("ai-generated"), ctx))
    assert len(matches) == 1


def test_label_does_not_match():
    ctx = _ctx(labels=("bug", "enhancement"))
    matches = list(DETECTORS[Detector.LABEL](_pattern("ai-generated"), ctx))
    assert matches == []


# -- branch_pattern --------------------------------------------------------------------------------


def test_branch_pattern_matches():
    ctx = _ctx(head_ref="codex/fix-flaky-test")
    matches = list(DETECTORS[Detector.BRANCH_PATTERN](_pattern(r"^(codex|cursor|claude|copilot)/"), ctx))
    assert len(matches) == 1


def test_branch_pattern_does_not_match():
    ctx = _ctx(head_ref="feature/fix-flaky-test")
    matches = list(DETECTORS[Detector.BRANCH_PATTERN](_pattern(r"^(codex|cursor|claude|copilot)/"), ctx))
    assert matches == []


# -- commit_message --------------------------------------------------------------------------------


@pytest.mark.django_db
def test_commit_message_matches():
    commit = CommitFactory(message="Generated with Claude Code")
    ctx = _ctx(commits=(commit,))
    matches = list(DETECTORS[Detector.COMMIT_MESSAGE](_pattern("Generated with Claude Code"), ctx))
    assert len(matches) == 1
    assert matches[0].commit_id == commit.pk


@pytest.mark.django_db
def test_commit_message_does_not_match():
    commit = CommitFactory(message="Fix flaky test")
    ctx = _ctx(commits=(commit,))
    matches = list(DETECTORS[Detector.COMMIT_MESSAGE](_pattern("Generated with Claude Code"), ctx))
    assert matches == []


@pytest.mark.django_db
def test_match_on_the_second_of_three_commits_records_that_commit():
    commit_1 = CommitFactory(message="Add feature")
    commit_2 = CommitFactory(message="Generated with Claude Code")
    commit_3 = CommitFactory(message="Fix typo")
    ctx = _ctx(commits=(commit_1, commit_2, commit_3))
    matches = list(DETECTORS[Detector.COMMIT_MESSAGE](_pattern("Generated with Claude Code"), ctx))
    assert len(matches) == 1
    assert matches[0].commit_id == commit_2.pk
