"""The nine policy rules (spec §7), pure: an evaluator reads a `PolicyContext` and yields
`Finding`s, it never writes. `apps.policy.services` owns loading, diffing against stored
`PolicyViolation` rows and writing; this module only decides what *should* exist right now."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from apps.activity.models import AIDisclosure, PRFile, PullRequest, Review
from apps.ai_detection.models import Confidence
from apps.ai_detection.services import ai_cohort_statuses as compute_ai_cohort_statuses
from apps.catalog.services import get_int
from apps.policy.models import AIPolicy, PolicyViolation, SensitivePathRule

RuleCode = PolicyViolation.RuleCode


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
}

MERGE_DEPENDENT: frozenset[str] = frozenset({RuleCode.NO_HUMAN_APPROVAL, RuleCode.SELF_MERGE})

AI_ONLY: frozenset[str] = frozenset(
    {
        RuleCode.SENSITIVE_PATH_FORBIDDEN,
        RuleCode.SENSITIVE_PATH_REVIEW,
        RuleCode.NO_HUMAN_APPROVAL,
        RuleCode.SELF_MERGE,
        RuleCode.NO_TESTS,
        RuleCode.AI_PR_TOO_LARGE,
    }
)

PARAM_SCHEMA: dict[str, frozenset[str]] = {
    RuleCode.DISCLOSURE_MISSING: frozenset(),
    RuleCode.DISCLOSURE_MISMATCH: frozenset(),
    RuleCode.TOOL_NOT_ALLOWED: frozenset({"tool"}),
    RuleCode.SENSITIVE_PATH_FORBIDDEN: frozenset({"sensitive_rule_id", "paths", "path_count"}),
    RuleCode.SENSITIVE_PATH_REVIEW: frozenset(
        {"sensitive_rule_id", "paths", "path_count", "approvals", "required"}
    ),
    RuleCode.NO_HUMAN_APPROVAL: frozenset({"approvals", "required"}),
    RuleCode.SELF_MERGE: frozenset(),
    RuleCode.NO_TESTS: frozenset({"non_test_lines", "threshold"}),
    RuleCode.AI_PR_TOO_LARGE: frozenset({"effective_lines", "limit"}),
}


def _is_merged(pr: PullRequest) -> bool:
    return pr.state == PullRequest.State.MERGED and pr.merged_at is not None


def _disclosure_missing(ctx: PolicyContext) -> Iterable[Finding]:
    if not ctx.policy.require_disclosure:
        return
    if ctx.pull_request.ai_disclosure in (AIDisclosure.MISSING, AIDisclosure.AMBIGUOUS):
        yield Finding(RuleCode.DISCLOSURE_MISSING, SEVERITY[RuleCode.DISCLOSURE_MISSING])


def _disclosure_mismatch(ctx: PolicyContext) -> Iterable[Finding]:
    if ctx.pull_request.ai_disclosure == AIDisclosure.NONE and Confidence.HIGH in ctx.signal_confidences:
        yield Finding(RuleCode.DISCLOSURE_MISMATCH, SEVERITY[RuleCode.DISCLOSURE_MISMATCH])


def _tool_not_allowed(ctx: PolicyContext) -> Iterable[Finding]:
    allowed = set(ctx.policy.allowed_tools or [])
    if not allowed:
        return
    tools = set(ctx.pull_request.ai_tools or []) | set(ctx.signal_tools)
    for tool in sorted(tools):
        if tool not in allowed:
            yield Finding(
                RuleCode.TOOL_NOT_ALLOWED,
                SEVERITY[RuleCode.TOOL_NOT_ALLOWED],
                details_params={"tool": tool},
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
    required = ctx.policy.min_human_approvals + 1
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
    required = ctx.policy.min_human_approvals
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


def _ai_pr_too_large(ctx: PolicyContext) -> Iterable[Finding]:
    if not ctx.is_ai:
        return
    limit = ctx.policy.ai_pr_max_effective_lines
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
            details_params={"effective_lines": total, "limit": limit},
        )


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


def load_context(
    pull_request_id: int,
    policy: AIPolicy,
    *,
    sensitive_rules: Sequence[SensitivePathRule] | None = None,
    no_tests_min_lines: int | None = None,
    paths_in_params: int | None = None,
    ai_cohort_statuses: frozenset[str] | None = None,
) -> PolicyContext:
    """Loads a PR with everything the nine evaluators read, in a fixed number of queries
    regardless of how many files/reviews/signals it has. `sensitive_rules`, the two settings and
    `ai_cohort_statuses` are optional so a single-PR call keeps loading them itself, but the bulk
    entry point in `services.py` loads them once and passes them in — `ai_cohort_statuses` in
    particular reads an `AppSetting` (`AI_COHORT_INCLUDE_SUSPECTED`), so leaving it unset would
    cost one query per PR in a batch."""
    pr = (
        PullRequest.objects.select_related("author__person")
        .prefetch_related(
            "files__matched_sensitive_rule",
            "reviews__reviewer__person",
            "ai_signals",
            "repository__projects",
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

    project_ids = {project.pk for project in pr.repository.projects.all()}
    applicable_rules = tuple(
        rule for rule in sensitive_rules if rule.project_id is None or rule.project_id in project_ids
    )

    reviews = tuple(pr.reviews.all())
    signals = tuple(pr.ai_signals.all())

    return PolicyContext(
        pull_request=pr,
        policy=policy,
        files=tuple(pr.files.all()),
        reviews=reviews,
        signal_confidences=frozenset(signal.confidence for signal in signals),
        signal_tools=frozenset(signal.tool for signal in signals),
        sensitive_rules=applicable_rules,
        is_ai=pr.ai_status in ai_cohort_statuses,
        human_approver_person_ids=_human_approver_person_ids(pr, reviews),
        no_tests_min_lines=no_tests_min_lines,
        paths_in_params=paths_in_params,
    )
