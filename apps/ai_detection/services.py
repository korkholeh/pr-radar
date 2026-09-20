"""AI detection: turning stored `DetectionRule`s and a PR's stored rows into `AISignal` rows and
a resolved `ai_status` (spec §6). `detect_pull_request` is the pipeline's third step, the same
shape as `activity.derive.derive_pull_request` — a wanted-vs-existing diff over stored signals, so
re-running it is idempotent by construction."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.db.models import QuerySet

from apps.activity.models import AIDisclosure, AIStatus, PullRequest
from apps.ai_detection.detectors import DETECTORS, load_context, load_contexts
from apps.ai_detection.disclosure import DisclosureConfig, load_config, parse_disclosure
from apps.ai_detection.models import AISignal, Confidence, DetectionRule, SignalRule
from apps.ai_detection.structural import load_structural_context, run_kind
from apps.catalog.services import get_bool, get_int

if TYPE_CHECKING:
    from apps.accounts.selectors import ScopeFilter

logger = logging.getLogger(__name__)


def _evidence_hash(evidence: str) -> str:
    return hashlib.sha256(evidence.encode()).hexdigest()


def structural_evidence_hash(code: str, params: dict[str, Any]) -> str:
    """The identity of a structural signal is its code plus the exact parameters that produced it,
    thresholds included. Canonical JSON with sorted keys, so re-running over unchanged data hashes
    the same and writes nothing — and so raising a threshold retires the old signal and writes a
    new one instead of silently leaving a stale sentence on the page."""
    canonical = json.dumps({"code": code, "params": params}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def resolve_ai_status(
    confidences: Collection[str],
    disclosure: str,
    structural_kinds: Collection[str] = (),
) -> str:
    """`confidences` are the *regex* family's confidences; `structural_kinds` are the distinct
    `SignalKind`s the structural family produced.

    The two families are weighed differently on purpose. A regex signal matched something a tool
    wrote, so one of them is enough to suspect AI. A structural signal matched a *shape* — fast,
    bursty, many new files — which an honest developer can produce on any given day, so it takes
    `AI_SUSPECTED_MIN_STRUCTURAL_KINDS` distinct kinds before the status moves, and no number of
    them ever reaches `ai_explicit` (a `CheckConstraint` keeps structural rules below `high`).

    Counted over distinct kinds rather than rows: five commit bursts in one pull request are one
    kind of evidence, not five. A single structural signal is still written, still shown on the
    pull request page and still exported — it is evidence a lead reads, not a verdict.
    """
    if Confidence.HIGH in confidences:
        return AIStatus.AI_EXPLICIT
    if disclosure in (AIDisclosure.PARTIAL, AIDisclosure.SUBSTANTIAL):
        return AIStatus.AI_DISCLOSED
    if confidences:
        return AIStatus.AI_SUSPECTED
    distinct_kinds = len(set(structural_kinds))
    if distinct_kinds and distinct_kinds >= get_int("AI_SUSPECTED_MIN_STRUCTURAL_KINDS"):
        return AIStatus.AI_SUSPECTED
    if disclosure == AIDisclosure.NONE:
        return AIStatus.NO_AI
    return AIStatus.UNKNOWN


def ai_cohort_statuses() -> frozenset[str]:
    statuses = {AIStatus.AI_EXPLICIT, AIStatus.AI_DISCLOSED}
    if get_bool("AI_COHORT_INCLUDE_SUSPECTED"):
        statuses.add(AIStatus.AI_SUSPECTED)
    return frozenset(statuses)


def _compile_active_rule_patterns(rules: list[DetectionRule]) -> list[tuple[DetectionRule, re.Pattern[str]]]:
    compiled = []
    for rule in rules:
        try:
            pattern = re.compile(rule.pattern, re.IGNORECASE | re.MULTILINE)
        except re.error:
            logger.warning("DetectionRule %r has an invalid pattern; skipped.", rule.name)
            continue
        compiled.append((rule, pattern))
    return compiled


@transaction.atomic
def detect_pull_request(
    pull_request_id: int,
    rules: list[tuple[DetectionRule, re.Pattern[str]]] | None = None,
    config: DisclosureConfig | None = None,
    signal_rules: list[SignalRule] | None = None,
) -> None:
    """Loads a PR with its dependents, runs both signal families over it, writes/deletes
    `AISignal` rows so stored signals equal the current wanted set exactly, then resolves the
    three `ai_*` fields. A second call over unchanged inputs writes the same rows and the same
    fields.

    The two families differ in what they produce — a regex rule quotes the text it matched, a
    structural rule emits a code plus the parameters that produced it — but they converge on one
    wanted-vs-existing diff, keyed by `(family, rule pk, evidence hash)`. That is what keeps
    re-running idempotent for both and makes a threshold change retire the old signal.

    `rules`, `signal_rules` and `config` are optional so a single-PR call keeps loading them
    itself, but `detect_pull_requests` loads them once and passes them in, instead of re-running
    2 rule queries + 6 settings queries per PR."""
    ctx = load_context(pull_request_id)
    if rules is None:
        rules = _compile_active_rule_patterns(list(DetectionRule.objects.filter(is_active=True)))
    if signal_rules is None:
        signal_rules = list(SignalRule.objects.filter(is_active=True))
    if config is None:
        config = load_config()

    # key: (detection rule pk or None, signal rule pk or None, evidence hash)
    wanted: dict[tuple[int | None, int | None, str], dict[str, Any]] = {}
    for rule, pattern in rules:
        detector = DETECTORS[rule.detector]
        for match in detector(pattern, ctx):
            evidence_hash = _evidence_hash(match.evidence)
            # setdefault, not assignment: when two commits carry identical evidence, the signal
            # must point at the first commit in ctx's deterministic (committed_at, sha) order.
            wanted.setdefault(
                (rule.pk, None, evidence_hash),
                {
                    "rule": rule,
                    "signal_rule": None,
                    "tool": rule.tool,
                    "confidence": rule.confidence,
                    "evidence": match.evidence,
                    "evidence_code": "",
                    "evidence_params": {},
                    "commit_id": match.commit_id,
                },
            )

    if signal_rules:
        structural_ctx = load_structural_context(ctx.pull_request)
        for signal_rule in signal_rules:
            for structural_match in run_kind(
                signal_rule.kind, signal_rule.effective_params(), structural_ctx
            ):
                evidence_hash = structural_evidence_hash(structural_match.code, structural_match.params)
                wanted.setdefault(
                    (None, signal_rule.pk, evidence_hash),
                    {
                        "rule": None,
                        "signal_rule": signal_rule,
                        "tool": signal_rule.tool,
                        "confidence": signal_rule.confidence,
                        "evidence": "",
                        "evidence_code": structural_match.code,
                        "evidence_params": structural_match.params,
                        "commit_id": None,
                    },
                )

    existing = {
        (signal.rule_id, signal.signal_rule_id, signal.evidence_hash): signal
        for signal in ctx.pull_request.ai_signals.all()
    }

    to_create = [
        AISignal(
            pull_request=ctx.pull_request,
            commit_id=payload["commit_id"],
            rule=payload["rule"],
            signal_rule=payload["signal_rule"],
            tool=payload["tool"],
            confidence=payload["confidence"],
            evidence=payload["evidence"],
            evidence_code=payload["evidence_code"],
            evidence_params=payload["evidence_params"],
            evidence_hash=key[2],
        )
        for key, payload in wanted.items()
        if key not in existing
    ]
    if to_create:
        AISignal.objects.bulk_create(to_create)

    stale_ids = [signal.pk for key, signal in existing.items() if key not in wanted]
    if stale_ids:
        AISignal.objects.filter(pk__in=stale_ids).delete()

    disclosure_result = parse_disclosure(ctx.pull_request.body, config)
    regex_confidences = {payload["confidence"] for payload in wanted.values() if payload["rule"] is not None}
    structural_kinds = {
        payload["signal_rule"].kind for payload in wanted.values() if payload["signal_rule"] is not None
    }
    signal_tools = {payload["tool"] for payload in wanted.values()}

    pr = ctx.pull_request
    pr.ai_status = resolve_ai_status(regex_confidences, disclosure_result.disclosure, structural_kinds)
    pr.ai_tools = sorted(set(signal_tools) | set(disclosure_result.tools))
    pr.ai_disclosure = disclosure_result.disclosure
    pr.save(update_fields=["ai_status", "ai_tools", "ai_disclosure"])


def detect_pull_requests(queryset: QuerySet[PullRequest]) -> int:
    """Bulk entry point for `manage.py recompute`: re-detects every given PR instead of letting
    it drift after a rule is added, edited or deactivated. Loads the compiled rules and the
    disclosure config once, not once per PR (RISKS row 10)."""
    rules = _compile_active_rule_patterns(list(DetectionRule.objects.filter(is_active=True)))
    signal_rules = list(SignalRule.objects.filter(is_active=True))
    config = load_config()
    count = 0
    for pk in queryset.values_list("pk", flat=True):
        detect_pull_request(pk, rules=rules, config=config, signal_rules=signal_rules)
        count += 1
    return count


@dataclass(frozen=True)
class StructuralDryRunMatch:
    pull_request: PullRequest
    evidence_code: str
    evidence_params: dict[str, Any]


def dry_run_signal_rule(
    signal_rule: SignalRule, scope: ScopeFilter, limit: int
) -> list[StructuralDryRunMatch]:
    """Runs a (possibly unsaved) structural rule over the last `limit` PRs in scope, writing
    nothing. Every seeded structural rule ships deactivated precisely so a lead does this first:
    a threshold that is right for one team scaffolds false positives on another."""
    from apps.ai_detection.selectors import recent_pull_requests  # avoid a services<->selectors cycle

    params = signal_rule.effective_params()
    matches: list[StructuralDryRunMatch] = []
    for ctx in load_contexts(recent_pull_requests(scope, limit)):
        structural_ctx = load_structural_context(ctx.pull_request)
        for match in run_kind(signal_rule.kind, params, structural_ctx):
            matches.append(
                StructuralDryRunMatch(
                    pull_request=ctx.pull_request,
                    evidence_code=match.code,
                    evidence_params=match.params,
                )
            )
    return matches


@dataclass(frozen=True)
class DryRunMatch:
    pull_request: PullRequest
    evidence: str
    commit_id: int | None


def dry_run_rule(rule: DetectionRule, scope: ScopeFilter, limit: int) -> list[DryRunMatch]:
    """Tests a (possibly unsaved) rule against the last `limit` PRs in scope. Never writes an
    `AISignal` row — the settings page uses this to preview a pattern before it is saved."""
    from apps.ai_detection.selectors import recent_pull_requests  # avoid a services<->selectors import cycle

    try:
        pattern = re.compile(rule.pattern, re.IGNORECASE | re.MULTILINE)
    except re.error as exc:
        raise ValueError(str(exc)) from exc

    detector = DETECTORS[rule.detector]
    matches: list[DryRunMatch] = []
    for ctx in load_contexts(recent_pull_requests(scope, limit)):
        for match in detector(pattern, ctx):
            matches.append(
                DryRunMatch(pull_request=ctx.pull_request, evidence=match.evidence, commit_id=match.commit_id)
            )
    return matches
