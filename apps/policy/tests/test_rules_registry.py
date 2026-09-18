import pytest

from apps.activity.factories import PRFileFactory, PullRequestFactory, ReviewFactory
from apps.policy.factories import AIPolicyFactory
from apps.policy.models import PolicyViolation
from apps.policy.rules import AI_ONLY, MERGE_DEPENDENT, PARAM_SCHEMA, RULES, SEVERITY, load_context

RuleCode = PolicyViolation.RuleCode


def test_rules_registry_has_exactly_the_nine_rule_codes():
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
    }
    assert SEVERITY == expected


def test_every_rule_has_a_param_schema_entry():
    assert set(PARAM_SCHEMA.keys()) == set(RuleCode.values)


def test_merge_dependent_set_is_exactly_the_two_merge_rules():
    assert MERGE_DEPENDENT == {RuleCode.NO_HUMAN_APPROVAL, RuleCode.SELF_MERGE}


def test_ai_only_set_excludes_the_three_any_pr_rules():
    assert AI_ONLY == set(RuleCode.values) - {
        RuleCode.DISCLOSURE_MISSING,
        RuleCode.DISCLOSURE_MISMATCH,
        RuleCode.TOOL_NOT_ALLOWED,
    }


@pytest.mark.django_db
def test_load_context_query_budget(django_assert_num_queries):
    pr = PullRequestFactory()
    for _ in range(5):
        PRFileFactory(pull_request=pr)
        ReviewFactory(pull_request=pr)
    policy = AIPolicyFactory()

    load_context(pr.pk, policy)  # warm the process-wide settings cache before pinning a count

    with django_assert_num_queries(7):
        ctx = load_context(pr.pk, policy)

    assert len(ctx.files) == 5
    assert len(ctx.reviews) == 5

    # the budget must not grow with row count: a PR with twice as many files/reviews takes the
    # same number of queries, only bigger result sets.
    big_pr = PullRequestFactory()
    for _ in range(10):
        PRFileFactory(pull_request=big_pr)
        ReviewFactory(pull_request=big_pr)
    with django_assert_num_queries(7):
        big_ctx = load_context(big_pr.pk, policy)
    assert len(big_ctx.files) == 10
    assert len(big_ctx.reviews) == 10
