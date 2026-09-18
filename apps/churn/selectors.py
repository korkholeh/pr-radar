"""Eligibility for churn computation. Deliberately not `scope_for_user`-based (CLAUDE.md's
authorization choke point is for read surfaces); this is a background job over every synced
repository, not a page a lead views."""

from __future__ import annotations

import datetime
from collections.abc import Iterable

from django.db.models import Exists, OuterRef, QuerySet
from django.utils import timezone

from apps.activity.models import PullRequest
from apps.churn.models import ChurnResult

_SETTLED_STATUSES = (
    ChurnResult.Status.OK,
    ChurnResult.Status.TOO_LARGE,
    ChurnResult.Status.UNSUPPORTED_MERGE_METHOD,
)


def eligible_pull_requests(
    window_days: int,
    *,
    repo_full_names: Iterable[str] | None = None,
    project_slug: str | None = None,
    limit: int | None = None,
) -> QuerySet[PullRequest]:
    """Merged PRs whose `window_days` window has elapsed and that have no settled `ChurnResult`
    for that window. A `status=error` row does not settle the PR -- it is retried, since an error
    is a transient statement about git, not a verdict about the PR."""
    cutoff = timezone.now() - datetime.timedelta(days=window_days)

    settled = ChurnResult.objects.filter(
        pull_request=OuterRef("pk"), window_days=window_days, status__in=_SETTLED_STATUSES
    )
    queryset = (
        PullRequest.objects.filter(
            state=PullRequest.State.MERGED, merged_at__isnull=False, merged_at__lte=cutoff
        )
        .annotate(_settled=Exists(settled))
        .filter(_settled=False)
        .select_related("repository__connection")
        .order_by("merged_at")
    )

    if repo_full_names is not None:
        queryset = queryset.filter(repository__full_name__in=list(repo_full_names))
    if project_slug is not None:
        queryset = queryset.filter(repository__projects__slug=project_slug).distinct()
    if limit is not None:
        queryset = queryset[:limit]

    return queryset
