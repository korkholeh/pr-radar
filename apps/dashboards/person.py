"""Person page extras beyond the shared KPI row/chart cards `views.dashboard` already renders at
`ScopeType.PERSON` (plan §2): the person-vs-baseline comparison table and the per-rule violation
statistics. `metrics.compute()` stays the only aggregate read entry point — every baseline here is a
real median over raw rows at its own level (project or organization), never a median of per-person
medians (ADR 0007, RISKS row 1)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from django.db.models import Count

from apps.catalog.models import Person
from apps.dashboards.params import DashboardParams
from apps.metrics.models import Cohort, ScopeType
from apps.metrics.registry import get_metric
from apps.metrics.selectors import scoped_pull_requests, scoped_violations
from apps.metrics.services import compute
from apps.metrics.timeframe import day_end_exclusive, day_start
from apps.metrics.types import Scope
from apps.policy.models import PolicyViolation

# Compliance first, then quality, then flow, and volume last — `docs/POLICY.md`'s product
# constraint, from the standards themselves: the number of pull requests a person produced is not
# a measure of that person. A lead who opens this page before a 1:1 reads how the work was done
# before they read how much of it there was.
PERSON_COMPARISON_METRIC_KEYS: tuple[str, ...] = (
    "ai_pr_share",
    "structural_signal_rate",
    "rework_rate",
    "churn_21d",
    "followup_fix_rate",
    "lead_time_p50",
    "time_to_first_review_p50",
    "reviewer_response_p50",
    "pr_size_p50",
    "prs_merged",
    "reviews_given",
)


@dataclass(frozen=True)
class Deviation:
    """How far the person's value sits from a baseline: `delta` in the metric's own unit
    (percentage points for a ratio), `delta_ratio` relative to the baseline (`None` when the
    baseline is zero)."""

    delta: float
    delta_ratio: float | None


@dataclass(frozen=True)
class ComparisonRow:
    metric: str
    person_value: float | None
    person_sample: int
    project_value: float | None
    org_value: float | None
    previous_value: float | None
    below_min_sample: bool
    project_deviation: Deviation | None = None
    org_deviation: Deviation | None = None


def _deviation(metric: str, person_value: float | None, baseline: float | None) -> Deviation | None:
    """`None` for a counter: its project/organization value is the whole level's total, not a
    typical person's number, so "the person merged 90% fewer PRs than the organization" would be
    true and meaningless — and volume is not a measure of the person (`docs/POLICY.md`)."""
    if person_value is None or baseline is None or get_metric(metric).kind == "counter":
        return None
    delta = person_value - baseline
    return Deviation(delta=delta, delta_ratio=delta / abs(baseline) if baseline else None)


def _primary_project_id(scope: Scope, params: DashboardParams) -> int | None:
    """The project with the most of the person's PRs authored in the period — `None` when they
    authored none in the reader's scope, so the caller renders no project baseline rather than one
    for an arbitrary project."""
    start, end = day_start(params.date_from), day_end_exclusive(params.date_to)
    counts = (
        scoped_pull_requests(scope)
        .filter(created_at__gte=start, created_at__lt=end, repository__projects__isnull=False)
        .values("repository__projects__id")
        .annotate(count=Count("id"))
        .order_by("-count", "repository__projects__id")
    )
    row = counts.first()
    return row["repository__projects__id"] if row else None


def build_comparison(scope: Scope, params: DashboardParams, person: Person) -> list[ComparisonRow]:
    """`scope` is the person-level `Scope` (`scope_type=PERSON`, `scope_id=person.pk`) the page
    already built for the KPI row/charts, reused here so the person's own numbers and this table
    can never disagree. One row per `PERSON_COMPARISON_METRIC_KEYS`, each with the person's value,
    their primary project's value (`None` when they have no project in scope for the period) and
    the reader's whole visible organization's value, and how far the person sits from each
    (`Deviation`). `reviewer_response_p50` is registered at
    `ScopeType.PERSON` only (no project/organization baseline exists for it in the registry), so it
    is excluded from the project/organization `compute()` calls and gets `None` baselines rather
    than raising `MetricNotAvailableAtLevel`."""
    # `ComparisonRow` below only ever reads `.value`/`.sample_size`/`.previous_value`/
    # `.below_min_sample` off each result — never `.series` — so all three calls pass
    # `include_series=False` (T11) to skip their per-bucket series entirely.
    keys = list(PERSON_COMPARISON_METRIC_KEYS)
    baseline_keys = [key for key in keys if key != "reviewer_response_p50"]
    person_set = compute(
        keys, scope, params.date_from, params.date_to, granularity=params.granularity, include_series=False
    )

    project_id = _primary_project_id(scope, params)
    project_set = (
        compute(
            baseline_keys,
            Scope(ScopeType.PROJECT, project_id, scope.access),
            params.date_from,
            params.date_to,
            granularity=params.granularity,
            include_series=False,
        )
        if project_id is not None
        else None
    )

    org_set = compute(
        baseline_keys,
        Scope(ScopeType.GLOBAL, None, scope.access),
        params.date_from,
        params.date_to,
        granularity=params.granularity,
        include_series=False,
    )

    rows = []
    for key in keys:
        person_value = person_set[key].value
        project_value = project_set[key].value if project_set is not None and key in baseline_keys else None
        org_value = org_set[key].value if key in baseline_keys else None
        rows.append(
            ComparisonRow(
                metric=key,
                person_value=person_value,
                person_sample=person_set[key].sample_size,
                project_value=project_value,
                org_value=org_value,
                previous_value=person_set[key].previous_value,
                below_min_sample=person_set[key].below_min_sample,
                project_deviation=_deviation(key, person_value, project_value),
                org_deviation=_deviation(key, person_value, org_value),
            )
        )
    return rows


_SEVERITY_ORDER = {
    PolicyViolation.Severity.HIGH: 0,
    PolicyViolation.Severity.MEDIUM: 1,
    PolicyViolation.Severity.LOW: 2,
}


_STATUS_FIELDS: tuple[str, ...] = ("total", "open", "acknowledged", "waived", "resolved")


@dataclass(frozen=True)
class ViolationStatsRow:
    rule_code: str
    severity: str
    total: int
    open: int
    acknowledged: int
    waived: int
    resolved: int

    @property
    def severity_label(self) -> str:
        try:
            return str(PolicyViolation.Severity(self.severity).label)
        except ValueError:
            return self.severity


@dataclass(frozen=True)
class ViolationStats:
    rows: list[ViolationStatsRow]
    totals: dict[str, int]
    # Distinct pull requests behind every counted violation — one pull request can break several
    # rules, so this is not derivable from the rows.
    pull_requests: int


def build_violation_stats(scope: Scope, params: DashboardParams) -> ViolationStats:
    """One row per (rule, severity) with at least one violation on the person's pull requests
    recorded in the period — the violation's own `created_at`, the same window as the
    `violations_by_rule` metric — split by each violation's *current* status. Highest severity
    first, then the most frequent rule, plus how many distinct pull requests the violations sit
    on. One grouped query; `scoped_violations` is the metrics layer's own scoped
    entry point, so the reader never sees a violation on a pull request outside their scope."""
    rows = (
        scoped_violations(scope)
        .filter(
            created_at__gte=day_start(params.date_from),
            created_at__lt=day_end_exclusive(params.date_to),
        )
        .values("rule_code", "severity", "status", "pull_request_id")
        .annotate(count=Count("id"))
    )
    counts: dict[tuple[str, str], Counter[str]] = {}
    pull_request_ids: set[int] = set()
    for row in rows:
        counts.setdefault((row["rule_code"], row["severity"]), Counter())[row["status"]] += row["count"]
        pull_request_ids.add(row["pull_request_id"])

    status = PolicyViolation.Status
    stats = [
        ViolationStatsRow(
            rule_code=rule_code,
            severity=severity,
            total=sum(by_status.values()),
            open=by_status.get(status.OPEN, 0),
            acknowledged=by_status.get(status.ACKNOWLEDGED, 0),
            waived=by_status.get(status.WAIVED, 0),
            resolved=by_status.get(status.RESOLVED, 0),
        )
        for (rule_code, severity), by_status in counts.items()
    ]
    stats.sort(key=lambda row: (_SEVERITY_ORDER.get(row.severity, 3), -row.total, row.rule_code))
    totals = {field: sum(getattr(row, field) for row in stats) for field in _STATUS_FIELDS}
    return ViolationStats(rows=stats, totals=totals, pull_requests=len(pull_request_ids))


# The quality metrics compared between a person's AI-assisted and other pull requests on the person
# page's AI adoption section: outcomes first, then the signals of how the change was checked, then size.
AI_COHORT_QUALITY_METRIC_KEYS: tuple[str, ...] = (
    "followup_fix_rate",
    "revert_rate",
    "churn_21d",
    "rework_rate",
    "ci_first_pass_rate",
    "test_change_ratio",
    "pr_size_p50",
)


@dataclass(frozen=True)
class CohortComparison:
    """One quality metric measured separately on the person's AI-cohort and non-AI pull requests."""

    metric: str
    ai_value: float | None
    ai_sample: int
    non_ai_value: float | None
    non_ai_sample: int


