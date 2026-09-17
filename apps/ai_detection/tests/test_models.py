import hashlib

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from apps.activity.models import Commit, PullRequest
from apps.ai_detection.models import AISignal, Confidence, DetectionRule, Detector, Tool
from apps.catalog.models import Organization, Repository
from apps.connections.models import GitHubConnection


@pytest.fixture
def pull_request(db):
    org = Organization.objects.create(login="acme", type=Organization.Type.ORG, github_id="O_1")
    connection = GitHubConnection.objects.create(name="conn", kind=GitHubConnection.Kind.FINE_GRAINED_PAT)
    repository = Repository.objects.create(
        organization=org, connection=connection, name="repo", full_name="acme/repo", github_id="R_1"
    )
    return PullRequest.objects.create(
        repository=repository,
        number=1,
        github_id="PR_1",
        state=PullRequest.State.OPEN,
        created_at=timezone.now(),
    )


@pytest.fixture
def rule(db):
    return DetectionRule.objects.create(
        name="commit trailer",
        detector=Detector.COMMIT_TRAILER,
        pattern="Co-Authored-By: Claude",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.django_db
def test_evidence_over_200_chars_fails_full_clean(pull_request, rule):
    signal = AISignal(
        pull_request=pull_request,
        rule=rule,
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
        evidence="x" * 201,
    )
    with pytest.raises(ValidationError):
        signal.full_clean()


@pytest.mark.django_db
def test_deleting_rule_with_signals_is_protected(pull_request, rule):
    AISignal.objects.create(
        pull_request=pull_request,
        rule=rule,
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
        evidence="evidence",
    )
    with pytest.raises(ProtectedError):
        rule.delete()


@pytest.mark.django_db
def test_two_signals_from_one_rule_on_two_commits_of_one_pr_are_accepted(pull_request, rule):
    org_repo = pull_request.repository
    commit_a = Commit.objects.create(repository=org_repo, sha="a" * 40)
    commit_b = Commit.objects.create(repository=org_repo, sha="b" * 40)
    AISignal.objects.create(
        pull_request=pull_request,
        commit=commit_a,
        rule=rule,
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
        evidence="evidence a",
        evidence_hash=_hash("evidence a"),
    )
    AISignal.objects.create(
        pull_request=pull_request,
        commit=commit_b,
        rule=rule,
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
        evidence="evidence b",
        evidence_hash=_hash("evidence b"),
    )
    assert AISignal.objects.filter(pull_request=pull_request, rule=rule).count() == 2


@pytest.mark.django_db
def test_duplicate_signal_violates_unique_constraint(pull_request, rule):
    AISignal.objects.create(
        pull_request=pull_request,
        rule=rule,
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
        evidence="same evidence",
        evidence_hash=_hash("same evidence"),
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            AISignal.objects.create(
                pull_request=pull_request,
                rule=rule,
                tool=Tool.CLAUDE_CODE,
                confidence=Confidence.HIGH,
                evidence="same evidence",
                evidence_hash=_hash("same evidence"),
            )


@pytest.mark.django_db
def test_same_evidence_on_a_different_pr_is_allowed(pull_request, rule):
    other_pr = PullRequest.objects.create(
        repository=pull_request.repository,
        number=2,
        github_id="PR_2",
        state=PullRequest.State.OPEN,
        created_at=timezone.now(),
    )
    AISignal.objects.create(
        pull_request=pull_request,
        rule=rule,
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
        evidence="same evidence",
        evidence_hash=_hash("same evidence"),
    )
    AISignal.objects.create(
        pull_request=other_pr,
        rule=rule,
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
        evidence="same evidence",
        evidence_hash=_hash("same evidence"),
    )
    assert AISignal.objects.filter(rule=rule).count() == 2


@pytest.mark.django_db
def test_duplicate_rule_name_rejected():
    DetectionRule.objects.create(
        name="commit trailer",
        detector=Detector.COMMIT_TRAILER,
        pattern="Co-Authored-By: Claude",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            DetectionRule.objects.create(
                name="commit trailer",
                detector=Detector.COMMIT_TRAILER,
                pattern="something else",
                tool=Tool.CLAUDE_CODE,
                confidence=Confidence.HIGH,
            )


@pytest.mark.django_db
def test_unclosed_pattern_raises_validation_error():
    invalid_rule = DetectionRule(
        name="broken",
        detector=Detector.COMMIT_TRAILER,
        pattern="[unclosed",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )
    with pytest.raises(ValidationError):
        invalid_rule.full_clean()


@pytest.mark.django_db
def test_valid_regex_pattern_passes_full_clean():
    valid_rule = DetectionRule(
        name="valid",
        detector=Detector.COMMIT_TRAILER,
        pattern=r"^(codex|cursor|claude|copilot)/",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )
    valid_rule.full_clean()


@pytest.mark.django_db
def test_nested_quantifier_pattern_raises_validation_error():
    """`(a+)+` and its siblings are the classic catastrophic-backtracking shape: rejected before
    an admin can save or dry-run it against real PR bodies."""
    catastrophic_rule = DetectionRule(
        name="catastrophic",
        detector=Detector.COMMIT_TRAILER,
        pattern=r"(a+)+$",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )
    with pytest.raises(ValidationError):
        catastrophic_rule.full_clean()
