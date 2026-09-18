from dataclasses import dataclass


@dataclass(frozen=True)
class ScopeFilter:
    """Authorization contract every selectors.py function takes instead of Model.objects.all().

    Phase 1 always returns an unrestricted filter; phase 8 narrows it per-user with
    project_ids from UserProjectAccess.

    `project_ids`/`repository_ids` narrow independently: `None` means "no restriction on this
    axis", an explicit (possibly empty) frozenset restricts to it. `apps/dashboards` reuses the
    same mechanism to apply the filter bar's project/repository multi-select (spec §10.1) as a
    UI-level tightening of `access` — never a widening of what `scope_for_user()` granted.
    """

    unrestricted: bool = True
    project_ids: frozenset[int] | None = None
    repository_ids: frozenset[int] | None = None


def scope_for_user(user) -> ScopeFilter:
    return ScopeFilter(unrestricted=True, project_ids=None)
