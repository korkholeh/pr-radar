"""`evaluate_pull_request`/`evaluate_pull_requests`: the wanted-vs-existing diff that may only
create an open violation or auto-resolve one, parametrized over every rule code per
RISKS row 6 and acceptance criteria 1-3."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.factories import UserFactory
from apps.accounts.models import AuditEntry
from apps.activity.factories import (
    CommitFactory,
    PRFileFactory,
    PullRequestCommitFactory,
    PullRequestFactory,
    ReviewCommentFactory,
    ReviewFactory,
)
from apps.activity.models import AIDisclosure, AIStatus, PullRequest, Review
from apps.ai_detection.factories import AISignalFactory
from apps.ai_detection.models import Confidence
from apps.catalog.factories import IdentityFactory, PersonFactory
from apps.catalog.services import set_setting
from apps.policy.factories import AIPolicyFactory, PolicyViolationFactory, SensitivePathRuleFactory
from apps.policy.models import PolicyViolation, SensitivePathRule
from apps.policy.services import evaluate_pull_request, evaluate_pull_requests

pytestmark = pytest.mark.django_db

RuleCode = PolicyViolation.RuleCode


def _past():
    return timezone.now() - timezone.timedelta(days=1)


def _human_reviewer(pr):
    identity = IdentityFactory(person=PersonFactory())
    ReviewFactory(pull_request=pr, reviewer=identity, state=Review.State.APPROVED)


def _setup_disclosure_missing():
    AIPolicyFactory(require_disclosure=True, effective_from=_past())
    pr = PullRequestFactory(ai_disclosure=AIDisclosure.MISSING)

    def fix():
        pr.ai_disclosure = AIDisclosure.SUBSTANTIAL
        pr.save(update_fields=["ai_disclosure"])

    return pr, fix


def _setup_disclosure_mismatch():
    AIPolicyFactory(effective_from=_past())
    pr = PullRequestFactory(ai_disclosure=AIDisclosure.NONE)
    AISignalFactory(pull_request=pr, confidence=Confidence.HIGH)

    def fix():
        pr.ai_disclosure = AIDisclosure.PARTIAL
        pr.save(update_fields=["ai_disclosure"])

    return pr, fix


def _setup_tool_not_allowed():
    AIPolicyFactory(allowed_tools=["copilot"], effective_from=_past())
    pr = PullRequestFactory(ai_tools=["cursor"])

    def fix():
        pr.ai_tools = ["copilot"]
        pr.save(update_fields=["ai_tools"])

    return pr, fix


def _setup_sensitive_path_forbidden():
    AIPolicyFactory(effective_from=_past())
    rule = SensitivePathRuleFactory(glob="secrets/**", ai_mode=SensitivePathRule.AiMode.FORBIDDEN)
    pr = PullRequestFactory(ai_status=AIStatus.AI_EXPLICIT)
    PRFileFactory(pull_request=pr, path="secrets/key.pem")

    def fix():
        rule.is_active = False
        rule.save(update_fields=["is_active"])

    return pr, fix


def _setup_sensitive_path_review():
    AIPolicyFactory(min_human_approvals=1, effective_from=_past())
    SensitivePathRuleFactory(glob="infra/**", ai_mode=SensitivePathRule.AiMode.NEEDS_EXTRA_REVIEW)
    pr = PullRequestFactory(ai_status=AIStatus.AI_EXPLICIT)
    PRFileFactory(pull_request=pr, path="infra/main.tf")

    def fix():
        _human_reviewer(pr)
        _human_reviewer(pr)

    return pr, fix


def _setup_no_human_approval():
    AIPolicyFactory(require_human_approval=True, min_human_approvals=1, effective_from=_past())
    pr = PullRequestFactory(
        ai_status=AIStatus.AI_EXPLICIT, state=PullRequest.State.MERGED, merged_at=timezone.now()
    )

    def fix():
        _human_reviewer(pr)

    return pr, fix


def _setup_self_merge():
    AIPolicyFactory(effective_from=_past())
    pr = PullRequestFactory(
        ai_status=AIStatus.AI_EXPLICIT,
        state=PullRequest.State.MERGED,
        merged_at=timezone.now(),
        is_self_merged=True,
    )

    def fix():
        _human_reviewer(pr)

    return pr, fix


def _setup_no_tests():
    AIPolicyFactory(require_tests_for_ai_prs=True, effective_from=_past())
    pr = PullRequestFactory(ai_status=AIStatus.AI_EXPLICIT, has_test_changes=False)
    PRFileFactory(pull_request=pr, additions=50, deletions=0)

    def fix():
        pr.has_test_changes = True
        pr.save(update_fields=["has_test_changes"])

    return pr, fix


def _setup_ai_pr_too_large():
    AIPolicyFactory(ai_pr_max_effective_lines=100, effective_from=_past())
    pr = PullRequestFactory(ai_status=AIStatus.AI_EXPLICIT, effective_additions=80, effective_deletions=50)

    def fix():
        pr.effective_additions = 10
        pr.effective_deletions = 10
        pr.save(update_fields=["effective_additions", "effective_deletions"])

    return pr, fix


def _merged_pr(**kwargs):
    kwargs.setdefault("state", PullRequest.State.MERGED)
    kwargs.setdefault("merged_at", timezone.now())
    return PullRequestFactory(**kwargs)


def _bot_approver(pr):
    person = PersonFactory(is_bot=True)
    ReviewFactory(pull_request=pr, reviewer=IdentityFactory(person=person), state=Review.State.APPROVED)


# --- the fifteen PLANEKS standards (phase 12, stage 7) --------------------------------------
#
# Each one sets the switch its evaluator reads, because every flag on AIPolicy defaults to False:
# a setup that forgot the switch would create no violation and fail as "no row" rather than as
# "the rule is wrong", which is the harder failure to read.


def _setup_quality_gate_bypassed():
    AIPolicyFactory(forbid_ci_bypass=True, effective_from=_past())
    pr = _merged_pr()
    commit = CommitFactory(repository=pr.repository, message="feat: ship it [skip ci]")
    PullRequestCommitFactory(pull_request=pr, commit=commit)

    def fix():
        commit.message = "feat: ship it"
        commit.save(update_fields=["message"])

    return pr, fix


def _setup_test_weakened():
    AIPolicyFactory(forbid_test_weakening=True, effective_from=_past())
    pr = PullRequestFactory()
    PRFileFactory(pull_request=pr, path="apps/billing/views.py", status="modified")
    removed = PRFileFactory(
        pull_request=pr, path="apps/billing/tests/test_views.py", is_test=True, status="removed"
    )

    def fix():
        removed.status = "modified"
        removed.save(update_fields=["status"])

    return pr, fix


def _setup_ai_only_approval():
    AIPolicyFactory(forbid_ai_only_approval=True, effective_from=_past())
    pr = _merged_pr()
    _bot_approver(pr)

    def fix():
        _human_reviewer(pr)

    return pr, fix


def _setup_ai_review_missing():
    AIPolicyFactory(require_ai_review_first=True, ai_reviewer_identities=["copilot"], effective_from=_past())
    pr = _merged_pr()

    def fix():
        ReviewFactory(pull_request=pr, reviewer=IdentityFactory(value="copilot", person=PersonFactory()))

    return pr, fix


def _setup_ai_review_unresolved():
    AIPolicyFactory(
        require_ai_comments_resolved=True, ai_reviewer_identities=["copilot"], effective_from=_past()
    )
    pr = _merged_pr()
    comment = ReviewCommentFactory(
        pull_request=pr, author=IdentityFactory(value="copilot", person=PersonFactory()), is_resolved=False
    )

    def fix():
        comment.is_resolved = True
        comment.save(update_fields=["is_resolved"])

    return pr, fix


def _setup_high_risk_no_plan():
    AIPolicyFactory(require_high_risk_plan=True, effective_from=_past())
    # A glob of its own: `infra/**` belongs to _setup_sensitive_path_review, and the column is
    # unique, so sharing it fails the moment a test runs every setup in one database.
    SensitivePathRuleFactory(
        glob="terraform/**",
        ai_mode=SensitivePathRule.AiMode.NEEDS_EXTRA_REVIEW,
        risk_level=SensitivePathRule.RiskLevel.HIGH,
    )
    pr = PullRequestFactory(body="Bumps the cluster size.")
    PRFileFactory(pull_request=pr, path="terraform/main.tf")

    def fix():
        pr.body = (
            "## Plan\n\nApply in the staging workspace first; roll back with "
            "`terraform apply` on the previous revision."
        )
        pr.save(update_fields=["body"])

    return pr, fix


def _setup_risk_level_missing():
    AIPolicyFactory(require_risk_level=True, effective_from=_past())
    pr = PullRequestFactory(body="Renames a column.")

    def fix():
        pr.body = "## Risk level\n\nLow — one column, no data migration."
        pr.save(update_fields=["body"])

    return pr, fix


def _setup_verification_missing():
    AIPolicyFactory(require_verification_note=True, effective_from=_past())
    pr = PullRequestFactory(body="Renames a column.")

    def fix():
        pr.body = "## Verification\n\nRan the suite and opened the page by hand."
        pr.save(update_fields=["body"])

    return pr, fix


def _setup_task_link_missing():
    AIPolicyFactory(require_task_link=True, effective_from=_past())
    pr = PullRequestFactory(body="Renames a column.")

    def fix():
        pr.body = "Renames a column. Closes #431."
        pr.save(update_fields=["body"])

    return pr, fix


def _setup_new_dependency_ai():
    AIPolicyFactory(flag_new_dependencies_in_ai_prs=True, effective_from=_past())
    pr = PullRequestFactory(ai_status=AIStatus.AI_EXPLICIT)
    manifest = PRFileFactory(pull_request=pr, path="pyproject.toml")

    def fix():
        manifest.path = "docs/dependencies.md"
        manifest.save(update_fields=["path"])

    return pr, fix


def _setup_migration_ai_insufficient_review():
    policy = AIPolicyFactory(effective_from=_past())
    reviewer = PersonFactory()
    policy.designated_reviewers.add(reviewer)
    pr = _merged_pr(ai_status=AIStatus.AI_EXPLICIT)
    PRFileFactory(pull_request=pr, path="apps/billing/migrations/0002_add_column.py")

    def fix():
        ReviewFactory(
            pull_request=pr,
            reviewer=IdentityFactory(person=reviewer),
            state=Review.State.APPROVED,
        )

    return pr, fix


def _setup_secret_artifact_committed():
    AIPolicyFactory(forbid_secret_artifacts=True, effective_from=_past())
    pr = PullRequestFactory()
    secret = PRFileFactory(pull_request=pr, path=".env")

    def fix():
        secret.path = ".env.example"
        secret.save(update_fields=["path"])

    return pr, fix


def _setup_agent_config_changed():
    AIPolicyFactory(flag_agent_config_changes=True, effective_from=_past())
    pr = PullRequestFactory()
    config_file = PRFileFactory(pull_request=pr, path="CLAUDE.md")

    def fix():
        config_file.path = "docs/conventions.md"
        config_file.save(update_fields=["path"])

    return pr, fix


def _setup_scope_creep():
    AIPolicyFactory(forbid_scope_creep=True, effective_from=_past())
    pr = PullRequestFactory(ai_status=AIStatus.AI_EXPLICIT, body="## Scope\n\nThe policy app only.")
    PRFileFactory(pull_request=pr, path="billing/views.py")

    def fix():
        pr.body = "## Scope\n\nThe policy app and billing."
        pr.save(update_fields=["body"])

    return pr, fix


def _setup_rubber_stamp_on_ai_pr():
    AIPolicyFactory(forbid_rubber_stamp_approval=True, effective_from=_past())
    pr = _merged_pr(ai_status=AIStatus.AI_EXPLICIT, is_rubber_stamp=True)

    def fix():
        pr.is_rubber_stamp = False
        pr.save(update_fields=["is_rubber_stamp"])

    return pr, fix


RULE_SETUPS = {
    RuleCode.DISCLOSURE_MISSING: _setup_disclosure_missing,
    RuleCode.DISCLOSURE_MISMATCH: _setup_disclosure_mismatch,
    RuleCode.TOOL_NOT_ALLOWED: _setup_tool_not_allowed,
    RuleCode.SENSITIVE_PATH_FORBIDDEN: _setup_sensitive_path_forbidden,
    RuleCode.SENSITIVE_PATH_REVIEW: _setup_sensitive_path_review,
    RuleCode.NO_HUMAN_APPROVAL: _setup_no_human_approval,
    RuleCode.SELF_MERGE: _setup_self_merge,
    RuleCode.NO_TESTS: _setup_no_tests,
    RuleCode.AI_PR_TOO_LARGE: _setup_ai_pr_too_large,
    RuleCode.QUALITY_GATE_BYPASSED: _setup_quality_gate_bypassed,
    RuleCode.TEST_WEAKENED: _setup_test_weakened,
    RuleCode.AI_ONLY_APPROVAL: _setup_ai_only_approval,
    RuleCode.AI_REVIEW_MISSING: _setup_ai_review_missing,
    RuleCode.AI_REVIEW_UNRESOLVED: _setup_ai_review_unresolved,
    RuleCode.HIGH_RISK_NO_PLAN: _setup_high_risk_no_plan,
    RuleCode.RISK_LEVEL_MISSING: _setup_risk_level_missing,
    RuleCode.VERIFICATION_MISSING: _setup_verification_missing,
    RuleCode.TASK_LINK_MISSING: _setup_task_link_missing,
    RuleCode.NEW_DEPENDENCY_AI: _setup_new_dependency_ai,
    RuleCode.MIGRATION_AI_INSUFFICIENT_REVIEW: _setup_migration_ai_insufficient_review,
    RuleCode.SECRET_ARTIFACT_COMMITTED: _setup_secret_artifact_committed,
    RuleCode.AGENT_CONFIG_CHANGED: _setup_agent_config_changed,
    RuleCode.SCOPE_CREEP: _setup_scope_creep,
    RuleCode.RUBBER_STAMP_ON_AI_PR: _setup_rubber_stamp_on_ai_pr,
}


def test_rule_setups_cover_every_code():
    """Deny-by-default: a rule code added without a setup here fails this test rather than
    silently dropping out of the parametrized pair below."""
    assert set(RULE_SETUPS.keys()) == set(RuleCode.values)


@pytest.mark.parametrize("rule_code", list(RuleCode.values))
def test_violating_pr_gets_one_row_and_second_run_creates_no_second_row(rule_code):
    pr, _fix = RULE_SETUPS[rule_code]()

    evaluate_pull_request(pr.pk)
    violations = list(PolicyViolation.objects.filter(pull_request=pr, rule_code=rule_code))
    assert len(violations) == 1
    assert violations[0].status == PolicyViolation.Status.OPEN
    assert violations[0].severity

    evaluate_pull_request(pr.pk)
    violations_again = list(PolicyViolation.objects.filter(pull_request=pr, rule_code=rule_code))
    assert len(violations_again) == 1
    assert violations_again[0].pk == violations[0].pk
    assert violations_again[0].status == PolicyViolation.Status.OPEN


@pytest.mark.parametrize("rule_code", list(RuleCode.values))
def test_condition_gone_auto_resolves(rule_code):
    pr, fix = RULE_SETUPS[rule_code]()
    evaluate_pull_request(pr.pk)
    violation = PolicyViolation.objects.get(pull_request=pr, rule_code=rule_code)

    fix()
    evaluate_pull_request(pr.pk)

    violation.refresh_from_db()
    assert violation.status == PolicyViolation.Status.RESOLVED
    assert violation.resolved_automatically is True
    assert violation.resolved_by is None

    entry = AuditEntry.objects.get(
        object_type="PolicyViolation", object_id=str(violation.pk), action="policy_violation.auto_resolve"
    )
    assert entry.actor is None
    assert entry.changes["after"]["status"] == PolicyViolation.Status.RESOLVED


@pytest.mark.parametrize("status", [PolicyViolation.Status.ACKNOWLEDGED, PolicyViolation.Status.WAIVED])
def test_auto_resolve_leaves_human_judged_rows_untouched(status):
    pr, fix = RULE_SETUPS[RuleCode.NO_TESTS]()
    evaluate_pull_request(pr.pk)
    violation = PolicyViolation.objects.get(pull_request=pr, rule_code=RuleCode.NO_TESTS)
    violation.status = status
    violation.resolved_by = UserFactory()
    violation.resolution_comment = "reviewed"
    violation.save(update_fields=["status", "resolved_by", "resolution_comment"])

    # condition still holds: a re-run must not touch a human-judged row
    evaluate_pull_request(pr.pk)
    violation.refresh_from_db()
    assert violation.status == status
    assert violation.resolution_comment == "reviewed"

    # condition gone: still must not be auto-resolved
    fix()
    evaluate_pull_request(pr.pk)
    violation.refresh_from_db()
    assert violation.status == status


def test_auto_resolved_row_is_not_reopened():
    pr, fix = RULE_SETUPS[RuleCode.DISCLOSURE_MISSING]()
    evaluate_pull_request(pr.pk)
    violation = PolicyViolation.objects.get(pull_request=pr, rule_code=RuleCode.DISCLOSURE_MISSING)

    fix()
    evaluate_pull_request(pr.pk)
    violation.refresh_from_db()
    assert violation.status == PolicyViolation.Status.RESOLVED

    # the condition returns
    pr.ai_disclosure = AIDisclosure.MISSING
    pr.save(update_fields=["ai_disclosure"])
    evaluate_pull_request(pr.pk)

    violation.refresh_from_db()
    assert violation.status == PolicyViolation.Status.RESOLVED
    assert PolicyViolation.objects.filter(pull_request=pr, rule_code=RuleCode.DISCLOSURE_MISSING).count() == 1


def test_pr_created_before_effective_from_produces_no_violation():
    now = timezone.now()
    AIPolicyFactory(require_disclosure=True, effective_from=now)
    pr = PullRequestFactory(ai_disclosure=AIDisclosure.MISSING, created_at=now - timezone.timedelta(days=1))

    evaluate_pull_request(pr.pk)

    assert not PolicyViolation.objects.filter(pull_request=pr).exists()


def test_no_policy_produces_nothing_and_auto_resolves_open_row():
    pr = PullRequestFactory()
    violation = PolicyViolationFactory(
        pull_request=pr, rule_code=RuleCode.NO_TESTS, status=PolicyViolation.Status.OPEN
    )

    evaluate_pull_request(pr.pk)

    violation.refresh_from_db()
    assert violation.status == PolicyViolation.Status.RESOLVED
    assert violation.resolved_automatically is True


def test_disabled_rule_auto_resolves():
    pr, _fix = RULE_SETUPS[RuleCode.DISCLOSURE_MISSING]()
    evaluate_pull_request(pr.pk)
    violation = PolicyViolation.objects.get(pull_request=pr, rule_code=RuleCode.DISCLOSURE_MISSING)
    assert violation.status == PolicyViolation.Status.OPEN

    set_setting("POLICY_DISABLED_RULES", [RuleCode.DISCLOSURE_MISSING])
    evaluate_pull_request(pr.pk)

    violation.refresh_from_db()
    assert violation.status == PolicyViolation.Status.RESOLVED
    assert violation.resolved_automatically is True


def test_moved_counter_refreshes_details_params_without_a_new_row():
    AIPolicyFactory(min_human_approvals=2, effective_from=_past())
    SensitivePathRuleFactory(glob="infra/**", ai_mode=SensitivePathRule.AiMode.NEEDS_EXTRA_REVIEW)
    pr = PullRequestFactory(ai_status=AIStatus.AI_EXPLICIT)
    PRFileFactory(pull_request=pr, path="infra/main.tf")

    evaluate_pull_request(pr.pk)
    violation = PolicyViolation.objects.get(pull_request=pr, rule_code=RuleCode.SENSITIVE_PATH_REVIEW)
    assert violation.details_params["approvals"] == 0

    _human_reviewer(pr)
    evaluate_pull_request(pr.pk)

    refreshed = PolicyViolation.objects.get(pull_request=pr, rule_code=RuleCode.SENSITIVE_PATH_REVIEW)
    assert refreshed.pk == violation.pk
    assert refreshed.status == PolicyViolation.Status.OPEN
    assert refreshed.details_params["approvals"] == 1
    assert (
        PolicyViolation.objects.filter(pull_request=pr, rule_code=RuleCode.SENSITIVE_PATH_REVIEW).count() == 1
    )


def test_saving_a_second_policy_version_does_not_retroactively_judge_an_existing_pr():
    """Review round-1 blocker: gating on the newest `AIPolicy` version (instead of the version
    in effect when the PR was created) meant saving any second version pushed every existing PR
    before its `effective_from`, auto-resolving the entire open-violation backlog."""
    t0 = timezone.now() - timezone.timedelta(days=10)
    AIPolicyFactory(require_disclosure=True, effective_from=t0)
    pr = PullRequestFactory(ai_disclosure=AIDisclosure.MISSING, created_at=t0 + timezone.timedelta(days=1))

    evaluate_pull_request(pr.pk)
    violation = PolicyViolation.objects.get(pull_request=pr, rule_code=RuleCode.DISCLOSURE_MISSING)
    assert violation.status == PolicyViolation.Status.OPEN

    # a second version is saved well after the PR was created and first evaluated
    AIPolicyFactory(require_disclosure=True, min_human_approvals=2, effective_from=timezone.now())
    evaluate_pull_request(pr.pk)

    violation.refresh_from_db()
    assert violation.status == PolicyViolation.Status.OPEN


def test_evaluate_pull_requests_judges_each_pr_under_its_own_policy_version():
    t0 = timezone.now() - timezone.timedelta(days=10)
    t1 = timezone.now() - timezone.timedelta(days=5)
    AIPolicyFactory(require_disclosure=True, effective_from=t0)
    old_pr = PullRequestFactory(
        ai_disclosure=AIDisclosure.MISSING, created_at=t0 + timezone.timedelta(days=1)
    )
    evaluate_pull_request(old_pr.pk)
    old_violation = PolicyViolation.objects.get(pull_request=old_pr, rule_code=RuleCode.DISCLOSURE_MISSING)

    # v2 turns disclosure off and only takes effect from t1 on
    AIPolicyFactory(require_disclosure=False, effective_from=t1)
    new_pr = PullRequestFactory(
        ai_disclosure=AIDisclosure.MISSING, created_at=t1 + timezone.timedelta(days=1)
    )

    evaluate_pull_requests(PullRequest.objects.filter(pk__in=[old_pr.pk, new_pr.pk]))

    old_violation.refresh_from_db()
    assert old_violation.status == PolicyViolation.Status.OPEN
    assert not PolicyViolation.objects.filter(pull_request=new_pr).exists()


def test_evaluate_pull_requests_loads_policy_and_settings_once():
    AIPolicyFactory(require_disclosure=True, effective_from=_past())
    one_pr = [PullRequestFactory(ai_disclosure=AIDisclosure.MISSING)]
    two_prs = [
        PullRequestFactory(ai_disclosure=AIDisclosure.MISSING),
        PullRequestFactory(ai_disclosure=AIDisclosure.MISSING),
    ]

    evaluate_pull_requests(PullRequest.objects.none())  # warm the process-wide settings cache

    with CaptureQueriesContext(connection) as one:
        evaluate_pull_requests(PullRequest.objects.filter(pk__in=[pr.pk for pr in one_pr]))
    with CaptureQueriesContext(connection) as two:
        evaluate_pull_requests(PullRequest.objects.filter(pk__in=[pr.pk for pr in two_prs]))

    def _shared_load_query_count(ctx: CaptureQueriesContext) -> int:
        # policy_aipolicy and catalog_appsetting are the tables `evaluate_pull_requests` loads
        # once for the whole batch (policy versions, POLICY_DISABLED_RULES, NO_TESTS_MIN_LINES,
        # POLICY_VIOLATION_PATHS_IN_PARAMS, AI_COHORT_INCLUDE_SUSPECTED); a query count against
        # them that doesn't grow with the PR count is a direct assertion of "loaded once", unlike
        # a bound on the total query count which also holds when the shared load is reloaded per
        # PR as long as some other query is fixed-cost.
        return sum(
            1
            for query in ctx.captured_queries
            if "policy_aipolicy" in query["sql"] or "catalog_appsetting" in query["sql"]
        )

    shared_for_one = _shared_load_query_count(one)
    shared_for_two = _shared_load_query_count(two)
    assert shared_for_one > 0
    assert shared_for_two == shared_for_one
