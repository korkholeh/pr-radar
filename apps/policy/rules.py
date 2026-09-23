"""The policy rules (spec §7, extended by phase 12 stage 7), pure: an evaluator reads a
`PolicyContext` and yields `Finding`s, it never writes. `apps.policy.services` owns loading,
diffing against stored `PolicyViolation` rows and writing; this module only decides what *should*
exist right now.

Nine rules came from the spec. Fifteen more turn the PLANEKS AI Engineering Standards into checks,
and every one of them is behind an `AIPolicy` toggle that is off until a lead turns it on — an
upgrade must raise nothing, because a tool that greets its operator with four hundred violations
they never asked for teaches them to ignore the console. `docs/POLICY.md` maps each code to the
standard it comes from.

A rule whose inputs are unavailable is silent, never accusing: a review thread whose resolution
GitHub did not report is not an unanswered one, and a repository whose diff was never analysed is
not one that weakened its tests.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from apps.activity.models import AIDisclosure, CheckStatus, PRFile, PullRequest, Review
from apps.ai_detection import body_sections
from apps.ai_detection.diffsignals import DiffFacts
from apps.ai_detection.disclosure import DisclosureConfig, parse_disclosure
from apps.ai_detection.disclosure import load_config as load_disclosure_config
from apps.ai_detection.models import Confidence, DiffAnalysis, SignalKind, Tool
from apps.ai_detection.services import ai_cohort_statuses as compute_ai_cohort_statuses
from apps.catalog.globs import compile_globs, matches_any
from apps.catalog.services import get_int
from apps.policy.config import PolicyConfig, load_policy_config
from apps.policy.models import AIPolicy, PolicyViolation, SensitivePathRule

RuleCode = PolicyViolation.RuleCode


RISK_ORDER: dict[str, int] = {
    SensitivePathRule.RiskLevel.LOW: 1,
    SensitivePathRule.RiskLevel.MEDIUM: 2,
    SensitivePathRule.RiskLevel.HIGH: 3,
}


@dataclass(frozen=True)
class PolicyContext:
    pull_request: PullRequest
    policy: AIPolicy
    files: tuple[PRFile, ...]
    reviews: tuple[Review, ...]
    signal_confidences: frozenset[str]
    signal_tools: frozenset[str]
    sensitive_rules: tuple[SensitivePathRule, ...]
    is_ai: bool
    human_approver_person_ids: frozenset[int]
    no_tests_min_lines: int
    paths_in_params: int
    # --- phase 12, stage 7 --------------------------------------------------------------------
    config: PolicyConfig = field(default_factory=PolicyConfig)
    commit_messages: tuple[str, ...] = ()
    final_rollup_state: str | None = None
    diff_facts: DiffFacts | None = None
    structural_kinds: frozenset[str] = frozenset()
    risk_level: str | None = None
    risk_paths: tuple[str, ...] = ()
    bot_approver_count: int = 0
    ai_reviewer_logins: frozenset[str] = frozenset()
    ai_review_logins_seen: frozenset[str] = frozenset()
    unresolved_ai_threads: int = 0
    designated_reviewer_person_ids: frozenset[int] = frozenset()
    designated_approver_person_ids: frozenset[int] = frozenset()
    # The tools the author named in the disclosure section, apart from the ones detection found:
    # `PullRequest.ai_tools` is the union of both. `None` (a hand-built context) reads `ai_tools`.
    declared_tools: frozenset[str] | None = None

    @property
    def body(self) -> str:
        return self.pull_request.body or ""

    def changed_paths(self, globs) -> tuple[str, ...]:
        """Non-excluded paths of this pull request matching `globs`, sorted.

        Excluded paths (`EXCLUDED_PATH_GLOBS`) are left out here as everywhere else — except that
        a lockfile is *both* commonly excluded from size metrics and exactly what the
        new-dependency check is about, which is why `POLICY_MANIFEST_PATH_GLOBS` is matched against
        every file instead (see `manifest_paths`).
        """
        return tuple(sorted(f.path for f in self.files if not f.is_excluded and matches_any(f.path, globs)))

    def all_changed_paths(self, globs) -> tuple[str, ...]:
        return tuple(sorted(f.path for f in self.files if matches_any(f.path, globs)))


@dataclass(frozen=True)
class Finding:
    rule_code: str
    severity: str
    details_params: dict[str, Any] = field(default_factory=dict)
    identity_params: dict[str, Any] = field(default_factory=dict)


Evaluator = Callable[[PolicyContext], Iterable[Finding]]

SEVERITY: dict[str, str] = {
    RuleCode.DISCLOSURE_MISSING: PolicyViolation.Severity.MEDIUM,
    RuleCode.DISCLOSURE_MISMATCH: PolicyViolation.Severity.HIGH,
    RuleCode.TOOL_NOT_ALLOWED: PolicyViolation.Severity.HIGH,
    RuleCode.SENSITIVE_PATH_FORBIDDEN: PolicyViolation.Severity.HIGH,
    RuleCode.SENSITIVE_PATH_REVIEW: PolicyViolation.Severity.MEDIUM,
    RuleCode.NO_HUMAN_APPROVAL: PolicyViolation.Severity.HIGH,
    RuleCode.SELF_MERGE: PolicyViolation.Severity.HIGH,
    RuleCode.NO_TESTS: PolicyViolation.Severity.LOW,
    RuleCode.AI_PR_TOO_LARGE: PolicyViolation.Severity.LOW,
    RuleCode.QUALITY_GATE_BYPASSED: PolicyViolation.Severity.HIGH,
    RuleCode.TEST_WEAKENED: PolicyViolation.Severity.HIGH,
    RuleCode.AI_ONLY_APPROVAL: PolicyViolation.Severity.HIGH,
    RuleCode.AI_REVIEW_MISSING: PolicyViolation.Severity.MEDIUM,
    RuleCode.AI_REVIEW_UNRESOLVED: PolicyViolation.Severity.MEDIUM,
    RuleCode.HIGH_RISK_NO_PLAN: PolicyViolation.Severity.HIGH,
    RuleCode.RISK_LEVEL_MISSING: PolicyViolation.Severity.LOW,
    RuleCode.VERIFICATION_MISSING: PolicyViolation.Severity.LOW,
    RuleCode.TASK_LINK_MISSING: PolicyViolation.Severity.LOW,
    RuleCode.NEW_DEPENDENCY_AI: PolicyViolation.Severity.MEDIUM,
    RuleCode.MIGRATION_AI_INSUFFICIENT_REVIEW: PolicyViolation.Severity.HIGH,
    RuleCode.SECRET_ARTIFACT_COMMITTED: PolicyViolation.Severity.HIGH,
    RuleCode.AGENT_CONFIG_CHANGED: PolicyViolation.Severity.LOW,
    RuleCode.SCOPE_CREEP: PolicyViolation.Severity.MEDIUM,
    RuleCode.RUBBER_STAMP_ON_AI_PR: PolicyViolation.Severity.HIGH,
}

# A rule whose answer is only final once the pull request has merged. An open pull request can
# still get its AI review, resolve its threads or fix its failing checks, so accusing it now would
# be reporting a state of affairs rather than a violation.
MERGE_DEPENDENT: frozenset[str] = frozenset(
    {
        RuleCode.NO_HUMAN_APPROVAL,
        RuleCode.SELF_MERGE,
        RuleCode.QUALITY_GATE_BYPASSED,
        RuleCode.AI_ONLY_APPROVAL,
        RuleCode.AI_REVIEW_MISSING,
        RuleCode.AI_REVIEW_UNRESOLVED,
        RuleCode.MIGRATION_AI_INSUFFICIENT_REVIEW,
        RuleCode.RUBBER_STAMP_ON_AI_PR,
    }
)

# A rule that only asks something of an AI-assisted pull request. The rest apply to everybody:
# a bypassed quality gate, a weakened test or a committed credential is no better for having been
# written by hand, and a policy that only checked the AI cohort would be measuring the tool rather
# than the engineering.
AI_ONLY: frozenset[str] = frozenset(
    {
        RuleCode.SENSITIVE_PATH_FORBIDDEN,
        RuleCode.SENSITIVE_PATH_REVIEW,
        RuleCode.NO_HUMAN_APPROVAL,
        RuleCode.SELF_MERGE,
        RuleCode.NO_TESTS,
        RuleCode.AI_PR_TOO_LARGE,
        RuleCode.NEW_DEPENDENCY_AI,
        RuleCode.MIGRATION_AI_INSUFFICIENT_REVIEW,
        RuleCode.SCOPE_CREEP,
        RuleCode.RUBBER_STAMP_ON_AI_PR,
    }
)

PARAM_SCHEMA: dict[str, frozenset[str]] = {
    RuleCode.DISCLOSURE_MISSING: frozenset(),
    RuleCode.DISCLOSURE_MISMATCH: frozenset(),
    RuleCode.TOOL_NOT_ALLOWED: frozenset({"tool", "source"}),
    RuleCode.SENSITIVE_PATH_FORBIDDEN: frozenset({"sensitive_rule_id", "paths", "path_count"}),
    RuleCode.SENSITIVE_PATH_REVIEW: frozenset(
        {"sensitive_rule_id", "paths", "path_count", "approvals", "required"}
    ),
    RuleCode.NO_HUMAN_APPROVAL: frozenset({"approvals", "required"}),
    RuleCode.SELF_MERGE: frozenset(),
    RuleCode.NO_TESTS: frozenset({"non_test_lines", "threshold"}),
    RuleCode.AI_PR_TOO_LARGE: frozenset({"effective_lines", "limit", "risk_level"}),
    RuleCode.QUALITY_GATE_BYPASSED: frozenset({"reason", "state", "marker", "codes", "checks_removed"}),
    RuleCode.TEST_WEAKENED: frozenset({"reason", "paths", "path_count", "markers", "assertions"}),
    RuleCode.AI_ONLY_APPROVAL: frozenset({"bot_approvals"}),
    RuleCode.AI_REVIEW_MISSING: frozenset({"reviewers"}),
    RuleCode.AI_REVIEW_UNRESOLVED: frozenset({"threads"}),
    RuleCode.HIGH_RISK_NO_PLAN: frozenset({"paths", "path_count"}),
    RuleCode.RISK_LEVEL_MISSING: frozenset(),
    RuleCode.VERIFICATION_MISSING: frozenset(),
    RuleCode.TASK_LINK_MISSING: frozenset(),
    RuleCode.NEW_DEPENDENCY_AI: frozenset({"paths", "path_count"}),
    RuleCode.MIGRATION_AI_INSUFFICIENT_REVIEW: frozenset({"paths", "path_count", "reviewers"}),
    RuleCode.SECRET_ARTIFACT_COMMITTED: frozenset({"paths", "path_count"}),
    RuleCode.AGENT_CONFIG_CHANGED: frozenset({"paths", "path_count"}),
    RuleCode.SCOPE_CREEP: frozenset({"reason", "modules", "module_count"}),
    RuleCode.RUBBER_STAMP_ON_AI_PR: frozenset(),
}


def _is_merged(pr: PullRequest) -> bool:
    return pr.state == PullRequest.State.MERGED and pr.merged_at is not None


def _required_approvals(ctx: PolicyContext) -> int:
    """`min_human_approvals`, raised to `high_risk_min_approvals` for a high-risk change.

    Only ever raised, never lowered: a lead who asks for three approvals everywhere does not mean
    two on the riskiest paths.
    """
    required = ctx.policy.min_human_approvals
    if ctx.risk_level == SensitivePathRule.RiskLevel.HIGH:
        required = max(required, ctx.policy.high_risk_min_approvals)
    return required


def _disclosure_missing(ctx: PolicyContext) -> Iterable[Finding]:
    if not ctx.policy.require_disclosure:
        return
    if ctx.pull_request.ai_disclosure in (AIDisclosure.MISSING, AIDisclosure.AMBIGUOUS):
        yield Finding(RuleCode.DISCLOSURE_MISSING, SEVERITY[RuleCode.DISCLOSURE_MISSING])


def _disclosure_mismatch(ctx: PolicyContext) -> Iterable[Finding]:
    if ctx.pull_request.ai_disclosure == AIDisclosure.NONE and Confidence.HIGH in ctx.signal_confidences:
        yield Finding(RuleCode.DISCLOSURE_MISMATCH, SEVERITY[RuleCode.DISCLOSURE_MISMATCH])


# Where a tool named by `TOOL_NOT_ALLOWED` came from, stored so the reader sees why it fired.
TOOL_SOURCE_DECLARED = "declared"
TOOL_SOURCE_DETECTED = "detected"
TOOL_SOURCE_DECLARED_AND_DETECTED = "declared_and_detected"


def _tool_not_allowed(ctx: PolicyContext) -> Iterable[Finding]:
    """A detected `other` is not a tool: behavioural and stylometric signals (a commit burst, mass
    file creation, an agent-config path) say "looks AI-made" without naming anything, and file under
    `other` for want of a name. Only the author can name `other`, by declaring it."""
    allowed = set(ctx.policy.allowed_tools or [])
    if not allowed:
        return
    declared = set(ctx.declared_tools if ctx.declared_tools is not None else ctx.pull_request.ai_tools or [])
    detected = set(ctx.signal_tools) - {Tool.OTHER}
    for tool in sorted(declared | detected):
        if tool in allowed:
            continue
        if tool in declared and tool in detected:
            source = TOOL_SOURCE_DECLARED_AND_DETECTED
        elif tool in declared:
            source = TOOL_SOURCE_DECLARED
        else:
            source = TOOL_SOURCE_DETECTED
        yield Finding(
            RuleCode.TOOL_NOT_ALLOWED,
            SEVERITY[RuleCode.TOOL_NOT_ALLOWED],
            details_params={"tool": tool, "source": source},
            identity_params={"tool": tool},
        )


def _matched_files_by_rule(ctx: PolicyContext, ai_mode: str) -> dict[int, list[str]]:
    rule_ids = {rule.pk for rule in ctx.sensitive_rules if rule.ai_mode == ai_mode}
    by_rule: dict[int, list[str]] = {}
    for pr_file in ctx.files:
        rule_id = pr_file.matched_sensitive_rule_id
        if rule_id in rule_ids:
            by_rule.setdefault(rule_id, []).append(pr_file.path)
    return by_rule


def _sensitive_path_forbidden(ctx: PolicyContext) -> Iterable[Finding]:
    if not ctx.is_ai:
        return
    for rule_id, paths in _matched_files_by_rule(ctx, SensitivePathRule.AiMode.FORBIDDEN).items():
        paths = sorted(paths)
        yield Finding(
            RuleCode.SENSITIVE_PATH_FORBIDDEN,
            SEVERITY[RuleCode.SENSITIVE_PATH_FORBIDDEN],
            details_params={
                "sensitive_rule_id": rule_id,
                "paths": paths[: ctx.paths_in_params],
                "path_count": len(paths),
            },
            identity_params={"sensitive_rule_id": rule_id},
        )


def _sensitive_path_review(ctx: PolicyContext) -> Iterable[Finding]:
    if not ctx.is_ai:
        return
    by_rule = _matched_files_by_rule(ctx, SensitivePathRule.AiMode.NEEDS_EXTRA_REVIEW)
    if not by_rule:
        return
    required = _required_approvals(ctx) + 1
    approvals = len(ctx.human_approver_person_ids)
    if approvals >= required:
        return
    for rule_id, paths in by_rule.items():
        paths = sorted(paths)
        yield Finding(
            RuleCode.SENSITIVE_PATH_REVIEW,
            SEVERITY[RuleCode.SENSITIVE_PATH_REVIEW],
            details_params={
                "sensitive_rule_id": rule_id,
                "paths": paths[: ctx.paths_in_params],
                "path_count": len(paths),
                "approvals": approvals,
                "required": required,
            },
            identity_params={"sensitive_rule_id": rule_id},
        )


def _no_human_approval(ctx: PolicyContext) -> Iterable[Finding]:
    if not ctx.is_ai or not ctx.policy.require_human_approval:
        return
    if not _is_merged(ctx.pull_request):
        return
    required = _required_approvals(ctx)
    approvals = len(ctx.human_approver_person_ids)
    if approvals < required:
        yield Finding(
            RuleCode.NO_HUMAN_APPROVAL,
            SEVERITY[RuleCode.NO_HUMAN_APPROVAL],
            details_params={"approvals": approvals, "required": required},
        )


def _self_merge(ctx: PolicyContext) -> Iterable[Finding]:
    if not ctx.is_ai:
        return
    if not _is_merged(ctx.pull_request):
        return
    if not ctx.pull_request.is_self_merged:
        return
    if len(ctx.human_approver_person_ids) == 0:
        yield Finding(RuleCode.SELF_MERGE, SEVERITY[RuleCode.SELF_MERGE])


def _no_tests(ctx: PolicyContext) -> Iterable[Finding]:
    if not ctx.is_ai or not ctx.policy.require_tests_for_ai_prs:
        return
    if ctx.pull_request.has_test_changes:
        return
    non_test_lines = sum(
        (pr_file.additions or 0) + (pr_file.deletions or 0)
        for pr_file in ctx.files
        if not pr_file.is_test and not pr_file.is_excluded
    )
    if non_test_lines > ctx.no_tests_min_lines:
        yield Finding(
            RuleCode.NO_TESTS,
            SEVERITY[RuleCode.NO_TESTS],
            details_params={"non_test_lines": non_test_lines, "threshold": ctx.no_tests_min_lines},
        )


def _risk_limit(ctx: PolicyContext) -> int | None:
    """The size limit that applies: the risk-level limit when one is configured for this pull
    request's risk, otherwise the flat `ai_pr_max_effective_lines`.

    `max_effective_lines_by_risk` defaults to `{}`, which is why an upgrade raises nothing here:
    the standards' own numbers (800 medium, 400 high) are in `docs/POLICY.md` for a lead to set
    deliberately, not applied to an installation's whole history the moment it upgrades.
    """
    by_risk = ctx.policy.max_effective_lines_by_risk or {}
    if ctx.risk_level and isinstance(by_risk, dict):
        value = by_risk.get(ctx.risk_level)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return ctx.policy.ai_pr_max_effective_lines


def _ai_pr_too_large(ctx: PolicyContext) -> Iterable[Finding]:
    if not ctx.is_ai:
        return
    limit = _risk_limit(ctx)
    if limit is None:
        return
    additions = ctx.pull_request.effective_additions
    deletions = ctx.pull_request.effective_deletions
    if additions is None or deletions is None:
        return
    total = additions + deletions
    if total > limit:
        yield Finding(
            RuleCode.AI_PR_TOO_LARGE,
            SEVERITY[RuleCode.AI_PR_TOO_LARGE],
            details_params={
                "effective_lines": total,
                "limit": limit,
                # Which limit was applied, so the sentence can say "the high-risk limit" rather
                # than leaving a reader to guess why 500 lines was too many here and not there.
                "risk_level": ctx.risk_level or "",
            },
            # The risk level is part of the finding's identity: a pull request that becomes
            # high-risk after a rule change has a different violation, not the same one.
            identity_params={"risk_level": ctx.risk_level or ""},
        )


# --- the PLANEKS standards (phase 12, stage 7) ---------------------------------------------------


def _quality_gate_bypassed(ctx: PolicyContext) -> Iterable[Finding]:
    """Merged with the checks red, with CI told to skip, or with the gate itself relaxed.

    One finding per *reason*, not one per pull request: "merged with failing checks" and "the diff
    turned a check off" are different things a reader acts on differently, and keying them
    separately lets one resolve while the other stands.
    """
    if not ctx.policy.forbid_ci_bypass or not _is_merged(ctx.pull_request):
        return

    state = ctx.final_rollup_state
    if state in (CheckStatus.RollupState.FAILURE, CheckStatus.RollupState.ERROR):
        yield Finding(
            RuleCode.QUALITY_GATE_BYPASSED,
            SEVERITY[RuleCode.QUALITY_GATE_BYPASSED],
            details_params={"reason": "checks_failing", "state": state},
            identity_params={"reason": "checks_failing"},
        )

    markers = [
        marker
        for marker in ctx.config.skip_ci_markers
        if any(marker.casefold() in message.casefold() for message in ctx.commit_messages)
    ]
    if markers:
        yield Finding(
            RuleCode.QUALITY_GATE_BYPASSED,
            SEVERITY[RuleCode.QUALITY_GATE_BYPASSED],
            details_params={"reason": "skip_ci_marker", "marker": markers[0]},
            identity_params={"reason": "skip_ci_marker"},
        )

    facts = ctx.diff_facts
    if facts is None:
        return
    if facts.quality_gate_relaxations:
        yield Finding(
            RuleCode.QUALITY_GATE_BYPASSED,
            SEVERITY[RuleCode.QUALITY_GATE_BYPASSED],
            details_params={"reason": "gate_relaxed", "codes": list(facts.quality_gate_relaxations)},
            identity_params={"reason": "gate_relaxed"},
        )
    if facts.checks_removed:
        yield Finding(
            RuleCode.QUALITY_GATE_BYPASSED,
            SEVERITY[RuleCode.QUALITY_GATE_BYPASSED],
            details_params={"reason": "check_removed", "checks_removed": facts.checks_removed},
            identity_params={"reason": "check_removed"},
        )


def _test_weakened(ctx: PolicyContext) -> Iterable[Finding]:
    """A test deleted, skipped or stripped of its assertions while the code around it changed.

    Deleting a test file on its own is ordinary housekeeping — a module went away, its tests went
    with it. What this reads is a test disappearing *while non-test code changed*, which is the
    shape of a failing test being got out of the way.
    """
    if not ctx.policy.forbid_test_weakening:
        return

    changed_non_test = any(not f.is_test and not f.is_excluded for f in ctx.files)
    removed_tests = sorted(
        f.path for f in ctx.files if f.is_test and (f.status or "").lower() in {"removed", "deleted"}
    )
    if removed_tests and changed_non_test:
        yield Finding(
            RuleCode.TEST_WEAKENED,
            SEVERITY[RuleCode.TEST_WEAKENED],
            details_params={
                "reason": "test_file_removed",
                "paths": removed_tests[: ctx.paths_in_params],
                "path_count": len(removed_tests),
            },
            identity_params={"reason": "test_file_removed"},
        )

    facts = ctx.diff_facts
    if facts is None:
        return
    if facts.skip_markers_added:
        yield Finding(
            RuleCode.TEST_WEAKENED,
            SEVERITY[RuleCode.TEST_WEAKENED],
            details_params={"reason": "skip_marker_added", "markers": facts.skip_markers_added},
            identity_params={"reason": "skip_marker_added"},
        )
    # Assertions removed and none added back: a rewritten test removes and adds, so netting the
    # two is what separates a rewrite from a weakening.
    if facts.assertions_removed and facts.test_lines_added == 0:
        yield Finding(
            RuleCode.TEST_WEAKENED,
            SEVERITY[RuleCode.TEST_WEAKENED],
            details_params={"reason": "assertions_removed", "assertions": facts.assertions_removed},
            identity_params={"reason": "assertions_removed"},
        )


def _ai_only_approval(ctx: PolicyContext) -> Iterable[Finding]:
    """Every approval came from a bot. The standard's point is that a human remains accountable
    for merged code, so an AI reviewer's approval is not an approval for this purpose."""
    if not ctx.policy.forbid_ai_only_approval or not _is_merged(ctx.pull_request):
        return
    if ctx.bot_approver_count == 0 or ctx.human_approver_person_ids:
        return
    yield Finding(
        RuleCode.AI_ONLY_APPROVAL,
        SEVERITY[RuleCode.AI_ONLY_APPROVAL],
        details_params={"bot_approvals": ctx.bot_approver_count},
    )


