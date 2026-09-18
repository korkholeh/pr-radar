"""Quality metrics (spec §8.2): review/rework/CI/test/churn ratios over pull requests merged on a
day, plus `churn_21d` (a distribution read from `ChurnResult`) and `followup_fix_rate` (a ratio
over the `PullRequest.has_followup_fix` cache field materialised by `apps.activity.followup`)."""

from __future__ import annotations

import datetime
from collections.abc import Callable

from django.db.models import QuerySet, Sum
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _

from apps.activity.models import CheckStatus, PRFile, PullRequest, ReviewComment
from apps.catalog.setting_defs import SETTING_DEFS
from apps.churn.models import ChurnResult
from apps.metrics.calculators.base import (
    GLOBAL_SCOPE,
    DayContext,
    PeriodContext,
    batch_count,
    batch_sum,
    batch_value_over_population,
    count_value,
    median,
)
from apps.metrics.models import ScopeType
from apps.metrics.registry import BatchFunction, DistributionCalc, MetricDef, RatioCalc, _register
from apps.metrics.selectors import scoped_pull_requests
from apps.metrics.timeframe import day_end_exclusive, day_start
from apps.metrics.types import MetricValue

_ALL_LEVELS = frozenset(ScopeType.values)


def _merged_on_day(ctx: DayContext) -> QuerySet[PullRequest]:
    return scoped_pull_requests(ctx.scope, ctx.cohort).filter(
        merged_at__gte=day_start(ctx.date), merged_at__lt=day_end_exclusive(ctx.date)
    )


def _merged_on_day_population(cohort: str, date: datetime.date) -> QuerySet[PullRequest]:
    """`_merged_on_day()`'s population at `GLOBAL_SCOPE`, i.e. before any repository/project/
    person narrowing — the base every quality ratio's batch function groups by scope from."""
    return scoped_pull_requests(GLOBAL_SCOPE, cohort).filter(
        merged_at__gte=day_start(date), merged_at__lt=day_end_exclusive(date)
    )


def _merged_population_count(ctx: DayContext) -> MetricValue:
    return count_value(_merged_on_day(ctx).count())


_merged_population_count_batch = batch_count(_merged_on_day_population)


def _register_ratio_over_merged_prs(
    key: str,
    title: Promise,
    description: Promise,
    unit: str,
    direction: str,
    numerator: Callable[[DayContext], MetricValue],
    numerator_batch: BatchFunction,
    formula: str,
    params: dict[str, object] | None = None,
) -> None:
    _register(
        MetricDef(
            key=key,
            title=title,
            description=description,
            unit=unit,
            direction=direction,
            kind="ratio",
            levels=_ALL_LEVELS,
            supports_cohorts=True,
            calculator=RatioCalc(
                numerator=numerator,
                denominator=_merged_population_count,
                numerator_batch=numerator_batch,
                denominator_batch=_merged_population_count_batch,
            ),
            formula=formula,
            params=params or {},
        )
    )


def _review_rounds_population(cohort: str, date: datetime.date) -> QuerySet[PullRequest]:
    return _merged_on_day_population(cohort, date).filter(review_rounds__isnull=False)


def _review_rounds_sum(ctx: DayContext) -> MetricValue:
    queryset = _merged_on_day(ctx).filter(review_rounds__isnull=False)
    total = queryset.aggregate(total=Sum("review_rounds"))["total"] or 0
    count = queryset.count()
    return MetricValue.empty() if count == 0 else MetricValue(float(total), count)


def _review_rounds_denominator(ctx: DayContext) -> MetricValue:
    count = _merged_on_day(ctx).filter(review_rounds__isnull=False).count()
    return count_value(count)


