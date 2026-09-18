"""Scoped population selectors every calculator reads from (spec §8.2's population rule). Every
function composes on `activity.selectors.pull_requests_for_metrics()` (bots and
`exclude_from_metrics` people excluded) and narrows by `Scope`/cohort — CLAUDE.md's single
authorization choke point, applied once here rather than by each calculator."""

from __future__ import annotations

from django.db.models import QuerySet

from apps.activity.models import PullRequest, Review, ReviewComment
from apps.activity.selectors import excluded_pull_requests, pull_requests_for_metrics
from apps.ai_detection.services import ai_cohort_statuses
from apps.metrics.models import Cohort, ScopeType
from apps.metrics.types import Scope
from apps.policy.models import PolicyViolation
from apps.policy.selectors import violations_in_scope


def _narrow_by_scope(queryset: QuerySet, scope: Scope, prefix: str = "") -> QuerySet:
    if scope.scope_type == ScopeType.PROJECT:
        return queryset.filter(**{f"{prefix}repository__projects__id": scope.scope_id})
    if scope.scope_type == ScopeType.REPO:
        return queryset.filter(**{f"{prefix}repository_id": scope.scope_id})
    if scope.scope_type == ScopeType.PERSON:
        return queryset.filter(**{f"{prefix}author__person_id": scope.scope_id})
    return queryset


def _narrow_by_cohort(queryset: QuerySet, cohort: str, field: str = "ai_status") -> QuerySet:
    if cohort == Cohort.ALL:
        return queryset
    statuses = ai_cohort_statuses()
    if cohort == Cohort.AI:
        return queryset.filter(**{f"{field}__in": statuses})
    if cohort == Cohort.NON_AI:
        return queryset.exclude(**{f"{field}__in": statuses})
    raise ValueError(f"Unknown cohort {cohort!r}.")


def scoped_pull_requests(scope: Scope, cohort: str = Cohort.ALL) -> QuerySet[PullRequest]:
    queryset = pull_requests_for_metrics(scope.access)
    queryset = _narrow_by_scope(queryset, scope)
    return _narrow_by_cohort(queryset, cohort)


def scoped_excluded_pull_requests(scope: Scope) -> QuerySet[PullRequest]:
    """Bot and `exclude_from_metrics` PRs — the population `scoped_pull_requests` leaves out,
    surfaced by the `prs_excluded_from_metrics` counter instead of silently vanishing."""
    queryset = excluded_pull_requests(scope.access)
    return _narrow_by_scope(queryset, scope)


def scoped_reviews(scope: Scope, cohort: str = Cohort.ALL) -> QuerySet[Review]:
    return Review.objects.filter(pull_request__in=scoped_pull_requests(scope, cohort))


def scoped_review_comments(scope: Scope, cohort: str = Cohort.ALL) -> QuerySet[ReviewComment]:
    return ReviewComment.objects.filter(pull_request__in=scoped_pull_requests(scope, cohort))


def scoped_violations(scope: Scope) -> QuerySet[PolicyViolation]:
    queryset = violations_in_scope(scope.access)
    return _narrow_by_scope(queryset, scope, prefix="pull_request__")