def _ai_review_missing(ctx: PolicyContext) -> Iterable[Finding]:
    """None of the configured AI reviewers ever looked at it.

    Silent when no AI reviewer is configured: the policy cannot require a reviewer nobody named.
    """
    if not ctx.policy.require_ai_review_first or not ctx.ai_reviewer_logins:
        return
    if not _is_merged(ctx.pull_request):
        return
    if ctx.ai_review_logins_seen:
        return
    yield Finding(
        RuleCode.AI_REVIEW_MISSING,
        SEVERITY[RuleCode.AI_REVIEW_MISSING],
        details_params={"reviewers": sorted(ctx.ai_reviewer_logins)},
    )


def _ai_review_unresolved(ctx: PolicyContext) -> Iterable[Finding]:
    """Merged with an AI reviewer's comment thread still open.

    Counts only threads GitHub reported as unresolved. A thread whose resolution the server did not
    report at all is `None` — unknown — and never counted, because "we could not tell" must not be
    rendered as "the author ignored the reviewer".
    """
    if not ctx.policy.require_ai_comments_resolved or not _is_merged(ctx.pull_request):
        return
    if not ctx.unresolved_ai_threads:
        return
    yield Finding(
        RuleCode.AI_REVIEW_UNRESOLVED,
        SEVERITY[RuleCode.AI_REVIEW_UNRESOLVED],
        details_params={"threads": ctx.unresolved_ai_threads},
    )


