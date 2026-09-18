"""Dashboard filter state, entirely round-tripped through the query string (spec §10.1,
ARCHITECTURE "UI filter state -> the query string"). `DashboardParams` is the resolved,
canonical shape every view/chart/table/export call builds on; `parse()` is the only way to build
one from a request, so an unknown preset, a malformed date, a bad granularity or an out-of-scope
id can never reach the rest of the phase — they fall back to defaults instead (RISKS row 3)."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from django.http import QueryDict

from apps.accounts.selectors import ScopeFilter
from apps.dashboards.forms import DashboardFilterForm, PullRequestFilterForm
from apps.dashboards.pr_filters import PRFilters
from apps.metrics.timeframe import today as report_today
from apps.metrics.types import Scope

DEFAULT_MODE = "period"
DEFAULT_PRESET = "30d"
DEFAULT_GRANULARITY = "day"
DEFAULT_COHORT = "all"
DEFAULT_PAGE = 1

PRESET_CHOICES = ("7d", "30d", "90d", "this_month", "last_month", "quarter", "custom")
GRANULARITY_CHOICES = ("day", "week", "month")
COHORT_CHOICES = ("all", "ai", "non_ai", "compare")
MODE_CHOICES = ("period", "day")

_PRESET_DAYS = {"7d": 7, "30d": 30, "90d": 90}


def auto_granularity(date_from: date, date_to: date) -> str:
    """Spec §10.1's "auto-choice by period length": <=14 days -> day, <=92 days -> week,
    otherwise month."""
    days = (date_to - date_from).days + 1
    if days <= 14:
        return "day"
    if days <= 92:
        return "week"
    return "month"


def _quarter_start(today: date) -> date:
    quarter_index = (today.month - 1) // 3
    start_month = quarter_index * 3 + 1
    return today.replace(month=start_month, day=1)


def _default_period(today: date) -> tuple[date, date]:
    days = _PRESET_DAYS[DEFAULT_PRESET]
    return today - timedelta(days=days - 1), today


def resolve_period(
    preset: str, custom_from: date | None, custom_to: date | None, today: date
) -> tuple[date, date]:
    """A shared link with `preset=30d` means "the last 30 days *now*" (every preset but `custom`
    is derived from `today`, never stored), which is what a shared dashboard link should mean."""
    if preset in _PRESET_DAYS:
        days = _PRESET_DAYS[preset]
        return today - timedelta(days=days - 1), today
    if preset == "this_month":
        return today.replace(day=1), today
    if preset == "last_month":
        first_of_this_month = today.replace(day=1)
        last_month_end = first_of_this_month - timedelta(days=1)
        return last_month_end.replace(day=1), last_month_end
    if preset == "quarter":
        return _quarter_start(today), today
    if preset == "custom":
        if custom_from and custom_to:
            return (custom_from, custom_to) if custom_from <= custom_to else (custom_to, custom_from)
        return _default_period(today)
    return _default_period(today)


@dataclass(frozen=True)
class DashboardParams:
    mode: str
    preset: str
    date_from: date
    date_to: date
    day: date
    granularity: str
    granularity_is_auto: bool
    cohort: str
    project_ids: tuple[int, ...]
    repository_ids: tuple[int, ...]
    q: str
    sort: str
    page: int
    table: str
    pr_filters: PRFilters = PRFilters()

    def to_query_dict(self) -> QueryDict:
        """Canonical, sorted, omits defaults — what the filter bar's links and every chart/export
        URL are built from."""
        query_dict = QueryDict(mutable=True)
        if self.mode != DEFAULT_MODE:
            query_dict["mode"] = self.mode
        if self.mode == "day":
            query_dict["day"] = self.day.isoformat()
        else:
            if self.preset != DEFAULT_PRESET:
                query_dict["preset"] = self.preset
            if self.preset == "custom":
                query_dict["from"] = self.date_from.isoformat()
                query_dict["to"] = self.date_to.isoformat()
            if not self.granularity_is_auto:
                query_dict["granularity"] = self.granularity
        if self.cohort != DEFAULT_COHORT:
            query_dict["cohort"] = self.cohort
        if self.project_ids:
            query_dict.setlist("project", [str(project_id) for project_id in self.project_ids])
        if self.repository_ids:
            query_dict.setlist("repository", [str(repo_id) for repo_id in self.repository_ids])
        if self.q:
            query_dict["q"] = self.q
        if self.sort:
            query_dict["sort"] = self.sort
        if self.page != DEFAULT_PAGE:
            query_dict["page"] = str(self.page)
        if self.table:
            query_dict["table"] = self.table
        if self.pr_filters.author_ids:
            query_dict.setlist("author", [str(author_id) for author_id in self.pr_filters.author_ids])
        if self.pr_filters.states:
            query_dict.setlist("state", list(self.pr_filters.states))
        if self.pr_filters.ai_statuses:
            query_dict.setlist("ai_status", list(self.pr_filters.ai_statuses))
        if self.pr_filters.tools:
            query_dict.setlist("tool", list(self.pr_filters.tools))
        if self.pr_filters.size_buckets:
            query_dict.setlist("size", list(self.pr_filters.size_buckets))
        if self.pr_filters.has_violations:
            query_dict["has_violations"] = self.pr_filters.has_violations
        query_dict = query_dict.copy()
        query_dict._mutable = False  # noqa: SLF001 -- QueryDict has no public freeze API.
        return query_dict

    def replace(self, **kwargs: Any) -> DashboardParams:
        return dataclasses.replace(self, **kwargs)


def parse(
    query: QueryDict,
    *,
    projects=None,
    repositories=None,
    people=None,
    today: date | None = None,
) -> DashboardParams:
    """The only way a `DashboardParams` is built from a request. `projects`/`repositories`/`people`
    are the caller's already-scoped querysets (same contract as `policy.forms.ViolationFilterForm`);
    an id outside them is dropped, never a validation error."""
    resolved_today = today if today is not None else report_today()
    form = DashboardFilterForm(query, projects=projects, repositories=repositories)
    form.is_valid()
    cleaned = form.cleaned_data

    pr_filter_form = PullRequestFilterForm(query, people=people)
    pr_filter_form.is_valid()
    pr_cleaned = pr_filter_form.cleaned_data
    pr_filters = PRFilters(
        author_ids=tuple(sorted(pr_cleaned["author"].values_list("pk", flat=True))),
        states=tuple(sorted(pr_cleaned["state"])),
        ai_statuses=tuple(sorted(pr_cleaned["ai_status"])),
        tools=tuple(sorted(pr_cleaned["tool"])),
        size_buckets=tuple(sorted(pr_cleaned["size"])),
        has_violations=pr_cleaned["has_violations"],
    )

    mode = cleaned["mode"] or DEFAULT_MODE
    day = cleaned["day"] or resolved_today
    preset = cleaned["preset"] or DEFAULT_PRESET

    if mode == "day":
        # Day mode has no granularity selector (a single day has no series buckets to choose
        # a bucket size for) and no `preset`/`from`/`to` of its own, so both are forced to their
        # defaults rather than echoing a stray query param — what keeps to_query_dict()'s
        # omission of them on the round trip.
        date_from = date_to = day
        preset = DEFAULT_PRESET
        granularity = DEFAULT_GRANULARITY
        granularity_is_auto = True
    else:
        date_from, date_to = resolve_period(preset, cleaned["from_date"], cleaned["to_date"], resolved_today)
        explicit_granularity = cleaned["granularity"]
        granularity_is_auto = not explicit_granularity
        granularity = explicit_granularity or auto_granularity(date_from, date_to)

    return DashboardParams(
        mode=mode,
        preset=preset,
        date_from=date_from,
        date_to=date_to,
        day=day,
        granularity=granularity,
        granularity_is_auto=granularity_is_auto,
        cohort=cleaned["cohort"] or DEFAULT_COHORT,
        project_ids=tuple(sorted(cleaned["project"].values_list("pk", flat=True))),
        repository_ids=tuple(sorted(cleaned["repository"].values_list("pk", flat=True))),
        q=cleaned["q"],
        sort=cleaned["sort"],
        page=cleaned["page"] or DEFAULT_PAGE,
        table=cleaned["table"],
        pr_filters=pr_filters,
    )


def narrow_scope(scope: Scope, params: DashboardParams) -> Scope:
    """Applies the filter bar's project/repository multi-select (spec §10.1) to `scope.access`,
    reusing the same `ScopeFilter` narrowing `compute()`/`compute_many()` and every scoped
    selector already honour (CLAUDE.md's single authorization choke point) — a UI-level
    tightening of what `scope_for_user()` granted, never a widening of it. A no-op when neither
    select has a value, so a page rendered without the filter bar (or with it left at "all") never
    pays the restricted, per-day `compute()` path (RISKS row 10)."""
    if not params.project_ids and not params.repository_ids:
        return scope
    access = scope.access
    if access.unrestricted:
        project_ids = frozenset(params.project_ids) if params.project_ids else None
        repository_ids = frozenset(params.repository_ids) if params.repository_ids else None
    else:
        project_ids = (
            frozenset(params.project_ids) & (access.project_ids or frozenset())
            if params.project_ids
            else access.project_ids
        )
        repository_ids = (
            frozenset(params.repository_ids) & (access.repository_ids or frozenset())
            if params.repository_ids
            else access.repository_ids
        )
    narrowed_access = ScopeFilter(unrestricted=False, project_ids=project_ids, repository_ids=repository_ids)
    return dataclasses.replace(scope, access=narrowed_access)
