"""Read-side of policy evaluation. Every function starts from a `ScopeFilter`, never from
`PolicyViolation.objects.all()` (CLAUDE.md's single authorization choke point). These four
functions are also phase 7's data source for the `violations_open`, `violations_new` and
`violations_by_rule` metrics (spec §8.2) — a deliberate, logged deviation from `metrics.compute()`
being the only read entry point, since the registry does not exist until phase 7."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from django.db.models import Count, QuerySet

from apps.accounts.selectors import ScopeFilter
from apps.activity.models import PullRequest
from apps.activity.selectors import pull_requests_in_scope
from apps.ai_detection.selectors import ai_cohort_pull_requests
from apps.catalog.models import Project, Repository
from apps.metrics.timeframe import day_end_exclusive, day_start
from apps.policy.models import PolicyViolation


def projects_in_scope(scope: ScopeFilter) -> QuerySet[Project]:
    """The Policy console's project filter dropdown: every project a user may see, in scope
    order — same `ScopeFilter` contract as every other selector here, so a restricted lead never
    sees another project's name in the list once phase 9 turns scope narrowing on."""
    queryset = Project.objects.order_by("name")
    if scope.unrestricted:
        return queryset
    return queryset.filter(id__in=scope.project_ids or frozenset())


def repositories_in_scope(scope: ScopeFilter) -> QuerySet[Repository]:
    """The Policy console's repository filter dropdown, same scoping contract as
    `projects_in_scope`."""
    queryset = Repository.objects.order_by("full_name")
    if scope.unrestricted:
        return queryset
    return queryset.filter(projects__id__in=scope.project_ids or frozenset()).distinct()


def violations_in_scope(scope: ScopeFilter) -> QuerySet[PolicyViolation]:
    return PolicyViolation.objects.filter(pull_request__in=pull_requests_in_scope(scope))


def violations_for_pull_request(scope: ScopeFilter, pk: int) -> QuerySet[PolicyViolation]:
    return violations_in_scope(scope).filter(pull_request_id=pk)


@dataclass(frozen=True)
class RuleCount:
    rule_code: str
    count: int


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
    rows = queryset.values("rule_code").annotate(count=Count("id")).order_by("-count", "rule_code")
    return [RuleCount(rule_code=row["rule_code"], count=row["count"]) for row in rows]


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
