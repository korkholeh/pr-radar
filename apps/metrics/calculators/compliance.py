"""Compliance metrics (phase 12, stage 8): the five rates that read the PLANEKS standards'
subject matter — who approved, whether the gate was green, whether the AI reviewer ever looked,
how much AI work lands on risky paths, and how often the structural family fires.

Two rules shape every definition here.

**A metric is not a violation count.** `violations_by_rule` already counts what the policy
*raised*, and every new check is off until a lead turns it on. These five read the underlying
facts instead, so an installation that has enabled nothing still sees its own shape — a rate that
only moved when somebody ticked a checkbox would measure the configuration, not the engineering.

**No row multiplication.** Every "has a review/file/signal like this" test is an `Exists`
subquery rather than a join filter: `calculators/base.py::_grouped_value` takes the global value
from `queryset.count()`, which a joined row would inflate.
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from functools import reduce
from operator import or_

from django.db.models import Exists, F, OuterRef, Q, QuerySet, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils.translation import gettext_lazy as _

from apps.activity.models import CheckStatus, PRFile, PullRequest, Review
from apps.ai_detection.models import AISignal
from apps.catalog.globs import compile_globs, matches_any
from apps.metrics.calculators.base import (
    GLOBAL_SCOPE,
    DayContext,
    batch_count,
    count_value,
)
from apps.metrics.models import Cohort, ScopeType
from apps.metrics.registry import MetricDef, RatioCalc, _register
from apps.metrics.selectors import scoped_pull_requests
from apps.metrics.timeframe import day_end_exclusive, day_start
from apps.metrics.types import MetricValue
from apps.policy.models import SensitivePathRule
from apps.policy.services import current_policy

_ALL_LEVELS = frozenset(ScopeType.values)
_RED_ROLLUP_STATES = (CheckStatus.RollupState.FAILURE, CheckStatus.RollupState.ERROR)


def _merged_on_day(ctx: DayContext, cohort: str | None = None) -> QuerySet[PullRequest]:
    return scoped_pull_requests(ctx.scope, cohort or ctx.cohort).filter(
        merged_at__gte=day_start(ctx.date), merged_at__lt=day_end_exclusive(ctx.date)
    )


def _merged_population(cohort: str, date: datetime.date) -> QuerySet[PullRequest]:
    """`_merged_on_day()`'s population at `GLOBAL_SCOPE`, before any narrowing — the base every
    batch function here groups by scope from."""
    return scoped_pull_requests(GLOBAL_SCOPE, cohort).filter(
        merged_at__gte=day_start(date), merged_at__lt=day_end_exclusive(date)
    )


def _register_compliance_ratio(
    key: str,
    title,
    description,
    direction: str,
    numerator_filter,
    denominator_filter,
    formula: str,
    *,
    supports_cohorts: bool,
    fixed_cohort: str | None = None,
) -> None:
    """One ratio over pull requests merged on a day, declared as two queryset narrowings.

    `numerator_filter`/`denominator_filter` each take the merged-on-day queryset and return it
    narrowed; the same two callables serve the per-scope and the batched path, so the KPI card and
    the rollup writer can never drift apart.
    """

    def _numerator(ctx: DayContext) -> MetricValue:
        return count_value(numerator_filter(_merged_on_day(ctx, fixed_cohort)).count())

    def _denominator(ctx: DayContext) -> MetricValue:
        return count_value(denominator_filter(_merged_on_day(ctx, fixed_cohort)).count())

    _register(
        MetricDef(
            key=key,
            title=title,
            description=description,
            unit="ratio",
            direction=direction,
            kind="ratio",
            levels=_ALL_LEVELS,
            supports_cohorts=supports_cohorts,
            calculator=RatioCalc(
                numerator=_numerator,
                denominator=_denominator,
                numerator_batch=batch_count(
                    lambda cohort, date: numerator_filter(_merged_population(fixed_cohort or cohort, date))
                ),
                denominator_batch=batch_count(
                    lambda cohort, date: denominator_filter(_merged_population(fixed_cohort or cohort, date))
                ),
            ),
            formula=formula,
        )
    )


# --- ai_only_approval_rate ----------------------------------------------------------------------


def _approved_by_a_bot() -> Exists:
    return Exists(
        Review.objects.filter(
            pull_request=OuterRef("pk"),
            state=Review.State.APPROVED,
            reviewer__person__is_bot=True,
        )
    )


def _approved_by_a_human() -> Exists:
    """An approval from a person who is neither a bot nor the author — the same "human approval"
    `apps.policy.rules` counts, so the metric and the `AI_ONLY_APPROVAL` check read one definition.
    """
    return Exists(
        Review.objects.filter(
            pull_request=OuterRef("pk"),
            state=Review.State.APPROVED,
            reviewer__person__is_bot=False,
        ).exclude(
            # `Coalesce`, because a pull request whose author never resolved to a person has a
            # NULL here, and `person_id <> NULL` is NULL in SQL — without it, every approval on
            # such a PR would be excluded and the PR would read as bot-only. `0` is not a
            # `Person.pk`, so an unknown author excludes nobody, matching
            # `apps.policy.rules._human_approver_person_ids()`.
            reviewer__person_id=Coalesce(OuterRef("author__person_id"), Value(0))
        )
    )


def _approved_at_all() -> Exists:
    return Exists(Review.objects.filter(pull_request=OuterRef("pk"), state=Review.State.APPROVED))


_register_compliance_ratio(
    "ai_only_approval_rate",
    _("Bot-only approval rate"),
    _(
        "Share of pull requests merged on a day, among those that were approved at all, whose "
        "approvals came only from bot accounts — no human other than the author approved."
    ),
    "lower_is_better",
    lambda queryset: queryset.filter(_approved_by_a_bot()).exclude(_approved_by_a_human()),
    lambda queryset: queryset.filter(_approved_at_all()),
    "merged PRs approved only by bots / merged PRs with at least one approval, on the day",
    supports_cohorts=True,
)


# --- quality_gate_bypass_rate -------------------------------------------------------------------


def _final_rollup_state() -> Subquery:
    """The rollup of the commit the pull request ended on — the *last* observed, matching
    `apps.policy.rules._final_rollup_state()`: a PR that failed at noon and was green at five did
    not bypass anything. A row whose `observed_at` GitHub never reported sorts last there, so it
    sorts first under this reversed ordering."""
    return Subquery(
        CheckStatus.objects.filter(pull_request=OuterRef("pk"))
        .order_by(F("observed_at").desc(nulls_first=True), "-pk")
        .values("rollup_state")[:1]
    )


def _has_checks() -> Exists:
    return Exists(CheckStatus.objects.filter(pull_request=OuterRef("pk")))


def _merged_over_a_red_gate(queryset: QuerySet[PullRequest]) -> QuerySet[PullRequest]:
    """The annotation stays inside a `pk__in` subquery on purpose: `_grouped_value()` re-groups
    whatever it is handed with `.values(...).annotate(Count(...))`, and a non-aggregate annotation
    carried into that call lands in the `GROUP BY` and splits the groups."""
    red = (
        queryset.annotate(final_rollup_state=_final_rollup_state())
        .filter(final_rollup_state__in=_RED_ROLLUP_STATES)
        .values("pk")
    )
    return queryset.filter(pk__in=red)


_register_compliance_ratio(
    "quality_gate_bypass_rate",
    _("Quality-gate bypass rate"),
    _(
        "Share of pull requests merged on a day whose last observed status-check rollup was a "
        "failure or an error. A repository that runs no checks cannot bypass them, so PRs with no "
        "check at all are excluded from both sides."
    ),
    "lower_is_better",
    _merged_over_a_red_gate,
    lambda queryset: queryset.filter(_has_checks()),
    "merged PRs whose last check rollup was FAILURE or ERROR / merged PRs with any check rollup, on the day",
    supports_cohorts=True,
)


# --- ai_review_coverage -------------------------------------------------------------------------


def _ai_reviewer_logins() -> frozenset[str]:
    """The logins a lead named in `AIPolicy.ai_reviewer_identities`, case-folded like everywhere
    else (GitHub treats `Copilot` and `copilot` as one account)."""
    policy = current_policy()
    if policy is None:
        return frozenset()
    return frozenset(str(login).casefold() for login in (policy.ai_reviewer_identities or []) if login)


def _reviewed_by_an_ai_reviewer(queryset: QuerySet[PullRequest]) -> QuerySet[PullRequest]:
    logins = _ai_reviewer_logins()
    if not logins:
        return queryset.none()
    matches = reduce(or_, (Q(reviewer__value__iexact=login) for login in sorted(logins)))
    return queryset.filter(Exists(Review.objects.filter(matches, pull_request=OuterRef("pk"))))


def _measurable_for_ai_review(queryset: QuerySet[PullRequest]) -> QuerySet[PullRequest]:
    """Nothing is measurable until a lead names an AI reviewer: with no login configured the
    denominator is empty and the metric reads `None`, never a 0% that would be read as "the AI
    reviewer never looks" when in fact nobody told PR Radar who it is."""
    return queryset if _ai_reviewer_logins() else queryset.none()


