import pytest
from django.core.exceptions import ValidationError
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
    )
    AISignal.objects.create(
        pull_request=pull_request,
        commit=commit_b,
        rule=rule,
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
        evidence="evidence b",
    )
    assert AISignal.objects.filter(pull_request=pull_request, rule=rule).count() == 2