def _high_risk_no_plan(ctx: PolicyContext) -> Iterable[Finding]:
    if not ctx.policy.require_high_risk_plan:
        return
    if ctx.risk_level != SensitivePathRule.RiskLevel.HIGH:
        return
    if body_sections.has_section_content(ctx.body, ctx.config.plan_headings):
        return
    paths = list(ctx.risk_paths)
    yield Finding(
        RuleCode.HIGH_RISK_NO_PLAN,
        SEVERITY[RuleCode.HIGH_RISK_NO_PLAN],
        details_params={"paths": paths[: ctx.paths_in_params], "path_count": len(paths)},
    )


def _risk_level_missing(ctx: PolicyContext) -> Iterable[Finding]:
    if not ctx.policy.require_risk_level:
        return
    if body_sections.has_section_content(ctx.body, ctx.config.risk_headings):
        return
    yield Finding(RuleCode.RISK_LEVEL_MISSING, SEVERITY[RuleCode.RISK_LEVEL_MISSING])


def _verification_missing(ctx: PolicyContext) -> Iterable[Finding]:
    if not ctx.policy.require_verification_note:
        return
    if body_sections.has_section_content(ctx.body, ctx.config.verification_headings):
        return
    yield Finding(RuleCode.VERIFICATION_MISSING, SEVERITY[RuleCode.VERIFICATION_MISSING])