_register(
    MetricDef(
        key="review_rounds_avg",
        title=_("Review rounds (average)"),
        description=_("Average number of review rounds (CHANGES_REQUESTED + 1) for PRs merged on a day."),
        unit="count",
        direction="lower_is_better",
        kind="ratio",
        levels=_ALL_LEVELS,
        supports_cohorts=True,
        calculator=RatioCalc(
            numerator=_review_rounds_sum,
            denominator=_review_rounds_denominator,
            numerator_batch=batch_sum(_review_rounds_population, sum_fields=("review_rounds",)),
            denominator_batch=batch_count(_review_rounds_population),
        ),
        formula="sum(review_rounds) / count of merged PRs with a known review_rounds, on the day",
    )
)


_register_ratio_over_merged_prs(
    "rework_rate",
    _("Rework rate"),
    _("Share of PRs merged on a day that had at least one commit after their first review."),
    "ratio",
    "lower_is_better",
    lambda ctx: count_value(_merged_on_day(ctx).filter(commits_after_first_review__gt=0).count()),
    batch_count(
        lambda cohort, date: _merged_on_day_population(cohort, date).filter(commits_after_first_review__gt=0)
    ),
    "PRs merged on the day with commits_after_first_review > 0 / PRs merged on the day",
)


def _ci_first_pass_numerator_population(cohort: str, date: datetime.date) -> QuerySet[CheckStatus]:
    return CheckStatus.objects.filter(
        pull_request__in=_merged_on_day_population(cohort, date),
        is_first_ci_commit=True,
        rollup_state=CheckStatus.RollupState.SUCCESS,
    )


def _ci_first_pass_numerator(ctx: DayContext) -> MetricValue:
    count = CheckStatus.objects.filter(
        pull_request__in=_merged_on_day(ctx),
        is_first_ci_commit=True,
        rollup_state=CheckStatus.RollupState.SUCCESS,
    ).count()
    return count_value(count)


def _ci_first_pass_denominator_population(cohort: str, date: datetime.date) -> QuerySet[CheckStatus]:
    return CheckStatus.objects.filter(
        pull_request__in=_merged_on_day_population(cohort, date), is_first_ci_commit=True
    )


def _ci_first_pass_denominator(ctx: DayContext) -> MetricValue:
    count = CheckStatus.objects.filter(pull_request__in=_merged_on_day(ctx), is_first_ci_commit=True).count()
    return count_value(count)


_CHECK_STATUS_REPO_FIELD = "pull_request__repository_id"
_CHECK_STATUS_PERSON_FIELD = "pull_request__author__person_id"


_register(
    MetricDef(
        key="ci_first_pass_rate",
        title=_("CI first-pass rate"),
        description=_(
            "Share of PRs merged on a day whose first CI-covered commit passed (SUCCESS). PRs "
            "with no first-CI-commit record are excluded from both sides."
        ),
        unit="ratio",
        direction="higher_is_better",
        kind="ratio",
        levels=_ALL_LEVELS,
        supports_cohorts=True,
        calculator=RatioCalc(
            numerator=_ci_first_pass_numerator,
            denominator=_ci_first_pass_denominator,
            numerator_batch=batch_count(
                _ci_first_pass_numerator_population,
                repo_field=_CHECK_STATUS_REPO_FIELD,
                person_field=_CHECK_STATUS_PERSON_FIELD,
            ),
            denominator_batch=batch_count(
                _ci_first_pass_denominator_population,
                repo_field=_CHECK_STATUS_REPO_FIELD,
                person_field=_CHECK_STATUS_PERSON_FIELD,
            ),
        ),
        formula=(
            "PRs merged on the day whose first-CI-commit check is SUCCESS / PRs with a first-CI-commit check"
        ),
    )
)


_register_ratio_over_merged_prs(
    "revert_rate",
    _("Revert rate"),
    _("Share of PRs merged on a day that were later reverted."),
    "ratio",
    "lower_is_better",
    lambda ctx: count_value(_merged_on_day(ctx).filter(reverted_by__isnull=False).distinct().count()),
    batch_count(
        lambda cohort, date: (
            _merged_on_day_population(cohort, date).filter(reverted_by__isnull=False).distinct()
        )
    ),
    "PRs merged on the day with a later revert / PRs merged on the day",
)


