"""Dashboard-specific scoped reads that don't go through `metrics.compute()` — a day's raw PR
lists and its per-person activity table (plan T8). Every function starts from
`apps.metrics.selectors.scoped_pull_requests()`/`scoped_reviews()`, which already compose on
`scope_for_user()` (CLAUDE.md's single authorization choke point)."""

from __future__ import annotations

import datetime
from collections import Counter

from django.db.models import QuerySet

from apps.activity.models import PullRequest
from apps.catalog.models import Person
from apps.catalog.selectors import people_in_scope
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
