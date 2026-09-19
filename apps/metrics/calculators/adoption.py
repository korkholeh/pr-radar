"""Adoption metrics (spec §8.2): AI cohort share, disclosure and the policy console's counters
(`violations_*`, delegated to `apps.policy.selectors`'s query shape so the console and the
registry cannot disagree — the same filters, narrowed by `Scope` instead of only `ScopeFilter`)."""

from __future__ import annotations

import datetime
from collections import Counter

from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from apps.activity.models import AIDisclosure, PullRequest
from apps.metrics.calculators.base import (
    GLOBAL_SCOPE,
    DayContext,
    DayContextMany,
    PeriodContext,
    batch_count,
    breakdown_value,
    count_value,
)
from apps.metrics.models import Cohort, ScopeType
from apps.metrics.registry import CounterCalc, DistributionCalc, MetricDef, RatioCalc, StateCalc, _register
from apps.metrics.selectors import scoped_pull_requests, scoped_violations
from apps.metrics.timeframe import day_end_exclusive, day_start
from apps.metrics.types import BreakdownItem, MetricValue
from apps.policy.models import PolicyViolation

_ALL_LEVELS = frozenset(ScopeType.values)
_VALID_DISCLOSURE_STATUSES = frozenset({AIDisclosure.PARTIAL, AIDisclosure.SUBSTANTIAL})


def _ai_pr_share_numerator_population(_cohort: str, date: datetime.date) -> QuerySet[PullRequest]:
    return scoped_pull_requests(GLOBAL_SCOPE, cohort=Cohort.AI).filter(
        merged_at__gte=day_start(date), merged_at__lt=day_end_exclusive(date)
    )


def _ai_pr_share_numerator(ctx: DayContext) -> MetricValue:
    count = (
        scoped_pull_requests(ctx.scope, cohort=Cohort.AI)
        .filter(merged_at__gte=day_start(ctx.date), merged_at__lt=day_end_exclusive(ctx.date))
        .count()
    )
    return count_value(count)


def _ai_pr_share_denominator_population(_cohort: str, date: datetime.date) -> QuerySet[PullRequest]:
    return scoped_pull_requests(GLOBAL_SCOPE, cohort=Cohort.ALL).filter(
        merged_at__gte=day_start(date), merged_at__lt=day_end_exclusive(date)
    )


def _ai_pr_share_denominator(ctx: DayContext) -> MetricValue:
    count = (
        scoped_pull_requests(ctx.scope, cohort=Cohort.ALL)
        .filter(merged_at__gte=day_start(ctx.date), merged_at__lt=day_end_exclusive(ctx.date))
        .count()
    )
    return count_value(count)


_register(
    MetricDef(
        key="ai_pr_share",
        title=_("AI PR share"),
        description=_("Share of pull requests merged on a day that belong to the AI cohort (spec §15)."),
        unit="ratio",
        direction="neutral",
        kind="ratio",
        levels=_ALL_LEVELS,
        supports_cohorts=False,
        calculator=RatioCalc(
            numerator=_ai_pr_share_numerator,
            denominator=_ai_pr_share_denominator,
            numerator_batch=batch_count(_ai_pr_share_numerator_population),
            denominator_batch=batch_count(_ai_pr_share_denominator_population),
        ),
        formula="AI-cohort PRs merged on the day / all PRs merged on the day",
    )
)


def _ai_pr_count_population(_cohort: str, date: datetime.date) -> QuerySet[PullRequest]:
    return scoped_pull_requests(GLOBAL_SCOPE, cohort=Cohort.AI).filter(
        created_at__gte=day_start(date), created_at__lt=day_end_exclusive(date)
    )


def _ai_pr_count_daily(ctx: DayContext) -> MetricValue:
    count = (
        scoped_pull_requests(ctx.scope, cohort=Cohort.AI)
        .filter(created_at__gte=day_start(ctx.date), created_at__lt=day_end_exclusive(ctx.date))
        .count()
    )
    return count_value(count)