def _task_link_missing(ctx: PolicyContext) -> Iterable[Finding]:
    """A task reference in the title or anywhere in the body satisfies this, not only under a
    heading: plenty of teams write "closes #431" in the first line and never fill a section in, and
    plenty more put the key in the title — "[ENG-175] treat a 200 as success" — and nowhere else."""
    if not ctx.policy.require_task_link:
        return
    title = ctx.pull_request.title or ""
    body = body_sections.HTML_COMMENT_RE.sub("", ctx.body)
    if any(pattern.search(text) for text in (title, body) for pattern in ctx.config.task_link_patterns):
        return
    yield Finding(RuleCode.TASK_LINK_MISSING, SEVERITY[RuleCode.TASK_LINK_MISSING])


def _new_dependency_ai(ctx: PolicyContext) -> Iterable[Finding]:
    """An AI pull request that changes a manifest or lockfile. Not a fault — a prompt to check that
    somebody chose the dependency rather than a model reaching for the first package it recalled.

    Matched against every file, including excluded ones: a lockfile is exactly what this is about,
    and `EXCLUDED_PATH_GLOBS` leaves lockfiles out of the *size* metrics for unrelated reasons.
    """
    if not ctx.policy.flag_new_dependencies_in_ai_prs or not ctx.is_ai:
        return
    paths = list(ctx.all_changed_paths(ctx.config.manifest_globs))
    if not paths:
        return
    yield Finding(
        RuleCode.NEW_DEPENDENCY_AI,
        SEVERITY[RuleCode.NEW_DEPENDENCY_AI],
        details_params={"paths": paths[: ctx.paths_in_params], "path_count": len(paths)},
    )


