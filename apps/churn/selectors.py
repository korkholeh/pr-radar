"""Eligibility for churn computation. Deliberately not `scope_for_user`-based (CLAUDE.md's
authorization choke point is for read surfaces); this is a background job over every synced
repository, not a page a lead views."""

from __future__ import annotations

import datetime
from collections.abc import Iterable

from django.db.models import Exists, OuterRef, QuerySet
from django.utils import timezone

from apps.activity.models import PullRequest
from apps.ai_detection.models import DiffAnalysis
from apps.churn.models import ChurnResult

_SETTLED_STATUSES = (
    ChurnResult.Status.OK,
    ChurnResult.Status.TOO_LARGE,
    ChurnResult.Status.UNSUPPORTED_MERGE_METHOD,
)

_SETTLED_DIFF_STATUSES = (
    DiffAnalysis.Status.OK,
    DiffAnalysis.Status.TOO_LARGE,
    DiffAnalysis.Status.UNSUPPORTED_MERGE_METHOD,
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


def pull_requests_needing_diff_analysis(
    opted_in_repositories: Iterable[str],
    *,
    repo_full_names: Iterable[str] | None = None,
    project_slug: str | None = None,
    limit: int | None = None,
) -> QuerySet[PullRequest]:
    """Merged PRs in an opted-in repository with no settled `DiffAnalysis`.

    Unlike churn, there is no waiting period: a diff is final the moment the pull request merges,
    so the analysis can run on the first nightly pass after the merge. And unlike churn, this is
    not bounded to recent history — opting a repository in is a request to analyse what it already
    has, so the backlog drains over successive nights under the same per-repository time budget.

    `opted_in_repositories` is `DIFF_ANALYSIS_REPOSITORIES`, already read on the main thread.
    A single `"*"` entry means every repository.
    """
    opted_in = list(opted_in_repositories)
    if not opted_in:
        return PullRequest.objects.none()

    settled = DiffAnalysis.objects.filter(pull_request=OuterRef("pk"), status__in=_SETTLED_DIFF_STATUSES)
    queryset = (
        PullRequest.objects.filter(
            state=PullRequest.State.MERGED, merged_at__isnull=False, merge_commit_sha__gt=""
        )
        .annotate(_analysed=Exists(settled))
        .filter(_analysed=False)
        .select_related("repository__connection")
        .order_by("-merged_at")
    )

    if "*" not in opted_in:
        queryset = queryset.filter(repository__full_name__in=opted_in)
    if repo_full_names is not None:
        queryset = queryset.filter(repository__full_name__in=list(repo_full_names))
    if project_slug is not None:
        queryset = queryset.filter(repository__projects__slug=project_slug).distinct()
    if limit is not None:
        queryset = queryset[:limit]

    return queryset
