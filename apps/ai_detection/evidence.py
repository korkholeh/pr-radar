"""Rendering a structural signal's `(evidence_code, evidence_params)` into a translated sentence
at read time (CLAUDE.md: system-generated text is stored as a code plus parameters, never a
rendered message). The shape deliberately mirrors `apps/policy/messages.py`, including its failure
behaviour: an unknown code renders the code itself rather than raising, and a missing parameter
renders `?`, so a row written by an older version of a kind can never 500 the pull-request page.

A *regex* signal needs none of this. Its `AISignal.evidence` is a quote of the source data it
matched — a commit trailer, a file path, a login — which is not prose and is the same in every
language. Only the structural family, whose evidence is a generated sentence, goes through here.

Every sentence states the thresholds that produced it as well as the measurement, because a lead
reading "delivered in 40 minutes" needs to know the rule said two hours before they can judge it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict

from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop, ngettext


class EvidenceCode:
    """The codes `structural.py`, `baselines.py` (stage 5) and `diffsignals.py` (stage 6) may write.

    Plain string constants rather than `TextChoices`: these never reach a model field's `choices`,
    only `AISignal.evidence_code`, and the registry below is what a test checks them against.
    """

    FAST_LARGE_PR = "fast_large_pr"
    COMMIT_BURST = "commit_burst"
    SINGLE_LARGE_COMMIT = "single_large_commit"
    MASS_FILE_CREATION = "mass_file_creation"
    UNUSED_NEW_DEPENDENCY = "unused_new_dependency"
    INSTANT_REVIEW_RESPONSE = "instant_review_response"
    THROUGHPUT_SHIFT = "throughput_shift"
    OFF_HOURS_VOLUME = "off_hours_volume"
    TEST_RATIO_LOCKSTEP = "test_ratio_lockstep"
    BODY_STYLE_SHIFT = "body_style_shift"
    WHOLESALE_REFORMAT = "wholesale_reformat"
    COMMENT_DENSITY_OUTLIER = "comment_density_outlier"
    DUPLICATED_BLOCKS = "duplicated_blocks"


class _MessageDef(TypedDict, total=False):
    singular: str
    plural: str
    count_key: str


EVIDENCE_MESSAGES: dict[str, _MessageDef] = {
    EvidenceCode.FAST_LARGE_PR: {
        "singular": gettext_noop(
            "%(lines)s lines across %(files)s files arrived %(hours)s hours after the first commit "
            "(the rule flags %(min_lines)s lines and %(min_files)s files within %(max_hours)s hours)."
        ),
    },
    EvidenceCode.COMMIT_BURST: {
        "singular": gettext_noop(
            "%(commits)s substantial commits landed less than %(gap_seconds)s seconds apart "
            "(the rule flags %(min_commits)s such commits within %(max_gap_seconds)s seconds)."
        ),
        "plural": gettext_noop(
            "%(commits)s substantial commits landed less than %(gap_seconds)s seconds apart "
            "(the rule flags %(min_commits)s such commits within %(max_gap_seconds)s seconds)."
        ),
        "count_key": "commits",
    },
    EvidenceCode.SINGLE_LARGE_COMMIT: {
        "singular": gettext_noop(
            "The whole change — %(lines)s lines across %(files)s files — arrived in a single commit "
            "(the rule flags %(min_lines)s lines and %(min_files)s files)."
        ),
    },
    EvidenceCode.MASS_FILE_CREATION: {
        "singular": gettext_noop(
            "%(added_files)s new files were added across %(directories)s directories "
            "(the rule flags %(min_added_files)s files across %(min_directories)s directories)."
        ),
    },
    EvidenceCode.UNUSED_NEW_DEPENDENCY: {
        "singular": gettext_noop(
            "%(manifest)s adds the dependency %(package)s, and nothing else in this change imports it."
        ),
    },
    EvidenceCode.INSTANT_REVIEW_RESPONSE: {
        "singular": gettext_noop(
            "A new commit followed a review comment within %(max_minutes)s minutes, %(occurrences)s time "
            "(the rule flags %(min_occurrences)s such times)."
        ),
        "plural": gettext_noop(
            "A new commit followed a review comment within %(max_minutes)s minutes, %(occurrences)s times "
            "(the rule flags %(min_occurrences)s such times)."
        ),
        "count_key": "occurrences",
    },
    EvidenceCode.THROUGHPUT_SHIFT: {
        "singular": gettext_noop(
            "This author opened %(recent_per_week)s pull requests a week recently, against "
            "%(earlier_per_week)s a week before that — %(ratio)s times their own rate once the "
            "team's own change of %(team_ratio)s is divided out (the rule flags %(min_ratio)s)."
        ),
    },
    EvidenceCode.OFF_HOURS_VOLUME: {
        "singular": gettext_noop(
            "%(share)s%% of this author's recent volume landed outside the hours they usually "
            "commit in (%(usual_hours)s); the rule flags %(min_share)s%%."
        ),
    },
    EvidenceCode.TEST_RATIO_LOCKSTEP: {
        "singular": gettext_noop(
            "Across %(pr_count)s pull requests the test-to-code ratio stayed at about "
            "%(mean_ratio)s, varying by only %(spread)s (the rule flags a variation under "
            "%(max_variance)s)."
        ),
    },
    EvidenceCode.BODY_STYLE_SHIFT: {
        "singular": gettext_noop(
            "This author's pull-request descriptions run about %(recent_length)s characters "
            "recently, against %(earlier_length)s before — %(ratio)s times the difference the "
            "rule flags at %(min_ratio)s."
        ),
    },
    EvidenceCode.WHOLESALE_REFORMAT: {
        "singular": gettext_noop(
            "Of %(lines)s changed lines only %(semantic_lines)s change anything but whitespace — "
            "%(share)s%% of the diff (the rule flags %(min_lines)s lines at or below %(max_share)s%%)."
        ),
    },
    EvidenceCode.COMMENT_DENSITY_OUTLIER: {
        "singular": gettext_noop(
            "The added code is %(density)s%% comments and docstrings (%(comment_lines)s comment "
            "lines to %(code_lines)s code lines), against %(baseline_density)s%% in the same files "
            "before the change — the rule flags %(ratio)s times the baseline, at %(min_density)s%% "
            "or more."
        ),
    },
    EvidenceCode.DUPLICATED_BLOCKS: {
        "singular": gettext_noop(
            "The same %(block_lines)s-line block of added code appears %(occurrences)s time across "
            "%(files)s files (the rule flags %(min_occurrences)s occurrences across %(min_files)s "
            "files)."
        ),
        "plural": gettext_noop(
            "The same %(block_lines)s-line block of added code appears %(occurrences)s times across "
            "%(files)s files (the rule flags %(min_occurrences)s occurrences across %(min_files)s "
            "files)."
        ),
        "count_key": "occurrences",
    },
}


def _register_plural_forms_for_makemessages() -> None:  # pragma: no cover
    # Never called; same device as `policy.messages._register_plural_forms_for_makemessages`.
    # `render_evidence` builds its `ngettext()` call from `EVIDENCE_MESSAGES` at runtime, so these
    # literal calls are what let `makemessages` record the singular/plural pairing and Ukrainian's
    # four plural forms. The catalog is keyed by string content, not by call site.
    ngettext(
        "%(commits)s substantial commits landed less than %(gap_seconds)s seconds apart "
        "(the rule flags %(min_commits)s such commits within %(max_gap_seconds)s seconds).",
        "%(commits)s substantial commits landed less than %(gap_seconds)s seconds apart "
        "(the rule flags %(min_commits)s such commits within %(max_gap_seconds)s seconds).",
        1,
    )
    ngettext(
        "A new commit followed a review comment within %(max_minutes)s minutes, %(occurrences)s time "
        "(the rule flags %(min_occurrences)s such times).",
        "A new commit followed a review comment within %(max_minutes)s minutes, %(occurrences)s times "
        "(the rule flags %(min_occurrences)s such times).",
        1,
    )
    ngettext(
        "The same %(block_lines)s-line block of added code appears %(occurrences)s time across "
        "%(files)s files (the rule flags %(min_occurrences)s occurrences across %(min_files)s "
        "files).",
        "The same %(block_lines)s-line block of added code appears %(occurrences)s times across "
        "%(files)s files (the rule flags %(min_occurrences)s occurrences across %(min_files)s "
        "files).",
        1,
    )


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "?"


def render_evidence(evidence_code: str, params: Mapping[str, Any] | None = None) -> str:
    entry = EVIDENCE_MESSAGES.get(evidence_code)
    if entry is None:
        return evidence_code

    merged: dict[str, Any] = dict(params or {})
    plural = entry.get("plural")
    count_key = entry.get("count_key")
    if plural is not None and count_key is not None:
        count = merged.get(count_key, 0)
        count = count if isinstance(count, int) else 0
        template = ngettext(entry["singular"], plural, count)
    else:
        template = _(entry["singular"])

    return template % _SafeDict(merged)