_register_compliance_ratio(
    "ai_review_coverage",
    _("AI review coverage"),
    _(
        "Share of pull requests merged on a day that one of the AI reviewers named in the AI "
        "policy reviewed. Empty until at least one AI reviewer login is configured."
    ),
    "higher_is_better",
    _reviewed_by_an_ai_reviewer,
    _measurable_for_ai_review,
    "merged PRs reviewed by a login in AIPolicy.ai_reviewer_identities / merged PRs, on the day",
    supports_cohorts=True,
)


# --- high_risk_ai_pr_rate -----------------------------------------------------------------------


def _high_risk_pull_request_ids(queryset: QuerySet[PullRequest]) -> frozenset[int]:
    """Which of these pull requests touch a high-risk path, read from the *globs*.

    Not from `PRFile.matched_sensitive_rule`, which would be one join and no Python at all: an
    `advisory` rule is deliberately kept out of that column (`policy.services.match_sensitive_paths`
    — a broad risk glob there would shadow a narrow rule somebody wrote to forbid a path), and the
    whole shipped PLANEKS risk table is advisory. Reading the column would therefore have returned
    0% on every installation that seeded the table, which is exactly the installation this metric
    is for. `apps.policy.rules._risk_of` classifies risk the same way, from the globs.

    Two queries regardless of how many pull requests are in the window: their project ids (only
    when a project-scoped risk rule exists) and their non-excluded file paths.
    """
    rules = [
        rule
        for rule in SensitivePathRule.objects.filter(
            is_active=True, risk_level=SensitivePathRule.RiskLevel.HIGH
        )
    ]
    if not rules:
        return frozenset()

    global_matchers = [compile_globs([rule.glob]) for rule in rules if rule.project_id is None]
    project_matchers: dict[int, list] = defaultdict(list)
    for rule in rules:
        if rule.project_id is not None:
            project_matchers[rule.project_id].append(compile_globs([rule.glob]))

    projects_by_pr: dict[int, set[int]] = defaultdict(set)
    if project_matchers:
        rows = queryset.filter(repository__projects__isnull=False).values_list(
            "pk", "repository__projects__id"
        )
        for pull_request_id, project_id in rows:
            projects_by_pr[pull_request_id].add(project_id)

    matched: set[int] = set()
    paths = PRFile.objects.filter(pull_request__in=queryset, is_excluded=False).values_list(
        "pull_request_id", "path"
    )
    for pull_request_id, path in paths.iterator():
        if pull_request_id in matched:
            continue
        applicable = global_matchers
        if project_matchers:
            applicable = [
                *global_matchers,
                *(
                    patterns
                    for project_id in projects_by_pr.get(pull_request_id, ())
                    for patterns in project_matchers.get(project_id, ())
                ),
            ]
        if any(matches_any(path, patterns) for patterns in applicable):
            matched.add(pull_request_id)
    return frozenset(matched)


