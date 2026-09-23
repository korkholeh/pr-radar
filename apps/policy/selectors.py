"""Read-side of policy evaluation. Every function starts from a `ScopeFilter`, never from
`PolicyViolation.objects.all()` (CLAUDE.md's single authorization choke point). These four
functions are also phase 7's data source for the `violations_open`, `violations_new` and
`violations_by_rule` metrics (spec §8.2) — a deliberate, logged deviation from `metrics.compute()`
being the only read entry point, since the registry does not exist until phase 7."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from django.db.models import Count, Q, QuerySet

from apps.accounts.selectors import ScopeFilter
from apps.activity.models import PullRequest
from apps.activity.selectors import pull_requests_in_scope
from apps.ai_detection.selectors import ai_cohort_pull_requests
from apps.catalog.models import Project, Repository
from apps.catalog.selectors import projects_in_scope as _catalog_projects_in_scope
from apps.catalog.selectors import repositories_in_scope as _catalog_repositories_in_scope
from apps.metrics.timeframe import day_end_exclusive, day_start
from apps.policy.models import PolicyViolation

__all__ = [
    "compliance_kpis",
    "disclosure_mismatch_pull_requests",
    "projects_in_scope",
    "repositories_in_scope",
    "violations_by_rule",
    "violations_for_pull_request",
    "violations_in_scope",
]

# The Policy console's project/repository filter dropdowns: `apps.catalog.selectors` is now the
# canonical home for this query (phase 8), so `dashboards` and `policy` share one authorization
# code path (RISKS row 3) instead of keeping two copies in step by hand. Unlike dashboards, the
# Policy console must still list an archived project/repository — an existing violation on one
# needs to stay filterable — so this delegates with `include_inactive=True` (round 1 review: the
# dashboards-only default silently dropped that behaviour when the two selectors were unified).


def projects_in_scope(scope: ScopeFilter) -> QuerySet[Project]:
    return _catalog_projects_in_scope(scope, include_inactive=True)


def repositories_in_scope(scope: ScopeFilter) -> QuerySet[Repository]:
    return _catalog_repositories_in_scope(scope, include_inactive=True)


def violations_in_scope(scope: ScopeFilter) -> QuerySet[PolicyViolation]:
    """`pull_request__in=` is skipped for an unrestricted caller (T11, RISKS row 10): every
    `PolicyViolation.pull_request_id` references an existing `PullRequest` row (a `NOT NULL`
    `ForeignKey`), so `pull_request__in=pull_requests_in_scope(scope)` with an unfiltered
    population is a redundant `WHERE ... IN (SELECT id FROM activity_pullrequest)` subquery —
    profiling on 50 repos/20,000 PRs found this the dominant cost of the `violations_open` state
    metric's per-bucket series (one such subquery per day bucket). Same "unrestricted skips the
    filter" shape already used by `apps.catalog.selectors`/`apps.activity.selectors`."""
    if scope.unrestricted:
        return PolicyViolation.objects.all()
    return PolicyViolation.objects.filter(pull_request__in=pull_requests_in_scope(scope))


def violations_for_pull_request(scope: ScopeFilter, pk: int) -> QuerySet[PolicyViolation]:
    return violations_in_scope(scope).filter(pull_request_id=pk)


@dataclass(frozen=True)
class RuleCount:
    rule_code: str
    count: int
    # Of `count`, how many are still open. The rest were acknowledged, waived or resolved (often
    # automatically, when a recompute found the condition gone), which is why the console's table —
    # open only by default — can hold none of a rule the chart still shows.
    open_count: int = 0


def violations_by_rule(
    scope: ScopeFilter, start: date, end: date, status: Sequence[str] | None = None
) -> list[RuleCount]:
    """One row per rule code with at least one matching violation in the period, ordered by
    count descending then rule code — the by-rule distribution chart's series."""
    queryset = violations_in_scope(scope).filter(
        created_at__gte=day_start(start), created_at__lt=day_end_exclusive(end)
    )
    if status:
        queryset = queryset.filter(status__in=status)
    rows = (
        queryset.values("rule_code")
        .annotate(count=Count("id"), open_count=Count("id", filter=Q(status=PolicyViolation.Status.OPEN)))
        .order_by("-count", "rule_code")
    )
    return [
        RuleCount(rule_code=row["rule_code"], count=row["count"], open_count=row["open_count"])
        for row in rows
    ]


@dataclass(frozen=True)
class ComplianceKPIs:
    violations_open: int
    violations_new: int
    ai_pr_compliance_rate: float | None
    disclosure_mismatch_count: int
    sample_size: int


def compliance_kpis(scope: ScopeFilter, start: date, end: date) -> ComplianceKPIs:
    """`ai_pr_compliance_rate` is the share of AI-cohort PRs created in the period with no open
    violation; `None` (never `0`) when the denominator (`sample_size`) is empty, per CLAUDE.md.
    `violations_open` is a current snapshot; `violations_new` is counted by violation `created_at`
    inside the period. `disclosure_mismatch_count` counts the same pull requests as
    `disclosure_mismatch_pull_requests` (anchored on the PR's own `created_at`), so the KPI card
    and the list under it on the console never disagree."""
    period_start = day_start(start)
    period_end = day_end_exclusive(end)

    scoped_violations = violations_in_scope(scope)
    violations_open = scoped_violations.filter(status=PolicyViolation.Status.OPEN).count()
    violations_new = scoped_violations.filter(created_at__gte=period_start, created_at__lt=period_end).count()
    disclosure_mismatch_count = disclosure_mismatch_pull_requests(scope, start, end).count()

    ai_prs = ai_cohort_pull_requests(scope).filter(created_at__gte=period_start, created_at__lt=period_end)
    sample_size = ai_prs.count()
    if sample_size == 0:
        ai_pr_compliance_rate = None
    else:
        non_compliant = ai_prs.filter(violations__status=PolicyViolation.Status.OPEN).distinct().count()
        ai_pr_compliance_rate = (sample_size - non_compliant) / sample_size

    return ComplianceKPIs(
        violations_open=violations_open,
        violations_new=violations_new,
        ai_pr_compliance_rate=ai_pr_compliance_rate,
        disclosure_mismatch_count=disclosure_mismatch_count,
        sample_size=sample_size,
    )


def disclosure_mismatch_pull_requests(scope: ScopeFilter, start: date, end: date) -> QuerySet[PullRequest]:
    return (
        pull_requests_in_scope(scope)
        .filter(
            violations__rule_code=PolicyViolation.RuleCode.DISCLOSURE_MISMATCH,
            violations__status=PolicyViolation.Status.OPEN,
            created_at__gte=day_start(start),
            created_at__lt=day_end_exclusive(end),
        )
        .distinct()
    )
