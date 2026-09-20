import pytest

from apps.activity.factories import PRFileFactory, PullRequestFactory, ReviewFactory
from apps.policy.factories import AIPolicyFactory
from apps.policy.models import PolicyViolation
from apps.policy.rules import AI_ONLY, MERGE_DEPENDENT, PARAM_SCHEMA, RULES, SEVERITY, load_context

RuleCode = PolicyViolation.RuleCode


def test_rules_registry_has_an_evaluator_for_every_rule_code():
    assert set(RULES.keys()) == set(RuleCode.values)


def test_severity_matches_spec_table():
    expected = {
        RuleCode.DISCLOSURE_MISSING: "medium",
        RuleCode.DISCLOSURE_MISMATCH: "high",
        RuleCode.TOOL_NOT_ALLOWED: "high",
        RuleCode.SENSITIVE_PATH_FORBIDDEN: "high",
        RuleCode.SENSITIVE_PATH_REVIEW: "medium",
        RuleCode.NO_HUMAN_APPROVAL: "high",
        RuleCode.SELF_MERGE: "high",
        RuleCode.NO_TESTS: "low",
        RuleCode.AI_PR_TOO_LARGE: "low",
        # The PLANEKS standards (phase 12, stage 7), per PLAN D8's table.
        RuleCode.QUALITY_GATE_BYPASSED: "high",
        RuleCode.TEST_WEAKENED: "high",
        RuleCode.AI_ONLY_APPROVAL: "high",
        RuleCode.AI_REVIEW_MISSING: "medium",
        RuleCode.AI_REVIEW_UNRESOLVED: "medium",
        RuleCode.HIGH_RISK_NO_PLAN: "high",
        RuleCode.RISK_LEVEL_MISSING: "low",
        RuleCode.VERIFICATION_MISSING: "low",
        RuleCode.TASK_LINK_MISSING: "low",
        RuleCode.NEW_DEPENDENCY_AI: "medium",
        RuleCode.MIGRATION_AI_INSUFFICIENT_REVIEW: "high",
        RuleCode.SECRET_ARTIFACT_COMMITTED: "high",
        RuleCode.AGENT_CONFIG_CHANGED: "low",
        RuleCode.SCOPE_CREEP: "medium",
        RuleCode.RUBBER_STAMP_ON_AI_PR: "high",
    }
    assert SEVERITY == expected


def test_every_rule_has_a_param_schema_entry():
    assert set(PARAM_SCHEMA.keys()) == set(RuleCode.values)


def test_merge_dependent_set_is_exactly_the_rules_a_merge_settles():
    assert MERGE_DEPENDENT == {
        RuleCode.NO_HUMAN_APPROVAL,
        RuleCode.SELF_MERGE,
        RuleCode.QUALITY_GATE_BYPASSED,
        RuleCode.AI_ONLY_APPROVAL,
        RuleCode.AI_REVIEW_MISSING,
        RuleCode.AI_REVIEW_UNRESOLVED,
        RuleCode.MIGRATION_AI_INSUFFICIENT_REVIEW,
        RuleCode.RUBBER_STAMP_ON_AI_PR,
    }


def test_ai_only_set_leaves_the_engineering_rules_applying_to_everybody():
    """A bypassed quality gate, a weakened test or a committed credential is no better for having
    been written by hand, so those rules are not in the AI-only set."""
    assert AI_ONLY == {
        RuleCode.SENSITIVE_PATH_FORBIDDEN,
        RuleCode.SENSITIVE_PATH_REVIEW,
        RuleCode.NO_HUMAN_APPROVAL,
        RuleCode.SELF_MERGE,
        RuleCode.NO_TESTS,
        RuleCode.AI_PR_TOO_LARGE,
        RuleCode.NEW_DEPENDENCY_AI,
        RuleCode.MIGRATION_AI_INSUFFICIENT_REVIEW,
        RuleCode.SCOPE_CREEP,
        RuleCode.RUBBER_STAMP_ON_AI_PR,
    }
    for rule_code in (
        RuleCode.QUALITY_GATE_BYPASSED,
        RuleCode.TEST_WEAKENED,
        RuleCode.SECRET_ARTIFACT_COMMITTED,
        RuleCode.AGENT_CONFIG_CHANGED,
        RuleCode.RISK_LEVEL_MISSING,
        RuleCode.VERIFICATION_MISSING,
        RuleCode.TASK_LINK_MISSING,
        RuleCode.HIGH_RISK_NO_PLAN,
        RuleCode.AI_REVIEW_MISSING,
        RuleCode.AI_REVIEW_UNRESOLVED,
        RuleCode.AI_ONLY_APPROVAL,
    ):
        assert rule_code not in AI_ONLY


@pytest.mark.django_db
def test_load_context_query_budget(django_assert_num_queries):
    pr = PullRequestFactory()
    for _ in range(5):
        PRFileFactory(pull_request=pr)
        ReviewFactory(pull_request=pr)
    policy = AIPolicyFactory()

    load_context(pr.pk, policy)  # warm the process-wide settings cache before pinning a count

    with django_assert_num_queries(10):
        ctx = load_context(pr.pk, policy)

    assert len(ctx.files) == 5
    assert len(ctx.reviews) == 5

    # the budget must not grow with row count: a PR with twice as many files/reviews takes the
    # same number of queries, only bigger result sets.
    big_pr = PullRequestFactory()
    for _ in range(10):
        PRFileFactory(pull_request=big_pr)
        ReviewFactory(pull_request=big_pr)
    with django_assert_num_queries(10):
        big_ctx = load_context(big_pr.pk, policy)
    assert len(big_ctx.files) == 10
    assert len(big_ctx.reviews) == 10
