import pytest
from django.utils.translation import override

from apps.policy.messages import ENUM_PARAM_LABELS, RULE_MESSAGES, render_violation, rule_label
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
    RuleCode.AI_PR_TOO_LARGE: {"effective_lines": 900, "limit": 500, "risk_level": "high"},
    RuleCode.QUALITY_GATE_BYPASSED: {"reason": "checks_failing", "state": "FAILURE"},
    RuleCode.TEST_WEAKENED: {"reason": "skip_marker_added", "markers": 2},
    RuleCode.AI_ONLY_APPROVAL: {"bot_approvals": 1},
    RuleCode.AI_REVIEW_MISSING: {"reviewers": ["copilot"]},
    RuleCode.AI_REVIEW_UNRESOLVED: {"threads": 2},
    RuleCode.HIGH_RISK_NO_PLAN: {"paths": ["apps/x/migrations/0002.py"], "path_count": 1},
    RuleCode.RISK_LEVEL_MISSING: {},
    RuleCode.VERIFICATION_MISSING: {},
    RuleCode.TASK_LINK_MISSING: {},
    RuleCode.NEW_DEPENDENCY_AI: {"paths": ["pyproject.toml"], "path_count": 1},
    RuleCode.MIGRATION_AI_INSUFFICIENT_REVIEW: {
        "paths": ["apps/x/migrations/0002.py"],
        "path_count": 1,
        "reviewers": 2,
    },
    RuleCode.SECRET_ARTIFACT_COMMITTED: {"paths": [".env"], "path_count": 1},
    RuleCode.AGENT_CONFIG_CHANGED: {"paths": ["CLAUDE.md"], "path_count": 1},
    RuleCode.SCOPE_CREEP: {"reason": "outside_stated_scope", "modules": ["billing"], "module_count": 1},
    RuleCode.RUBBER_STAMP_ON_AI_PR: {},
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
        (RuleCode.AI_ONLY_APPROVAL, "bot_approvals"),
        (RuleCode.AI_REVIEW_UNRESOLVED, "threads"),
        (RuleCode.HIGH_RISK_NO_PLAN, "path_count"),
        (RuleCode.NEW_DEPENDENCY_AI, "path_count"),
        (RuleCode.MIGRATION_AI_INSUFFICIENT_REVIEW, "path_count"),
        (RuleCode.SECRET_ARTIFACT_COMMITTED, "path_count"),
        (RuleCode.AGENT_CONFIG_CHANGED, "path_count"),
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


# -- the enum params (phase 12, stage 7) ----------------------------------------------------------


def test_a_reason_code_is_rendered_as_words_not_left_as_a_code():
    """A violation stores `reason="checks_failing"` — a code, never a sentence — and the words are
    built here, in the reader's language."""
    text = render_violation(RuleCode.QUALITY_GATE_BYPASSED, {"reason": "checks_failing"})
    assert "checks_failing" not in text
    assert text.endswith(".")


def test_every_reason_code_a_rule_can_emit_has_a_label():
    """A code with no label would render as the raw code on the page a lead reads."""
    emitted = {
        "checks_failing",
        "skip_ci_marker",
        "gate_relaxed",
        "check_removed",
        "test_file_removed",
        "skip_marker_added",
        "assertions_removed",
        "reformat_mixed_in",
        "outside_stated_scope",
    }
    assert emitted <= set(ENUM_PARAM_LABELS["reason"])


def test_an_unknown_reason_code_renders_the_code_rather_than_raising():
    """A row written by a future version of a rule must not break the console."""
    text = render_violation(RuleCode.QUALITY_GATE_BYPASSED, {"reason": "some_future_reason"})
    assert "some_future_reason" in text


def test_a_reason_code_renders_in_ukrainian():
    params = {"reason": "checks_failing"}
    with override("en"):
        english = render_violation(RuleCode.QUALITY_GATE_BYPASSED, params)
    with override("uk"):
        ukrainian = render_violation(RuleCode.QUALITY_GATE_BYPASSED, params)
    assert ukrainian != english


@pytest.mark.parametrize("rule_code", list(RuleCode.values))
def test_every_rule_code_renders_in_ukrainian_with_its_placeholders_filled(rule_code):
    with override("uk"):
        text = render_violation(rule_code, _SAMPLE_PARAMS[rule_code])
    assert text
    assert "%(" not in text
    assert "?" not in text
