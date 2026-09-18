"""Settings → People queries: the unmapped-identity queue, the people list and their counters.
Every selector starts from `scope_for_user()`'s `ScopeFilter` (CLAUDE.md's authorization choke
point) even though `Person`/`Identity` are not project-scoped yet — phase 8 narrows this in one
place rather than in every call site."""

from django.db.models import Count, QuerySet

from apps.accounts.selectors import ScopeFilter
from apps.catalog.models import Identity, Person, Project, Repository


def projects_in_scope(scope: ScopeFilter, *, include_inactive: bool = False) -> QuerySet[Project]:
    """Every project a user may see, in name order (CLAUDE.md's authorization choke point),
    active-only by default. Canonical home for this query — `policy.selectors.projects_in_scope`
    delegates here with `include_inactive=True` so `dashboards` and `policy` share one scoping
    rule (RISKS row 3) while the Policy console's filter dropdown keeps its pre-phase-8 behaviour
    of still listing an archived project so an existing violation on it can be filtered to."""
    queryset = Project.objects.order_by("name")
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    if scope.unrestricted:
        return queryset
    if scope.project_ids is not None:
        queryset = queryset.filter(id__in=scope.project_ids)
    if scope.repository_ids is not None:
        queryset = queryset.filter(repositories__id__in=scope.repository_ids)
    return queryset.distinct()


def repositories_in_scope(scope: ScopeFilter, *, include_inactive: bool = False) -> QuerySet[Repository]:
    """Every repository a user may see, in full-name order, active-only by default,
    de-duplicated when a repository belongs to more than one of the caller's projects."""
    queryset = Repository.objects.order_by("full_name")
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    if scope.unrestricted:
        return queryset
    if scope.project_ids is not None:
        queryset = queryset.filter(projects__id__in=scope.project_ids)
    if scope.repository_ids is not None:
        queryset = queryset.filter(id__in=scope.repository_ids)
    return queryset.distinct()


def people_in_scope(scope: ScopeFilter) -> QuerySet[Person]:
    """Active, non-bot people counted in metrics — excludes bots and
    `exclude_from_metrics=True` people by construction, so a caller never has to remember to
    filter them out again. Restricted to people with an identity that authored a PR in one of
    the caller's projects' repositories."""
    queryset = Person.objects.filter(is_active=True, is_bot=False, exclude_from_metrics=False).order_by(
        "display_name"
    )
    if scope.unrestricted:
        return queryset
    if scope.project_ids is not None:
        queryset = queryset.filter(
            identities__authored_pull_requests__repository__projects__id__in=scope.project_ids
        )
    if scope.repository_ids is not None:
        queryset = queryset.filter(identities__authored_pull_requests__repository_id__in=scope.repository_ids)
    return queryset.distinct()


def unmapped_identities(scope: ScopeFilter) -> QuerySet[Identity]:
    queryset = Identity.objects.filter(person=None).order_by("kind", "value")
    if scope.unrestricted:
        return queryset
    if scope.project_ids is not None:
        queryset = queryset.filter(authored_pull_requests__repository__projects__id__in=scope.project_ids)
    if scope.repository_ids is not None:
        queryset = queryset.filter(authored_pull_requests__repository_id__in=scope.repository_ids)
    return queryset.distinct()


def unmapped_identity_count(scope: ScopeFilter) -> int:
    return unmapped_identities(scope).count()


def people_for_settings(scope: ScopeFilter) -> QuerySet[Person]:
    queryset = (
        Person.objects.prefetch_related("identities")
        .annotate(pr_count=Count("identities__authored_pull_requests", distinct=True))
        .order_by("display_name")
    )
    if scope.unrestricted:
        return queryset
    if scope.project_ids is not None:
        queryset = queryset.filter(
            identities__authored_pull_requests__repository__projects__id__in=scope.project_ids
        )
    if scope.repository_ids is not None:
        queryset = queryset.filter(identities__authored_pull_requests__repository_id__in=scope.repository_ids)
    return queryset.distinct()


def bot_person_count(scope: ScopeFilter) -> int:
    queryset = Person.objects.filter(is_bot=True)
    if scope.unrestricted:
        return queryset.count()
    if scope.project_ids is not None:
        queryset = queryset.filter(
            identities__authored_pull_requests__repository__projects__id__in=scope.project_ids
        )
    if scope.repository_ids is not None:
        queryset = queryset.filter(identities__authored_pull_requests__repository_id__in=scope.repository_ids)
    return queryset.distinct().count()
