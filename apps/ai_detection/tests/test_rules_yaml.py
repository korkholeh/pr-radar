import re

import pytest

from apps.ai_detection.models import Detector
from apps.ai_detection.rules import DEFAULT_RULES_PATH, RuleDefinitionError, load_rule_definitions

_SEED_TOOLS = {"claude_code", "copilot", "cursor", "codex", "devin", "gemini", "aider", "windsurf"}


def test_the_file_parses():
    definitions = load_rule_definitions()
    assert len(definitions) > 0


def test_all_eight_tools_are_represented():
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


def test_unknown_detector_raises_named_error(tmp_path):
    bad_file = tmp_path / "bad.yaml"
    bad_file.write_text(
        "rules:\n"
        "  - name: bad rule\n"
        "    detector: not_a_real_detector\n"
        "    pattern: 'x'\n"
        "    tool: claude_code\n"
        "    confidence: high\n"
        "    notes: 'test'\n"
    )
    with pytest.raises(RuleDefinitionError):
        load_rule_definitions(bad_file)


def test_unknown_tool_raises_named_error(tmp_path):
    bad_file = tmp_path / "bad.yaml"
    bad_file.write_text(
        "rules:\n"
        "  - name: bad rule\n"
        "    detector: label\n"
        "    pattern: 'x'\n"
        "    tool: not_a_real_tool\n"
        "    confidence: high\n"
        "    notes: 'test'\n"
    )
    with pytest.raises(RuleDefinitionError):
        load_rule_definitions(bad_file)


def test_default_rules_path_points_at_the_fixture():
    assert DEFAULT_RULES_PATH.name == "detection_rules.yaml"
    assert DEFAULT_RULES_PATH.exists()


def test_disputed_rules_are_flagged_and_ship_low_confidence():
    definitions = load_rule_definitions()
    disputed = [d for d in definitions if d.disputed]
    assert disputed, "fixture should mark at least one rule disputed"
    for definition in disputed:
        assert definition.confidence == "low"


def test_disputed_rule_with_non_low_confidence_raises_named_error(tmp_path):
    bad_file = tmp_path / "bad.yaml"
    bad_file.write_text(
        "rules:\n"
        "  - name: bad rule\n"
        "    detector: label\n"
        "    pattern: 'x'\n"
        "    tool: claude_code\n"
        "    confidence: high\n"
        "    disputed: true\n"
        "    notes: 'test'\n"
    )
    with pytest.raises(RuleDefinitionError):
        load_rule_definitions(bad_file)