@dataclass(frozen=True)
class AIProfile:
    # AI status (`AIStatus` code) -> pull requests opened in the period with it.
    status_counts: dict[str, int]
    # AI-cohort pull requests opened in the period — the cohort honours `AI_COHORT_INCLUDE_SUSPECTED`,
    # so this is not always the explicit + disclosed sum.
    ai_count: int
    # (tool code, AI-cohort pull requests that disclosed it), most used first.
    tools: list[tuple[str, int]]
    cohort_quality: list[CohortComparison]
    # Share of the person's AI-cohort pull requests merged in the period that touched a high-risk path.
    high_risk_rate: float | None
    high_risk_sample: int

    @property
    def opened(self) -> int:
        return sum(self.status_counts.values())


def build_ai_profile(scope: Scope, params: DashboardParams) -> AIProfile:
    """The person's AI adoption at a glance: how their pull requests opened in the period split by AI
    status and tool, and how their AI-assisted pull requests compare with their other ones on the
    quality metrics (`AI_COHORT_QUALITY_METRIC_KEYS`). Three `compute()` calls — the whole person, the
    AI cohort, the non-AI cohort — so every number is the same one the dashboards show with that
    cohort filter. The share against the project/organization is not here: it is `ai_pr_share` in
    `build_comparison`, which the caller already has."""
    date_from, date_to = params.date_from, params.date_to
    overall = compute(
        ["ai_pr_count", "ai_status_breakdown", "ai_tool_breakdown", "high_risk_ai_pr_rate"],
        scope,
        date_from,
        date_to,
        granularity=params.granularity,
        include_series=False,
    )
    by_cohort = {
        cohort: compute(
            list(AI_COHORT_QUALITY_METRIC_KEYS),
            scope,
            date_from,
            date_to,
            cohort=cohort,
            granularity=params.granularity,
            include_series=False,
        )
        for cohort in (Cohort.AI, Cohort.NON_AI)
    }
    tools = sorted(
        ((item.label, int(item.value or 0)) for item in overall["ai_tool_breakdown"].breakdown if item.value),
        key=lambda pair: (-pair[1], pair[0]),
    )
    return AIProfile(
        status_counts={
            item.label: int(item.value or 0)
            for item in overall["ai_status_breakdown"].breakdown
            if item.value
        },
        ai_count=int(overall["ai_pr_count"].value or 0),
        tools=tools,
        cohort_quality=[
            CohortComparison(
                metric=key,
                ai_value=by_cohort[Cohort.AI][key].value,
                ai_sample=by_cohort[Cohort.AI][key].sample_size,
                non_ai_value=by_cohort[Cohort.NON_AI][key].value,
                non_ai_sample=by_cohort[Cohort.NON_AI][key].sample_size,
            )
            for key in AI_COHORT_QUALITY_METRIC_KEYS
        ],
        high_risk_rate=overall["high_risk_ai_pr_rate"].value,
        high_risk_sample=overall["high_risk_ai_pr_rate"].sample_size,
    )
