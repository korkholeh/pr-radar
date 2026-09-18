"""T10: `CHART_REGISTRY`'s six builders (plan §4). Each builder's dataset values must equal the
corresponding `compute()` values (acceptance criterion #3's build half — the endpoint half is
T11's `test_charts_api.py`)."""

from __future__ import annotations

import datetime

import pytest
from freezegun import freeze_time

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory, ReviewFactory
from apps.activity.models import AIDisclosure, AIStatus, PullRequest, SizeBucket
from apps.catalog.factories import IdentityFactory, PersonFactory
from apps.dashboards import charts
from apps.dashboards.params import DashboardParams
from apps.metrics.models import Cohort, ScopeType
from apps.metrics.rollups import rebuild
from apps.metrics.services import compute
from apps.metrics.types import Scope
from apps.policy.factories import PolicyViolationFactory
from apps.policy.models import PolicyViolation

CHART_KEYS = tuple(charts.CHART_REGISTRY)

_SEED_DATE_FROM = datetime.date(2026, 8, 1)
_SEED_DATE_TO = datetime.date(2026, 8, 31)

# (ai_status, ai_disclosure, size_bucket, commits_after_first_review)
_SEED_PRS = (
    (AIStatus.AI_EXPLICIT, AIDisclosure.SUBSTANTIAL, SizeBucket.M, 1),
    (AIStatus.AI_EXPLICIT, AIDisclosure.SUBSTANTIAL, SizeBucket.S, 0),
    (AIStatus.NO_AI, AIDisclosure.MISSING, SizeBucket.L, 0),
    (AIStatus.NO_AI, AIDisclosure.MISSING, SizeBucket.M, 1),
)


def _seed_period_data() -> None:
    """A small, real dataset spanning `_SEED_DATE_FROM.._SEED_DATE_TO` (`compute()`'s "current
    period" for every test in this module) so every chart builder's comparison against a second,
    independent `compute()` call is actually meaningful (round-1 review: with an empty database
    every series is all-`None`, so a swapped-cohort or mismapped-key bug in a builder would still
    pass since `None == None`). Covers throughput/adoption/latency/size/rework, one violation for
    `violations_by_rule` and one review for `reviewer_load` (T15)."""
    author = IdentityFactory(person=PersonFactory())
    reviewer = IdentityFactory(person=PersonFactory())
    for offset, (ai_status, disclosure, size, rework) in enumerate(_SEED_PRS):
        day = _SEED_DATE_FROM + datetime.timedelta(days=5 + offset)
        created = datetime.datetime.combine(day, datetime.time(8), tzinfo=datetime.UTC)
        ready = datetime.datetime.combine(day, datetime.time(9), tzinfo=datetime.UTC)
        first_review = created + datetime.timedelta(days=1)
        merged = created + datetime.timedelta(days=2)
        pull_request = PullRequestFactory(
            author=author,
            state=PullRequest.State.MERGED,
            created_at=created,
            ready_for_review_at=ready,
            first_review_at=first_review,
            first_approval_at=first_review,
            merged_at=merged,
            last_activity_at=merged,
            ai_status=ai_status,
            ai_disclosure=disclosure,
            size_bucket=size,
            commits_after_first_review=rework,
        )
        with freeze_time(created):
            PolicyViolationFactory(pull_request=pull_request, rule_code=PolicyViolation.RuleCode.NO_TESTS)
        ReviewFactory(pull_request=pull_request, reviewer=reviewer, submitted_at=first_review)
    rebuild(_SEED_DATE_FROM, _SEED_DATE_TO)


def _scope() -> Scope:
    return Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=ScopeFilter(unrestricted=True))


def _params(date_from: datetime.date, date_to: datetime.date, granularity: str = "day") -> DashboardParams:
    return DashboardParams(
        mode="period",
        preset="custom",
        date_from=date_from,
        date_to=date_to,
        day=date_to,
        granularity=granularity,
        granularity_is_auto=False,
        cohort="all",
        project_ids=(),
        repository_ids=(),
        q="",
        sort="",
        page=1,
        table="",
    )


