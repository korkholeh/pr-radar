import datetime

import pytest

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory
from apps.metrics.calculators.base import DayContext, PeriodContext
from apps.metrics.models import Cohort, ScopeType
from apps.metrics.registry import get_metric
from apps.metrics.timeframe import day_start
from apps.metrics.types import MetricValue, Scope
from apps.policy.factories import PolicyViolationFactory
from apps.policy.models import PolicyViolation
from apps.policy.selectors import violations_by_rule as selector_violations_by_rule

pytestmark = pytest.mark.django_db

UNRESTRICTED = ScopeFilter(unrestricted=True, project_ids=None)
DAY = datetime.date(2026, 6, 15)


def _scope() -> Scope:
    return Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=UNRESTRICTED)


def _day_ctx(date: datetime.date = DAY) -> DayContext:
    return DayContext(scope=_scope(), cohort=Cohort.ALL, date=date)


def _period_ctx(date_from: datetime.date = DAY, date_to: datetime.date = DAY) -> PeriodContext:
    return PeriodContext(scope=_scope(), cohort=Cohort.ALL, date_from=date_from, date_to=date_to)


def _violation_created_on(day: datetime.date, **kwargs) -> PolicyViolation:
    violation = PolicyViolationFactory(pull_request=PullRequestFactory(), **kwargs)
    violation.created_at = day_start(day)
    violation.save(update_fields=["created_at"])
    return violation


def test_violations_new_counts_by_created_at_in_kyiv_days():
    _violation_created_on(DAY, rule_code=PolicyViolation.RuleCode.NO_TESTS)
    just_before = _violation_created_on(DAY, rule_code=PolicyViolation.RuleCode.SELF_MERGE)
    just_before.created_at = day_start(DAY) - datetime.timedelta(seconds=1)
    just_before.save(update_fields=["created_at"])

    metric_def = get_metric("violations_new")
    assert metric_def.calculator.daily(_day_ctx()) == MetricValue(1.0, 1)


def test_acknowledged_violation_leaves_violations_open_but_stays_in_violations_new():
    violation = _violation_created_on(DAY, status=PolicyViolation.Status.OPEN)
    violation.status = PolicyViolation.Status.ACKNOWLEDGED
    violation.save(update_fields=["status"])

    violations_new = get_metric("violations_new")
    violations_open = get_metric("violations_open")
    assert violations_new.calculator.daily(_day_ctx()) == MetricValue(1.0, 1)
    assert violations_open.calculator.at_date(_day_ctx()) == MetricValue.empty()


def test_violations_open_counts_open_violations_created_on_or_before_the_date():
    _violation_created_on(DAY, status=PolicyViolation.Status.OPEN)
    _violation_created_on(DAY, status=PolicyViolation.Status.RESOLVED)

    metric_def = get_metric("violations_open")
    assert metric_def.calculator.at_date(_day_ctx()) == MetricValue(1.0, 1)
    day_before = _day_ctx(date=DAY - datetime.timedelta(days=1))
    assert metric_def.calculator.at_date(day_before) == MetricValue.empty()


def test_violations_by_rule_matches_the_policy_console_selector_for_the_same_window():
    _violation_created_on(DAY, rule_code=PolicyViolation.RuleCode.NO_TESTS)
    _violation_created_on(DAY, rule_code=PolicyViolation.RuleCode.NO_TESTS)
    _violation_created_on(DAY, rule_code=PolicyViolation.RuleCode.SELF_MERGE)

    metric_def = get_metric("violations_by_rule")
    registry_counts = {item.label: int(item.value) for item in metric_def.calculator.breakdown(_period_ctx())}

    console_rows = selector_violations_by_rule(UNRESTRICTED, DAY, DAY)
    console_counts = {row.rule_code: row.count for row in console_rows}

    assert (
        registry_counts
        == console_counts
        == {
            PolicyViolation.RuleCode.NO_TESTS: 2,
            PolicyViolation.RuleCode.SELF_MERGE: 1,
        }
    )
