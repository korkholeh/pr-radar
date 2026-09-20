"""Loading `fixtures/detection_rules.yaml` into validated, DB-independent `RuleDefinition`s. The
loader never touches the database — `seed_detection_rules` decides how to reconcile a definition
with a stored `DetectionRule` row.

Two independent axes qualify a rule, and they answer different questions:

- `disputed` — *is the signal itself a reliable indicator of AI authorship?* A team convention
  (a manually applied label, a branch someone names by hand) is not, so a disputed rule is capped
  at `low` confidence and can never alone resolve a PR to `ai_explicit`.
- `provenance` — *do we have evidence the string is ever actually emitted?* A pattern nobody has
  seen in real data may be perfectly reliable if it fires, and useless if the vendor never writes
  it. An `unverified` rule is therefore seeded **inactive** rather than downgraded, so a lead
  confirms it against their own pull requests (dry run, then activate) before it counts.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from apps.ai_detection.models import SIGNAL_PARAM_DEFAULTS, Confidence, Detector, SignalKind, Tool

DEFAULT_RULES_PATH = Path(__file__).resolve().parent.parent.parent / "fixtures" / "detection_rules.yaml"
DEFAULT_SAMPLES_PATH = Path(__file__).resolve().parent.parent.parent / "fixtures" / "detection_samples.yaml"


class Provenance(enum.StrEnum):
    """Where a rule's pattern came from, which decides whether it is seeded active."""

    DOCUMENTED = "documented"  # the vendor documents it; `notes` carries the URL
    OBSERVED = "observed"  # seen in real synced data; `notes` carries the PR reference
    UNVERIFIED = "unverified"  # recalled or inferred; seeded inactive until a lead confirms it


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
    provenance: str
    disputed: bool = False

    @property
    def seeds_active(self) -> bool:
        return self.provenance != Provenance.UNVERIFIED


@dataclass(frozen=True)
class RuleSamples:
    """The strings a rule's pattern must match, and the ones it must not.

    A regression lock, not proof: it catches a pattern edited into uselessness or into
    over-matching. It says nothing about whether a vendor emits the string — only `provenance`
    does, and only a human can move a rule to `observed`.
    """

    matches: tuple[str, ...]
    non_matches: tuple[str, ...]


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
        provenance = row.get("provenance")
        disputed = bool(row.get("disputed", False))

        if provenance not in set(Provenance):
            raise RuleDefinitionError(
                f"Rule {name!r} has an unknown provenance: {provenance!r}. Expected one of "
                f"{', '.join(sorted(Provenance))}."
            )
        if detector not in Detector.values:
            raise RuleDefinitionError(f"Rule {name!r} has an unknown detector: {detector!r}.")
        if tool not in Tool.values:
            raise RuleDefinitionError(f"Rule {name!r} has an unknown tool: {tool!r}.")
        if confidence not in Confidence.values:
            raise RuleDefinitionError(f"Rule {name!r} has an unknown confidence: {confidence!r}.")
        if not notes:
            raise RuleDefinitionError(
                f"Rule {name!r} has no notes. State where the pattern comes from: a vendor URL for "
                f"'documented', a repo#number for 'observed', or what is assumed for 'unverified'."
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
                provenance=provenance,
                disputed=disputed,
            )
        )
    return definitions


def load_rule_samples(path: Path = DEFAULT_SAMPLES_PATH) -> dict[str, RuleSamples]:
    """The match / non-match corpus, keyed by rule name. Validation that every rule has an entry
    lives in the test suite, not here: a lead who points `--path` at their own rule file must not
    be blocked by a corpus that only covers the shipped set."""
    with open(path) as handle:
        raw = yaml.safe_load(handle)

    rows = raw.get("samples") if isinstance(raw, dict) else raw
    if not rows:
        raise RuleDefinitionError(f"{path} defines no samples.")

    samples = {}
    for rule_name, entry in rows.items():
        matches = tuple(entry.get("matches") or ())
        non_matches = tuple(entry.get("non_matches") or ())
        if not matches:
            raise RuleDefinitionError(f"Samples for {rule_name!r} list nothing the pattern must match.")
        if not non_matches:
            raise RuleDefinitionError(
                f"Samples for {rule_name!r} list nothing the pattern must reject. A rule that only "
                f"proves what it matches cannot catch an over-broad edit."
            )
        samples[rule_name] = RuleSamples(matches=matches, non_matches=non_matches)
    return samples


DEFAULT_SIGNAL_RULES_PATH = Path(__file__).resolve().parent.parent.parent / "fixtures" / "signal_rules.yaml"


@dataclass(frozen=True)
class SignalRuleDefinition:
    """A seeded `SignalRule` (phase 12, stage 4). No `provenance` here, and no `disputed`: both
    answer questions about a vendor's string, and a structural rule has no string. What it has
    instead is thresholds with no right answer across teams, which is why every one of these seeds
    deactivated regardless of anything in the file."""

    name: str
    kind: str
    params: dict
    tool: str
    confidence: str
    notes: str


def load_signal_rule_definitions(path: Path = DEFAULT_SIGNAL_RULES_PATH) -> list[SignalRuleDefinition]:
    with open(path) as handle:
        raw = yaml.safe_load(handle)

    rows = raw.get("rules") if isinstance(raw, dict) else raw
    if not rows:
        raise RuleDefinitionError(f"{path} defines no rules.")

    definitions = []
    for row in rows:
        name = row.get("name")
        kind = row.get("kind")
        params = row.get("params") or {}
        tool = row.get("tool") or Tool.OTHER
        confidence = row.get("confidence")
        notes = (row.get("notes") or "").strip()

        if kind not in SignalKind.values:
            raise RuleDefinitionError(f"Signal rule {name!r} has an unknown kind: {kind!r}.")
        if tool not in Tool.values:
            raise RuleDefinitionError(f"Signal rule {name!r} has an unknown tool: {tool!r}.")
        if confidence not in Confidence.values:
            raise RuleDefinitionError(f"Signal rule {name!r} has an unknown confidence: {confidence!r}.")
        if confidence == Confidence.HIGH:
            raise RuleDefinitionError(
                f"Signal rule {name!r} ships high confidence. A structural heuristic can never be "
                f"high: only an artefact the tool itself wrote proves AI authorship."
            )
        if not notes:
            raise RuleDefinitionError(
                f"Signal rule {name!r} has no notes. State what it measures and what will trip it "
                f"legitimately, so a lead can judge the thresholds before activating it."
            )
        if not isinstance(params, dict):
            raise RuleDefinitionError(f"Signal rule {name!r} has params that are not a mapping.")
        unknown = sorted(set(params) - set(SIGNAL_PARAM_DEFAULTS[kind]))
        if unknown:
            raise RuleDefinitionError(
                f"Signal rule {name!r} sets parameter(s) its kind does not understand: {', '.join(unknown)}."
            )

        definitions.append(
            SignalRuleDefinition(
                name=name,
                kind=kind,
                params=params,
                tool=tool,
                confidence=confidence,
                notes=notes,
            )
        )
    return definitions
