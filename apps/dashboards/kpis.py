"""The KPI rows spec §10.3 puts on every dashboard page, declared as data rather than markup
(plan §3) so `services.build_dashboard()`, its tests and `partials/kpi_row.html` all walk the same
list instead of three hand-kept copies."""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from apps.metrics.models import Cohort
from apps.metrics.services import compute
from apps.metrics.types import MetricResult, Scope


@dataclass(frozen=True)
class KpiSpec:
    metric_key: str
    secondary_key: str | None = None
    compare_cohorts: bool = False


@dataclass(frozen=True)
class KpiCard:
    result: MetricResult
    secondary_result: MetricResult | None = None
    compare: dict[str, MetricResult] | None = None


ADOPTION_ROW: tuple[KpiSpec, ...] = (
    KpiSpec("ai_pr_share"),
    KpiSpec("disclosure_rate"),
    KpiSpec("violations_open"),
    KpiSpec("ai_active_people"),
)

FLOW_ROW: tuple[KpiSpec, ...] = (
    KpiSpec("prs_merged"),
    KpiSpec("lead_time_p50"),
    KpiSpec("time_to_first_review_p50"),
    KpiSpec("open_prs", secondary_key="stale_prs"),
)

QUALITY_ROW: tuple[KpiSpec, ...] = (
    KpiSpec("rework_rate", compare_cohorts=True),
    KpiSpec("ci_first_pass_rate", compare_cohorts=True),
    KpiSpec("revert_rate", compare_cohorts=True),
    KpiSpec("churn_21d", compare_cohorts=True),
)

COMPLIANCE_ROW: tuple[KpiSpec, ...] = (
    KpiSpec("ai_only_approval_rate"),
    KpiSpec("quality_gate_bypass_rate"),
    KpiSpec("ai_review_coverage"),
    KpiSpec("high_risk_ai_pr_rate", secondary_key="structural_signal_rate"),
)

DAY_ACTIVITY_ROW: tuple[KpiSpec, ...] = (
    KpiSpec("prs_opened"),
    KpiSpec("prs_merged"),
    KpiSpec("prs_closed_unmerged"),
    KpiSpec("reviews_given"),
    KpiSpec("ai_pr_count"),
    KpiSpec("violations_new"),
)

DAY_STATE_ROW: tuple[KpiSpec, ...] = (
    KpiSpec("open_prs"),
    KpiSpec("waiting_review_24h"),
    KpiSpec("stale_prs"),
)

PERIOD_ROWS: tuple[tuple[KpiSpec, ...], ...] = (ADOPTION_ROW, FLOW_ROW, QUALITY_ROW, COMPLIANCE_ROW)
DAY_ROWS: tuple[tuple[KpiSpec, ...], ...] = (DAY_ACTIVITY_ROW, DAY_STATE_ROW)


def build_kpi_row(
    scope: Scope,
    row: tuple[KpiSpec, ...],
    date_from: datetime.date,
    date_to: datetime.date,
    granularity: str,
    cohort: str,
) -> list[KpiCard]:
    """Resolves one declared row into `KpiCard`s. `cohort="compare"` (the filter bar's global
    toggle) turns every card in the row into an AI/non-AI comparison, same as a row whose own
    `KpiSpec.compare_cohorts` is `True`. Spec §10.2 requires the card anatomy (big value, delta
    arrow, sparkline) even on a comparison card, so a compare card's `main` is the cohort-ALL
    result — the AI/non-AI pair renders as a sub-line under it, never in place of it. That is
    three extra `compute()` calls for a compare row (cohort `all`, `ai`, `non_ai`), never a
    client-side recombination of a single call's numbers."""
    show_compare_all = cohort == "compare"
    compare_keys = sorted({spec.metric_key for spec in row if spec.compare_cohorts or show_compare_all})
    normal_keys = sorted(
        {
            key
            for spec in row
            if not (spec.compare_cohorts or show_compare_all)
            for key in (spec.metric_key, spec.secondary_key)
            if key
        }
    )
    secondary_keys = sorted({spec.secondary_key for spec in row if spec.secondary_key})

    resolved_cohort = cohort if cohort in (Cohort.ALL, Cohort.AI, Cohort.NON_AI) else Cohort.ALL
    # `normal_set`/`compare_all_set` back a card's `result` (kpi_card.html reads `result.series` for
    # the sparkline), so both keep the default `include_series=True`. `ai_set`/`non_ai_set` only ever
    # feed `compare.ai.value`/`compare.non_ai.value` (a sub-line, no sparkline) and `secondary_set`
    # only feeds `secondary_result.value` — none of the three read `.series`, so `include_series=False`
    # (T11) skips their per-bucket series entirely.
    normal_set = (
        compute(normal_keys, scope, date_from, date_to, cohort=resolved_cohort, granularity=granularity)
        if normal_keys
        else None
    )
    compare_all_set = (
        compute(compare_keys, scope, date_from, date_to, cohort=Cohort.ALL, granularity=granularity)
        if compare_keys
        else None
    )
    ai_set = (
        compute(
            compare_keys,
            scope,
            date_from,
            date_to,
            cohort=Cohort.AI,
            granularity=granularity,
            include_series=False,
        )
        if compare_keys
        else None
    )
    non_ai_set = (
        compute(
            compare_keys,
            scope,
            date_from,
            date_to,
            cohort=Cohort.NON_AI,
            granularity=granularity,
            include_series=False,
        )
        if compare_keys
        else None
    )
    secondary_set = (
        compute(
            secondary_keys,
            scope,
            date_from,
            date_to,
            cohort=Cohort.ALL,
            granularity=granularity,
            include_series=False,
        )
        if secondary_keys
        else None
    )

    cards = []
    for spec in row:
        if spec.compare_cohorts or show_compare_all:
            assert compare_all_set is not None and ai_set is not None and non_ai_set is not None
            main = compare_all_set[spec.metric_key]
            compare = {"ai": ai_set[spec.metric_key], "non_ai": non_ai_set[spec.metric_key]}
        else:
            assert normal_set is not None
            main = normal_set[spec.metric_key]
            compare = None
        secondary = secondary_set[spec.secondary_key] if spec.secondary_key and secondary_set else None
        cards.append(KpiCard(result=main, secondary_result=secondary, compare=compare))
    return cards
