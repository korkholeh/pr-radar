"""T12: `pr_detail.timeline()`/`pr_detail.pr_metrics()` — event order and omission of absent
events, durations against hand-computed values, `None` (never `0`) for a missing bound."""

from __future__ import annotations

import datetime

import pytest

from apps.activity.factories import PullRequestFactory, ReviewFactory
from apps.catalog.factories import IdentityFactory, PersonFactory
from apps.dashboards.pr_detail import pr_metrics, timeline


def _dt(hour: int) -> datetime.datetime:
    return datetime.datetime(2026, 6, 1, hour, tzinfo=datetime.UTC)


@pytest.mark.django_db
def test_timeline_orders_events_and_omits_absent_ones():
    """No `first_commit_at`, no reviews, no `merged_at`/`closed_at` (an open PR) — only the events
    that actually happened appear, in time order."""
    pull_request = PullRequestFactory(
        created_at=_dt(1),
        ready_for_review_at=_dt(2),
        first_commit_at=None,
        merged_at=None,
        closed_at=None,
    )

    events = timeline(pull_request)

    assert [event.kind for event in events] == ["created", "ready_for_review"]
    assert [event.at for event in events] == [_dt(1), _dt(2)]


@pytest.mark.django_db
def test_timeline_includes_reviews_in_order_with_actor():
    person = PersonFactory(display_name="Ada")
    reviewer = IdentityFactory(person=person)
    pull_request = PullRequestFactory(created_at=_dt(1), ready_for_review_at=_dt(1), merged_at=_dt(5))
    ReviewFactory(pull_request=pull_request, reviewer=reviewer, state="APPROVED", submitted_at=_dt(3))

    events = timeline(pull_request)

    assert [event.kind for event in events] == ["created", "ready_for_review", "review", "merged"]
    review_event = events[2]
    assert review_event.actor == "Ada"
    assert review_event.label_code == "APPROVED"


@pytest.mark.django_db
def test_timeline_reviews_with_no_submitted_at_are_omitted():
    pull_request = PullRequestFactory(created_at=_dt(1))
    ReviewFactory(pull_request=pull_request, submitted_at=None)

    events = timeline(pull_request)

    assert [event.kind for event in events] == ["created"]


@pytest.mark.django_db
def test_pr_metrics_durations_match_hand_computed_values():
    pull_request = PullRequestFactory(
        created_at=_dt(0),
        first_commit_at=_dt(0),
        ready_for_review_at=_dt(1),
        first_review_at=_dt(3),
        merged_at=_dt(9),
        effective_additions=40,
        effective_deletions=10,
        review_rounds=2,
        commits_after_first_review=1,
        size_bucket="M",
        is_rubber_stamp=False,
        is_self_merged=False,
    )

    metrics = pr_metrics(pull_request)

    assert metrics.lead_time_hours == pytest.approx(8.0)
    assert metrics.time_to_first_review_hours == pytest.approx(2.0)
    assert metrics.cycle_time_hours == pytest.approx(9.0)
    assert metrics.effective_size == 50
    assert metrics.size_bucket == "M"
    assert metrics.review_rounds == 2
    assert metrics.commits_after_first_review == 1


@pytest.mark.django_db
def test_pr_metrics_draft_pr_has_none_durations_not_zero():
    pull_request = PullRequestFactory(
        created_at=_dt(0),
        ready_for_review_at=None,
        merged_at=None,
        first_review_at=None,
        effective_additions=None,
        effective_deletions=None,
    )

    metrics = pr_metrics(pull_request)

    assert metrics.lead_time_hours is None
    assert metrics.time_to_first_review_hours is None
    assert metrics.effective_size is None
