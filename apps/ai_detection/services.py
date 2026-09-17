"""AI detection: turning stored `DetectionRule`s and a PR's stored rows into `AISignal` rows and
a resolved `ai_status` (spec §6). `detect_pull_request` is the pipeline's third step, the same
shape as `activity.derive.derive_pull_request` — a wanted-vs-existing diff over stored signals, so
re-running it is idempotent by construction."""

from __future__ import annotations

import hashlib
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
from apps.ai_detection.models import AISignal, Confidence, DetectionRule
from apps.catalog.services import get_bool

if TYPE_CHECKING:
    from apps.accounts.selectors import ScopeFilter

logger = logging.getLogger(__name__)


def _evidence_hash(evidence: str) -> str:
    return hashlib.sha256(evidence.encode()).hexdigest()


def resolve_ai_status(confidences: Collection[str], disclosure: str) -> str:
    if Confidence.HIGH in confidences:
        return AIStatus.AI_EXPLICIT
    if disclosure in (AIDisclosure.PARTIAL, AIDisclosure.SUBSTANTIAL):
        return AIStatus.AI_DISCLOSED
    if confidences:
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
) -> None:
    """Loads a PR with its dependents, matches every active rule, writes/deletes `AISignal` rows
    so stored signals equal the current wanted set exactly, then resolves the three `ai_*`
    fields. A second call over unchanged inputs writes the same rows and the same fields.

    `rules` and `config` are the compiled active rules and the disclosure config: optional so a
    single-PR call keeps loading them itself, but `detect_pull_requests` loads them once and
    passes them in, instead of re-running 1 rules query + 6 settings queries per PR."""
    ctx = load_context(pull_request_id)
    if rules is None:
        rules = _compile_active_rule_patterns(list(DetectionRule.objects.filter(is_active=True)))
    if config is None:
        config = load_config()

    wanted: dict[tuple[int, str], dict[str, Any]] = {}
    for rule, pattern in rules:
        detector = DETECTORS[rule.detector]
        for match in detector(pattern, ctx):
            evidence_hash = _evidence_hash(match.evidence)
            # setdefault, not assignment: when two commits carry identical evidence, the signal
            # must point at the first commit in ctx's deterministic (committed_at, sha) order.
            wanted.setdefault(
                (rule.pk, evidence_hash),
                {
                    "rule": rule,
                    "tool": rule.tool,
                    "confidence": rule.confidence,
                    "evidence": match.evidence,
                    "commit_id": match.commit_id,
                },
            )

    existing = {
        (signal.rule_id, signal.evidence_hash): signal for signal in ctx.pull_request.ai_signals.all()
    }

    to_create = [
        AISignal(
            pull_request=ctx.pull_request,
            commit_id=payload["commit_id"],
            rule=payload["rule"],
            tool=payload["tool"],
            confidence=payload["confidence"],
            evidence=payload["evidence"],
            evidence_hash=key[1],
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
    signal_confidences = {payload["confidence"] for payload in wanted.values()}
    signal_tools = {payload["tool"] for payload in wanted.values()}

    pr = ctx.pull_request
    pr.ai_status = resolve_ai_status(signal_confidences, disclosure_result.disclosure)
    pr.ai_tools = sorted(set(signal_tools) | set(disclosure_result.tools))
    pr.ai_disclosure = disclosure_result.disclosure
    pr.save(update_fields=["ai_status", "ai_tools", "ai_disclosure"])


def detect_pull_requests(queryset: QuerySet[PullRequest]) -> int:
    """Bulk entry point for `manage.py recompute`: re-detects every given PR instead of letting
    it drift after a rule is added, edited or deactivated. Loads the compiled rules and the
    disclosure config once, not once per PR (RISKS row 10)."""
    rules = _compile_active_rule_patterns(list(DetectionRule.objects.filter(is_active=True)))
    config = load_config()
    count = 0
    for pk in queryset.values_list("pk", flat=True):
        detect_pull_request(pk, rules=rules, config=config)
        count += 1
    return count


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