def _migration_ai_insufficient_review(ctx: PolicyContext) -> Iterable[Finding]:
    """An AI pull request carrying a database migration that no designated reviewer approved.

    Silent until a lead names designated reviewers: with none configured there is nobody whose
    approval could satisfy it, and firing anyway would make the check unanswerable.
    """
    if not ctx.designated_reviewer_person_ids or not ctx.is_ai:
        return
    if not _is_merged(ctx.pull_request):
        return
    paths = list(ctx.all_changed_paths(ctx.config.migration_globs))
    if not paths:
        return
    if ctx.designated_approver_person_ids:
        return
    yield Finding(
        RuleCode.MIGRATION_AI_INSUFFICIENT_REVIEW,
        SEVERITY[RuleCode.MIGRATION_AI_INSUFFICIENT_REVIEW],
        details_params={
            "paths": paths[: ctx.paths_in_params],
            "path_count": len(paths),
            "reviewers": len(ctx.designated_reviewer_person_ids),
        },
    )


def _secret_artifact_committed(ctx: PolicyContext) -> Iterable[Finding]:
    """A credential file, a private key or an agent's own chat history in the repository.

    Decided from the path alone, so this works for every repository rather than only the ones
    opted in to diff analysis — and so nothing here ever reads, logs or stores the contents of the
    file it is complaining about.
    """
    if not ctx.policy.forbid_secret_artifacts:
        return
    paths = [
        path
        for path in ctx.all_changed_paths(ctx.config.secret_artifact_globs)
        if not matches_any(path, ctx.config.secret_artifact_exception_globs)
    ]
    if not paths:
        return
    yield Finding(
        RuleCode.SECRET_ARTIFACT_COMMITTED,
        SEVERITY[RuleCode.SECRET_ARTIFACT_COMMITTED],
        details_params={"paths": paths[: ctx.paths_in_params], "path_count": len(paths)},
    )


