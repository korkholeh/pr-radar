from dataclasses import dataclass

from apps.accounts.models import UserProjectAccess


@dataclass(frozen=True)
class ScopeFilter:
    """Authorization contract every selectors.py function takes instead of Model.objects.all().

    `project_ids`/`repository_ids` narrow independently: `None` means "no restriction on this
    axis", an explicit (possibly empty) frozenset restricts to it. `apps/dashboards` reuses the
    same mechanism to apply the filter bar's project/repository multi-select (spec §10.1) as a
    UI-level tightening of `access` — never a widening of what `scope_for_user()` granted.
    """

    unrestricted: bool = True
    project_ids: frozenset[int] | None = None
    repository_ids: frozenset[int] | None = None


def scope_for_user(user) -> ScopeFilter:
    """Grant-list authorization: no `UserProjectAccess` rows means unrestricted (spec §11).

    Anonymous users get a restricted-empty scope (defence in depth; every view is
    `login_required` already). The result is memoised on the user instance for the lifetime of
    the request so a page calling this a dozen times pays one query (RISKS row 10) — it is not a
    cross-request cache, so a revoked grant takes effect on the next request.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return ScopeFilter(unrestricted=False, project_ids=frozenset())
    cached = getattr(user, "_pr_radar_scope", None)
    if cached is not None:
        return cached
    project_ids = frozenset(UserProjectAccess.objects.filter(user=user).values_list("project_id", flat=True))
    scope = (
        ScopeFilter(unrestricted=True)
        if not project_ids
        else ScopeFilter(unrestricted=False, project_ids=project_ids)
    )
    user._pr_radar_scope = scope
    return scope
