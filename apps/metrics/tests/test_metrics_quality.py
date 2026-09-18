import datetime

import pytest

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import (
    CheckStatusFactory,
    PRFileFactory,
    PullRequestFactory,
    ReviewCommentFactory,
)
from apps.activity.models import CheckStatus, PullRequest
from apps.churn.factories import ChurnResultFactory
from apps.churn.models import ChurnResult
from apps.metrics.calculators.base import DayContext, PeriodContext, ratio_value
from apps.metrics.models import Cohort, ScopeType
from apps.metrics.registry import get_metric
from apps.metrics.types import MetricValue, Scope

pytestmark = pytest.mark.django_db

UNRESTRICTED = ScopeFilter(unrestricted=True, project_ids=None)
DAY = datetime.date(2026, 6, 15)
UTC = datetime.UTC


def _scope() -> Scope:
    return Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=UNRESTRICTED)


def _day_ctx(date: datetime.date = DAY) -> DayContext:
    return DayContext(scope=_scope(), cohort=Cohort.ALL, date=date)


def _period_ctx(date_from: datetime.date = DAY, date_to: datetime.date = DAY) -> PeriodContext:
    return PeriodContext(scope=_scope(), cohort=Cohort.ALL, date_from=date_from, date_to=date_to)


def _merged_pr(**kwargs):
    kwargs.setdefault("state", PullRequest.State.MERGED)
    kwargs.setdefault("merged_at", datetime.datetime(2026, 6, 15, 12, tzinfo=UTC))
    return PullRequestFactory(**kwargs)


def _ratio(metric_key: str, ctx=None):
    ctx = ctx or _day_ctx()
    metric_def = get_metric(metric_key)
    numerator = metric_def.calculator.numerator(ctx)
    denominator = metric_def.calculator.denominator(ctx)
    return ratio_value(numerator, denominator)


def test_review_rounds_avg_over_two_merged_prs():
    _merged_pr(review_rounds=1)
    _merged_pr(review_rounds=3)

    assert _ratio("review_rounds_avg") == MetricValue(2.0, 2)


def test_rework_rate_positive_and_negative_row():
    _merged_pr(commits_after_first_review=2)
    _merged_pr(commits_after_first_review=0)

    assert _ratio("rework_rate") == MetricValue(0.5, 2)


def test_ci_first_pass_rate_counts_a_successful_first_ci_commit():
    passed = _merged_pr()
    CheckStatusFactory(
        pull_request=passed, is_first_ci_commit=True, rollup_state=CheckStatus.RollupState.SUCCESS
    )

    assert _ratio("ci_first_pass_rate") == MetricValue(1.0, 1)


def test_ci_first_pass_rate_excludes_a_failed_first_ci_commit_from_the_numerator_only():
    failed = _merged_pr()
    CheckStatusFactory(
        pull_request=failed, is_first_ci_commit=True, rollup_state=CheckStatus.RollupState.FAILURE
    )

    assert _ratio("ci_first_pass_rate") == MetricValue(0.0, 1)


def test_ci_first_pass_rate_ignores_prs_with_no_first_ci_commit_check_status():
    passed = _merged_pr()
    CheckStatusFactory(
        pull_request=passed, is_first_ci_commit=True, rollup_state=CheckStatus.RollupState.SUCCESS
    )
    failed = _merged_pr()
    CheckStatusFactory(
        pull_request=failed, is_first_ci_commit=True, rollup_state=CheckStatus.RollupState.FAILURE
    )
    _merged_pr()  # no CheckStatus row at all -> excluded from both sides

    assert _ratio("ci_first_pass_rate") == MetricValue(0.5, 2)


def test_revert_rate_positive_and_negative_row():
    reverted = _merged_pr()
    _merged_pr(reverts_pr=reverted)  # the reverting PR itself is not "a reverted PR"
    _merged_pr()  # never reverted

    # 1 of 3 merged PRs (`reverted`) was later reverted.
    assert _ratio("revert_rate") == MetricValue(1 / 3, 3)


def test_test_change_ratio_positive_and_negative_row():
    _merged_pr(has_test_changes=True)
    _merged_pr(has_test_changes=False)

    assert _ratio("test_change_ratio") == MetricValue(0.5, 2)


def test_test_lines_ratio_test_lines_over_all_effective_lines():
    pr_with_tests = _merged_pr(effective_additions=80, effective_deletions=20)  # 100 total
    PRFileFactory(pull_request=pr_with_tests, is_test=True, is_excluded=False, additions=30, deletions=10)
    PRFileFactory(pull_request=pr_with_tests, is_test=False, is_excluded=False, additions=50, deletions=10)
    _merged_pr(effective_additions=50, effective_deletions=50)  # 100 total, no test files

    # test lines = 40, all lines = 200 -> 0.2
    assert _ratio("test_lines_ratio") == MetricValue(0.2, 2)


def test_review_comments_per_100_lines():
    pr = _merged_pr(effective_additions=100, effective_deletions=0)  # 100 lines
    ReviewCommentFactory(pull_request=pr)
    ReviewCommentFactory(pull_request=pr)

    # 2 comments * 100 / 100 lines = 2.0
    assert _ratio("review_comments_per_100_lines") == MetricValue(2.0, 1)


def test_rubber_stamp_rate_positive_and_negative_row():
    _merged_pr(is_rubber_stamp=True)
    _merged_pr(is_rubber_stamp=False)

    assert _ratio("rubber_stamp_rate") == MetricValue(0.5, 2)


def test_self_merge_rate_positive_and_negative_row():
    _merged_pr(is_self_merged=True)
    _merged_pr(is_self_merged=False)

    assert _ratio("self_merge_rate") == MetricValue(0.5, 2)


def test_churn_21d_is_none_without_churn_result_rows_and_median_with_them():
    metric_def = get_metric("churn_21d")
    assert metric_def.calculator.period(_period_ctx()) == MetricValue.empty()

    pr_a = _merged_pr()
    pr_b = _merged_pr()
    ChurnResultFactory(pull_request=pr_a, window_days=21, status=ChurnResult.Status.OK, churn_ratio=0.1)
    ChurnResultFactory(pull_request=pr_b, window_days=21, status=ChurnResult.Status.OK, churn_ratio=0.3)

    assert metric_def.calculator.period(_period_ctx()) == MetricValue(0.2, 2)


def test_followup_fix_rate_positive_and_negative_row():
    _merged_pr(has_followup_fix=True)
    _merged_pr(has_followup_fix=False)

    assert _ratio("followup_fix_rate") == MetricValue(0.5, 2)


def test_followup_fix_rate_is_registered_as_a_heuristic():
    from django.utils import translation

    metric_def = get_metric("followup_fix_rate")
    assert metric_def is not None
    assert metric_def.params["heuristic"] is True
    with translation.override("en"):
        assert "heuristic" in str(metric_def.title).lower()
