"""Metric population (spec §4.1, §8.2): bots and `exclude_from_metrics` people are out of the
metrics denominator everywhere, counted separately. Both selectors start from
`scope_for_user()`'s `ScopeFilter`, never `PullRequest.objects.all()` — CLAUDE.md's single
authorization choke point."""

from django.db.models import Q, QuerySet

from apps.accounts.selectors import ScopeFilter
from apps.activity.models import PullRequest
from apps.catalog.models import Repository


def _scoped(scope: ScopeFilter) -> QuerySet[PullRequest]:
    queryset = PullRequest.objects.all()
    if scope.unrestricted:
        return queryset
    scoped_repositories = Repository.objects.filter(projects__id__in=scope.project_ids or frozenset())
    return queryset.filter(repository__in=scoped_repositories)


def pull_requests_for_metrics(scope: ScopeFilter) -> QuerySet[PullRequest]:
    """A PR with an unmapped author stays in the population — only a known bot or a person a
    lead has explicitly excluded leaves it."""
    return _scoped(scope).exclude(
        Q(author__person__is_bot=True) | Q(author__person__exclude_from_metrics=True)
    )


def bot_pull_request_count(scope: ScopeFilter) -> int:
    return _scoped(scope).filter(author__person__is_bot=True).count()
