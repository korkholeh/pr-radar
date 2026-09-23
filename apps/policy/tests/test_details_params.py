"""CLAUDE.md: system-generated text is stored as a code plus params, never a rendered message.
Fires every rule through the real pipeline (`apps.policy.services.evaluate_pull_request`,
reusing `test_evaluate.RULE_SETUPS`) and checks every stored `details_params` value is data, not
prose, and that the rendered sentence itself never lands in a database column (criterion 7)."""

import pytest

from apps.policy.messages import RULE_MESSAGES, render_violation
from apps.policy.models import PolicyViolation
from apps.policy.rules import PARAM_SCHEMA
from apps.policy.services import evaluate_pull_request
from apps.policy.tests.test_evaluate import RULE_SETUPS

pytestmark = pytest.mark.django_db

ALLOWED_SCALAR_TYPES = (str, int, float, bool)


def _fire_every_rule() -> list[PolicyViolation]:
    violations = []
    for rule_code, setup in RULE_SETUPS.items():
        pr, _fix = setup()
        evaluate_pull_request(pr.pk)
        violations.extend(PolicyViolation.objects.filter(pull_request=pr, rule_code=rule_code))
    return violations


def _prose_words() -> set[str]:
    words: set[str] = set()
    for entry in RULE_MESSAGES.values():
        for template in (entry.get("singular"), entry.get("plural"), *entry.get("variants", {}).values()):
            if not template:
                continue
            for word in template.split():
                cleaned = word.strip(".,;:()%").lower()
                if cleaned and "%(" not in cleaned and len(cleaned) > 3:
                    words.add(cleaned)
    return words


def test_every_rule_actually_fired():
    violations = _fire_every_rule()
    assert {v.rule_code for v in violations} == set(PolicyViolation.RuleCode.values)


def test_every_param_key_is_declared_in_the_schema():
    for violation in _fire_every_rule():
        schema = PARAM_SCHEMA[violation.rule_code]
        assert set(violation.details_params.keys()) <= schema, violation.rule_code


def test_every_param_value_is_data_not_a_rich_type():
    for violation in _fire_every_rule():
        for key, value in violation.details_params.items():
            if isinstance(value, list):
                assert all(isinstance(item, str) for item in value), (violation.rule_code, key)
            else:
                assert isinstance(value, ALLOWED_SCALAR_TYPES), (violation.rule_code, key)


def test_no_param_value_contains_a_word_from_any_rule_message():
    prose_words = _prose_words()
    for violation in _fire_every_rule():
        for key, value in violation.details_params.items():
            text_values = value if isinstance(value, list) else [value]
            for item in text_values:
                if not isinstance(item, str):
                    continue
                words = {w.strip(".,;:()%").lower() for w in item.split()}
                assert not (words & prose_words), (violation.rule_code, key, item)


def test_rendered_message_is_in_no_database_column():
    for violation in _fire_every_rule():
        rendered = render_violation(violation.rule_code, violation.details_params)
        for field in violation._meta.fields:
            value = getattr(violation, field.name)
            if isinstance(value, str) and value:
                assert rendered not in value, (violation.rule_code, field.name)