_register(
    MetricDef(
        key="ai_pr_count",
        title=_("AI PR count"),
        description=_("Pull requests created on a day that belong to the AI cohort."),
        unit="count",
        direction="neutral",
        kind="counter",
        levels=_ALL_LEVELS,
        supports_cohorts=False,
        calculator=CounterCalc(daily=_ai_pr_count_daily, batch=batch_count(_ai_pr_count_population)),
        formula="count of AI-cohort PRs created on the day",
    )
)


def _ai_status_counts(ctx: PeriodContext) -> Counter[str]:
    queryset = scoped_pull_requests(ctx.scope, cohort=Cohort.ALL).filter(
        created_at__gte=day_start(ctx.date_from), created_at__lt=day_end_exclusive(ctx.date_to)
    )
    return Counter(queryset.values_list("ai_status", flat=True))


def _ai_status_breakdown_period(ctx: PeriodContext) -> MetricValue:
    return count_value(sum(_ai_status_counts(ctx).values()))


def _ai_status_breakdown_items(ctx: PeriodContext) -> tuple[BreakdownItem, ...]:
    return breakdown_value(_ai_status_counts(ctx))


_register(
    MetricDef(
        key="ai_status_breakdown",
        title=_("AI status breakdown"),
        description=_("Pull requests created in the period, grouped by their detected AI status."),
        unit="breakdown",
        direction="neutral",
        kind="distribution",
        levels=_ALL_LEVELS,
        supports_cohorts=False,
        calculator=DistributionCalc(period=_ai_status_breakdown_period, breakdown=_ai_status_breakdown_items),
        formula="count of PRs created in the period, grouped by ai_status",
    )
)


def _ai_tool_counts(ctx: PeriodContext) -> Counter[str]:
    queryset = scoped_pull_requests(ctx.scope, cohort=Cohort.AI).filter(
        created_at__gte=day_start(ctx.date_from), created_at__lt=day_end_exclusive(ctx.date_to)
    )
    counts: Counter[str] = Counter()
    for tools in queryset.values_list("ai_tools", flat=True):
        counts.update(tools or [])
    return counts


def _ai_tool_breakdown_period(ctx: PeriodContext) -> MetricValue:
    return count_value(sum(_ai_tool_counts(ctx).values()))


def _ai_tool_breakdown_items(ctx: PeriodContext) -> tuple[BreakdownItem, ...]:
    return breakdown_value(_ai_tool_counts(ctx))


_register(
    MetricDef(
        key="ai_tool_breakdown",
        title=_("AI tool breakdown"),
        description=_(
            "AI-cohort pull requests created in the period, grouped by disclosed tool. A PR with "
            "no disclosed tool contributes to no label."
        ),
        unit="breakdown",
        direction="neutral",
        kind="distribution",
        levels=_ALL_LEVELS,
        supports_cohorts=False,
        calculator=DistributionCalc(period=_ai_tool_breakdown_period, breakdown=_ai_tool_breakdown_items),
        formula="count of ai_tools entries across AI-cohort PRs created in the period, grouped by tool",
    )
)


def _disclosure_rate_numerator_population(_cohort: str, date: datetime.date) -> QuerySet[PullRequest]:
    return scoped_pull_requests(GLOBAL_SCOPE, cohort=Cohort.AI).filter(
        created_at__gte=day_start(date),
        created_at__lt=day_end_exclusive(date),
        ai_disclosure__in=_VALID_DISCLOSURE_STATUSES,
    )


def _disclosure_rate_numerator(ctx: DayContext) -> MetricValue:
    count = (
        scoped_pull_requests(ctx.scope, cohort=Cohort.AI)
        .filter(
            created_at__gte=day_start(ctx.date),
            created_at__lt=day_end_exclusive(ctx.date),
            ai_disclosure__in=_VALID_DISCLOSURE_STATUSES,
        )
        .count()
    )
    return count_value(count)


