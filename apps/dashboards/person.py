"""Person page extras beyond the shared KPI row/chart cards `views.dashboard` already renders at
`ScopeType.PERSON` (plan §2): the person-vs-baseline comparison table. `metrics.compute()` stays
the only aggregate read entry point — every baseline here is a real median over raw rows at its own
level (project or organization), never a median of per-person medians (ADR 0007, RISKS row 1)."""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Count

from apps.catalog.models import Person
from apps.dashboards.params import DashboardParams
from apps.metrics.models import ScopeType
from apps.metrics.selectors import scoped_pull_requests
from apps.metrics.services import compute
from apps.metrics.timeframe import day_end_exclusive, day_start
from apps.metrics.types import Scope

# Compliance first, then quality, then flow, and volume last — `docs/POLICY.md`'s product
# constraint, from the standards themselves: the number of pull requests a person produced is not
# a measure of that person. A lead who opens this page before a 1:1 reads how the work was done
# before they read how much of it there was.
PERSON_COMPARISON_METRIC_KEYS: tuple[str, ...] = (
    "ai_pr_share",
    "disclosure_rate",
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
class ComparisonRow:
    metric: str
    person_value: float | None
    person_sample: int
    project_value: float | None
    org_value: float | None
    previous_value: float | None
    below_min_sample: bool


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
    the reader's whole visible organization's value. `reviewer_response_p50` is registered at
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

    return [
        ComparisonRow(
            metric=key,
            person_value=person_set[key].value,
            person_sample=person_set[key].sample_size,
            project_value=(
                project_set[key].value if project_set is not None and key in baseline_keys else None
            ),
            org_value=org_set[key].value if key in baseline_keys else None,
            previous_value=person_set[key].previous_value,
            below_min_sample=person_set[key].below_min_sample,
        )
        for key in keys
    ]