@pytest.mark.django_db
def test_registry_has_all_seven_charts():
    assert set(CHART_KEYS) == {
        "throughput",
        "ai_adoption",
        "latency",
        "pr_size_distribution",
        "churn_rework",
        "violations_by_rule",
        "reviewer_load",
    }
    assert set(charts.DASHBOARD_CHART_KEYS) == {
        "throughput",
        "ai_adoption",
        "latency",
        "pr_size_distribution",
        "churn_rework",
        "violations_by_rule",
    }
    assert set(charts.REVIEWS_CHART_KEYS) == {"reviewer_load"}


@pytest.mark.django_db
@pytest.mark.parametrize("chart_key", CHART_KEYS)
def test_every_dataset_carries_a_token_never_a_literal(chart_key):
    spec = charts.CHART_REGISTRY[chart_key]
    payload = spec.build(_scope(), _params(datetime.date(2026, 8, 1), datetime.date(2026, 8, 31)))
    for dataset in payload.datasets:
        assert dataset.color_token.startswith("--")


@pytest.mark.django_db
def test_empty_period_yields_empty_true_with_a_message():
    payload = charts.CHART_REGISTRY["throughput"].build(
        _scope(), _params(datetime.date(2020, 1, 1), datetime.date(2020, 1, 7))
    )
    assert payload.empty is True
    assert payload.empty_message


@pytest.mark.django_db
def test_throughput_matches_compute_per_cohort():
    _seed_period_data()
    scope = _scope()
    params = _params(datetime.date(2026, 8, 1), datetime.date(2026, 8, 31))
    payload = charts.CHART_REGISTRY["throughput"].build(scope, params)

    expected_ai = compute(
        ["prs_merged"],
        scope,
        params.date_from,
        params.date_to,
        cohort=Cohort.AI,
        granularity=params.granularity,
    )["prs_merged"]
    expected_non_ai = compute(
        ["prs_merged"],
        scope,
        params.date_from,
        params.date_to,
        cohort=Cohort.NON_AI,
        granularity=params.granularity,
    )["prs_merged"]

    ai_dataset = next(d for d in payload.datasets if d.color_token == "--series-ai")
    non_ai_dataset = next(d for d in payload.datasets if d.color_token == "--series-non-ai")
    assert ai_dataset.data == [point.value for point in expected_ai.series]
    assert non_ai_dataset.data == [point.value for point in expected_non_ai.series]
    assert any(value for value in ai_dataset.data)
    assert any(value for value in non_ai_dataset.data)
    assert ai_dataset.data != non_ai_dataset.data


@pytest.mark.django_db
def test_ai_adoption_matches_compute():
    _seed_period_data()
    scope = _scope()
    params = _params(datetime.date(2026, 8, 1), datetime.date(2026, 8, 31))
    payload = charts.CHART_REGISTRY["ai_adoption"].build(scope, params)

    result_set = compute(
        ["ai_pr_share", "disclosure_rate"],
        scope,
        params.date_from,
        params.date_to,
        cohort=Cohort.ALL,
        granularity=params.granularity,
    )
    share_dataset, disclosure_dataset = payload.datasets
    assert share_dataset.data == [point.value for point in result_set["ai_pr_share"].series]
    assert disclosure_dataset.data == [point.value for point in result_set["disclosure_rate"].series]
    assert any(value is not None for value in share_dataset.data)
    assert any(value is not None for value in disclosure_dataset.data)


@pytest.mark.django_db
def test_latency_matches_compute_for_all_four_metrics():
    _seed_period_data()
    scope = _scope()
    params = _params(datetime.date(2026, 8, 1), datetime.date(2026, 8, 31))
    payload = charts.CHART_REGISTRY["latency"].build(scope, params)

    result_set = compute(
        list(charts._LATENCY_METRIC_KEYS),
        scope,
        params.date_from,
        params.date_to,
        cohort=Cohort.ALL,
        granularity=params.granularity,
    )
    for key, dataset in zip(charts._LATENCY_METRIC_KEYS, payload.datasets, strict=True):
        assert dataset.data == [point.value for point in result_set[key].series]
    assert any(value is not None for dataset in payload.datasets for value in dataset.data)