def _agent_config_changed(ctx: PolicyContext) -> Iterable[Finding]:
    """`CLAUDE.md`, `AGENTS.md`, `.claude/**` and friends changed. Low severity by design: this is
    a "somebody should have read this" marker, not a fault. It changes how every later agent run
    behaves, which is precisely why the standards ask for it to go through normal review."""
    if not ctx.policy.flag_agent_config_changes:
        return
    paths = list(ctx.all_changed_paths(ctx.config.agent_config_globs))
    if not paths:
        return
    yield Finding(
        RuleCode.AGENT_CONFIG_CHANGED,
        SEVERITY[RuleCode.AGENT_CONFIG_CHANGED],
        details_params={"paths": paths[: ctx.paths_in_params], "path_count": len(paths)},
    )


def _top_level_modules(paths: Iterable[str]) -> set[str]:
    modules = set()
    for path in paths:
        head = path.split("/", 1)[0]
        if head:
            modules.add(head)
    return modules


def _scope_creep(ctx: PolicyContext) -> Iterable[Finding]:
    """An AI pull request that did more than it said it would.

    Two shapes, keyed separately. A formatting sweep mixed into a feature change — the
    `wholesale_reformat` signal, which only exists where diff analysis runs. And a pull request
    whose body states its scope while the change reaches into top-level modules that section never
    mentions; with no scope section stated, there is nothing to have exceeded and nothing fires.
    """
    if not ctx.policy.forbid_scope_creep or not ctx.is_ai:
        return

    if SignalKind.WHOLESALE_REFORMAT in ctx.structural_kinds:
        yield Finding(
            RuleCode.SCOPE_CREEP,
            SEVERITY[RuleCode.SCOPE_CREEP],
            details_params={"reason": "reformat_mixed_in"},
            identity_params={"reason": "reformat_mixed_in"},
        )

    stated = body_sections.section_text(ctx.body, ctx.config.scope_headings)
    if not stated:
        return
    stated_folded = stated.casefold()
    touched = _top_level_modules(f.path for f in ctx.files if not f.is_excluded)
    unstated = sorted(module for module in touched if module.casefold() not in stated_folded)
    if not unstated:
        return
    yield Finding(
        RuleCode.SCOPE_CREEP,
        SEVERITY[RuleCode.SCOPE_CREEP],
        details_params={
            "reason": "outside_stated_scope",
            "modules": unstated[: ctx.paths_in_params],
            "module_count": len(unstated),
        },
        identity_params={"reason": "outside_stated_scope"},
    )


def _rubber_stamp_on_ai_pr(ctx: PolicyContext) -> Iterable[Finding]:
    """An AI pull request approved with an empty review, no comments, inside
    `RUBBER_STAMP_MAX_MINUTES`. Promoted from `PullRequest.is_rubber_stamp`, computed in phase 4 —
    the most direct measurement of the manifesto's "we do not accept code we do not understand",
    and it needs no data this project was not already deriving."""
    if not ctx.policy.forbid_rubber_stamp_approval or not ctx.is_ai:
        return
    if not _is_merged(ctx.pull_request) or not ctx.pull_request.is_rubber_stamp:
        return
    yield Finding(RuleCode.RUBBER_STAMP_ON_AI_PR, SEVERITY[RuleCode.RUBBER_STAMP_ON_AI_PR])


