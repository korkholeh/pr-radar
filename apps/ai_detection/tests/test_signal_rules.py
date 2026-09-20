"""`SignalRule` — the structural rule family's model, its YAML seed and its one non-negotiable
invariant (phase 12, stage 4)."""

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction

from apps.ai_detection.models import (
    SIGNAL_PARAM_DEFAULTS,
    AISignal,
    Confidence,
    SignalKind,
    SignalRule,
    Tool,
)
from apps.ai_detection.rules import (
    DEFAULT_SIGNAL_RULES_PATH,
    RuleDefinitionError,
    load_signal_rule_definitions,
)


def _write_rule(tmp_path, **overrides):
    row = {
        "name": "a rule",
        "kind": "commit_burst",
        "tool": "other",
        "confidence": "medium",
        "notes": "test",
        **overrides,
    }
    lines = ["rules:"]
    for key, value in row.items():
        if value is None:  # `None` means "omit this key entirely"
            continue
        prefix = "  - " if len(lines) == 1 else "    "
        lines.append(f"{prefix}{key}: {value!r}")
    path = tmp_path / "signal_rules.yaml"
    path.write_text("\n".join(lines) + "\n")
    return path


# --- the invariant ------------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_structural_rule_can_never_be_high_confidence_at_the_database_level():
    """Not only a form validator. "Only a tool-written artefact proves AI authorship" is the one
    property of this feature that must not be reachable by editing a row in the Django admin."""
    with pytest.raises(IntegrityError), transaction.atomic():
        SignalRule.objects.create(
            name="too confident", kind=SignalKind.COMMIT_BURST, confidence=Confidence.HIGH
        )


def test_high_confidence_is_also_rejected_by_the_model_validator_with_a_reason():
    rule = SignalRule(name="too confident", kind=SignalKind.COMMIT_BURST, confidence=Confidence.HIGH)
    with pytest.raises(ValidationError) as exc:
        rule.clean()
    assert "confidence" in exc.value.message_dict


def test_the_yaml_loader_refuses_a_high_confidence_rule(tmp_path):
    with pytest.raises(RuleDefinitionError, match="high"):
        load_signal_rule_definitions(_write_rule(tmp_path, confidence="high"))


# --- params validation --------------------------------------------------------------------------


def test_a_parameter_the_kind_does_not_understand_is_rejected():
    """Silently ignoring it would leave a lead believing they had tuned something."""
    rule = SignalRule(
        name="typo", kind=SignalKind.COMMIT_BURST, confidence=Confidence.LOW, params={"min_commmits": 5}
    )
    with pytest.raises(ValidationError, match="min_commmits"):
        rule.clean()


@pytest.mark.parametrize("value", [-1, "five", None])
def test_a_non_numeric_or_negative_threshold_is_rejected(value):
    rule = SignalRule(
        name="bad", kind=SignalKind.COMMIT_BURST, confidence=Confidence.LOW, params={"min_commits": value}
    )
    with pytest.raises(ValidationError):
        rule.clean()


def test_an_unknown_kind_is_rejected():
    rule = SignalRule(name="mystery", kind="reads_minds", confidence=Confidence.LOW)
    with pytest.raises(ValidationError, match="kind"):
        rule.clean()


def test_effective_params_fills_in_the_kinds_defaults():
    """A rule that sets one threshold still gets the rest, and a kind that grows a parameter does
    not silently read it as absent on rules saved before it existed."""
    rule = SignalRule(kind=SignalKind.COMMIT_BURST, params={"min_commits": 9})
    assert rule.effective_params() == {
        "min_commits": 9,
        "max_gap_seconds": 90,
        "min_lines_per_commit": 20,
    }


def test_every_kind_has_a_parameter_default_entry():
    assert set(SIGNAL_PARAM_DEFAULTS) == set(SignalKind.values)


# --- the AISignal dual-family constraints -------------------------------------------------------


@pytest.mark.django_db
def test_a_signal_with_both_rule_families_is_rejected(pull_request, detection_rule, signal_rule):
    with pytest.raises(IntegrityError), transaction.atomic():
        AISignal.objects.create(
            pull_request=pull_request,
            rule=detection_rule,
            signal_rule=signal_rule,
            tool=Tool.OTHER,
            confidence=Confidence.MEDIUM,
            evidence_hash="a" * 64,
        )


@pytest.mark.django_db
def test_a_signal_with_neither_rule_family_is_rejected(pull_request):
    with pytest.raises(IntegrityError), transaction.atomic():
        AISignal.objects.create(
            pull_request=pull_request,
            tool=Tool.OTHER,
            confidence=Confidence.MEDIUM,
            evidence_hash="b" * 64,
        )


# --- the shipped seed set -----------------------------------------------------------------------


def test_the_shipped_file_parses_and_covers_every_kind():
    definitions = load_signal_rule_definitions()
    assert {definition.kind for definition in definitions} == set(SignalKind.values)


def test_no_shipped_rule_is_high_confidence():
    for definition in load_signal_rule_definitions():
        assert definition.confidence != Confidence.HIGH, definition.name


def test_every_shipped_rule_explains_what_will_trip_it():
    for definition in load_signal_rule_definitions():
        assert definition.notes.strip() != "", definition.name


def test_every_shipped_rules_params_are_understood_by_its_kind():
    for definition in load_signal_rule_definitions():
        unknown = set(definition.params) - set(SIGNAL_PARAM_DEFAULTS[definition.kind])
        assert not unknown, (definition.name, unknown)


def test_default_signal_rules_path_points_at_the_fixture():
    assert DEFAULT_SIGNAL_RULES_PATH.name == "signal_rules.yaml"
    assert DEFAULT_SIGNAL_RULES_PATH.exists()


@pytest.mark.django_db
def test_seeding_creates_every_rule_deactivated():
    """A threshold has no right answer across teams, so nothing starts flagging pull requests
    until a lead has dry-run it against their own."""
    call_command("seed_signal_rules")

    rules = SignalRule.objects.all()
    assert rules.count() == len(load_signal_rule_definitions())
    assert not rules.filter(is_active=True).exists()


@pytest.mark.django_db
def test_re_seeding_changes_no_row_and_keeps_a_leads_activation():
    call_command("seed_signal_rules")
    rule = SignalRule.objects.first()
    rule.is_active = True
    rule.save(update_fields=["is_active"])

    call_command("seed_signal_rules")
    call_command("seed_signal_rules", "--update")

    rule.refresh_from_db()
    assert rule.is_active is True


@pytest.mark.django_db
def test_update_rewrites_params_but_not_activation():
    call_command("seed_signal_rules")
    rule = SignalRule.objects.get(kind=SignalKind.COMMIT_BURST)
    rule.params = {"min_commits": 99}
    rule.is_active = True
    rule.save(update_fields=["params", "is_active"])

    call_command("seed_signal_rules", "--update")

    rule.refresh_from_db()
    assert rule.params != {"min_commits": 99}
    assert rule.is_active is True
