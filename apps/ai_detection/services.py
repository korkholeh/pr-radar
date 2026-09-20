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
from datetime import date
from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.db.models import QuerySet

from apps.activity.models import AIDisclosure, AIStatus, PullRequest
from apps.ai_detection.baselines import load_author_baselines, run_baseline_kind
from apps.ai_detection.detectors import DETECTORS, load_context, load_contexts
from apps.ai_detection.disclosure import DisclosureConfig, load_config, parse_disclosure
from apps.ai_detection.models import (
    AISignal,
    Confidence,
    DetectionRule,
    SignalFamily,
    SignalRule,
    kinds_in_family,
)
from apps.ai_detection.structural import load_structural_context, run_kind
from apps.catalog.services import get_bool, get_int

if TYPE_CHECKING:
    from apps.accounts.selectors import ScopeFilter

logger = logging.getLogger(__name__)

PER_PR_KINDS = kinds_in_family(SignalFamily.PER_PR)
BASELINE_KINDS = kinds_in_family(SignalFamily.BASELINE)


def _evidence_hash(evidence: str) -> str:
    return hashlib.sha256(evidence.encode()).hexdigest()


def structural_evidence_hash(code: str, params: dict[str, Any]) -> str:
    """The identity of a structural signal is its code plus the exact parameters that produced it,
    thresholds included. Canonical JSON with sorted keys, so re-running over unchanged data hashes
    the same and writes nothing — and so raising a threshold retires the old signal and writes a
    new one instead of silently leaving a stale sentence on the page."""
    canonical = json.dumps({"code": code, "params": params}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _signal_kind(signal: AISignal) -> str | None:
    """The `SignalKind` a stored signal came from, or None for a regex signal. `signal_rule` is
    prefetched by `detectors._prefetch_for_detection`, so this costs no query."""
    return signal.signal_rule.kind if signal.signal_rule is not None else None


def _stored_baseline_kinds(pull_request: PullRequest) -> set[str]:
    kinds = {_signal_kind(signal) for signal in pull_request.ai_signals.all()}
    return {kind for kind in kinds if kind in BASELINE_KINDS}


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
        signal_rules = list(SignalRule.objects.filter(is_active=True, kind__in=PER_PR_KINDS))
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

    # Only the families this function computes. A baseline signal is written by the nightly
    # `run_baselines()` from an author's whole history, so treating one as "stale" here — it is
    # not in this pull request's wanted set, and never could be — would delete it on every sync.
    existing = {
        (signal.rule_id, signal.signal_rule_id, signal.evidence_hash): signal
        for signal in ctx.pull_request.ai_signals.all()
        if signal.rule_id is not None or _signal_kind(signal) in PER_PR_KINDS
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
    # Baseline signals were written by a different pass and are not in `wanted`, but they are
    # evidence about this pull request all the same, so they count toward the distinct-kind
    # threshold. Read from the stored rows rather than recomputed here.
    structural_kinds |= _stored_baseline_kinds(ctx.pull_request)
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


# --- the baseline family (phase 12, stage 5) ----------------------------------------------------


@dataclass(frozen=True)
class BaselineRunResult:
    authors: int
    created: int
    deleted: int
    pull_requests_restatused: int


@transaction.atomic
def run_baselines(reference: date | None = None) -> BaselineRunResult:
    """Recomputes every active baseline rule over the rolling window and reconciles the stored
    baseline signals against the result.

    Runs whole-window rather than per pull request because a baseline verdict changes when *other*
    pull requests arrive: an author who looked unusual in March may look ordinary once April's
    work lands, and the old signal has to disappear on its own. That is why this is a nightly job
    and not part of the sync pipeline.

    It touches only the baseline family. The per-PR signals the sync just wrote, and the regex
    signals, are left exactly as they are — each of the three writers reconciles its own family.
    """
    rules = list(SignalRule.objects.filter(is_active=True, kind__in=BASELINE_KINDS))
    existing = {
        (signal.pull_request_id, signal.signal_rule_id, signal.evidence_hash): signal
        for signal in AISignal.objects.filter(signal_rule__kind__in=BASELINE_KINDS).select_related(
            "signal_rule"
        )
    }
    if not rules:
        # Every baseline rule switched off means every baseline signal is stale. Deleting them is
        # the same promise `detect_pull_request` makes for its own families: deactivating a rule
        # removes its evidence on the next run rather than leaving it on the page forever.
        return _finish_baseline_run(authors=0, wanted={}, existing=existing)

    # The window is the widest any active rule asks for, so one load serves them all; each rule
    # still reads only as far back as its own `window_weeks`.
    window_weeks = max(float(rule.effective_params().get("window_weeks") or 8) for rule in rules)
    sustained_weeks = max(float(rule.effective_params().get("sustained_weeks") or 2) for rule in rules)
    authors = load_author_baselines(
        window_weeks=window_weeks, sustained_weeks=sustained_weeks, reference=reference
    )

    wanted: dict[tuple[int, int, str], dict[str, Any]] = {}
    for rule in rules:
        params = rule.effective_params()
        for author in authors:
            for match in run_baseline_kind(rule.kind, params, author):
                evidence_hash = structural_evidence_hash(match.code, match.params)
                wanted.setdefault(
                    (match.pull_request_id, rule.pk, evidence_hash),
                    {
                        "pull_request_id": match.pull_request_id,
                        "signal_rule": rule,
                        "tool": rule.tool,
                        "confidence": rule.confidence,
                        "evidence_code": match.code,
                        "evidence_params": match.params,
                    },
                )

    return _finish_baseline_run(authors=len(authors), wanted=wanted, existing=existing)


def _finish_baseline_run(
    *,
    authors: int,
    wanted: dict[tuple[int, int, str], dict[str, Any]],
    existing: dict[tuple[int, int | None, str], AISignal],
) -> BaselineRunResult:
    to_create = [
        AISignal(
            pull_request_id=payload["pull_request_id"],
            signal_rule=payload["signal_rule"],
            tool=payload["tool"],
            confidence=payload["confidence"],
            evidence="",
            evidence_code=payload["evidence_code"],
            evidence_params=payload["evidence_params"],
            evidence_hash=key[2],
        )
        for key, payload in wanted.items()
        if key not in existing
    ]
    if to_create:
        AISignal.objects.bulk_create(to_create)

    stale = [signal for key, signal in existing.items() if key not in wanted]
    if stale:
        AISignal.objects.filter(pk__in=[signal.pk for signal in stale]).delete()

    touched = (
        {payload["pull_request_id"] for payload in wanted.values()}
        | {signal.pull_request_id for signal in stale}
        | {signal.pull_request_id for signal in to_create}
    )
    restatused = _restatus_pull_requests(touched)
    return BaselineRunResult(
        authors=authors,
        created=len(to_create),
        deleted=len(stale),
        pull_requests_restatused=restatused,
    )


def _restatus_pull_requests(pull_request_ids: Collection[int]) -> int:
    """Re-resolves `ai_status` from the stored signals of *both* families, for the pull requests a
    baseline run added evidence to or took it away from.

    Cheaper and safer than re-running `detect_pull_request` on each: the regex and per-PR results
    are already on disk and have not changed, so recomputing them would burn the work and risk a
    different answer if a rule was edited in between.
    """
    if not pull_request_ids:
        return 0

    config = load_config()
    changed = 0
    pull_requests = PullRequest.objects.filter(pk__in=list(pull_request_ids)).prefetch_related(
        "ai_signals__signal_rule"
    )
    for pull_request in pull_requests:
        signals = list(pull_request.ai_signals.all())
        regex_confidences = {signal.confidence for signal in signals if signal.rule_id is not None}
        structural_kinds = {kind for kind in (_signal_kind(signal) for signal in signals) if kind is not None}
        disclosure = parse_disclosure(pull_request.body, config).disclosure
        resolved = resolve_ai_status(regex_confidences, disclosure, structural_kinds)
        if resolved != pull_request.ai_status:
            pull_request.ai_status = resolved
            pull_request.save(update_fields=["ai_status"])
            changed += 1
    return changed