def _disclosure_rate_denominator_population(_cohort: str, date: datetime.date) -> QuerySet[PullRequest]:
    return scoped_pull_requests(GLOBAL_SCOPE, cohort=Cohort.AI).filter(
        created_at__gte=day_start(date), created_at__lt=day_end_exclusive(date)
    )


def _disclosure_rate_denominator(ctx: DayContext) -> MetricValue:
    count = (
        scoped_pull_requests(ctx.scope, cohort=Cohort.AI)
        .filter(created_at__gte=day_start(ctx.date), created_at__lt=day_end_exclusive(ctx.date))
        .count()
    )
    return count_value(count)


_register(
    MetricDef(
        key="disclosure_rate",
        title=_("Disclosure rate"),
        description=_(
            "Share of AI-cohort pull requests created on a day whose AI disclosure is partial or "
            "substantial (as opposed to missing or ambiguous)."
        ),
        unit="ratio",
        direction="higher_is_better",
        kind="ratio",
        levels=_ALL_LEVELS,
        supports_cohorts=False,
        calculator=RatioCalc(
            numerator=_disclosure_rate_numerator,
            denominator=_disclosure_rate_denominator,
            numerator_batch=batch_count(_disclosure_rate_numerator_population),
            denominator_batch=batch_count(_disclosure_rate_denominator_population),
        ),
        formula="AI-cohort PRs with a partial/substantial disclosure / all AI-cohort PRs, by created_at day",
    )
)


def _disclosure_mismatch_count_population(_cohort: str, date: datetime.date) -> QuerySet[PullRequest]:
    return (
        scoped_pull_requests(GLOBAL_SCOPE, cohort=Cohort.ALL)
        .filter(
            violations__rule_code=PolicyViolation.RuleCode.DISCLOSURE_MISMATCH,
            violations__status=PolicyViolation.Status.OPEN,
            created_at__gte=day_start(date),
            created_at__lt=day_end_exclusive(date),
        )
        .distinct()
    )


def _disclosure_mismatch_count_daily(ctx: DayContext) -> MetricValue:
    count = (
        scoped_pull_requests(ctx.scope, cohort=Cohort.ALL)
        .filter(
            violations__rule_code=PolicyViolation.RuleCode.DISCLOSURE_MISMATCH,
            violations__status=PolicyViolation.Status.OPEN,
            created_at__gte=day_start(ctx.date),
            created_at__lt=day_end_exclusive(ctx.date),
        )
        .distinct()
        .count()
    )
    return count_value(count)


_register(
    MetricDef(
        key="disclosure_mismatch_count",
        title=_("Disclosure mismatches"),
        description=_(
            "Pull requests created on a day with an open disclosure-mismatch violation — same "
            "population as the policy console's disclosure mismatch list."
        ),
        unit="count",
        direction="lower_is_better",
        kind="counter",
        levels=_ALL_LEVELS,
        supports_cohorts=False,
        calculator=CounterCalc(
            daily=_disclosure_mismatch_count_daily, batch=batch_count(_disclosure_mismatch_count_population)
        ),
        formula="count of PRs created on the day with an open DISCLOSURE_MISMATCH violation",
    )
)


def _ai_active_people_period(ctx: PeriodContext) -> MetricValue:
    count = (
        scoped_pull_requests(ctx.scope, cohort=Cohort.AI)
        .filter(
            created_at__gte=day_start(ctx.date_from),
            created_at__lt=day_end_exclusive(ctx.date_to),
            author__person__isnull=False,
        )
        .values("author__person_id")
        .distinct()
        .count()
    )
    return count_value(count)


