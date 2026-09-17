"""Rendering `(rule_code, details_params)` into a translated sentence at read time (CLAUDE.md:
system-generated text is stored as a code plus params, never a rendered message). Every entry in
`RULE_MESSAGES` is a full sentence with named placeholders; a count-bearing message carries a
`count_key` and a plural form, dispatched through `ngettext`. An unknown `rule_code` renders the
code itself instead of raising, so a stale row can never 500 the console; a missing param renders
`?` in its place instead of raising a `KeyError`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict

from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop, ngettext

from apps.policy.models import PolicyViolation

RuleCode = PolicyViolation.RuleCode


class _MessageDef(TypedDict, total=False):
    singular: str
    plural: str
    count_key: str


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


def render_violation(rule_code: str, params: Mapping[str, Any] | None = None) -> str:
    entry = RULE_MESSAGES.get(rule_code)
    if entry is None:
        return rule_code

    merged: dict[str, Any] = dict(params or {})
    if "paths" in merged:
        merged["paths"] = _paths_text(merged)

    plural = entry.get("plural")
    count_key = entry.get("count_key")
    if plural is not None and count_key is not None:
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
