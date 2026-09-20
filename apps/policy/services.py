from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Min, QuerySet
from django.utils import timezone

from apps.accounts.services import record_audit
from apps.activity.models import PRFile, PullRequest
from apps.ai_detection.services import ai_cohort_statuses as compute_ai_cohort_statuses
from apps.catalog.globs import compile_globs, matches_any
from apps.catalog.services import get_int, get_list
from apps.policy.config import PolicyConfig, load_policy_config
from apps.policy.models import AIPolicy, PolicyViolation, SensitivePathRule
from apps.policy.rules import RULES, Finding, compile_risk_matchers, load_context

logger = logging.getLogger(__name__)

ACTION_TO_STATUS = {
    "acknowledge": PolicyViolation.Status.ACKNOWLEDGED,
    "waive": PolicyViolation.Status.WAIVED,
}
ACTION_TO_AUDIT_CODE = {
    "acknowledge": "policy_violation.acknowledge",
    "waive": "policy_violation.waive",
}


class ViolationResolvedError(Exception):
    """Raised by `apply_status_change` when the target violation is already `resolved`: a
    resolved violation is history (the condition is gone), so judging it is meaningless."""


def details_hash(params: Mapping[str, object]) -> str:
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def current_policy(at: datetime | None = None) -> AIPolicy | None:
    """No production caller yet (evaluation uses `_policy_at` over all loaded versions instead) —
    this is the read-side "what applies right now" helper phase 7/9 call sites (a settings page
    banner, a metrics calculator) will use."""
    moment = at or timezone.now()
    return AIPolicy.objects.filter(effective_from__lte=moment).order_by("-effective_from").first()


def _policy_at(moment: datetime, versions: Sequence[AIPolicy]) -> AIPolicy | None:
    """`versions` must be ordered newest-`effective_from`-first (as `AIPolicy.Meta.ordering`
    already is); picks the newest version already in effect at `moment`, or `None` if none was."""
    for version in versions:
        if version.effective_from <= moment:
            return version
    return None


def match_sensitive_paths(pr: PullRequest, sensitive_rules: Sequence[SensitivePathRule]) -> None:
    """Writes `PRFile.matched_sensitive_rule` for every non-excluded file of `pr`: the first
    applicable rule (global, or scoped to one of the PR's repository's projects) whose glob
    matches, in `sensitive_rules`' own order. Idempotent (same inputs, no write) and self-healing
    (a rule no longer in `sensitive_rules` — deactivated or deleted — clears the mark, because the
    file list is re-read and re-matched from scratch every call, not patched incrementally).

    An `advisory` rule is skipped here on purpose (phase 12, stage 7). Those rules exist only to
    classify risk, and the seeded PLANEKS risk table is broad — `**/migrations/**`, `infra/**`. If
    they took part in this first-match-wins loop they would shadow the rule a lead actually wrote
    to forbid a path, and the forbidden-path violation would silently stop firing."""
    project_ids = {project.pk for project in pr.repository.projects.all()}
    applicable = [
        rule
        for rule in sensitive_rules
        if (rule.project_id is None or rule.project_id in project_ids)
        and rule.ai_mode != SensitivePathRule.AiMode.ADVISORY
    ]
    compiled = [(rule, compile_globs([rule.glob])) for rule in applicable]

    to_update = []
    for pr_file in pr.files.all():
        matched_id = None
        if not pr_file.is_excluded:
            for rule, patterns in compiled:
                if matches_any(pr_file.path, patterns):
                    matched_id = rule.pk
                    break
        if pr_file.matched_sensitive_rule_id != matched_id:
            pr_file.matched_sensitive_rule_id = matched_id
            to_update.append(pr_file)

    if to_update:
        PRFile.objects.bulk_update(to_update, ["matched_sensitive_rule"])


def _disabled_rule_codes(raw: Sequence[str]) -> frozenset[str]:
    valid = set(PolicyViolation.RuleCode.values)
    codes = set()
    for code in raw:
        if code in valid:
            codes.add(code)
        else:
            logger.warning("POLICY_DISABLED_RULES contains unknown rule code %r; ignored.", code)
    return frozenset(codes)


