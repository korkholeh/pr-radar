"""Loading `fixtures/detection_rules.yaml` into validated, DB-independent `RuleDefinition`s. The
loader never touches the database — `seed_detection_rules` decides how to reconcile a definition
with a stored `DetectionRule` row."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from apps.ai_detection.models import Confidence, Detector, Tool

DEFAULT_RULES_PATH = Path(__file__).resolve().parent.parent.parent / "fixtures" / "detection_rules.yaml"


class RuleDefinitionError(Exception):
    pass


@dataclass(frozen=True)
class RuleDefinition:
    name: str
    detector: str
    pattern: str
    tool: str
    confidence: str
    notes: str
    disputed: bool = False


def load_rule_definitions(path: Path = DEFAULT_RULES_PATH) -> list[RuleDefinition]:
    with open(path) as handle:
        raw = yaml.safe_load(handle)

    rows = raw.get("rules") if isinstance(raw, dict) else raw
    if not rows:
        raise RuleDefinitionError(f"{path} defines no rules.")

    definitions = []
    for row in rows:
        name = row.get("name")
        detector = row.get("detector")
        pattern = row.get("pattern")
        tool = row.get("tool")
        confidence = row.get("confidence")
        notes = (row.get("notes") or "").strip()
        disputed = bool(row.get("disputed", False))

        if detector not in Detector.values:
            raise RuleDefinitionError(f"Rule {name!r} has an unknown detector: {detector!r}.")
        if tool not in Tool.values:
            raise RuleDefinitionError(f"Rule {name!r} has an unknown tool: {tool!r}.")
        if confidence not in Confidence.values:
            raise RuleDefinitionError(f"Rule {name!r} has an unknown confidence: {confidence!r}.")
        if not notes:
            raise RuleDefinitionError(
                f"Rule {name!r} has no notes (a source or provenance note is required)."
            )
        if disputed and confidence != Confidence.LOW:
            raise RuleDefinitionError(
                f"Rule {name!r} is marked disputed but ships confidence {confidence!r}, not "
                f"{Confidence.LOW!r}."
            )
        try:
            re.compile(pattern)
        except re.error as exc:
            raise RuleDefinitionError(f"Rule {name!r} has a pattern that does not compile: {exc}") from exc

        definitions.append(
            RuleDefinition(
                name=name,
                detector=detector,
                pattern=pattern,
                tool=tool,
                confidence=confidence,
                notes=notes,
                disputed=disputed,
            )
        )
    return definitions
