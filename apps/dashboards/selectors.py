"""Dashboard-specific scoped reads that don't go through `metrics.compute()` — a day's raw PR
lists and its per-person activity table (plan T8). Every function starts from
`apps.metrics.selectors.scoped_pull_requests()`/`scoped_reviews()`, which already compose on
`scope_for_user()` (CLAUDE.md's single authorization choke point)."""

from __future__ import annotations

import datetime
from collections import Counter

from django.db.models import Q, QuerySet

from apps.accounts.selectors import ScopeFilter
from apps.activity.models import PullRequest
from apps.catalog.models import Person
from apps.catalog.selectors import people_in_scope, repositories_in_scope
from apps.dashboards.params import DashboardParams
from apps.github_sync.models import SyncRun
from apps.metrics.models import Cohort
from apps.metrics.selectors import scoped_pull_requests, scoped_reviews
from apps.metrics.timeframe import day_end_exclusive, day_start
from apps.metrics.types import Scope


def prs_opened_on_day(scope: Scope, day: datetime.date) -> QuerySet[PullRequest]:
    return (
        scoped_pull_requests(scope)
        .filter(created_at__gte=day_start(day), created_at__lt=day_end_exclusive(day))
        .select_related("repository", "author__person")
        .order_by("repository__full_name", "number")
    )


def period_has_pull_requests(scope: Scope, params: DashboardParams) -> bool:
    """One `EXISTS`/`LIMIT 1` query (plan T2): whether any PR touching `params`'s period is
    visible in `scope` under `params`'s cohort and PR filters. Starts from `scoped_pull_requests()`
    like every other selector here — the scope choke point applies to an "is this empty" check
    exactly as it does to the data itself, so a restricted lead's out-of-scope project reads as
    empty, never as an error. Matches on `created_at` *or* `last_activity_at` (round 1 review
    MINOR) because the surfaces below the banner disagree on which field defines "in the period" —
    KPIs narrow by `merged_at`, the Recent PRs table by `last_activity_at` — so a PR created
    earlier but merged/active in the window must count as present, and a cohort/filter combination
    that matches nothing must count as empty even if PRs exist elsewhere in scope."""
    period_from = day_start(params.date_from)
    period_to = day_end_exclusive(params.date_to)
    cohort = params.cohort if params.cohort in (Cohort.AI, Cohort.NON_AI) else Cohort.ALL
    queryset = scoped_pull_requests(scope, cohort).filter(
        Q(created_at__gte=period_from, created_at__lt=period_to)
        | Q(last_activity_at__gte=period_from, last_activity_at__lt=period_to)
    )
    return params.pr_filters.apply(queryset).exists()


def no_repositories_configured(access: ScopeFilter) -> bool:
    """Whether the caller can see no repository at all — "the tool is not set up yet", which is a
    different emptiness from "synced, but this period is empty". A sync over zero repositories
    still finishes `success`, so `nothing_ever_synced()` cannot tell the two apart and a fresh
    install reads as "widen the period", advice that can never work. Starts from
    `repositories_in_scope()` so a restricted lead with no repository in their projects gets the
    same honest answer rather than someone else's repository count."""
    return not repositories_in_scope(access).exists()


def nothing_ever_synced() -> bool:
    """Whether any `SyncRun` has ever completed successfully — distinguishes "nobody has synced
    yet" from "synced, but this period/filter is empty" (plan T2), the same `SyncRun` query
    `data_as_of_banner()` already runs."""
    return not SyncRun.objects.filter(status=SyncRun.Status.SUCCESS, finished_at__isnull=False).exists()


def prs_merged_on_day(scope: Scope, day: datetime.date) -> QuerySet[PullRequest]:
    return (
        scoped_pull_requests(scope)
        .filter(merged_at__gte=day_start(day), merged_at__lt=day_end_exclusive(day))
        .select_related("repository", "author__person")
        .order_by("repository__full_name", "number")
    )


class PersonDayActivity:
    __slots__ = ("person", "prs_opened", "prs_merged", "reviews_given")

    def __init__(self, person: Person, prs_opened: int, prs_merged: int, reviews_given: int) -> None:
        self.person = person
        self.prs_opened = prs_opened
        self.prs_merged = prs_merged
        self.reviews_given = reviews_given


def person_activity_for_day(scope: Scope, day: datetime.date) -> list[PersonDayActivity]:
    """One row per person with any activity in scope on `day`, in name order (RISKS row 1: no
    ranking column). Four queries regardless of how many people are active — people in scope,
    then one `Counter` each for opened/merged/reviews — never one query per person."""
    start, end = day_start(day), day_end_exclusive(day)
    opened_counts = Counter(prs_opened_on_day(scope, day).values_list("author__person_id", flat=True))
    merged_counts = Counter(prs_merged_on_day(scope, day).values_list("author__person_id", flat=True))
    review_counts = Counter(
        scoped_reviews(scope)
        .filter(submitted_at__gte=start, submitted_at__lt=end)
        .values_list("reviewer__person_id", flat=True)
    )
    active_person_ids = set(opened_counts) | set(merged_counts) | set(review_counts)
    active_person_ids.discard(None)

    rows = [
        PersonDayActivity(
            person=person,
            prs_opened=opened_counts.get(person.id, 0),
            prs_merged=merged_counts.get(person.id, 0),
            reviews_given=review_counts.get(person.id, 0),
        )
        for person in people_in_scope(scope.access)
        if person.id in active_person_ids
    ]
    return rows