@transaction.atomic
def evaluate_pull_request(
    pull_request_id: int,
    *,
    policy_versions: Sequence[AIPolicy] | None = None,
    sensitive_rules: Sequence[SensitivePathRule] | None = None,
    disabled_rules: frozenset[str] | None = None,
    no_tests_min_lines: int | None = None,
    paths_in_params: int | None = None,
    ai_cohort_statuses: frozenset[str] | None = None,
    config: PolicyConfig | None = None,
    designated_reviewers_by_policy: Mapping[int, frozenset[int]] | None = None,
    risk_matchers: Sequence[tuple[SensitivePathRule, Any]] | None = None,
) -> None:
    """The pipeline's fourth stage: a wanted-vs-existing diff over `PolicyViolation` rows, the
    same shape as `ai_detection.services.detect_pull_request`. May only create an open violation
    or auto-resolve one — it never overwrites a lead's `acknowledged`/`waived` judgement.

    A PR is judged under the policy version in effect **when it was created**, not the newest
    version: otherwise saving a second `AIPolicy` version would push every existing PR's
    `created_at` before the new `effective_from` and auto-resolve the entire open backlog.

    `policy_versions`, `sensitive_rules`, the three settings and `ai_cohort_statuses` are optional
    so a single-PR call keeps loading them itself; `evaluate_pull_requests` loads them once (all
    versions, newest first) and passes them in."""
    if policy_versions is None:
        policy_versions = list(AIPolicy.objects.order_by("-effective_from"))
    if sensitive_rules is None:
        sensitive_rules = list(SensitivePathRule.objects.filter(is_active=True))
    if disabled_rules is None:
        disabled_rules = _disabled_rule_codes(get_list("POLICY_DISABLED_RULES"))
    if no_tests_min_lines is None:
        no_tests_min_lines = get_int("NO_TESTS_MIN_LINES")
    if paths_in_params is None:
        paths_in_params = get_int("POLICY_VIOLATION_PATHS_IN_PARAMS")
    if ai_cohort_statuses is None:
        ai_cohort_statuses = compute_ai_cohort_statuses()
    if config is None:
        config = load_policy_config()
    if risk_matchers is None:
        risk_matchers = compile_risk_matchers(sensitive_rules)

    # Sensitive-path matching is bookkeeping independent of any one policy version: it always
    # runs so a deactivated/deleted rule clears PRFile.matched_sensitive_rule on the next pass.
    pr_for_match = (
        PullRequest.objects.select_related("repository")
        .prefetch_related("repository__projects", "files")
        .get(pk=pull_request_id)
    )
    match_sensitive_paths(pr_for_match, sensitive_rules)

    policy = _policy_at(pr_for_match.created_at, policy_versions)

    wanted: dict[tuple[str, str], Finding] = {}
    if policy is not None:
        ctx = load_context(
            pull_request_id,
            policy,
            sensitive_rules=sensitive_rules,
            no_tests_min_lines=no_tests_min_lines,
            paths_in_params=paths_in_params,
            ai_cohort_statuses=ai_cohort_statuses,
            config=config,
            designated_reviewer_person_ids=(
                designated_reviewers_by_policy.get(policy.pk)
                if designated_reviewers_by_policy is not None
                else None
            ),
            risk_matchers=risk_matchers,
        )
        for rule_code, evaluator in RULES.items():
            if rule_code in disabled_rules:
                continue
            for finding in evaluator(ctx):
                key = (finding.rule_code, details_hash(finding.identity_params))
                wanted.setdefault(key, finding)

    existing = {
        (violation.rule_code, violation.details_hash): violation
        for violation in PolicyViolation.objects.filter(pull_request_id=pull_request_id)
    }

    to_create = [
        PolicyViolation(
            pull_request_id=pull_request_id,
            rule_code=finding.rule_code,
            severity=finding.severity,
            details_params=finding.details_params,
            details_hash=key[1],
            status=PolicyViolation.Status.OPEN,
        )
        for key, finding in wanted.items()
        if key not in existing
    ]
    if to_create:
        PolicyViolation.objects.bulk_create(to_create)

    to_refresh = []
    for key, finding in wanted.items():
        violation = existing.get(key)
        if violation is not None and violation.details_params != finding.details_params:
            violation.details_params = finding.details_params
            to_refresh.append(violation)
    if to_refresh:
        PolicyViolation.objects.bulk_update(to_refresh, ["details_params"])

    to_resolve = [
        violation
        for key, violation in existing.items()
        if key not in wanted and violation.status == PolicyViolation.Status.OPEN
    ]
    if to_resolve:
        for violation in to_resolve:
            violation.status = PolicyViolation.Status.RESOLVED
            violation.resolved_automatically = True
        PolicyViolation.objects.bulk_update(to_resolve, ["status", "resolved_automatically"])
        for violation in to_resolve:
            record_audit(
                None,
                "policy_violation.auto_resolve",
                violation,
                before={"status": PolicyViolation.Status.OPEN},
                after={"status": PolicyViolation.Status.RESOLVED, "resolved_automatically": True},
            )


