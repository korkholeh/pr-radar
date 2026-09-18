import datetime

import pytest

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory
from apps.activity.models import AIDisclosure, AIStatus
from apps.metrics.calculators.base import DayContext, PeriodContext
from apps.metrics.models import Cohort, ScopeType
from apps.metrics.registry import CounterCalc, DistributionCalc, RatioCalc, get_metric
from apps.metrics.types import MetricValue, Scope

pytestmark = pytest.mark.django_db

UNRESTRICTED = ScopeFilter(unrestricted=True, project_ids=None)
DAY = datetime.date(2026, 6, 15)


def _scope() -> Scope:
    return Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=UNRESTRICTED)


def _day_ctx(cohort: str = Cohort.ALL, date: datetime.date = DAY) -> DayContext:
    return DayContext(scope=_scope(), cohort=cohort, date=date)


def _period_ctx(cohort: str = Cohort.ALL, date_from: datetime.date = DAY, date_to: datetime.date = DAY):
    return PeriodContext(scope=_scope(), cohort=cohort, date_from=date_from, date_to=date_to)


def _pr(**kwargs):
    kwargs.setdefault("created_at", datetime.datetime(2026, 6, 15, 10, tzinfo=datetime.UTC))
    return PullRequestFactory(**kwargs)


def test_ai_pr_share_is_ai_merged_over_all_merged():
    # 1 AI merge, 3 merges total on the day -> 1/3.
    merged_at = datetime.datetime(2026, 6, 15, 10, tzinfo=datetime.UTC)
    _pr(ai_status=AIStatus.AI_EXPLICIT, state="merged", merged_at=merged_at)
    _pr(ai_status=AIStatus.NO_AI, state="merged", merged_at=merged_at)
    _pr(ai_status=AIStatus.NO_AI, state="merged", merged_at=merged_at)
    _pr(ai_status=AIStatus.AI_EXPLICIT, state="open")  # not merged: excluded from both sides

    metric_def = get_metric("ai_pr_share")
    assert isinstance(metric_def.calculator, RatioCalc)
    numerator = metric_def.calculator.numerator(_day_ctx())
    denominator = metric_def.calculator.denominator(_day_ctx())
    assert numerator == MetricValue(1.0, 1)
    assert denominator == MetricValue(3.0, 3)


def test_ai_pr_count_counts_ai_cohort_prs_created_on_the_day():
    _pr(ai_status=AIStatus.AI_EXPLICIT)
    _pr(ai_status=AIStatus.AI_DISCLOSED)
    _pr(ai_status=AIStatus.NO_AI)

    metric_def = get_metric("ai_pr_count")
    assert isinstance(metric_def.calculator, CounterCalc)
    assert metric_def.calculator.daily(_day_ctx()) == MetricValue(2.0, 2)


def test_ai_status_breakdown_groups_by_status_and_drops_zero_labels():
    _pr(ai_status=AIStatus.AI_EXPLICIT)
    _pr(ai_status=AIStatus.AI_EXPLICIT)
    _pr(ai_status=AIStatus.NO_AI)

    metric_def = get_metric("ai_status_breakdown")
    assert isinstance(metric_def.calculator, DistributionCalc)
    assert metric_def.calculator.period(_period_ctx()) == MetricValue(3.0, 3)
    items = {item.label: item.value for item in metric_def.calculator.breakdown(_period_ctx())}
    assert items == {AIStatus.AI_EXPLICIT: 2.0, AIStatus.NO_AI: 1.0}
    assert AIStatus.AI_SUSPECTED not in items


def test_ai_tool_breakdown_ignores_prs_with_no_disclosed_tool():
    _pr(ai_status=AIStatus.AI_EXPLICIT, ai_tools=["claude_code", "copilot"])
    _pr(ai_status=AIStatus.AI_EXPLICIT, ai_tools=["claude_code"])
    _pr(ai_status=AIStatus.AI_EXPLICIT, ai_tools=[])  # no disclosed tool -> absent, not a zero row

    metric_def = get_metric("ai_tool_breakdown")
    items = {item.label: item.value for item in metric_def.calculator.breakdown(_period_ctx())}
    assert items == {"claude_code": 2.0, "copilot": 1.0}


def test_disclosure_rate_is_valid_disclosures_over_all_ai_cohort_prs():
    # valid disclosure = PARTIAL or SUBSTANTIAL; MISSING/AMBIGUOUS/NONE are not.
    _pr(ai_status=AIStatus.AI_EXPLICIT, ai_disclosure=AIDisclosure.SUBSTANTIAL)
    _pr(ai_status=AIStatus.AI_EXPLICIT, ai_disclosure=AIDisclosure.PARTIAL)
    _pr(ai_status=AIStatus.AI_EXPLICIT, ai_disclosure=AIDisclosure.MISSING)
    _pr(ai_status=AIStatus.AI_EXPLICIT, ai_disclosure=AIDisclosure.AMBIGUOUS)

    metric_def = get_metric("disclosure_rate")
    numerator = metric_def.calculator.numerator(_day_ctx())
    denominator = metric_def.calculator.denominator(_day_ctx())
    assert numerator == MetricValue(2.0, 2)
    assert denominator == MetricValue(4.0, 4)


def test_disclosure_mismatch_count_matches_open_disclosure_mismatch_violations():
    from apps.policy.factories import PolicyViolationFactory
    from apps.policy.models import PolicyViolation

    mismatched_pr = _pr()
    PolicyViolationFactory(
        pull_request=mismatched_pr,
        rule_code=PolicyViolation.RuleCode.DISCLOSURE_MISMATCH,
        status=PolicyViolation.Status.OPEN,
    )
    resolved_pr = _pr()
    PolicyViolationFactory(
        pull_request=resolved_pr,
        rule_code=PolicyViolation.RuleCode.DISCLOSURE_MISMATCH,
        status=PolicyViolation.Status.RESOLVED,
    )
    _pr()  # no violation at all

    metric_def = get_metric("disclosure_mismatch_count")
    assert metric_def.calculator.daily(_day_ctx()) == MetricValue(1.0, 1)


def test_ai_active_people_is_distinct_not_the_sum_of_daily_counts():
    from apps.catalog.factories import IdentityFactory, PersonFactory

    person_a = IdentityFactory(person=PersonFactory())
    person_b = IdentityFactory(person=PersonFactory())
    day_one = datetime.date(2026, 6, 15)
    day_two = datetime.date(2026, 6, 16)

    _pr(
        author=person_a,
        ai_status=AIStatus.AI_EXPLICIT,
        created_at=datetime.datetime(2026, 6, 15, 10, tzinfo=datetime.UTC),
    )
    _pr(
        author=person_a,
        ai_status=AIStatus.AI_EXPLICIT,
        created_at=datetime.datetime(2026, 6, 16, 10, tzinfo=datetime.UTC),
    )
    _pr(
        author=person_b,
        ai_status=AIStatus.AI_EXPLICIT,
        created_at=datetime.datetime(2026, 6, 16, 10, tzinfo=datetime.UTC),
    )

    metric_def = get_metric("ai_active_people")
    assert isinstance(metric_def.calculator, DistributionCalc)
    period = _period_ctx(date_from=day_one, date_to=day_two)
    # person_a active both days, person_b active on day_two -> 2 distinct people, not 3.
    assert metric_def.calculator.period(period) == MetricValue(2.0, 2)
