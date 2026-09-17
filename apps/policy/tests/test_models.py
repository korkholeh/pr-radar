import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.activity.models import PullRequest
from apps.catalog.models import Organization, Repository
from apps.connections.models import GitHubConnection
from apps.policy.models import PolicyViolation, SensitivePathRule


@pytest.mark.django_db
def test_global_sensitive_path_rule_has_no_project():
    rule = SensitivePathRule.objects.create(glob="**/secrets/**", ai_mode=SensitivePathRule.AiMode.FORBIDDEN)
    assert rule.project is None


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


@pytest.mark.django_db
def test_duplicate_violation_rejected(pull_request):
    PolicyViolation.objects.create(
        pull_request=pull_request,
        rule_code=PolicyViolation.RuleCode.NO_TESTS,
        severity=PolicyViolation.Severity.LOW,
        details_hash="abc",
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        PolicyViolation.objects.create(
            pull_request=pull_request,
            rule_code=PolicyViolation.RuleCode.NO_TESTS,
            severity=PolicyViolation.Severity.LOW,
            details_hash="abc",
        )


@pytest.mark.django_db
def test_two_different_hashes_for_one_pr_rule_coexist(pull_request):
    PolicyViolation.objects.create(
        pull_request=pull_request,
        rule_code=PolicyViolation.RuleCode.NO_TESTS,
        severity=PolicyViolation.Severity.LOW,
        details_hash="abc",
    )
    PolicyViolation.objects.create(
        pull_request=pull_request,
        rule_code=PolicyViolation.RuleCode.NO_TESTS,
        severity=PolicyViolation.Severity.LOW,
        details_hash="def",
    )
    assert PolicyViolation.objects.filter(pull_request=pull_request).count() == 2


@pytest.mark.django_db
def test_status_defaults_to_open(pull_request):
    violation = PolicyViolation.objects.create(
        pull_request=pull_request,
        rule_code=PolicyViolation.RuleCode.NO_TESTS,
        severity=PolicyViolation.Severity.LOW,
        details_hash="abc",
    )
    assert violation.status == PolicyViolation.Status.OPEN


def test_no_field_stores_a_rendered_message():
    allowed_fields = {
        "id",
        "pull_request",
        "rule_code",
        "severity",
        "details_params",
        "details_hash",
        "status",
        "resolved_by",
        "resolution_comment",
        "resolved_automatically",
        "created_at",
        "updated_at",
    }
    field_names = {f.name for f in PolicyViolation._meta.fields}
    assert field_names <= allowed_fields
