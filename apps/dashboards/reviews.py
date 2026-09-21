"""The Reviews page's own row builders (plan §4): reviewer workload, the author×reviewer heat map
and the "waiting for review" list. All three read `metrics.selectors.scoped_reviews()`/
`scoped_pull_requests()` directly rather than through `compute()` — page-specific lists, the same
exception `rows.py` already documents for `recent_prs`."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from django.db.models import Count, F, QuerySet
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.activity.models import PullRequest, Review
from apps.catalog.models import Person
from apps.catalog.services import get_int
from apps.dashboards.params import DashboardParams
from apps.metrics.calculators.base import duration_hours
from apps.metrics.selectors import scoped_pull_requests, scoped_reviews
from apps.metrics.timeframe import day_end_exclusive, day_start
from apps.metrics.types import Scope


def _period_reviews(scope: Scope, params: DashboardParams) -> QuerySet[Review]:
    """Reviews submitted in the period, with a real reviewer identity mapped to a non-bot,
    non-excluded person, and self-review dropped — the population every function below builds on."""
    return (
        scoped_reviews(scope)
        .filter(
            submitted_at__gte=day_start(params.date_from),
            submitted_at__lt=day_end_exclusive(params.date_to),
        )
        .exclude(reviewer__isnull=True)
        .exclude(reviewer__person__isnull=True)
        .exclude(reviewer__person__is_bot=True)
        .exclude(reviewer__person__exclude_from_metrics=True)
        .exclude(reviewer__person_id=F("pull_request__author__person_id"))
    )


@dataclass(frozen=True)
class ReviewerLoad:
    """One reviewer's workload in the period. `reviews_given` counts review submissions,
    `pull_requests_reviewed` the distinct pull requests they fall on: the two are equal for a
    reviewer who passes over each PR once, and diverge for one doing several rounds on the same
    PR, which is the difference the Reviews page exists to show."""

    person: Person
    reviews_given: int
    pull_requests_reviewed: int


def reviewer_load(scope: Scope, params: DashboardParams) -> list[ReviewerLoad]:
    """`ReviewerLoad` per reviewer, descending by reviews given — a workload view, not a people
    ranking (RISKS row 1; the page's own subtitle says so). Both counts come from the one
    `values(...).annotate(...)` query, so adding the distinct-PR count costs no extra round trip."""
    counts = list(
        _period_reviews(scope, params)
        .values("reviewer__person_id")
        .annotate(
            reviews_given=Count("id"),
            pull_requests_reviewed=Count("pull_request_id", distinct=True),
        )
        .order_by("-reviews_given", "reviewer__person_id")
    )
    people = Person.objects.in_bulk(row["reviewer__person_id"] for row in counts)
    return [
        ReviewerLoad(
            person=people[row["reviewer__person_id"]],
            reviews_given=row["reviews_given"],
            pull_requests_reviewed=row["pull_requests_reviewed"],
        )
        for row in counts
        if row["reviewer__person_id"] in people
    ]


@dataclass(frozen=True)
class HeatMapAxis:
    id: int | None  # `None` is the folded "Other" bucket, never a real Person pk.
    label: str


@dataclass(frozen=True)
class HeatMap:
    authors: list[HeatMapAxis] = field(default_factory=list)
    reviewers: list[HeatMapAxis] = field(default_factory=list)
    cells: dict[int | None, dict[int | None, int]] = field(default_factory=dict)
    max_value: int = 0


def _top_ids(totals: Counter[int], top_n: int) -> set[int]:
    return {person_id for person_id, _count in totals.most_common(top_n)}


def _axes(
    top_ids: set[int], totals: Counter[int], people: dict[int, Person], has_other: bool
) -> list[HeatMapAxis]:
    ordered_ids = sorted(top_ids, key=lambda person_id: (-totals[person_id], person_id))
    axes = [HeatMapAxis(person_id, people[person_id].display_name) for person_id in ordered_ids]
    if has_other:
        axes.append(HeatMapAxis(None, str(_("Other"))))
    return axes


def author_reviewer_matrix(scope: Scope, params: DashboardParams) -> HeatMap:
    """One `values(...).annotate(Count)` query, then the top `REVIEW_HEATMAP_TOP_N` people per
    axis kept as themselves with the rest folded into an "Other" row/column (plan §4) so the table
    stays readable regardless of how many people are in scope."""
    rows = list(
        _period_reviews(scope, params)
        .exclude(pull_request__author__person__isnull=True)
        .values("pull_request__author__person_id", "reviewer__person_id")
        .annotate(count=Count("id"))
    )
    if not rows:
        return HeatMap()

    top_n = get_int("REVIEW_HEATMAP_TOP_N")
    author_totals: Counter[int] = Counter()
    reviewer_totals: Counter[int] = Counter()
    for row in rows:
        author_totals[row["pull_request__author__person_id"]] += row["count"]
        reviewer_totals[row["reviewer__person_id"]] += row["count"]
    top_author_ids = _top_ids(author_totals, top_n)
    top_reviewer_ids = _top_ids(reviewer_totals, top_n)

    cells: dict[int | None, dict[int | None, int]] = {}
    for row in rows:
        author_id = row["pull_request__author__person_id"]
        reviewer_id = row["reviewer__person_id"]
        author_key = author_id if author_id in top_author_ids else None
        reviewer_key = reviewer_id if reviewer_id in top_reviewer_ids else None
        row_cells = cells.setdefault(author_key, {})
        row_cells[reviewer_key] = row_cells.get(reviewer_key, 0) + row["count"]

    has_other_author = any(author_id not in top_author_ids for author_id in author_totals)
    has_other_reviewer = any(reviewer_id not in top_reviewer_ids for reviewer_id in reviewer_totals)
    people = Person.objects.in_bulk(top_author_ids | top_reviewer_ids)
    max_value = max((count for row_cells in cells.values() for count in row_cells.values()), default=0)

    return HeatMap(
        authors=_axes(top_author_ids, author_totals, people, has_other_author),
        reviewers=_axes(top_reviewer_ids, reviewer_totals, people, has_other_reviewer),
        cells=cells,
        max_value=max_value,
    )


HEAT_LEVELS = 4


def heat_level(value: int, max_value: int) -> int:
    """1..`HEAT_LEVELS` for a positive cell, 0 for an empty one — the index into the
    `--heat-0`..`--heat-4` tokens (plan §4, spec §10.5: colour is never the only signal, so the
    template also prints `value`)."""
    if value <= 0 or max_value <= 0:
        return 0
    return min(HEAT_LEVELS, max(1, math.ceil(value / max_value * HEAT_LEVELS)))


@dataclass(frozen=True)
class HeatMapCell:
    reviewer: HeatMapAxis
    value: int
    level: int


@dataclass(frozen=True)
class HeatMapRow:
    author: HeatMapAxis
    cells: list[HeatMapCell]


def heat_map_grid(heat_map: HeatMap) -> list[HeatMapRow]:
    """`HeatMap.cells` as a template-friendly grid: one `HeatMapRow` per author, in the same
    order as `HeatMap.authors`, each with one `HeatMapCell` per reviewer in `HeatMap.reviewers`
    order — Django templates cannot index a dict by a loop variable, so this is resolved here."""
    return [
        HeatMapRow(
            author=author,
            cells=[
                HeatMapCell(
                    reviewer=reviewer,
                    value=heat_map.cells.get(author.id, {}).get(reviewer.id, 0),
                    level=heat_level(
                        heat_map.cells.get(author.id, {}).get(reviewer.id, 0), heat_map.max_value
                    ),
                )
                for reviewer in heat_map.reviewers
            ],
        )
        for author in heat_map.authors
    ]


@dataclass(frozen=True)
class WaitingPullRequest:
    pull_request: PullRequest
    hours_waited: float | None  # `None` only if the clock computes a negative span (bad data).


def prs_waiting_for_review(scope: Scope, params: DashboardParams) -> list[WaitingPullRequest]:
    """Open, non-draft PRs with no review yet, longest wait first. `params` is accepted for the
    same signature every page row-builder has, but the list is a point-in-time snapshot (spec
    §10.5's "waiting now"), not narrowed to the selected period."""
    del params
    now = timezone.now()
    queryset = (
        scoped_pull_requests(scope)
        .filter(state=PullRequest.State.OPEN, is_draft=False, first_review_at__isnull=True)
        .exclude(ready_for_review_at__isnull=True)
        .select_related("repository", "author__person")
        .order_by("ready_for_review_at")
    )
    return [
        WaitingPullRequest(pull_request, duration_hours(pull_request.ready_for_review_at, now))
        for pull_request in queryset
    ]