_register(
    MetricDef(
        key="ai_active_people",
        title=_("AI-active people"),
        description=_(
            "Distinct people who authored at least one AI-cohort pull request in the period (not "
            "the sum of each day's distinct count)."
        ),
        unit="count",
        direction="neutral",
        kind="distribution",
        levels=_ALL_LEVELS,
        supports_cohorts=False,
        calculator=DistributionCalc(period=_ai_active_people_period),
        formula="distinct authors of AI-cohort PRs created in the period",
    )
)


def _violations_open_at_date(ctx: DayContext) -> MetricValue:
    count = (
        scoped_violations(ctx.scope)
        .filter(status=PolicyViolation.Status.OPEN, created_at__lt=day_end_exclusive(ctx.date))
        .count()
    )
    return count_value(count)


def _violations_open_at_date_by_person(ctx: DayContextMany) -> dict[int, MetricValue]:
    counts = Counter(
        scoped_violations(GLOBAL_SCOPE)
        .filter(
            status=PolicyViolation.Status.OPEN,
            created_at__lt=day_end_exclusive(ctx.date),
            pull_request__author__person_id__in=ctx.person_ids,
        )
        .values_list("pull_request__author__person_id", flat=True)
    )
    return {person_id: count_value(count) for person_id, count in counts.items()}


_register(
    MetricDef(
        key="violations_open",
        title=_("Open violations"),
        description=_(
            "Violations created on or before the end of the period that are still open — a "
            "current snapshot bounded by created_at, matching the policy console."
        ),
        unit="count",
        direction="lower_is_better",
        kind="state",
        levels=_ALL_LEVELS,
        supports_cohorts=False,
        calculator=StateCalc(
            at_date=_violations_open_at_date, at_date_by_person=_violations_open_at_date_by_person
        ),
        formula="open violations with created_at <= end of the day",
    )
)


def _violations_new_population(_cohort: str, date: datetime.date) -> QuerySet[PolicyViolation]:
    return scoped_violations(GLOBAL_SCOPE).filter(
        created_at__gte=day_start(date), created_at__lt=day_end_exclusive(date)
    )


def _violations_new_daily(ctx: DayContext) -> MetricValue:
    count = (
        scoped_violations(ctx.scope)
        .filter(created_at__gte=day_start(ctx.date), created_at__lt=day_end_exclusive(ctx.date))
        .count()
    )
    return count_value(count)


_register(
    MetricDef(
        key="violations_new",
        title=_("New violations"),
        description=_("Violations created on a day, regardless of their current status."),
        unit="count",
        direction="lower_is_better",
        kind="counter",
        levels=_ALL_LEVELS,
        supports_cohorts=False,
        calculator=CounterCalc(
            daily=_violations_new_daily,
            batch=batch_count(
                _violations_new_population,
                repo_field="pull_request__repository_id",
                person_field="pull_request__author__person_id",
            ),
        ),
        formula="count of violations with created_at on the day",
    )
)


def _violations_by_rule_counts(ctx: PeriodContext) -> Counter[str]:
    queryset = scoped_violations(ctx.scope).filter(
        created_at__gte=day_start(ctx.date_from), created_at__lt=day_end_exclusive(ctx.date_to)
    )
    return Counter(queryset.values_list("rule_code", flat=True))


def _violations_by_rule_period(ctx: PeriodContext) -> MetricValue:
    return count_value(sum(_violations_by_rule_counts(ctx).values()))


def _violations_by_rule_items(ctx: PeriodContext) -> tuple[BreakdownItem, ...]:
    return breakdown_value(_violations_by_rule_counts(ctx))


_register(
    MetricDef(
        key="violations_by_rule",
        title=_("Violations by rule"),
        description=_("Violations created in the period, grouped by rule code."),
        unit="breakdown",
        direction="neutral",
        kind="distribution",
        levels=_ALL_LEVELS,
        supports_cohorts=False,
        calculator=DistributionCalc(period=_violations_by_rule_period, breakdown=_violations_by_rule_items),
        formula="count of violations created in the period, grouped by rule_code",
    )
)
