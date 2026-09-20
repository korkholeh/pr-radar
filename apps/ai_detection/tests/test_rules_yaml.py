import re

import pytest

from apps.ai_detection.models import Detector
from apps.ai_detection.rules import (
    DEFAULT_RULES_PATH,
    DEFAULT_SAMPLES_PATH,
    Provenance,
    RuleDefinitionError,
    load_rule_definitions,
    load_rule_samples,
)

_SEED_TOOLS = {
    "claude_code",
    "copilot",
    "cursor",
    "codex",
    "devin",
    "gemini",
    "aider",
    "windsurf",
    "charlie",
    "other",
}

# A well-formed rule body, for the malformed-input cases to mutate one field of at a time. Without
# a shared baseline each of those cases risks tripping an *earlier* validation than the one it
# means to exercise, and still passing.
_VALID_ROW = {
    "name": "a rule",
    "detector": "label",
    "pattern": "x",
    "tool": "claude_code",
    "confidence": "high",
    "provenance": "documented",
    "notes": "test",
}


def _write_rule(tmp_path, **overrides):
    row = {**_VALID_ROW, **overrides}
    lines = ["rules:"]
    for key, value in row.items():
        if value is None:  # `None` means "omit this key entirely"
            continue
        prefix = "  - " if len(lines) == 1 else "    "
        lines.append(f"{prefix}{key}: {value!r}")
    path = tmp_path / "rules.yaml"
    path.write_text("\n".join(lines) + "\n")
    return path


def test_the_file_parses():
    definitions = load_rule_definitions()
    assert len(definitions) > 0


def test_every_seeded_tool_is_represented():
    definitions = load_rule_definitions()
    assert {d.tool for d in definitions} == _SEED_TOOLS


def test_every_rule_has_non_empty_notes():
    for definition in load_rule_definitions():
        assert definition.notes.strip() != ""


def test_every_pattern_compiles():
    for definition in load_rule_definitions():
        re.compile(definition.pattern)


def test_at_least_one_rule_per_detector():
    definitions = load_rule_definitions()
    detectors_covered = {d.detector for d in definitions}
    assert detectors_covered == set(Detector.values)


def test_default_rules_path_points_at_the_fixture():
    assert DEFAULT_RULES_PATH.name == "detection_rules.yaml"
    assert DEFAULT_RULES_PATH.exists()


# --- disputed: is the signal a reliable indicator at all? ---------------------------------------


def test_disputed_rules_are_flagged_and_ship_low_confidence():
    definitions = load_rule_definitions()
    disputed = [d for d in definitions if d.disputed]
    assert disputed, "fixture should mark at least one rule disputed"
    for definition in disputed:
        assert definition.confidence == "low"


def test_disputed_rule_with_non_low_confidence_raises_named_error(tmp_path):
    with pytest.raises(RuleDefinitionError):
        load_rule_definitions(_write_rule(tmp_path, disputed=True, confidence="high"))


# --- provenance: do we have evidence the string is ever emitted? --------------------------------


def test_every_rule_declares_a_known_provenance():
    for definition in load_rule_definitions():
        assert definition.provenance in set(Provenance), definition.name


def test_the_fixture_carries_every_provenance_value():
    """If the seed set ever loses its `observed` or `unverified` rules, the tests below stop
    proving anything, so assert the fixture keeps exercising all three."""
    values = {d.provenance for d in load_rule_definitions()}
    assert values == set(Provenance)


def test_unverified_rules_do_not_seed_active():
    definitions = load_rule_definitions()
    for definition in definitions:
        assert definition.seeds_active is (definition.provenance != Provenance.UNVERIFIED)


def test_documented_and_observed_rules_seed_active():
    for definition in load_rule_definitions():
        if definition.provenance in (Provenance.DOCUMENTED, Provenance.OBSERVED):
            assert definition.seeds_active is True


def test_missing_provenance_raises_named_error(tmp_path):
    with pytest.raises(RuleDefinitionError, match="provenance"):
        load_rule_definitions(_write_rule(tmp_path, provenance=None))


def test_unknown_provenance_raises_named_error(tmp_path):
    with pytest.raises(RuleDefinitionError, match="provenance"):
        load_rule_definitions(_write_rule(tmp_path, provenance="probably"))


def test_an_unverified_rule_may_still_ship_high_confidence(tmp_path):
    """Deliberate: a tool-written artefact is conclusive *if* it fires, so downgrading an
    unconfirmed one would only manufacture false negatives. Seeding it deactivated is the guard."""
    definitions = load_rule_definitions(_write_rule(tmp_path, provenance="unverified", confidence="high"))
    assert definitions[0].confidence == "high"
    assert definitions[0].seeds_active is False


# --- malformed input ----------------------------------------------------------------------------


def test_unknown_detector_raises_named_error(tmp_path):
    with pytest.raises(RuleDefinitionError, match="detector"):
        load_rule_definitions(_write_rule(tmp_path, detector="not_a_real_detector"))


def test_unknown_tool_raises_named_error(tmp_path):
    with pytest.raises(RuleDefinitionError, match="tool"):
        load_rule_definitions(_write_rule(tmp_path, tool="not_a_real_tool"))


def test_unknown_confidence_raises_named_error(tmp_path):
    with pytest.raises(RuleDefinitionError, match="confidence"):
        load_rule_definitions(_write_rule(tmp_path, confidence="certain"))


def test_missing_notes_raises_named_error(tmp_path):
    with pytest.raises(RuleDefinitionError, match="notes"):
        load_rule_definitions(_write_rule(tmp_path, notes=""))


def test_uncompilable_pattern_raises_named_error(tmp_path):
    with pytest.raises(RuleDefinitionError, match="compile"):
        load_rule_definitions(_write_rule(tmp_path, pattern="(unclosed"))


# --- the sample corpus --------------------------------------------------------------------------


def test_default_samples_path_points_at_the_fixture():
    assert DEFAULT_SAMPLES_PATH.name == "detection_samples.yaml"
    assert DEFAULT_SAMPLES_PATH.exists()


def test_every_rule_has_samples():
    rule_names = {d.name for d in load_rule_definitions()}
    sample_names = set(load_rule_samples())
    assert rule_names - sample_names == set(), "rule(s) with no sample corpus"
    assert sample_names - rule_names == set(), "sample(s) for a rule that no longer exists"


@pytest.mark.parametrize("definition", load_rule_definitions(), ids=lambda d: d.name)
def test_every_rule_matches_its_samples_and_rejects_its_non_matches(definition):
    samples = load_rule_samples()[definition.name]
    pattern = re.compile(definition.pattern, re.IGNORECASE | re.MULTILINE)

    for value in samples.matches:
        assert pattern.search(value) is not None, f"{definition.name} should match {value!r}"
    for value in samples.non_matches:
        assert pattern.search(value) is None, f"{definition.name} should not match {value!r}"


def test_samples_without_a_non_match_are_rejected(tmp_path):
    path = tmp_path / "samples.yaml"
    path.write_text("samples:\n  a rule:\n    matches:\n      - 'x'\n")
    with pytest.raises(RuleDefinitionError, match="reject"):
        load_rule_samples(path)


def test_samples_without_a_match_are_rejected(tmp_path):
    path = tmp_path / "samples.yaml"
    path.write_text("samples:\n  a rule:\n    non_matches:\n      - 'x'\n")
    with pytest.raises(RuleDefinitionError, match="must match"):
        load_rule_samples(path)
