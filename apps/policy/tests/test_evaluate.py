"""`evaluate_pull_request`/`evaluate_pull_requests`: the wanted-vs-existing diff that may only
create an open violation or auto-resolve one, parametrized over all nine rule codes per
RISKS row 6 and acceptance criteria 1-3."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.factories import UserFactory
from apps.accounts.models import AuditEntry
from apps.activity.factories import PRFileFactory, PullRequestFactory, ReviewFactory
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
}


def test_rule_setups_cover_all_nine_codes():
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