RULES: dict[str, Evaluator] = {
    RuleCode.DISCLOSURE_MISSING: _disclosure_missing,
    RuleCode.DISCLOSURE_MISMATCH: _disclosure_mismatch,
    RuleCode.TOOL_NOT_ALLOWED: _tool_not_allowed,
    RuleCode.SENSITIVE_PATH_FORBIDDEN: _sensitive_path_forbidden,
    RuleCode.SENSITIVE_PATH_REVIEW: _sensitive_path_review,
    RuleCode.NO_HUMAN_APPROVAL: _no_human_approval,
    RuleCode.SELF_MERGE: _self_merge,
    RuleCode.NO_TESTS: _no_tests,
    RuleCode.AI_PR_TOO_LARGE: _ai_pr_too_large,
    RuleCode.QUALITY_GATE_BYPASSED: _quality_gate_bypassed,
    RuleCode.TEST_WEAKENED: _test_weakened,
    RuleCode.AI_ONLY_APPROVAL: _ai_only_approval,
    RuleCode.AI_REVIEW_MISSING: _ai_review_missing,
    RuleCode.AI_REVIEW_UNRESOLVED: _ai_review_unresolved,
    RuleCode.HIGH_RISK_NO_PLAN: _high_risk_no_plan,
    RuleCode.RISK_LEVEL_MISSING: _risk_level_missing,
    RuleCode.VERIFICATION_MISSING: _verification_missing,
    RuleCode.TASK_LINK_MISSING: _task_link_missing,
    RuleCode.NEW_DEPENDENCY_AI: _new_dependency_ai,
    RuleCode.MIGRATION_AI_INSUFFICIENT_REVIEW: _migration_ai_insufficient_review,
    RuleCode.SECRET_ARTIFACT_COMMITTED: _secret_artifact_committed,
    RuleCode.AGENT_CONFIG_CHANGED: _agent_config_changed,
    RuleCode.SCOPE_CREEP: _scope_creep,
    RuleCode.RUBBER_STAMP_ON_AI_PR: _rubber_stamp_on_ai_pr,
}


def _human_approver_person_ids(pr: PullRequest, reviews: Sequence[Review]) -> frozenset[int]:
    author_person_id = pr.author.person_id if pr.author_id and pr.author is not None else None
    person_ids = set()
    for review in reviews:
        if review.state != Review.State.APPROVED or review.reviewer is None:
            continue
        person = review.reviewer.person
        if person is None or person.pk == author_person_id or person.is_bot:
            continue
        person_ids.add(person.pk)
    return frozenset(person_ids)


def _bot_approver_count(pr: PullRequest, reviews: Sequence[Review]) -> int:
    """How many distinct bot accounts approved. An AI reviewer's approval is what
    `AI_ONLY_APPROVAL` is about, and the bot flag is where that is already recorded."""
    person_ids = set()
    for review in reviews:
        if review.state != Review.State.APPROVED or review.reviewer is None:
            continue
        person = review.reviewer.person
        if person is not None and person.is_bot:
            person_ids.add(person.pk)
    return len(person_ids)


def _final_rollup_state(pr: PullRequest) -> str | None:
    """The status-check rollup of the commit the pull request ended on.

    The *last* observed, not the worst: a pull request that failed CI at noon and was green at
    five o'clock did not bypass anything, and reading the worst state ever seen would accuse every
    developer who ever pushed a fix. Falls back to the only row when there is one, and to `None`
    when the repository runs no checks at all — a repository without CI cannot bypass it.
    """
    statuses = sorted(
        pr.check_statuses.all(),
        key=lambda status: (status.observed_at is None, status.observed_at, status.pk),
    )
    return statuses[-1].rollup_state if statuses else None


def _risk_of(
    files: Sequence[PRFile], matchers: Sequence[tuple[SensitivePathRule, Any]]
) -> tuple[str | None, tuple[str, ...]]:
    """The pull request's risk level — the maximum over its non-excluded files — and the paths that
    set it.

    The maximum, because a change is as risky as the riskiest thing it touches: a hundred lines of
    documentation plus one line of a payment path is a payment change. Only rules carrying a
    `risk_level` take part; a rule written to forbid a path says nothing about how risky it is.
    """
    level: str | None = None
    paths: list[str] = []
    for pr_file in files:
        if pr_file.is_excluded:
            continue
        for rule, patterns in matchers:
            if not matches_any(pr_file.path, patterns):
                continue
            if level is None or RISK_ORDER.get(rule.risk_level, 0) > RISK_ORDER.get(level, 0):
                level = rule.risk_level
                paths = [pr_file.path]
            elif rule.risk_level == level:
                paths.append(pr_file.path)
    return level, tuple(sorted(set(paths)))


def compile_risk_matchers(
    sensitive_rules: Sequence[SensitivePathRule],
) -> tuple[tuple[SensitivePathRule, Any], ...]:
    """Compiles the globs of the risk-classifying rules once. `services.py` builds this for a whole
    batch and passes it in, so a run over ten thousand pull requests compiles them once; each pull
    request still uses only the rules applicable to its own projects."""
    return tuple((rule, compile_globs([rule.glob])) for rule in sensitive_rules if rule.risk_level)


