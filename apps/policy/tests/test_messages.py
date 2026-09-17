import pytest
from django.utils.translation import override

from apps.policy.messages import RULE_MESSAGES, render_violation, rule_label
from apps.policy.models import PolicyViolation

RuleCode = PolicyViolation.RuleCode

_SAMPLE_PARAMS: dict[str, dict] = {
    RuleCode.DISCLOSURE_MISSING: {},
    RuleCode.DISCLOSURE_MISMATCH: {},
    RuleCode.TOOL_NOT_ALLOWED: {"tool": "cursor"},
    RuleCode.SENSITIVE_PATH_FORBIDDEN: {"paths": ["secrets/a.py"], "path_count": 1, "sensitive_rule_id": 1},
    RuleCode.SENSITIVE_PATH_REVIEW: {
        "paths": ["infra/a.tf"],
        "path_count": 1,
        "sensitive_rule_id": 2,
        "approvals": 1,
        "required": 2,
    },
    RuleCode.NO_HUMAN_APPROVAL: {"approvals": 0, "required": 1},
    RuleCode.SELF_MERGE: {},
    RuleCode.NO_TESTS: {"non_test_lines": 120, "threshold": 20},
    RuleCode.AI_PR_TOO_LARGE: {"effective_lines": 900, "limit": 500},
}


@pytest.mark.parametrize("rule_code", list(RuleCode.values))
def test_every_rule_code_renders_a_non_empty_sentence(rule_code):
    text = render_violation(rule_code, _SAMPLE_PARAMS[rule_code])
    assert text
    assert text != rule_code


def test_rules_registry_covers_all_rule_codes():
    assert set(RULE_MESSAGES.keys()) == set(RuleCode.values)


@pytest.mark.parametrize(
    "rule_code,count_key",
    [
        (RuleCode.SENSITIVE_PATH_FORBIDDEN, "path_count"),
        (RuleCode.SENSITIVE_PATH_REVIEW, "path_count"),
        (RuleCode.NO_HUMAN_APPROVAL, "required"),
        (RuleCode.NO_TESTS, "non_test_lines"),
        (RuleCode.AI_PR_TOO_LARGE, "effective_lines"),
    ],
)
def test_ngettext_gives_singular_for_one_and_plural_for_two(rule_code, count_key):
    singular_params = {**_SAMPLE_PARAMS[rule_code], count_key: 1}
    plural_params = {**_SAMPLE_PARAMS[rule_code], count_key: 2}
    singular_text = render_violation(rule_code, singular_params)
    plural_text = render_violation(rule_code, plural_params)
    assert singular_text != plural_text


def test_unknown_rule_code_renders_the_code_itself():
    assert render_violation("SOME_FUTURE_CODE", {}) == "SOME_FUTURE_CODE"


def test_missing_param_renders_without_raising():
    text = render_violation(RuleCode.NO_TESTS, {})
    assert text
    assert "?" in text


def test_message_renders_in_ukrainian():
    with override("en"):
        english = render_violation(RuleCode.NO_TESTS, _SAMPLE_PARAMS[RuleCode.NO_TESTS])
    with override("uk"):
        ukrainian = render_violation(RuleCode.NO_TESTS, _SAMPLE_PARAMS[RuleCode.NO_TESTS])
    assert ukrainian != english


def test_rule_label_returns_display_label():
    assert rule_label(RuleCode.NO_TESTS) == RuleCode.NO_TESTS.label


def test_rule_label_unknown_code_returns_code():
    assert rule_label("SOME_FUTURE_CODE") == "SOME_FUTURE_CODE"