def evaluate_pull_requests(queryset: QuerySet[PullRequest]) -> int:
    """Bulk entry point for `manage.py recompute`: loads every policy version, the active
    sensitive rules, the three settings and the AI cohort statuses once, not once per PR (RISKS
    row 10). Each PR is still judged under the version in effect at its own `created_at` (see
    `evaluate_pull_request`)."""
    policy_versions = list(
        AIPolicy.objects.order_by("-effective_from").prefetch_related("designated_reviewers")
    )
    sensitive_rules = list(SensitivePathRule.objects.filter(is_active=True))
    disabled_rules = _disabled_rule_codes(get_list("POLICY_DISABLED_RULES"))
    no_tests_min_lines = get_int("NO_TESTS_MIN_LINES")
    paths_in_params = get_int("POLICY_VIOLATION_PATHS_IN_PARAMS")
    ai_cohort_statuses = compute_ai_cohort_statuses()
    config = load_policy_config()
    risk_matchers = compile_risk_matchers(sensitive_rules)
    # Per policy *version*, not per pull request: each PR is judged under the version in effect at
    # its own `created_at`, and a version's designated reviewers are the same for every PR it
    # governs.
    designated_reviewers_by_policy = {
        version.pk: frozenset(person.pk for person in version.designated_reviewers.all())
        for version in policy_versions
    }

    count = 0
    for pk in queryset.values_list("pk", flat=True):
        evaluate_pull_request(
            pk,
            policy_versions=policy_versions,
            sensitive_rules=sensitive_rules,
            disabled_rules=disabled_rules,
            no_tests_min_lines=no_tests_min_lines,
            paths_in_params=paths_in_params,
            ai_cohort_statuses=ai_cohort_statuses,
            config=config,
            designated_reviewers_by_policy=designated_reviewers_by_policy,
            risk_matchers=risk_matchers,
        )
        count += 1
    return count


def apply_status_change(user: User, violation: PolicyViolation, action: str, comment: str) -> None:
    """A lead's judgement on one violation: `open -> acknowledged|waived` or a switch between the
    two. Refuses a `resolved` row (`ViolationResolvedError`) instead of silently doing nothing —
    a resolved violation's condition is gone, so a lead judging it would be meaningless. Writes
    exactly one `AuditEntry` with who, when (`created_at`) and the before/after status."""
    if violation.status == PolicyViolation.Status.RESOLVED:
        raise ViolationResolvedError(violation.pk)

    before_status = violation.status
    violation.status = ACTION_TO_STATUS[action]
    violation.resolved_by = user
    violation.resolution_comment = comment
    violation.resolved_automatically = False
    violation.save(update_fields=["status", "resolved_by", "resolution_comment", "resolved_automatically"])
    record_audit(
        user,
        ACTION_TO_AUDIT_CODE[action],
        violation,
        before={"status": before_status},
        after={"status": violation.status, "comment": comment},
    )


# The fields a policy version carries beyond the spec's original six (phase 12, stage 7). Listed
# once, so the form, the snapshot written to the audit trail and the new version all stay in step:
# a field added to the model and forgotten here would silently never be saved.
STANDARDS_BOOLEAN_FIELDS: tuple[str, ...] = (
    "require_ai_review_first",
    "forbid_ai_only_approval",
    "require_ai_comments_resolved",
    "require_risk_level",
    "require_task_link",
    "require_verification_note",
    "require_high_risk_plan",
    "forbid_ci_bypass",
    "forbid_test_weakening",
    "forbid_secret_artifacts",
    "forbid_scope_creep",
    "forbid_rubber_stamp_approval",
    "flag_agent_config_changes",
    "flag_new_dependencies_in_ai_prs",
)

