"""Rendering `(rule_code, details_params)` into a translated sentence at read time (CLAUDE.md:
system-generated text is stored as a code plus params, never a rendered message). Every entry in
`RULE_MESSAGES` is a full sentence with named placeholders; a count-bearing message carries a
`count_key` and a plural form, dispatched through `ngettext`. An unknown `rule_code` renders the
code itself instead of raising, so a stale row can never 500 the console; a missing param renders
`?` in its place instead of raising a `KeyError`. A message whose wording depends on a code-valued
param carries a `variant_key` and one full sentence per value in `variants`; a value it does not
know (or a row stored before the param existed) falls back to `singular`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict

from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop, ngettext

from apps.ai_detection.models import Tool
from apps.policy.models import PolicyViolation

RuleCode = PolicyViolation.RuleCode


class _MessageDef(TypedDict, total=False):
    singular: str
    plural: str
    count_key: str
    variant_key: str
    variants: dict[str, str]


# A param whose value is a *code* rather than a number or a path: the reason a quality gate counts
# as bypassed, the risk level a size limit came from. Stored as the code (CLAUDE.md: never store
# rendered text) and turned into words here, in the reader's language.
ENUM_PARAM_LABELS: dict[str, dict[str, str]] = {
    "reason": {
        "checks_failing": gettext_noop("the checks were not green"),
        "skip_ci_marker": gettext_noop("a commit told CI to skip the run"),
        "gate_relaxed": gettext_noop("the change relaxed the CI configuration"),
        "check_removed": gettext_noop("the change removed a check from CI"),
        "test_file_removed": gettext_noop("a test file was deleted alongside code changes"),
        "skip_marker_added": gettext_noop("a skip or xfail marker was added"),
        "assertions_removed": gettext_noop("assertions were removed and none added back"),
        "reformat_mixed_in": gettext_noop("a formatting sweep was mixed into the change"),
        "outside_stated_scope": gettext_noop("it reaches outside the scope the description states"),
    },
    "risk_level": {
        "low": gettext_noop("low-risk"),
        "medium": gettext_noop("medium-risk"),
        "high": gettext_noop("high-risk"),
    },
}


RULE_MESSAGES: dict[str, _MessageDef] = {
    RuleCode.DISCLOSURE_MISSING: {
        "singular": gettext_noop("This pull request is missing the required AI-assistance disclosure."),
    },
    RuleCode.DISCLOSURE_MISMATCH: {
        "singular": gettext_noop(
            "This pull request discloses no AI assistance, but a high-confidence AI signal was detected."
        ),
    },
    RuleCode.TOOL_NOT_ALLOWED: {
        "singular": gettext_noop("Tool %(tool)s is not in the policy's list of allowed tools."),
        "variant_key": "source",
        "variants": {
            "declared": gettext_noop(
                "The author declared %(tool)s in the description, and it is not in the policy's list "
                "of allowed tools."
            ),
            "detected": gettext_noop(
                "AI detection identified %(tool)s in this pull request, and it is not in the policy's "
                "list of allowed tools."
            ),
            "declared_and_detected": gettext_noop(
                "The author declared %(tool)s and AI detection confirmed it; it is not in the "
                "policy's list of allowed tools."
            ),
        },
    },
    RuleCode.SENSITIVE_PATH_FORBIDDEN: {
        "singular": (
            "This AI pull request touches %(path_count)s file forbidden by a sensitive-path rule: %(paths)s."
        ),
        "plural": (
            "This AI pull request touches %(path_count)s files forbidden by a sensitive-path rule: %(paths)s."
        ),
        "count_key": "path_count",
    },
    RuleCode.SENSITIVE_PATH_REVIEW: {
        "singular": (
            "This AI pull request touches %(path_count)s file needing extra review (%(paths)s) "
            "and has %(approvals)s of %(required)s required human approvals."
        ),
        "plural": (
            "This AI pull request touches %(path_count)s files needing extra review (%(paths)s) "
            "and has %(approvals)s of %(required)s required human approvals."
        ),
        "count_key": "path_count",
    },
    RuleCode.NO_HUMAN_APPROVAL: {
        "singular": (
            "This merged AI pull request has %(approvals)s of %(required)s required human approval."
        ),
        "plural": ("This merged AI pull request has %(approvals)s of %(required)s required human approvals."),
        "count_key": "required",
    },
    RuleCode.SELF_MERGE: {
        "singular": gettext_noop(
            "This merged AI pull request was self-merged with no approval from another human."
        ),
    },
    RuleCode.NO_TESTS: {
        "singular": (
            "Changes %(non_test_lines)s line of non-test code with no test changes (threshold %(threshold)s)."
        ),
        "plural": (
            "Changes %(non_test_lines)s lines of non-test code with no test changes "
            "(threshold %(threshold)s)."
        ),
        "count_key": "non_test_lines",
    },
    RuleCode.AI_PR_TOO_LARGE: {
        "singular": (
            "This AI pull request changes %(effective_lines)s effective line, over the limit of %(limit)s."
        ),
        "plural": (
            "This AI pull request changes %(effective_lines)s effective lines, over the limit of %(limit)s."
        ),
        "count_key": "effective_lines",
    },
    # --- the PLANEKS standards (phase 12, stage 7) -----------------------------------------------
    RuleCode.QUALITY_GATE_BYPASSED: {
        "singular": gettext_noop("This pull request was merged although %(reason)s."),
    },
    RuleCode.TEST_WEAKENED: {
        "singular": gettext_noop("The tests were weakened: %(reason)s."),
    },
    RuleCode.AI_ONLY_APPROVAL: {
        "singular": (
            "This pull request was merged with %(bot_approvals)s approval, all of them from a bot "
            "account and none from a person."
        ),
        "plural": (
            "This pull request was merged with %(bot_approvals)s approvals, all of them from bot "
            "accounts and none from a person."
        ),
        "count_key": "bot_approvals",
    },
    RuleCode.AI_REVIEW_MISSING: {
        "singular": gettext_noop(
            "None of the expected AI reviewers (%(reviewers)s) reviewed this pull request before it merged."
        ),
    },
    RuleCode.AI_REVIEW_UNRESOLVED: {
        "singular": (
            "This pull request merged with %(threads)s comment thread from an AI reviewer still unresolved."
        ),
        "plural": (
            "This pull request merged with %(threads)s comment threads from an AI reviewer still unresolved."
        ),
        "count_key": "threads",
    },
    RuleCode.HIGH_RISK_NO_PLAN: {
        "singular": (
            "This pull request touches %(path_count)s high-risk path (%(paths)s) and its description "
            "states no plan, risks or rollback."
        ),
        "plural": (
            "This pull request touches %(path_count)s high-risk paths (%(paths)s) and its description "
            "states no plan, risks or rollback."
        ),
        "count_key": "path_count",
    },
    RuleCode.RISK_LEVEL_MISSING: {
        "singular": gettext_noop("This pull request's description states no risk level."),
    },
    RuleCode.VERIFICATION_MISSING: {
        "singular": gettext_noop("This pull request's description says nothing about how it was verified."),
    },
    RuleCode.TASK_LINK_MISSING: {
        "singular": gettext_noop("Neither this pull request's title nor its description links a task."),
    },
    RuleCode.NEW_DEPENDENCY_AI: {
        "singular": (
            "This AI pull request changes %(path_count)s dependency manifest (%(paths)s); check that a "
            "person chose the dependency."
        ),
        "plural": (
            "This AI pull request changes %(path_count)s dependency manifests (%(paths)s); check that a "
            "person chose the dependencies."
        ),
        "count_key": "path_count",
    },
    RuleCode.MIGRATION_AI_INSUFFICIENT_REVIEW: {
        "singular": (
            "This AI pull request carries %(path_count)s migration file (%(paths)s) and no designated "
            "reviewer approved it."
        ),
        "plural": (
            "This AI pull request carries %(path_count)s migration files (%(paths)s) and no designated "
            "reviewer approved it."
        ),
        "count_key": "path_count",
    },
    RuleCode.SECRET_ARTIFACT_COMMITTED: {
        "singular": ("This pull request adds %(path_count)s file that should never be committed: %(paths)s."),
        "plural": ("This pull request adds %(path_count)s files that should never be committed: %(paths)s."),
        "count_key": "path_count",
    },
    RuleCode.AGENT_CONFIG_CHANGED: {
        "singular": (
            "This pull request changes %(path_count)s agent-configuration file (%(paths)s), which "
            "changes how later agent runs behave."
        ),
        "plural": (
            "This pull request changes %(path_count)s agent-configuration files (%(paths)s), which "
            "changes how later agent runs behave."
        ),
        "count_key": "path_count",
    },
    RuleCode.SCOPE_CREEP: {
        "singular": gettext_noop("This AI pull request did more than it stated: %(reason)s."),
    },
    RuleCode.RUBBER_STAMP_ON_AI_PR: {
        "singular": gettext_noop(
            "This AI pull request was approved with an empty review, no comments and almost no time "
            "spent reading it."
        ),
    },
}


def _register_plural_forms_for_makemessages() -> None:  # pragma: no cover
    # Never called. `render_violation` builds its `ngettext()` call from `RULE_MESSAGES` values at
    # runtime, using plain strings there (not `gettext_noop`, which would collide with the `ngettext`
    # extraction below for the same msgid and make `makemessages` warn). These literal calls, with
    # the exact same strings, are what let `makemessages` record the singular/plural pairing and the
    # four Ukrainian plural forms; Django's gettext catalog is keyed by string content, not call
    # site, so `render_violation`'s dynamic call still finds them.
    ngettext(
        "This AI pull request touches %(path_count)s file forbidden by a sensitive-path rule: %(paths)s.",
        "This AI pull request touches %(path_count)s files forbidden by a sensitive-path rule: %(paths)s.",
        1,
    )
    ngettext(
        "This AI pull request touches %(path_count)s file needing extra review (%(paths)s) "
        "and has %(approvals)s of %(required)s required human approvals.",
        "This AI pull request touches %(path_count)s files needing extra review (%(paths)s) "
        "and has %(approvals)s of %(required)s required human approvals.",
        1,
    )
    ngettext(
        "This merged AI pull request has %(approvals)s of %(required)s required human approval.",
        "This merged AI pull request has %(approvals)s of %(required)s required human approvals.",
        1,
    )
    ngettext(
        "Changes %(non_test_lines)s line of non-test code with no test changes (threshold %(threshold)s).",
        "Changes %(non_test_lines)s lines of non-test code with no test changes (threshold %(threshold)s).",
        1,
    )
    ngettext(
        "This AI pull request changes %(effective_lines)s effective line, over the limit of %(limit)s.",
        "This AI pull request changes %(effective_lines)s effective lines, over the limit of %(limit)s.",
        1,
    )
    ngettext(
        "This pull request was merged with %(bot_approvals)s approval, all of them from a bot "
        "account and none from a person.",
        "This pull request was merged with %(bot_approvals)s approvals, all of them from bot "
        "accounts and none from a person.",
        1,
    )
    ngettext(
        "This pull request merged with %(threads)s comment thread from an AI reviewer still unresolved.",
        "This pull request merged with %(threads)s comment threads from an AI reviewer still unresolved.",
        1,
    )
    ngettext(
        "This pull request touches %(path_count)s high-risk path (%(paths)s) and its description "
        "states no plan, risks or rollback.",
        "This pull request touches %(path_count)s high-risk paths (%(paths)s) and its description "
        "states no plan, risks or rollback.",
        1,
    )
    ngettext(
        "This AI pull request changes %(path_count)s dependency manifest (%(paths)s); check that a "
        "person chose the dependency.",
        "This AI pull request changes %(path_count)s dependency manifests (%(paths)s); check that a "
        "person chose the dependencies.",
        1,
    )
    ngettext(
        "This AI pull request carries %(path_count)s migration file (%(paths)s) and no designated "
        "reviewer approved it.",
        "This AI pull request carries %(path_count)s migration files (%(paths)s) and no designated "
        "reviewer approved it.",
        1,
    )
    ngettext(
        "This pull request adds %(path_count)s file that should never be committed: %(paths)s.",
        "This pull request adds %(path_count)s files that should never be committed: %(paths)s.",
        1,
    )
    ngettext(
        "This pull request changes %(path_count)s agent-configuration file (%(paths)s), which "
        "changes how later agent runs behave.",
        "This pull request changes %(path_count)s agent-configuration files (%(paths)s), which "
        "changes how later agent runs behave.",
        1,
    )


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "?"


def _paths_text(params: Mapping[str, Any]) -> str:
    paths = params.get("paths") or []
    shown = ", ".join(str(path) for path in paths)
    path_count = params.get("path_count", len(paths))
    hidden = path_count - len(paths) if isinstance(path_count, int) else 0
    if hidden > 0:
        return _("%(shown)s and %(more)s more") % {"shown": shown, "more": hidden}
    return shown


def _list_text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)


def render_violation(rule_code: str, params: Mapping[str, Any] | None = None) -> str:
    entry = RULE_MESSAGES.get(rule_code)
    if entry is None:
        return rule_code

    merged: dict[str, Any] = dict(params or {})
    if "paths" in merged:
        merged["paths"] = _paths_text(merged)
    if "modules" in merged:
        merged["modules"] = _list_text(merged["modules"])
    if "reviewers" in merged and isinstance(merged["reviewers"], (list, tuple)):
        merged["reviewers"] = _list_text(merged["reviewers"])
    if "codes" in merged:
        merged["codes"] = _list_text(merged["codes"])
    tool = merged.get("tool")
    if isinstance(tool, str) and tool in Tool.values:
        merged["tool"] = str(Tool(tool).label)
    for name, labels in ENUM_PARAM_LABELS.items():
        value = merged.get(name)
        if isinstance(value, str) and value in labels:
            merged[name] = _(labels[value])

    plural = entry.get("plural")
    count_key = entry.get("count_key")
    variant = entry.get("variants", {}).get(str(merged.get(entry.get("variant_key", ""), "")))
    if variant is not None:
        template = _(variant)
    elif plural is not None and count_key is not None:
        count = merged.get(count_key, 0)
        count = count if isinstance(count, int) else 0
        template = ngettext(entry["singular"], plural, count)
    else:
        template = _(entry["singular"])

    return template % _SafeDict(merged)


def rule_label(rule_code: str) -> str:
    try:
        return str(RuleCode(rule_code).label)
    except ValueError:
        return rule_code