def load_context(
    pull_request_id: int,
    policy: AIPolicy,
    *,
    sensitive_rules: Sequence[SensitivePathRule] | None = None,
    no_tests_min_lines: int | None = None,
    paths_in_params: int | None = None,
    ai_cohort_statuses: frozenset[str] | None = None,
    config: PolicyConfig | None = None,
    designated_reviewer_person_ids: frozenset[int] | None = None,
    risk_matchers: Sequence[tuple[SensitivePathRule, Any]] | None = None,
    disclosure_config: DisclosureConfig | None = None,
) -> PolicyContext:
    """Loads a PR with everything the evaluators read, in a fixed number of queries regardless of
    how many files/reviews/signals/commits it has. `sensitive_rules`, the settings, the config, the
    AI cohort statuses and the policy's designated reviewers are all optional so a single-PR call
    keeps loading them itself, but the bulk entry point in `services.py` loads them once and passes
    them in — each of them otherwise costs a query per pull request in a batch."""
    pr = (
        PullRequest.objects.select_related("author__person", "diff_analysis")
        .prefetch_related(
            "files__matched_sensitive_rule",
            "reviews__reviewer__person",
            "ai_signals__signal_rule",
            "repository__projects",
            "pull_request_commits__commit",
            "check_statuses",
            "review_comments__author",
        )
        .get(pk=pull_request_id)
    )

    if sensitive_rules is None:
        sensitive_rules = list(SensitivePathRule.objects.filter(is_active=True))
    if no_tests_min_lines is None:
        no_tests_min_lines = get_int("NO_TESTS_MIN_LINES")
    if paths_in_params is None:
        paths_in_params = get_int("POLICY_VIOLATION_PATHS_IN_PARAMS")
    if ai_cohort_statuses is None:
        ai_cohort_statuses = compute_ai_cohort_statuses()
    if config is None:
        config = load_policy_config()
    if designated_reviewer_person_ids is None:
        designated_reviewer_person_ids = frozenset(policy.designated_reviewers.values_list("pk", flat=True))
    if risk_matchers is None:
        risk_matchers = compile_risk_matchers(sensitive_rules)
    if disclosure_config is None:
        disclosure_config = load_disclosure_config()

    project_ids = {project.pk for project in pr.repository.projects.all()}
    applicable_rules = tuple(
        rule for rule in sensitive_rules if rule.project_id is None or rule.project_id in project_ids
    )

    reviews = tuple(pr.reviews.all())
    signals = tuple(pr.ai_signals.all())
    files = tuple(pr.files.all())

    # Logins are compared case-insensitively: GitHub treats `Copilot` and `copilot` as one account,
    # and a lead typing the wrong case into a setting must not silently switch a check off.
    ai_reviewer_logins = frozenset(
        str(login).casefold() for login in (policy.ai_reviewer_identities or []) if login
    )
    ai_review_logins_seen = frozenset(
        review.reviewer.value.casefold()
        for review in reviews
        if review.reviewer is not None and review.reviewer.value.casefold() in ai_reviewer_logins
    )
    unresolved_ai_threads = sum(
        1
        for comment in pr.review_comments.all()
        if comment.is_resolved is False
        and comment.author is not None
        and comment.author.value.casefold() in ai_reviewer_logins
    )

    approver_person_ids = {
        review.reviewer.person_id
        for review in reviews
        if review.state == Review.State.APPROVED
        and review.reviewer is not None
        and review.reviewer.person_id is not None
    }

    applicable_rule_ids = {rule.pk for rule in applicable_rules}
    applicable_matchers = tuple(
        (rule, patterns) for rule, patterns in risk_matchers if rule.pk in applicable_rule_ids
    )
    risk_level, risk_paths = _risk_of(files, applicable_matchers)
    diff_analysis = getattr(pr, "diff_analysis", None)

    return PolicyContext(
        pull_request=pr,
        policy=policy,
        files=files,
        reviews=reviews,
        signal_confidences=frozenset(signal.confidence for signal in signals),
        signal_tools=frozenset(signal.tool for signal in signals),
        declared_tools=frozenset(parse_disclosure(pr.body, disclosure_config).tools),
        sensitive_rules=applicable_rules,
        is_ai=pr.ai_status in ai_cohort_statuses,
        human_approver_person_ids=_human_approver_person_ids(pr, reviews),
        no_tests_min_lines=no_tests_min_lines,
        paths_in_params=paths_in_params,
        config=config,
        commit_messages=tuple(pr_commit.commit.message or "" for pr_commit in pr.pull_request_commits.all()),
        final_rollup_state=_final_rollup_state(pr),
        # Only an `ok` analysis carries facts. A repository that was never opted in, or whose clone
        # could not be read, has `None` here — and every check that reads the diff is silent then,
        # rather than reading "we do not know" as "nothing was wrong".
        diff_facts=(
            DiffFacts.from_dict(diff_analysis.facts)
            if diff_analysis is not None and diff_analysis.status == DiffAnalysis.Status.OK
            else None
        ),
        structural_kinds=frozenset(
            signal.signal_rule.kind for signal in signals if signal.signal_rule is not None
        ),
        risk_level=risk_level,
        risk_paths=risk_paths,
        bot_approver_count=_bot_approver_count(pr, reviews),
        ai_reviewer_logins=ai_reviewer_logins,
        ai_review_logins_seen=ai_review_logins_seen,
        unresolved_ai_threads=unresolved_ai_threads,
        designated_reviewer_person_ids=designated_reviewer_person_ids,
        designated_approver_person_ids=frozenset(approver_person_ids & designated_reviewer_person_ids),
    )