STANDARDS_VALUE_FIELDS: tuple[str, ...] = (
    "high_risk_min_approvals",
    "max_effective_lines_by_risk",
    "ai_reviewer_identities",
)


def _policy_snapshot(policy: AIPolicy) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "allowed_tools": list(policy.allowed_tools or []),
        "require_disclosure": policy.require_disclosure,
        "require_human_approval": policy.require_human_approval,
        "min_human_approvals": policy.min_human_approvals,
        "require_tests_for_ai_prs": policy.require_tests_for_ai_prs,
        "ai_pr_max_effective_lines": policy.ai_pr_max_effective_lines,
        "effective_from": policy.effective_from.isoformat(),
    }
    for name in STANDARDS_BOOLEAN_FIELDS:
        snapshot[name] = getattr(policy, name)
    snapshot["high_risk_min_approvals"] = policy.high_risk_min_approvals
    snapshot["max_effective_lines_by_risk"] = dict(policy.max_effective_lines_by_risk or {})
    snapshot["ai_reviewer_identities"] = list(policy.ai_reviewer_identities or [])
    snapshot["designated_reviewers"] = sorted(person.pk for person in policy.designated_reviewers.all())
    return snapshot


def save_policy_version(user: User, cleaned_data: Mapping[str, Any]) -> AIPolicy:
    """Writes a new `AIPolicy` row rather than editing one in place, so policy history stays
    append-only. An admin cannot backdate a version over already-judged PRs or schedule one for
    the future with no marker in the history list — except for the very first version ever saved,
    which is stamped at the earliest synced PR's `created_at` (or `now()` if there are no PRs yet)
    instead of `now()`: `evaluate_pull_request` judges a PR under the version in effect at its own
    `created_at`, so a plain `now()` stamp on the first save would mean the first policy a lead
    ever configures governs no pull request already in the database. Every subsequent version is
    still stamped at `now()`. Writes one `AuditEntry(action="ai_policy.update")` with the previous
    version's field values as `before` (empty if this is the first version ever saved)."""
    previous = AIPolicy.objects.order_by("-effective_from").first()
    if previous is None:
        earliest_created_at = PullRequest.objects.aggregate(Min("created_at"))["created_at__min"]
        effective_from = earliest_created_at or timezone.now()
    else:
        effective_from = timezone.now()
    before = _policy_snapshot(previous) if previous is not None else {}
    policy = AIPolicy.objects.create(
        allowed_tools=list(cleaned_data["allowed_tools"]),
        require_disclosure=cleaned_data["require_disclosure"],
        require_human_approval=cleaned_data["require_human_approval"],
        min_human_approvals=cleaned_data["min_human_approvals"],
        require_tests_for_ai_prs=cleaned_data["require_tests_for_ai_prs"],
        ai_pr_max_effective_lines=cleaned_data.get("ai_pr_max_effective_lines"),
        effective_from=effective_from,
        **{name: bool(cleaned_data.get(name, False)) for name in STANDARDS_BOOLEAN_FIELDS},
        high_risk_min_approvals=cleaned_data.get("high_risk_min_approvals") or 2,
        max_effective_lines_by_risk=cleaned_data.get("max_effective_lines_by_risk") or {},
        ai_reviewer_identities=list(cleaned_data.get("ai_reviewer_identities") or []),
    )
    # A version's designated reviewers are set after the row exists (a many-to-many needs a pk) and
    # copied forward from the form, so a new version never silently loses them.
    policy.designated_reviewers.set(cleaned_data.get("designated_reviewers") or [])
    record_audit(
        user,
        "ai_policy.update",
        policy,
        before=before,
        after=_policy_snapshot(policy),
    )
    return policy


@dataclass(frozen=True)
class BulkStatusChangeResult:
    updated: int
    skipped_resolved: int


def apply_bulk_status_change(
    user: User, violations: Iterable[PolicyViolation], action: str, comment: str
) -> BulkStatusChangeResult:
    """Applies `apply_status_change` to every violation, counting rather than aborting on a
    resolved one, so a lead's bulk selection is reported, not silently truncated."""
    updated = 0
    skipped_resolved = 0
    for violation in violations:
        try:
            apply_status_change(user, violation, action, comment)
        except ViolationResolvedError:
            skipped_resolved += 1
        else:
            updated += 1
    return BulkStatusChangeResult(updated=updated, skipped_resolved=skipped_resolved)