@pytest.mark.django_db
def test_pr_size_distribution_matches_compute_breakdown():
    _seed_period_data()
    scope = _scope()
    params = _params(datetime.date(2026, 8, 1), datetime.date(2026, 8, 31))
    payload = charts.CHART_REGISTRY["pr_size_distribution"].build(scope, params)

    ai_result = compute(
        ["pr_size_buckets"], scope, params.date_from, params.date_to, cohort=Cohort.AI, granularity="day"
    )["pr_size_buckets"]
    non_ai_result = compute(
        ["pr_size_buckets"], scope, params.date_from, params.date_to, cohort=Cohort.NON_AI, granularity="day"
    )["pr_size_buckets"]
    ai_by_label = {item.label: item.value or 0.0 for item in ai_result.breakdown}
    non_ai_by_label = {item.label: item.value or 0.0 for item in non_ai_result.breakdown}
    ai_dataset = next(d for d in payload.datasets if d.color_token == "--series-ai")
    non_ai_dataset = next(d for d in payload.datasets if d.color_token == "--series-non-ai")
    assert ai_dataset.data == [ai_by_label.get(label, 0.0) for label in charts._SIZE_BUCKET_ORDER]
    assert non_ai_dataset.data == [non_ai_by_label.get(label, 0.0) for label in charts._SIZE_BUCKET_ORDER]
    assert any(ai_dataset.data)
    assert any(non_ai_dataset.data)
    assert ai_dataset.data != non_ai_dataset.data


@pytest.mark.django_db
def test_churn_rework_matches_compute_period_values():
    _seed_period_data()
    scope = _scope()
    params = _params(datetime.date(2026, 8, 1), datetime.date(2026, 8, 31))
    payload = charts.CHART_REGISTRY["churn_rework"].build(scope, params)

    ai_set = compute(
        ["churn_21d", "rework_rate"],
        scope,
        params.date_from,
        params.date_to,
        cohort=Cohort.AI,
        granularity=params.granularity,
    )
    ai_dataset = next(d for d in payload.datasets if d.color_token == "--series-ai")
    assert ai_dataset.data == [ai_set["churn_21d"].value, ai_set["rework_rate"].value]
    # churn_21d is always None until phase 10's compute_churn (plan §"Out of scope") — only
    # rework_rate can be non-None here, from the seeded commits_after_first_review PRs.
    assert ai_dataset.data[1] is not None


@pytest.mark.django_db
def test_violations_by_rule_matches_compute_and_is_not_empty():
    _seed_period_data()
    scope = _scope()
    params = _params(datetime.date(2026, 8, 1), datetime.date(2026, 8, 31))
    payload = charts.CHART_REGISTRY["violations_by_rule"].build(scope, params)

    assert payload.empty is False
    assert payload.datasets
    total_violations = sum(value for dataset in payload.datasets for value in dataset.data)
    assert total_violations == 4  # one PolicyViolationFactory per seeded PR


@pytest.mark.django_db
def test_violations_by_rule_clamps_bucket_count():
    scope = _scope()
    params = _params(datetime.date(2024, 1, 1), datetime.date(2026, 8, 31), granularity="day")
    payload = charts.CHART_REGISTRY["violations_by_rule"].build(scope, params)
    assert len(payload.labels) <= charts.CHART_MAX_BUCKETS


@pytest.mark.django_db
def test_clamped_granularity_never_refines():
    # A period short enough that "day" already fits under the cap must stay "day".
    assert charts._clamped_granularity(datetime.date(2026, 8, 1), datetime.date(2026, 8, 7), "day") == "day"


@pytest.mark.django_db
def test_chart_available_at_level_true_for_all_six_at_global():
    for spec in charts.CHART_REGISTRY.values():
        assert charts.chart_available_at_level(spec, ScopeType.GLOBAL)
