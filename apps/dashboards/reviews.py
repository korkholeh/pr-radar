"""The Reviews page's own row builders (plan §4): reviewer workload, the author×reviewer heat map
and the "waiting for review" list. All three read `metrics.selectors.scoped_reviews()`/
`scoped_pull_requests()` directly rather than through `compute()` — page-specific lists, the same
exception `rows.py` already documents for `recent_prs`."""

from __future__ import annotations

import math
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from django.db.models import Count, Exists, F, OuterRef, Q, QuerySet
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


def _period_reviews_with_self(scope: Scope, params: DashboardParams) -> QuerySet[Review]:
    """Reviews submitted in the period, with a real reviewer identity mapped to a non-bot,
    non-excluded person. Self-review is still in: the heat map shows it on the diagonal."""
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
    )


def _period_reviews(scope: Scope, params: DashboardParams) -> QuerySet[Review]:
    """`_period_reviews_with_self()` with self-review dropped — the population reviewer workload
    builds on."""
    return _period_reviews_with_self(scope, params).exclude(
        reviewer__person_id=F("pull_request__author__person_id")
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
    # Authors: pull requests merged in the period. Reviewers: distinct pull requests by someone
    # else they reviewed in the period.
    pr_count: int = 0
    # Authors only: how many of `pr_count` someone other than the author reviewed.
    reviewed_by_others: int = 0
    # A very low-activity reviewer, or an author whose code others rarely review.
    flagged: bool = False


@dataclass(frozen=True)
class HeatMap:
    authors: list[HeatMapAxis] = field(default_factory=list)
    reviewers: list[HeatMapAxis] = field(default_factory=list)
    # Distinct pull requests per author × reviewer. A same-person key is a self-review.
    cells: dict[int | None, dict[int | None, int]] = field(default_factory=dict)
    max_value: int = 0  # Over cross-person cells only: self-review never sets the heat scale.
    # Self-reviewed pull requests the grid cannot show because the person is folded into "Other"
    # on at least one axis.
    hidden_self_reviews: int = 0


def _ranked(totals: Counter[int], tiebreak: Counter[int]) -> list[int]:
    return sorted(totals, key=lambda person_id: (-totals[person_id], -tiebreak[person_id], person_id))


def _merged_by_author(scope: Scope, params: DashboardParams) -> list[dict[str, Any]]:
    """Per author, pull requests merged in the period and how many of them carry at least one
    review (at any time) by a non-bot person other than the author — one aggregate query."""
    reviewed_by_other = (
        Review.objects.filter(pull_request=OuterRef("pk"), reviewer__person__isnull=False)
        .exclude(reviewer__person__is_bot=True)
        .exclude(reviewer__person_id=OuterRef("author__person_id"))
    )
    return list(
        scoped_pull_requests(scope)
        .filter(
            merged_at__gte=day_start(params.date_from),
            merged_at__lt=day_end_exclusive(params.date_to),
            author__person__isnull=False,
        )
        .values("author__person_id")
        .annotate(
            merged=Count("id"),
            reviewed=Count("id", filter=Q(Exists(reviewed_by_other))),
        )
        .order_by()
    )


def _low_activity_reviewers(reviewer_prs: Counter[int], candidates: list[int]) -> set[int]:
    """Reviewers below `REVIEW_LOW_ACTIVITY_PCT` % of the median active reviewer's count. The
    median is taken over people with at least one review, so a team where most people never
    review still flags them rather than making zero the norm."""
    active = [reviewer_prs[person_id] for person_id in candidates if reviewer_prs[person_id] > 0]
    if not active:
        return set()
    threshold = statistics.median(active) * get_int("REVIEW_LOW_ACTIVITY_PCT") / 100
    return {person_id for person_id in candidates if reviewer_prs[person_id] < threshold}


def _rarely_reviewed_authors(merged: Counter[int], reviewed: Counter[int]) -> set[int]:
    """Authors with at least `MIN_SAMPLE` merged pull requests of which fewer than
    `REVIEW_LOW_COVERAGE_PCT` % were reviewed by someone else — below the sample, no verdict."""
    min_sample = get_int("MIN_SAMPLE")
    pct = get_int("REVIEW_LOW_COVERAGE_PCT")
    return {
        person_id
        for person_id, count in merged.items()
        if count >= min_sample and reviewed[person_id] * 100 < count * pct
    }


def author_reviewer_matrix(scope: Scope, params: DashboardParams) -> HeatMap:
    """Two aggregate queries (review pairs, merged PRs per author) and one `in_bulk`. Cells count
    distinct pull requests, the same unit the axis labels carry. Authors are everyone with a
    merged PR or a reviewed PR in the period, so an author nobody reviews still gets a row;
    reviewers are everyone who reviewed plus those authors, so an author who never reviews shows
    as a zero column. The top `REVIEW_HEATMAP_TOP_N` people per axis are kept as themselves and
    the rest fold into an "Other" row/column (plan §4)."""
    pair_rows = list(
        _period_reviews_with_self(scope, params)
        .exclude(pull_request__author__person__isnull=True)
        .values("pull_request__author__person_id", "reviewer__person_id")
        .annotate(prs=Count("pull_request_id", distinct=True), reviews=Count("id"))
        .order_by()
    )
    merged_rows = _merged_by_author(scope, params)
    if not pair_rows and not merged_rows:
        return HeatMap()

    merged: Counter[int] = Counter()
    reviewed: Counter[int] = Counter()
    for row in merged_rows:
        merged[row["author__person_id"]] = row["merged"]
        reviewed[row["author__person_id"]] = row["reviewed"]

    received: Counter[int] = Counter()
    reviewer_prs: Counter[int] = Counter()
    reviewer_reviews: Counter[int] = Counter()
    author_totals: Counter[int] = Counter(merged)
    for row in pair_rows:
        author_id = row["pull_request__author__person_id"]
        reviewer_id = row["reviewer__person_id"]
        author_totals[author_id] += 0  # an author with reviewed but unmerged PRs still has a row
        if author_id == reviewer_id:
            continue
        received[author_id] += row["prs"]
        reviewer_prs[reviewer_id] += row["prs"]
        reviewer_reviews[reviewer_id] += row["reviews"]
    for person_id in author_totals:
        reviewer_prs[person_id] += 0

    top_n = get_int("REVIEW_HEATMAP_TOP_N")
    ranked_authors = _ranked(author_totals, received)
    ranked_reviewers = _ranked(reviewer_prs, reviewer_reviews)
    top_author_ids = set(ranked_authors[:top_n])
    top_reviewer_ids = set(ranked_reviewers[:top_n])
    flagged_reviewers = _low_activity_reviewers(reviewer_prs, ranked_reviewers)
    flagged_authors = _rarely_reviewed_authors(merged, reviewed)

    cells: dict[int | None, dict[int | None, int]] = {}
    hidden_self_reviews = 0
    for row in pair_rows:
        author_id = row["pull_request__author__person_id"]
        reviewer_id = row["reviewer__person_id"]
        if author_id == reviewer_id and not (author_id in top_author_ids and reviewer_id in top_reviewer_ids):
            hidden_self_reviews += row["prs"]
            continue
        author_key = author_id if author_id in top_author_ids else None
        reviewer_key = reviewer_id if reviewer_id in top_reviewer_ids else None
        row_cells = cells.setdefault(author_key, {})
        row_cells[reviewer_key] = row_cells.get(reviewer_key, 0) + row["prs"]

    people = Person.objects.in_bulk(top_author_ids | top_reviewer_ids)
    max_value = max(
        (
            count
            for author_key, row_cells in cells.items()
            for reviewer_key, count in row_cells.items()
            if author_key is None or author_key != reviewer_key
        ),
        default=0,
    )

    authors = [
        HeatMapAxis(
            person_id,
            people[person_id].display_name,
            pr_count=merged[person_id],
            reviewed_by_others=reviewed[person_id],
            flagged=person_id in flagged_authors,
        )
        for person_id in ranked_authors[:top_n]
    ]
    if len(ranked_authors) > top_n:
        folded = ranked_authors[top_n:]
        authors.append(
            HeatMapAxis(
                None,
                str(_("Other")),
                pr_count=sum(merged[person_id] for person_id in folded),
                reviewed_by_others=sum(reviewed[person_id] for person_id in folded),
            )
        )
    reviewers = [
        HeatMapAxis(
            person_id,
            people[person_id].display_name,
            pr_count=reviewer_prs[person_id],
            flagged=person_id in flagged_reviewers,
        )
        for person_id in ranked_reviewers[:top_n]
    ]
    if len(ranked_reviewers) > top_n:
        reviewers.append(
            HeatMapAxis(
                None,
                str(_("Other")),
                pr_count=sum(reviewer_prs[person_id] for person_id in ranked_reviewers[top_n:]),
            )
        )

    return HeatMap(
        authors=authors,
        reviewers=reviewers,
        cells=cells,
        max_value=max_value,
        hidden_self_reviews=hidden_self_reviews,
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
    same_person: bool = False  # author and reviewer are one person: muted, or a self-review

    @property
    def is_self_review(self) -> bool:
        return self.same_person and self.value > 0


@dataclass(frozen=True)
class HeatMapRow:
    author: HeatMapAxis
    cells: list[HeatMapCell]


def heat_map_grid(heat_map: HeatMap) -> list[HeatMapRow]:
    """`HeatMap.cells` as a template-friendly grid: one `HeatMapRow` per author, in the same
    order as `HeatMap.authors`, each with one `HeatMapCell` per reviewer in `HeatMap.reviewers`
    order — Django templates cannot index a dict by a loop variable, so this is resolved here."""
    rows = []
    for author in heat_map.authors:
        cells = []
        for reviewer in heat_map.reviewers:
            value = heat_map.cells.get(author.id, {}).get(reviewer.id, 0)
            same_person = author.id is not None and author.id == reviewer.id
            level = 0 if same_person else heat_level(value, heat_map.max_value)
            cells.append(HeatMapCell(reviewer, value, level, same_person=same_person))
        rows.append(HeatMapRow(author=author, cells=cells))
    return rows


def heat_map_thresholds() -> dict[str, int]:
    """The settings behind the heat map's flags, for the legend that explains them."""
    return {
        "low_activity_pct": get_int("REVIEW_LOW_ACTIVITY_PCT"),
        "low_coverage_pct": get_int("REVIEW_LOW_COVERAGE_PCT"),
        "min_sample": get_int("MIN_SAMPLE"),
    }


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
