"""Settings → People queries: the unmapped-identity queue, the people list and their counters.
Every selector starts from `scope_for_user()`'s `ScopeFilter` (CLAUDE.md's authorization choke
point) even though `Person`/`Identity` are not project-scoped yet — phase 8 narrows this in one
place rather than in every call site."""

from django.db.models import Count, QuerySet

from apps.accounts.selectors import ScopeFilter
from apps.catalog.models import Identity, Person


def unmapped_identities(scope: ScopeFilter) -> QuerySet[Identity]:
    return Identity.objects.filter(person=None).order_by("kind", "value")


def unmapped_identity_count(scope: ScopeFilter) -> int:
    return unmapped_identities(scope).count()


def people_for_settings(scope: ScopeFilter) -> QuerySet[Person]:
    return (
        Person.objects.prefetch_related("identities")
        .annotate(pr_count=Count("identities__authored_pull_requests", distinct=True))
        .order_by("display_name")
    )


def bot_person_count(scope: ScopeFilter) -> int:
    return Person.objects.filter(is_bot=True).count()