def _touches_a_high_risk_path(queryset: QuerySet[PullRequest]) -> QuerySet[PullRequest]:
    return queryset.filter(pk__in=_high_risk_pull_request_ids(queryset))


_register_compliance_ratio(
    "high_risk_ai_pr_rate",
    _("High-risk AI PR rate"),
    _(
        "Share of AI-cohort pull requests merged on a day that touched a path an active "
        "sensitive-path rule classifies as high risk, advisory rules included. Always measured "
        "over the AI cohort, whatever cohort the page is filtered to."
    ),
    "neutral",
    _touches_a_high_risk_path,
    lambda queryset: queryset,
    "AI-cohort merged PRs touching a glob of an active risk_level=high sensitive-path rule / "
    "AI-cohort merged PRs, on the day",
    supports_cohorts=False,
    fixed_cohort=Cohort.AI,
)


# --- structural_signal_rate ---------------------------------------------------------------------


def _has_a_structural_signal() -> Exists:
    """A signal from the structural family — `signal_rule` set, `rule` null. A regex signal quotes
    text the author wrote; a structural one is an inference, and this rate is how a lead sees how
    often the inference fires before trusting any number built on it."""
    return Exists(AISignal.objects.filter(pull_request=OuterRef("pk"), signal_rule__isnull=False))


_register_compliance_ratio(
    "structural_signal_rate",
    _("Structural signal rate"),
    _(
        "Share of pull requests merged on a day carrying at least one structural signal — a "
        "heuristic about the shape of the change, never a quoted AI marker."
    ),
    "neutral",
    lambda queryset: queryset.filter(_has_a_structural_signal()),
    lambda queryset: queryset,
    "merged PRs with at least one structural AISignal / merged PRs, on the day",
    supports_cohorts=True,
)
