"""`CHART_REGISTRY`: the six charts spec §10.3 puts on a dashboard page (plan §4). Every builder
calls the **same** `metrics.compute()` the KPI cards use, with the **same** `DashboardParams`, so a
chart and a KPI card can never disagree (acceptance criterion #3). Colours travel as **token
names** (`color_token`), never a literal, resolved client-side by `charts.js` — what keeps
`tests/test_no_hardcoded_colors.py` green and satisfies ADR 0008."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from django.utils.functional import Promise
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from apps.dashboards.params import DashboardParams
from apps.metrics.models import Cohort, ScopeType
from apps.metrics.services import compute
from apps.metrics.timeframe import bucket_ranges
from apps.metrics.types import Scope
from apps.policy.messages import rule_label

CHART_MAX_BUCKETS = 31
_GRANULARITY_ORDER = ("day", "week", "month")
_ALL_LEVELS = frozenset(ScopeType.values)
_REVIEWS_LEVELS = frozenset({ScopeType.GLOBAL, ScopeType.PROJECT, ScopeType.REPO})
_SIZE_BUCKET_ORDER = ("XS", "S", "M", "L", "XL")
_REVIEWER_LOAD_TOP_N = 20


@dataclass(frozen=True)
class ChartDataset:
    label: str
    color_token: str
    data: list[float | None]


@dataclass(frozen=True)
class ChartPayload:
    key: str
    type: str
    stacked: bool
    unit: str
    labels: list[str]
    x_title: str
    y_title: str
    datasets: list[ChartDataset]
    empty: bool
    empty_message: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "type": self.type,
            "stacked": self.stacked,
            "unit": self.unit,
            "labels": self.labels,
            "x_title": self.x_title,
            "y_title": self.y_title,
            "datasets": [
                {"label": dataset.label, "color_token": dataset.color_token, "data": dataset.data}
                for dataset in self.datasets
            ],
            "empty": self.empty,
            "empty_message": self.empty_message,
        }


@dataclass(frozen=True)
class ChartSpec:
    key: str
    title: Promise
    chart_type: str
    stacked: bool
    metric_keys: tuple[str, ...]
    build: Callable[[Scope, DashboardParams], ChartPayload]
    levels: frozenset[str] = _ALL_LEVELS


def _empty_message() -> str:
    return gettext("No data in this period.")


def _bucket_labels(series) -> list[str]:
    return [point.date_from.isoformat() for point in series]


def _clamped_granularity(date_from, date_to, granularity: str) -> str:
    """Coarsens `granularity` (never refines it) so a chart bucketed per bucket never issues more
    than `CHART_MAX_BUCKETS` `compute()` calls (RISKS row 10) — a user forcing `granularity=day`
    over a year must not cost 365 queries."""
    start = _GRANULARITY_ORDER.index(granularity)
    for candidate in _GRANULARITY_ORDER[start:]:
        if len(bucket_ranges(date_from, date_to, candidate)) <= CHART_MAX_BUCKETS:
            return candidate
    return _GRANULARITY_ORDER[-1]


def _build_throughput(scope: Scope, params: DashboardParams) -> ChartPayload:
    ai_result = compute(
        ["prs_merged"],
        scope,
        params.date_from,
        params.date_to,
        cohort=Cohort.AI,
        granularity=params.granularity,
    )["prs_merged"]
    non_ai_result = compute(
        ["prs_merged"],
        scope,
        params.date_from,
        params.date_to,
        cohort=Cohort.NON_AI,
        granularity=params.granularity,
    )["prs_merged"]
    datasets = [
        ChartDataset(gettext("AI"), "--series-ai", [point.value for point in ai_result.series]),
        ChartDataset(gettext("Non-AI"), "--series-non-ai", [point.value for point in non_ai_result.series]),
    ]
    empty = not any(point.value is not None for point in (*ai_result.series, *non_ai_result.series))
    return ChartPayload(
        key="throughput",
        type="bar",
        stacked=True,
        unit="count",
        labels=_bucket_labels(ai_result.series),
        x_title=gettext("Date"),
        y_title=gettext("Pull requests merged"),
        datasets=datasets,
        empty=empty,
        empty_message=_empty_message() if empty else None,
    )


def _build_ai_adoption(scope: Scope, params: DashboardParams) -> ChartPayload:
    result_set = compute(
        ["ai_pr_share", "disclosure_rate"],
        scope,
        params.date_from,
        params.date_to,
        cohort=Cohort.ALL,
        granularity=params.granularity,
    )
    share_series = result_set["ai_pr_share"].series
    disclosure_series = result_set["disclosure_rate"].series
    datasets = [
        ChartDataset(gettext("AI PR share"), "--series-1", [point.value for point in share_series]),
        ChartDataset(gettext("Disclosure rate"), "--series-2", [point.value for point in disclosure_series]),
    ]
    empty = not any(point.value is not None for point in (*share_series, *disclosure_series))
    return ChartPayload(
        key="ai_adoption",
        type="line",
        stacked=False,
        unit="ratio",
        labels=_bucket_labels(share_series),
        x_title=gettext("Date"),
        y_title=gettext("Share"),
        datasets=datasets,
        empty=empty,
        empty_message=_empty_message() if empty else None,
    )


_LATENCY_METRIC_KEYS = (
    "lead_time_p50",
    "lead_time_p90",
    "time_to_first_review_p50",
    "time_to_first_review_p90",
)
_LATENCY_LABELS = {
    "lead_time_p50": _("Lead time (p50)"),
    "lead_time_p90": _("Lead time (p90)"),
    "time_to_first_review_p50": _("Time to first review (p50)"),
    "time_to_first_review_p90": _("Time to first review (p90)"),
}
_LATENCY_COLOR_TOKENS = {
    "lead_time_p50": "--series-1",
    "lead_time_p90": "--series-2",
    "time_to_first_review_p50": "--series-3",
    "time_to_first_review_p90": "--series-4",
}


def _build_latency(scope: Scope, params: DashboardParams) -> ChartPayload:
    result_set = compute(
        list(_LATENCY_METRIC_KEYS),
        scope,
        params.date_from,
        params.date_to,
        cohort=Cohort.ALL,
        granularity=params.granularity,
    )
    datasets = [
        ChartDataset(
            gettext(str(_LATENCY_LABELS[key])),
            _LATENCY_COLOR_TOKENS[key],
            [point.value for point in result_set[key].series],
        )
        for key in _LATENCY_METRIC_KEYS
    ]
    empty = not any(
        point.value is not None for key in _LATENCY_METRIC_KEYS for point in result_set[key].series
    )
    return ChartPayload(
        key="latency",
        type="line",
        stacked=False,
        unit="duration",
        labels=_bucket_labels(result_set[_LATENCY_METRIC_KEYS[0]].series),
        x_title=gettext("Date"),
        y_title=gettext("Duration"),
        datasets=datasets,
        empty=empty,
        empty_message=_empty_message() if empty else None,
    )


def _build_pr_size_distribution(scope: Scope, params: DashboardParams) -> ChartPayload:
    # Only `.breakdown` is read below (a bucketed bar chart, not a time series): `include_series=False`
    # (T11) skips the per-bucket raw-row query this distribution metric would otherwise pay for.
    ai_result = compute(
        ["pr_size_buckets"],
        scope,
        params.date_from,
        params.date_to,
        cohort=Cohort.AI,
        granularity=params.granularity,
        include_series=False,
    )["pr_size_buckets"]
    non_ai_result = compute(
        ["pr_size_buckets"],
        scope,
        params.date_from,
        params.date_to,
        cohort=Cohort.NON_AI,
        granularity=params.granularity,
        include_series=False,
    )["pr_size_buckets"]
    ai_by_label = {item.label: item.value or 0.0 for item in ai_result.breakdown}
    non_ai_by_label = {item.label: item.value or 0.0 for item in non_ai_result.breakdown}
    datasets = [
        ChartDataset(
            gettext("AI"), "--series-ai", [ai_by_label.get(label, 0.0) for label in _SIZE_BUCKET_ORDER]
        ),
        ChartDataset(
            gettext("Non-AI"),
            "--series-non-ai",
            [non_ai_by_label.get(label, 0.0) for label in _SIZE_BUCKET_ORDER],
        ),
    ]
    empty = not ai_by_label and not non_ai_by_label
    return ChartPayload(
        key="pr_size_distribution",
        type="bar",
        stacked=True,
        unit="count",
        labels=list(_SIZE_BUCKET_ORDER),
        x_title=gettext("Size"),
        y_title=gettext("Pull requests"),
        datasets=datasets,
        empty=empty,
        empty_message=_empty_message() if empty else None,
    )


def _build_churn_rework(scope: Scope, params: DashboardParams) -> ChartPayload:
    keys = ["churn_21d", "rework_rate"]
    # Only `.value` is read below (two static bars, not a time series): `include_series=False`
    # (T11) skips the per-bucket series `churn_21d` (a distribution metric) would otherwise pay for.
    ai_set = compute(
        keys,
        scope,
        params.date_from,
        params.date_to,
        cohort=Cohort.AI,
        granularity=params.granularity,
        include_series=False,
    )
    non_ai_set = compute(
        keys,
        scope,
        params.date_from,
        params.date_to,
        cohort=Cohort.NON_AI,
        granularity=params.granularity,
        include_series=False,
    )
    datasets = [
        ChartDataset(gettext("AI"), "--series-ai", [ai_set[key].value for key in keys]),
        ChartDataset(gettext("Non-AI"), "--series-non-ai", [non_ai_set[key].value for key in keys]),
    ]
    empty = all(value is None for dataset in datasets for value in dataset.data)
    return ChartPayload(
        key="churn_rework",
        type="bar",
        stacked=False,
        unit="ratio",
        labels=[gettext("21-day churn"), gettext("Rework rate")],
        x_title=gettext("Metric"),
        y_title=gettext("Share"),
        datasets=datasets,
        empty=empty,
        empty_message=_empty_message() if empty else None,
    )


def _build_violations_by_rule(scope: Scope, params: DashboardParams) -> ChartPayload:
    granularity = _clamped_granularity(params.date_from, params.date_to, params.granularity)
    buckets = bucket_ranges(params.date_from, params.date_to, granularity)
    if len(buckets) > CHART_MAX_BUCKETS:
        # "month" is the coarsest granularity `bucket_ranges()` knows, so a custom range spanning
        # more than CHART_MAX_BUCKETS months (~2.6 years) cannot be coarsened further — keep the
        # most recent buckets instead, which is what a "how did this move recently" chart wants.
        buckets = buckets[-CHART_MAX_BUCKETS:]
    # Only `.breakdown` is read below, once per outer bucket: `include_series=False` (T11) skips the
    # nested per-inner-bucket series each of these ~31 calls would otherwise build (previously the
    # dominant cost of this chart — see DECISIONS p11/implement).
    per_bucket = [
        compute(
            ["violations_by_rule"],
            scope,
            bucket_from,
            bucket_to,
            cohort=Cohort.ALL,
            granularity="day",
            include_series=False,
        )["violations_by_rule"]
        for bucket_from, bucket_to in buckets
    ]
    rule_codes = sorted({item.label for result in per_bucket for item in result.breakdown})
    datasets = []
    for index, rule_code in enumerate(rule_codes):
        color_token = f"--series-{(index % 8) + 1}"
        data = [
            next((item.value for item in result.breakdown if item.label == rule_code), 0.0)
            for result in per_bucket
        ]
        datasets.append(ChartDataset(rule_label(rule_code), color_token, data))
    empty = not rule_codes
    return ChartPayload(
        key="violations_by_rule",
        type="bar",
        stacked=True,
        unit="count",
        labels=[bucket_from.isoformat() for bucket_from, _bucket_to in buckets],
        x_title=gettext("Date"),
        y_title=gettext("Violations"),
        datasets=datasets,
        empty=empty,
        empty_message=_empty_message() if empty else None,
    )


def _build_reviewer_load(scope: Scope, params: DashboardParams) -> ChartPayload:
    """The Reviews page's own chart (plan §4): `reviews.reviewer_load()` is a raw-row selector, not
    a `compute()` aggregate (same documented exception as `rows.py`'s per-row builders), capped at
    `_REVIEWER_LOAD_TOP_N` people so the bar chart stays readable — the full ranking is the page's
    table, this is the at-a-glance view."""
    from apps.dashboards.reviews import reviewer_load

    pairs = reviewer_load(scope, params)[:_REVIEWER_LOAD_TOP_N]
    labels = [person.display_name for person, _count in pairs]
    data: list[float | None] = [float(count) for _person, count in pairs]
    empty = not pairs
    return ChartPayload(
        key="reviewer_load",
        type="bar",
        stacked=False,
        unit="count",
        labels=labels,
        x_title=gettext("Reviewer"),
        y_title=gettext("Reviews given"),
        datasets=[ChartDataset(gettext("Reviews given"), "--series-1", data)],
        empty=empty,
        empty_message=_empty_message() if empty else None,
    )


CHART_REGISTRY: dict[str, ChartSpec] = {
    spec.key: spec
    for spec in (
        ChartSpec("throughput", _("Throughput"), "bar", True, ("prs_merged",), _build_throughput),
        ChartSpec(
            "ai_adoption",
            _("AI adoption"),
            "line",
            False,
            ("ai_pr_share", "disclosure_rate"),
            _build_ai_adoption,
        ),
        ChartSpec("latency", _("Latency"), "line", False, _LATENCY_METRIC_KEYS, _build_latency),
        ChartSpec(
            "pr_size_distribution",
            _("PR size distribution"),
            "bar",
            True,
            ("pr_size_buckets",),
            _build_pr_size_distribution,
        ),
        ChartSpec(
            "churn_rework",
            _("Churn & rework"),
            "bar",
            False,
            ("churn_21d", "rework_rate"),
            _build_churn_rework,
        ),
        ChartSpec(
            "violations_by_rule",
            _("Violations by rule"),
            "bar",
            True,
            ("violations_by_rule",),
            _build_violations_by_rule,
        ),
        ChartSpec(
            "reviewer_load",
            _("Reviewer load"),
            "bar",
            False,
            ("reviews_given",),
            _build_reviewer_load,
            levels=_REVIEWS_LEVELS,
        ),
    )
}

# Which charts a page renders, in order — `CHART_REGISTRY` alone no longer decides this (plan T15):
# a dashboard page (Overview/Project/Repository/Person) renders `DASHBOARD_CHART_KEYS`, the Reviews
# page renders `REVIEWS_CHART_KEYS`, so `reviewer_load` shows up once, on its own page.
DASHBOARD_CHART_KEYS: tuple[str, ...] = (
    "throughput",
    "ai_adoption",
    "latency",
    "pr_size_distribution",
    "churn_rework",
    "violations_by_rule",
)
REVIEWS_CHART_KEYS: tuple[str, ...] = ("reviewer_load",)


def chart_available_at_level(spec: ChartSpec, scope_type: str) -> bool:
    return scope_type in spec.levels
