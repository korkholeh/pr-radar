from dataclasses import dataclass


@dataclass(frozen=True)
class ScopeFilter:
    """Authorization contract every selectors.py function takes instead of Model.objects.all().

    Phase 1 always returns an unrestricted filter; phase 8 narrows it per-user with
    project_ids from UserProjectAccess.
    """

    unrestricted: bool = True
    project_ids: frozenset[int] | None = None


def scope_for_user(user) -> ScopeFilter:
    return ScopeFilter(unrestricted=True, project_ids=None)
