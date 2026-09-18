"""Metric population (spec §4.1, §8.2): bots and `exclude_from_metrics` people are out of the
metrics denominator everywhere, counted separately. Both selectors start from
`scope_for_user()`'s `ScopeFilter`, never `PullRequest.objects.all()` — CLAUDE.md's single
authorization choke point."""

from django.db.models import Q, QuerySet

from apps.accounts.selectors import ScopeFilter
from apps.activity.models import PullRequest
from apps.catalog.models import Repository


def _scoped(scope: ScopeFilter) -> QuerySet[PullRequest]:
    """Restricts by repository id via a subquery (`repository_id__in=...values("id")`), never a
    direct join across the `repository__projects` many-to-many on the `PullRequest` queryset
    itself — joining there fans a PR belonging to a repository in N projects out into N duplicate
    rows, which `.count()`/list results would then double-count."""
    queryset = PullRequest.objects.all()
    if scope.unrestricted:
        return queryset
    if scope.project_ids is not None:
        scoped_repository_ids = Repository.objects.filter(projects__id__in=scope.project_ids).values("id")
        queryset = queryset.filter(repository_id__in=scoped_repository_ids)
    if scope.repository_ids is not None:
        queryset = queryset.filter(repository_id__in=scope.repository_ids)
    return queryset


def pull_requests_in_scope(scope: ScopeFilter) -> QuerySet[PullRequest]:
    """Every PR a user may see, bots and excluded people included — the base a non-metrics
    reader (a PR page, an AI-detection selector) narrows further, as opposed to
    `pull_requests_for_metrics()`'s denominator."""
    return _scoped(scope)


def pull_requests_for_metrics(scope: ScopeFilter) -> QuerySet[PullRequest]:
    """A PR with an unmapped author stays in the population — only a known bot or a person a
    lead has explicitly excluded leaves it."""
    return _scoped(scope).exclude(
        Q(author__person__is_bot=True) | Q(author__person__exclude_from_metrics=True)
    )


def bot_pull_request_count(scope: ScopeFilter) -> int:
    return _scoped(scope).filter(author__person__is_bot=True).count()


def excluded_pull_requests(scope: ScopeFilter) -> QuerySet[PullRequest]:
    """The complement of `pull_requests_for_metrics()`: bot and `exclude_from_metrics` PRs, the
    population metrics leave out. Surfaced by the `prs_excluded_from_metrics` counter (spec §8.2)
    so exclusion is visible rather than a silent drop."""
    return _scoped(scope).filter(
        Q(author__person__is_bot=True) | Q(author__person__exclude_from_metrics=True)
    )