_register_ratio_over_merged_prs(
    "test_change_ratio",
    _("Test change ratio"),
    _("Share of PRs merged on a day that changed test code."),
    "ratio",
    "higher_is_better",
    lambda ctx: count_value(_merged_on_day(ctx).filter(has_test_changes=True).count()),
    batch_count(lambda cohort, date: _merged_on_day_population(cohort, date).filter(has_test_changes=True)),
    "PRs merged on the day with has_test_changes / PRs merged on the day",
)


def _lines_sum(queryset: QuerySet[PullRequest]) -> int:
    totals = queryset.aggregate(additions=Sum("effective_additions"), deletions=Sum("effective_deletions"))
    return (totals["additions"] or 0) + (totals["deletions"] or 0)


def _test_lines_numerator(ctx: DayContext) -> MetricValue:
    population = _merged_on_day(ctx)
    count = population.count()
    if count == 0:
        return MetricValue.empty()
    totals = PRFile.objects.filter(pull_request__in=population, is_test=True, is_excluded=False).aggregate(
        additions=Sum("additions"), deletions=Sum("deletions")
    )
    lines = (totals["additions"] or 0) + (totals["deletions"] or 0)
    return MetricValue(float(lines), count)


def _test_prfile_population(cohort: str, date: datetime.date) -> QuerySet[PRFile]:
    return PRFile.objects.filter(
        pull_request__in=_merged_on_day_population(cohort, date), is_test=True, is_excluded=False
    )


def _effective_lines_denominator(ctx: DayContext) -> MetricValue:
    """All effective lines changed by PRs merged on the day — the shared denominator of
    `test_lines_ratio` and `review_comments_per_100_lines`."""
    population = _merged_on_day(ctx)
    count = population.count()
    if count == 0:
        return MetricValue.empty()
    return MetricValue(float(_lines_sum(population)), count)


_effective_lines_denominator_batch = batch_sum(
    _merged_on_day_population, sum_fields=("effective_additions", "effective_deletions")
)

_register(
    MetricDef(
        key="test_lines_ratio",
        title=_("Test lines ratio"),
        description=_(
            "Test-file lines changed divided by all effective lines changed, for PRs merged on a day."
        ),
        unit="ratio",
        direction="higher_is_better",
        kind="ratio",
        levels=_ALL_LEVELS,
        supports_cohorts=True,
        calculator=RatioCalc(
            numerator=_test_lines_numerator,
            denominator=_effective_lines_denominator,
            numerator_batch=batch_value_over_population(
                population_factory=_merged_on_day_population,
                join_factory=_test_prfile_population,
                repo_field="repository_id",
                person_field="author__person_id",
                join_repo_field="pull_request__repository_id",
                join_person_field="pull_request__author__person_id",
                sum_fields=("additions", "deletions"),
            ),
            denominator_batch=_effective_lines_denominator_batch,
        ),
        formula="lines changed in non-excluded test files / all effective lines, PRs merged on the day",
    )
)


def _review_comments_per_100_lines_numerator(ctx: DayContext) -> MetricValue:
    population = _merged_on_day(ctx)
    count = population.count()
    if count == 0:
        return MetricValue.empty()
    comments = ReviewComment.objects.filter(pull_request__in=population).count()
    return MetricValue(float(comments) * 100, count)


def _review_comment_population(cohort: str, date: datetime.date) -> QuerySet[ReviewComment]:
    return ReviewComment.objects.filter(pull_request__in=_merged_on_day_population(cohort, date))


