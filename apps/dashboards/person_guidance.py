"""The person page's "recommendations for the lead" block: a short list of action items and
strengths derived by fixed rules from the two things the page already computed — the
person-vs-baseline comparison (`person.build_comparison`) and the per-rule violation statistics
(`person.build_violation_stats`). No extra query, so the block can never disagree with the tables
under it.

Each item is a code plus raw parameters (CLAUDE.md: system-generated text is never stored or built
as a rendered English message); `render_recommendation` turns it into a full translated sentence
at read time, and the `recommendation_text` template tag formats the numbers by the metric's unit
first. Volume is never an action item on its own (`docs/POLICY.md`): the number of pull requests a
person merged is only used to decide whether there is enough data to say anything."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from apps.activity.models import AIStatus
from apps.dashboards.person import (
    AIProfile,
    CohortComparison,
    ComparisonRow,
    ViolationStats,
    ViolationStatsRow,
)
from apps.metrics.registry import get_metric
from apps.policy.models import PolicyViolation

RuleCode = PolicyViolation.RuleCode

# How far from the baseline a metric must sit before it becomes an action item or a strength —
# deliberately wider than the comparison table's 5% "grey" band: the table shows every gap, this
# block names only the ones worth a conversation.
GAP_RATIO_THRESHOLD = 0.2
# For a rate, the relative gap alone overstates tiny shares (2% vs 1% is "+100%"), so a rate must
# also be this many percentage points away.
GAP_POINTS_THRESHOLD = 0.05
# A rule broken this many times in the period is called out as a pattern, not a slip.
REPEATED_RULE_THRESHOLD = 3
MAX_REPEATED_RULES = 2

# Rules about how AI-assisted work is disclosed, reviewed and bounded. Their violations are raised in
# the AI adoption section of the block, not among the general policy items, so each shows up once.
AI_RULE_CODES: frozenset[str] = frozenset(
    {
        RuleCode.DISCLOSURE_MISSING,
        RuleCode.DISCLOSURE_MISMATCH,
        RuleCode.TOOL_NOT_ALLOWED,
        RuleCode.SENSITIVE_PATH_FORBIDDEN,
        RuleCode.SENSITIVE_PATH_REVIEW,
        RuleCode.NO_HUMAN_APPROVAL,
        RuleCode.SELF_MERGE,
        RuleCode.AI_PR_TOO_LARGE,
        RuleCode.AI_ONLY_APPROVAL,
        RuleCode.AI_REVIEW_MISSING,
        RuleCode.AI_REVIEW_UNRESOLVED,
        RuleCode.NEW_DEPENDENCY_AI,
        RuleCode.MIGRATION_AI_INSUFFICIENT_REVIEW,
        RuleCode.AGENT_CONFIG_CHANGED,
        RuleCode.SCOPE_CREEP,
        RuleCode.RUBBER_STAMP_ON_AI_PR,
    }
)
# Below this share of AI-assisted pull requests in the baseline, the team itself barely uses AI, so a
# person using it less is no gap worth raising.
AI_ADOPTION_MIN_BASELINE = 0.1

Kind = Literal["action", "strength"]
AdoptionLevel = Literal["none", "below", "in_line", "above", "small_sample"]
BaselineLevel = Literal["project", "organization"]

# Metric-driven action items, in the order a lead should raise them: outcomes of the work first
# (fixes, churn, rework), then flow, then size. The neutral metrics (AI share, structural signals)
# have no better side and never produce an item.
_METRIC_ACTION_CODES: dict[str, str] = {
    "followup_fix_rate": "followup_fix_rate_high",
    "churn_21d": "churn_high",
    "rework_rate": "rework_high",
    "lead_time_p50": "lead_time_high",
    "time_to_first_review_p50": "first_review_slow",
    "pr_size_p50": "pr_size_high",
}

_PRIORITY: dict[str, int] = {
    "violations_open_high": 0,
    "followup_fix_rate_high": 10,
    "churn_high": 11,
    "rework_high": 12,
    "violations_repeated": 20,
    "violations_open": 21,
    "lead_time_high": 30,
    "first_review_slow": 31,
    "pr_size_high": 32,
    "no_reviews": 40,
}


@dataclass(frozen=True)
class Recommendation:
    code: str
    kind: Kind
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Guidance:
    actions: list[Recommendation]
    strengths: list[Recommendation]
    baseline_level: BaselineLevel
    # Too few merged pull requests in the period for the metric-driven items: only the policy
    # items (which do not depend on a sample) are listed, and the block says why.
    small_sample: bool


def _significant_gap(row_value: float, baseline: float, unit: str) -> bool:
    delta = row_value - baseline
    if unit == "ratio" and abs(delta) < GAP_POINTS_THRESHOLD:
        return False
    if baseline == 0:
        # No relative gap exists; a rate is still judged by its points, anything else is skipped.
        return unit == "ratio"
    return abs(delta) / abs(baseline) >= GAP_RATIO_THRESHOLD


def _side(row: ComparisonRow, baseline: float | None) -> Literal["better", "worse"] | None:
    definition = get_metric(row.metric)
    if (
        definition.direction == "neutral"
        or definition.kind == "counter"
        or row.below_min_sample
        or row.person_value is None
        or baseline is None
        or not _significant_gap(row.person_value, baseline, definition.unit)
    ):
        return None
    higher = row.person_value > baseline
    return "better" if higher == (definition.direction == "higher_is_better") else "worse"


def _violation_items(rows: list[ViolationStatsRow]) -> list[Recommendation]:
    severity = PolicyViolation.Severity
    items = []
    high_rows = [row for row in rows if row.severity == severity.HIGH and row.open]
    if high_rows:
        items.append(
            Recommendation(
                "violations_open_high",
                "action",
                {"count": sum(row.open for row in high_rows), "rules": [row.rule_code for row in high_rows]},
            )
        )
    other_open = sum(row.open for row in rows if row.severity != severity.HIGH)
    if other_open:
        items.append(Recommendation("violations_open", "action", {"count": other_open}))

    per_rule: dict[str, int] = {}
    for row in rows:
        per_rule[row.rule_code] = per_rule.get(row.rule_code, 0) + row.total
    repeated = sorted(
        ((rule, total) for rule, total in per_rule.items() if total >= REPEATED_RULE_THRESHOLD),
        key=lambda pair: (-pair[1], pair[0]),
    )
    items.extend(
        Recommendation("violations_repeated", "action", {"rule": rule, "count": total})
        for rule, total in repeated[:MAX_REPEATED_RULES]
    )
    return items


def build_guidance(
    comparison: list[ComparisonRow], violation_stats: ViolationStats, min_sample: int
) -> Guidance:
    """Every metric item compares the person with one baseline for the whole block — their
    primary project when they have one in scope for the period, otherwise the organization — so
    the block can say once what "the baseline" is instead of repeating it in every sentence."""
    rows = {row.metric: row for row in comparison}
    has_project = any(row.project_value is not None for row in comparison)
    level: BaselineLevel = "project" if has_project else "organization"

    def baseline(row: ComparisonRow) -> float | None:
        return row.project_value if has_project else row.org_value

    merged_row = rows.get("prs_merged")
    merged = merged_row.person_value if merged_row is not None else None
    small_sample = merged is None or merged < min_sample

    actions = _violation_items([row for row in violation_stats.rows if row.rule_code not in AI_RULE_CODES])
    strengths: list[Recommendation] = []
    if not small_sample:
        for row in comparison:
            side = _side(row, baseline(row))
            if side is None:
                continue
            params = {"metric": row.metric, "value": row.person_value, "baseline": baseline(row)}
            if side == "better":
                strengths.append(Recommendation("strength", "strength", params))
            elif row.metric in _METRIC_ACTION_CODES:
                actions.append(Recommendation(_METRIC_ACTION_CODES[row.metric], "action", params))

        reviews_row = rows.get("reviews_given")
        if reviews_row is not None and not reviews_row.person_value:
            actions.append(Recommendation("no_reviews", "action", {"count": merged}))

    actions.sort(key=lambda item: _PRIORITY.get(item.code, 99))
    return Guidance(actions=actions, strengths=strengths, baseline_level=level, small_sample=small_sample)


@dataclass(frozen=True)
class AIGuidance:
    """The AI adoption section: where the person stands against the baseline (`level`, with the
    share and baseline behind it), the profile it was derived from, and its own action items and
    strengths."""

    level: AdoptionLevel
    share: float | None
    baseline: float | None
    profile: AIProfile
    actions: list[Recommendation]
    strengths: list[Recommendation]

    @property
    def status_counts(self) -> dict[str, int]:
        counts = self.profile.status_counts
        return {
            "explicit": counts.get(AIStatus.AI_EXPLICIT, 0),
            "disclosed": counts.get(AIStatus.AI_DISCLOSED, 0),
            "suspected": counts.get(AIStatus.AI_SUSPECTED, 0),
            "other": counts.get(AIStatus.NO_AI, 0) + counts.get(AIStatus.UNKNOWN, 0),
        }


_AI_PRIORITY: dict[str, int] = {
    "violations_open_high": 0,
    "ai_quality_worse": 10,
    "violations_repeated": 20,
    "ai_undisclosed": 21,
    "violations_open": 22,
    "ai_high_risk": 30,
    "ai_adoption_low": 40,
}


def _cohort_side(row: CohortComparison, min_sample: int) -> Literal["better", "worse"] | None:
    definition = get_metric(row.metric)
    if (
        definition.direction == "neutral"
        or row.ai_value is None
        or row.non_ai_value is None
        or row.ai_sample < min_sample
        or row.non_ai_sample < min_sample
        or not _significant_gap(row.ai_value, row.non_ai_value, definition.unit)
    ):
        return None
    higher = row.ai_value > row.non_ai_value
    return "better" if higher == (definition.direction == "higher_is_better") else "worse"


def build_ai_guidance(
    comparison: list[ComparisonRow], profile: AIProfile, violation_stats: ViolationStats, min_sample: int
) -> AIGuidance:
    """AI use is described, never scored: a person using AI less than the team gets a question
    about what is in the way, not a target, and only when the team itself uses AI noticeably
    (`AI_ADOPTION_MIN_BASELINE`). What is judged is how AI-assisted work is done — disclosure, the
    AI policy rules, and whether the person's AI-assisted pull requests hold up as well as their
    other ones."""
    rows = {row.metric: row for row in comparison}
    has_project = any(row.project_value is not None for row in comparison)
    share_row = rows.get("ai_pr_share")
    share = share_row.person_value if share_row is not None else None
    baseline = None
    if share_row is not None:
        baseline = share_row.project_value if has_project else share_row.org_value
    merged_row = rows.get("prs_merged")
    merged = merged_row.person_value if merged_row is not None else None

    level: AdoptionLevel
    if merged is None or merged < min_sample or share is None or baseline is None:
        level = "small_sample"
    elif profile.ai_count == 0 and not share:
        level = "none"
    elif _significant_gap(share, baseline, "ratio"):
        level = "above" if share > baseline else "below"
    else:
        level = "in_line"

    actions = _violation_items([row for row in violation_stats.rows if row.rule_code in AI_RULE_CODES])
    strengths: list[Recommendation] = []

    suspected = profile.status_counts.get(AIStatus.AI_SUSPECTED, 0)
    if suspected:
        actions.append(Recommendation("ai_undisclosed", "action", {"count": suspected}))

    worse, better = [], []
    for row in profile.cohort_quality:
        side = _cohort_side(row, min_sample)
        pair = {"metric": row.metric, "value": row.ai_value, "baseline": row.non_ai_value}
        if side == "worse":
            worse.append(pair)
        elif side == "better":
            better.append(pair)
    if worse:
        actions.append(Recommendation("ai_quality_worse", "action", {"comparisons": worse}))
    if better:
        strengths.append(Recommendation("ai_quality_better", "strength", {"comparisons": better}))

    if profile.high_risk_rate and profile.high_risk_sample >= min_sample:
        actions.append(
            Recommendation(
                "ai_high_risk", "action", {"metric": "high_risk_ai_pr_rate", "value": profile.high_risk_rate}
            )
        )

    if level in ("none", "below") and baseline is not None and baseline >= AI_ADOPTION_MIN_BASELINE:
        actions.append(Recommendation("ai_adoption_low", "action"))

    has_open_ai_violation = any(row.open for row in violation_stats.rows if row.rule_code in AI_RULE_CODES)
    if (
        level in ("in_line", "above")
        and profile.ai_count >= min_sample
        and not worse
        and not has_open_ai_violation
    ):
        strengths.append(Recommendation("ai_champion", "strength"))

    actions.sort(key=lambda item: _AI_PRIORITY.get(item.code, 99))
    return AIGuidance(
        level=level, share=share, baseline=baseline, profile=profile, actions=actions, strengths=strengths
    )


def _template(code: str, count: int) -> str | None:
    """One literal `gettext`/`ngettext` call per code, so `makemessages` records every sentence
    and each plural pair (with the four Ukrainian forms) straight from the source."""
    if code == "violations_open_high":
        return ngettext(
            "%(count)s high-severity policy violation is still open (%(rules)s). Go through it with "
            "%(name)s this week and agree whether to fix the pull request or record a waiver with a "
            "reason.",
            "%(count)s high-severity policy violations are still open (%(rules)s). Go through them "
            "with %(name)s this week and agree for each whether to fix the pull request or record a "
            "waiver with a reason.",
            count,
        )
    if code == "violations_open":
        return ngettext(
            "%(count)s medium- or low-severity policy violation is still open. Ask %(name)s to "
            "triage it: fix it, or acknowledge it with a reason.",
            "%(count)s medium- or low-severity policy violations are still open. Ask %(name)s to "
            "triage them: fix each one, or acknowledge it with a reason.",
            count,
        )
    if code == "violations_repeated":
        return ngettext(
            "The rule “%(rule)s” was broken %(count)s time in this period — a habit rather "
            "than a slip. Walk through the rule with %(name)s and check that the pull request "
            "template or checklist makes it hard to miss.",
            "The rule “%(rule)s” was broken %(count)s times in this period — a habit rather "
            "than a slip. Walk through the rule with %(name)s and check that the pull request "
            "template or checklist makes it hard to miss.",
            count,
        )
    if code == "followup_fix_rate_high":
        return _(
            "Merged pull requests are followed by a fix more often than usual (%(value)s against "
            "%(baseline)s). Agree with %(name)s on stronger verification before merge: tests for "
            "the changed paths and a stated verification step in the description."
        )
    if code == "churn_high":
        return _(
            "Much of the merged code is rewritten within 21 days (%(value)s against %(baseline)s). "
            "Pick two recent examples with %(name)s and find out why: unclear design, missing "
            "tests or changing requirements."
        )
    if code == "rework_high":
        return _(
            "Pull requests often change after the first review (%(value)s against %(baseline)s). "
            "Ask %(name)s whether the task was unclear or the pull request went up too early; "
            "suggest a self-review pass or a draft pull request for early feedback."
        )
    if code == "lead_time_high":
        return _(
            "Pull requests take longer from ready for review to merge (%(value)s against "
            "%(baseline)s). Find out with %(name)s where they wait — review, rework or CI — and "
            "help remove the blocker."
        )
    if code == "first_review_slow":
        return _(
            "Pull requests by %(name)s wait longer for a first review (%(value)s against "
            "%(baseline)s). This usually sits with the team, not the author: check who is "
            "assigned to review and ask reviewers to pick these up sooner."
        )
    if code == "pr_size_high":
        return _(
            "Pull requests are larger than usual (%(value)s against %(baseline)s lines). Encourage "
            "%(name)s to split work into smaller pull requests that can be reviewed on their own."
        )
    if code == "no_reviews":
        return ngettext(
            "%(name)s merged %(count)s pull request but gave no reviews. Add them to the review "
            "rotation — reviewing others' code spreads knowledge both ways.",
            "%(name)s merged %(count)s pull requests but gave no reviews. Add them to the review "
            "rotation — reviewing others' code spreads knowledge both ways.",
            count,
        )
    if code == "ai_undisclosed":
        return ngettext(
            "%(count)s pull request looks AI-assisted but does not say so. Remind %(name)s of the "
            "disclosure rule: name the tool in the description and what it was used for.",
            "%(count)s pull requests look AI-assisted but do not say so. Remind %(name)s of the "
            "disclosure rule: name the tool in the description and what it was used for.",
            count,
        )
    if code == "ai_quality_worse":
        return _(
            "AI-assisted pull requests by %(name)s hold up worse than their other ones — "
            "%(comparisons)s. Go through a few of them together and agree how AI output is checked "
            "before review: run the tests, read the whole diff, cut what the task did not ask for."
        )
    if code == "ai_high_risk":
        return _(
            "%(value)s of AI-assisted pull requests by %(name)s touched high-risk paths. Make sure "
            "those carry a stated plan and get a designated reviewer, not a quick approval."
        )
    if code == "ai_adoption_low":
        return _(
            "Ask %(name)s what gets in the way of using AI — tool access, trust in the output, or "
            "tasks it does not fit — and consider pairing them with someone who uses it well. The "
            "aim is to remove blockers, not to push a number up."
        )
    if code == "ai_quality_better":
        return _("AI-assisted pull requests hold up better than the other ones — %(comparisons)s.")
    if code == "ai_champion":
        return _(
            "%(name)s gets results with AI at least as good as without it, with no open AI policy "
            "violations. Consider asking them to show the team how they work with it."
        )
    if code == "strength":
        return _("%(metric)s: %(value)s against %(baseline)s.")
    return None


def render_recommendation(code: str, params: Mapping[str, Any], count: int | None = None) -> str:
    """`params` must already be display-ready (formatted numbers, translated names); `count` is
    the raw number that picks the plural form. An unknown code renders the code itself and a missing
    param renders `?`, as `apps.policy.messages` does, so a stale item can never 500 the page."""
    template = _template(code, int(count or 0))
    if template is None:
        return code
    return template % _Params(params)


class _Params(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "?"
