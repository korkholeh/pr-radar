"""Per-PR facts for the PR detail page (plan §3): `timeline()` orders the events GitHub recorded
for one PR, `pr_metrics()` computes the same durations the flow metrics use but for a single row.
Both read `PullRequest` fields directly rather than through `metrics.compute()` — a per-row derived
value is not a dashboard aggregate (same exception `rows.py` already documents for `recent_prs`)."""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from django.urls import reverse
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _

from apps.accounts.selectors import ScopeFilter
from apps.activity.models import PullRequest, Review
from apps.ai_detection.body_sections import HTML_COMMENT_RE
from apps.catalog.selectors import people_in_scope
from apps.dashboards.markdown import render_markdown
from apps.metrics.calculators.base import duration_hours

_EVENT_LABELS = {
    "first_commit": _("First commit"),
    "created": _("Created"),
    "ready_for_review": _("Ready for review"),
    "merged": _("Merged"),
    "closed": _("Closed"),
}
_REVIEW_STATE_LABELS = dict(Review.State.choices)


@dataclass(frozen=True)
class TimelineEvent:
    kind: str
    at: datetime.datetime
    label_code: str
    actor: str | None = None

    @property
    def label(self) -> str:
        labels = _REVIEW_STATE_LABELS if self.kind == "review" else _EVENT_LABELS
        return str(labels.get(self.label_code, self.label_code))


def timeline(pull_request: PullRequest) -> list[TimelineEvent]:
    """Ordered by `at`; an event whose timestamp is absent (a draft PR has no
    `ready_for_review_at`, an open PR has no `merged_at`/`closed_at`) is omitted rather than
    rendered as an empty row."""
    events: list[TimelineEvent] = []
    if pull_request.first_commit_at is not None:
        events.append(TimelineEvent("first_commit", pull_request.first_commit_at, "first_commit"))
    events.append(TimelineEvent("created", pull_request.created_at, "created"))
    if pull_request.ready_for_review_at is not None:
        events.append(TimelineEvent("ready_for_review", pull_request.ready_for_review_at, "ready_for_review"))
    for review in pull_request.reviews.select_related("reviewer__person").order_by("submitted_at"):
        if review.submitted_at is None:
            continue
        actor = (
            review.reviewer.person.display_name
            if review.reviewer is not None and review.reviewer.person is not None
            else None
        )
        events.append(TimelineEvent("review", review.submitted_at, review.state, actor))
    if pull_request.merged_at is not None:
        events.append(TimelineEvent("merged", pull_request.merged_at, "merged"))
    elif pull_request.closed_at is not None:
        events.append(TimelineEvent("closed", pull_request.closed_at, "closed"))
    return sorted(events, key=lambda event: event.at)


@dataclass(frozen=True)
class PRMetrics:
    lead_time_hours: float | None
    time_to_first_review_hours: float | None
    cycle_time_hours: float | None
    effective_size: int | None
    size_bucket: str | None
    review_rounds: int | None
    commits_after_first_review: int | None
    is_rubber_stamp: bool
    is_self_merged: bool


def pr_metrics(pull_request: PullRequest) -> PRMetrics:
    """Single-PR facts, not aggregates: durations use the same field pairs as
    `metrics.calculators.flow`'s `lead_time_p50`/`cycle_time_p50`/`time_to_first_review_p50`
    (`ready_for_review_at`/`first_commit_at` -> `merged_at`/`first_review_at`), but for this one
    row rather than a percentile over a population. `duration_hours()` already returns `None` when
    either bound is missing (a draft PR with no `ready_for_review_at`) or the span is negative."""
    additions = pull_request.effective_additions
    deletions = pull_request.effective_deletions
    effective_size = None if additions is None or deletions is None else additions + deletions
    return PRMetrics(
        lead_time_hours=duration_hours(pull_request.ready_for_review_at, pull_request.merged_at),
        time_to_first_review_hours=duration_hours(
            pull_request.ready_for_review_at, pull_request.first_review_at
        ),
        cycle_time_hours=duration_hours(pull_request.first_commit_at, pull_request.merged_at),
        effective_size=effective_size,
        size_bucket=pull_request.size_bucket,
        review_rounds=pull_request.review_rounds,
        commits_after_first_review=pull_request.commits_after_first_review,
        is_rubber_stamp=pull_request.is_rubber_stamp,
        is_self_merged=pull_request.is_self_merged,
    )


@dataclass(frozen=True)
class Author:
    """Who opened the pull request, as the page shows it. `label` is `None` only when GitHub gave
    no author at all (a deleted account); an author with no `Person` mapped yet keeps its GitHub
    login as the label, so the page still names someone."""

    label: str | None
    url: str | None
    is_mapped: bool


def author(scope: ScopeFilter, pull_request: PullRequest) -> Author:
    """`url` is set only for a person `dashboards:person` would actually render: that page is
    bound to `people_in_scope()`, which drops bots, people excluded from metrics and anyone
    outside the reader's projects — linking to them would 404 (CLAUDE.md's one authorization
    choke point, read here rather than guessed at)."""
    identity = pull_request.author
    if identity is None:
        return Author(None, None, is_mapped=False)
    person = identity.person
    if person is None:
        return Author(identity.value, None, is_mapped=False)
    visible = people_in_scope(scope).filter(pk=person.pk).exists()
    url = reverse("dashboards:person", args=[person.pk]) if visible else None
    return Author(person.display_name, url, is_mapped=True)


def description(pull_request: PullRequest) -> SafeString:
    """The body rendered from Markdown, as the reader sees it on GitHub: HTML comments — a PR
    template's hints — are hidden there too, so they are left out here (the policy rules ignore them
    for the same reason). Empty when nothing but comments and whitespace is left."""
    body = HTML_COMMENT_RE.sub("", pull_request.body or "").strip()
    return render_markdown(body) if body else SafeString("")