_register(
    MetricDef(
        key="review_comments_per_100_lines",
        title=_("Review comments per 100 lines"),
        description=_("Review comments per 100 effective lines changed, for PRs merged on a day."),
        unit="ratio",
        direction="neutral",
        kind="ratio",
        levels=_ALL_LEVELS,
        supports_cohorts=True,
        calculator=RatioCalc(
            numerator=_review_comments_per_100_lines_numerator,
            denominator=_effective_lines_denominator,
            numerator_batch=batch_value_over_population(
                population_factory=_merged_on_day_population,
                join_factory=_review_comment_population,
                repo_field="repository_id",
                person_field="author__person_id",
                join_repo_field="pull_request__repository_id",
                join_person_field="pull_request__author__person_id",
                scale=100.0,
            ),
            denominator_batch=_effective_lines_denominator_batch,
        ),
        formula="review comments * 100 / all effective lines, PRs merged on the day",
    )
)


_register_ratio_over_merged_prs(
    "rubber_stamp_rate",
    _("Rubber-stamp rate"),
    _(
        "Share of PRs merged on a day that were approved suspiciously fast with no comment on a "
        "large change (RUBBER_STAMP_MAX_MINUTES)."
    ),
    "ratio",
    "lower_is_better",
    lambda ctx: count_value(_merged_on_day(ctx).filter(is_rubber_stamp=True).count()),
    batch_count(lambda cohort, date: _merged_on_day_population(cohort, date).filter(is_rubber_stamp=True)),
    "PRs merged on the day with is_rubber_stamp / PRs merged on the day",
)


_register_ratio_over_merged_prs(
    "self_merge_rate",
    _("Self-merge rate"),
    _("Share of PRs merged on a day that were merged by their own author."),
    "ratio",
    "lower_is_better",
    lambda ctx: count_value(_merged_on_day(ctx).filter(is_self_merged=True).count()),
    batch_count(lambda cohort, date: _merged_on_day_population(cohort, date).filter(is_self_merged=True)),
    "PRs merged on the day with is_self_merged / PRs merged on the day",
)


def _churn_21d_period(ctx: PeriodContext) -> MetricValue:
    population = scoped_pull_requests(ctx.scope, ctx.cohort).filter(
        merged_at__gte=day_start(ctx.date_from), merged_at__lt=day_end_exclusive(ctx.date_to)
    )
    ratios = ChurnResult.objects.filter(
        pull_request__in=population, window_days=21, status=ChurnResult.Status.OK
    ).values_list("churn_ratio", flat=True)
    return median(list(ratios))


_register(
    MetricDef(
        key="churn_21d",
        title=_("21-day churn"),
        description=_(
            "Median share of a merged PR's lines that were themselves changed again within 21 "
            "days, for PRs merged in the period. None until manage.py compute_churn has run."
        ),
        unit="ratio",
        direction="lower_is_better",
        kind="distribution",
        levels=_ALL_LEVELS,
        supports_cohorts=True,
        calculator=DistributionCalc(period=_churn_21d_period),
        formula="median(churn_ratio) over 21-day ChurnResult rows for PRs merged in the period",
    )
)


_FOLLOWUP_FIX_SETTING_DEFAULTS = {d.key: d.default for d in SETTING_DEFS}

_register_ratio_over_merged_prs(
    "followup_fix_rate",
    _("Follow-up fix rate (heuristic)"),
    _(
        "(heuristic) Share of merged PRs followed within FOLLOWUP_FIX_WINDOW_DAYS by a "
        "hotfix/fix PR overlapping at least FOLLOWUP_FIX_FILE_OVERLAP of their files."
    ),
    "ratio",
    "lower_is_better",
    lambda ctx: count_value(_merged_on_day(ctx).filter(has_followup_fix=True).count()),
    batch_count(lambda cohort, date: _merged_on_day_population(cohort, date).filter(has_followup_fix=True)),
    "PRs merged on the day with a follow-up fix within the window / PRs merged on the day",
    params={
        "heuristic": True,
        "window_days": _FOLLOWUP_FIX_SETTING_DEFAULTS["FOLLOWUP_FIX_WINDOW_DAYS"],
        "file_overlap": _FOLLOWUP_FIX_SETTING_DEFAULTS["FOLLOWUP_FIX_FILE_OVERLAP"],
    },
)
