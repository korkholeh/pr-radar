"""Read-side of AI detection. Every function starts from a `ScopeFilter`, never from
`PullRequest.objects.all()` or `AISignal.objects.all()` (CLAUDE.md's single authorization choke
point)."""

from __future__ import annotations

from django.db.models import QuerySet

from apps.accounts.selectors import ScopeFilter
from apps.activity.models import PullRequest
from apps.activity.selectors import pull_requests_for_metrics, pull_requests_in_scope
from apps.ai_detection.models import AISignal
from apps.ai_detection.services import ai_cohort_statuses


def signals_for_pull_request(scope: ScopeFilter, pk: int) -> QuerySet[AISignal]:
    return AISignal.objects.filter(pull_request__in=pull_requests_in_scope(scope), pull_request_id=pk)


def ai_cohort_pull_requests(scope: ScopeFilter) -> QuerySet[PullRequest]:
    return pull_requests_for_metrics(scope).filter(ai_status__in=ai_cohort_statuses())


def recent_pull_requests(scope: ScopeFilter, limit: int) -> QuerySet[PullRequest]:
    return pull_requests_in_scope(scope).order_by("-created_at")[:limit]
