import datetime
from zoneinfo import ZoneInfo

import pytest

from apps.activity.factories import PullRequestFactory, ReviewFactory
from apps.metrics.models import DataVersion, DirtyDay
from apps.metrics.services import bump_data_version, data_version, mark_dirty
from apps.metrics.timeframe import day_start
from apps.policy.factories import PolicyViolationFactory
from apps.policy.models import PolicyViolation

pytestmark = pytest.mark.django_db

KYIV = ZoneInfo("Europe/Kyiv")


def test_first_read_creates_version_one():
    assert DataVersion.objects.count() == 0
    assert data_version() == 1
    assert DataVersion.objects.count() == 1


def test_bump_increments_and_is_visible_to_a_fresh_read():
    assert data_version() == 1
    bumped = bump_data_version()
    assert bumped == 2
    # A second, independent read (simulating another connection/process) sees the same value.
    assert DataVersion.objects.get(id=1).version == 2
    assert data_version() == 2


def test_mark_dirty_records_kyiv_days_of_pr_and_review_timestamps_deduplicated():
    created = datetime.datetime(2026, 1, 10, 8, 0, tzinfo=KYIV).astimezone(datetime.UTC)
    merged = datetime.datetime(2026, 1, 12, 10, 0, tzinfo=KYIV).astimezone(datetime.UTC)
    pull_request = PullRequestFactory(created_at=created, merged_at=merged, closed_at=merged)
    ReviewFactory(pull_request=pull_request, submitted_at=merged)  # same day as merged_at
    other_review_day = datetime.datetime(2026, 1, 11, 9, 0, tzinfo=KYIV).astimezone(datetime.UTC)
    ReviewFactory(pull_request=pull_request, submitted_at=other_review_day)

    mark_dirty(pull_request.id)

    days = set(DirtyDay.objects.values_list("date", flat=True))
    assert days == {
        datetime.date(2026, 1, 10),
        datetime.date(2026, 1, 11),
        datetime.date(2026, 1, 12),
    }


def test_mark_dirty_a_pr_merged_at_2359_kyiv_marks_that_day_not_the_next():
    merged_2359_kyiv = datetime.datetime(2026, 1, 10, 23, 59, tzinfo=KYIV).astimezone(datetime.UTC)
    pull_request = PullRequestFactory(created_at=merged_2359_kyiv, merged_at=merged_2359_kyiv)

    mark_dirty(pull_request.id)

    days = set(DirtyDay.objects.values_list("date", flat=True))
    assert days == {datetime.date(2026, 1, 10)}


def test_mark_dirty_is_idempotent():
    pull_request = PullRequestFactory()
    mark_dirty(pull_request.id)
    mark_dirty(pull_request.id)
    assert DirtyDay.objects.count() == 1


def test_mark_dirty_records_the_reverted_pr_merge_day():
    """`revert_rate`'s numerator is a property of the *reverted* PR's merge day, not the reverting
    PR's own days — syncing the revert PR must still dirty the original merge day, or the rollup
    for it stays stale until a full recompute."""
    reverted_merged = datetime.datetime(2026, 6, 10, 12, 0, tzinfo=KYIV).astimezone(datetime.UTC)
    reverted_pr = PullRequestFactory(created_at=reverted_merged, merged_at=reverted_merged)
    revert_created = datetime.datetime(2026, 6, 20, 9, 0, tzinfo=KYIV).astimezone(datetime.UTC)
    revert_pr = PullRequestFactory(
        created_at=revert_created,
        merged_at=revert_created,
        is_revert=True,
        reverts_pr=reverted_pr,
    )

    mark_dirty(revert_pr.id)

    days = set(DirtyDay.objects.values_list("date", flat=True))
    assert days == {datetime.date(2026, 6, 10), datetime.date(2026, 6, 20)}


def test_mark_dirty_records_the_previously_reverted_pr_merge_day_when_repointed():
    """A caller (the sync pipeline) that captured the PR's `reverts_pr_id` before derive ran can
    pass it as `previous_reverts_pr_id` so a repointed or cleared revert link still dirties the
    merge day of the PR it used to target."""
    old_target_merged = datetime.datetime(2026, 6, 5, 12, 0, tzinfo=KYIV).astimezone(datetime.UTC)
    old_target = PullRequestFactory(created_at=old_target_merged, merged_at=old_target_merged)
    revert_created = datetime.datetime(2026, 6, 20, 9, 0, tzinfo=KYIV).astimezone(datetime.UTC)
    revert_pr = PullRequestFactory(created_at=revert_created, merged_at=revert_created, is_revert=True)

    mark_dirty(revert_pr.id, previous_reverts_pr_id=old_target.id)

    days = set(DirtyDay.objects.values_list("date", flat=True))
    assert datetime.date(2026, 6, 5) in days


def test_mark_dirty_does_not_record_the_violation_recording_day():
    """The violation metrics are dated by the pull request's `created_at`, which is already
    dirtied; the day a sync wrote the violation row (`auto_now_add`) affects no metric."""
    old_day = datetime.datetime(2026, 1, 1, 8, 0, tzinfo=KYIV).astimezone(datetime.UTC)
    pull_request = PullRequestFactory(created_at=old_day, merged_at=old_day)
    violation = PolicyViolationFactory(pull_request=pull_request, rule_code=PolicyViolation.RuleCode.NO_TESTS)
    violation_day = datetime.date(2026, 1, 15)
    violation.created_at = day_start(violation_day)
    violation.save(update_fields=["created_at"])

    mark_dirty(pull_request.id)

    days = set(DirtyDay.objects.values_list("date", flat=True))
    assert datetime.date(2026, 1, 1) in days
    assert violation_day not in days


def test_mark_dirty_records_extra_pull_request_ids_merge_days():
    """`followup_fix_rate` is a property of the *original* PR's merge day, discovered while
    syncing a later fix PR — `extra_pull_request_ids` lets that sync also dirty it."""
    original_merged = datetime.datetime(2026, 3, 1, 12, 0, tzinfo=KYIV).astimezone(datetime.UTC)
    original = PullRequestFactory(created_at=original_merged, merged_at=original_merged)
    fix_created = datetime.datetime(2026, 3, 10, 9, 0, tzinfo=KYIV).astimezone(datetime.UTC)
    fix_pr = PullRequestFactory(created_at=fix_created, merged_at=fix_created)

    mark_dirty(fix_pr.id, extra_pull_request_ids={original.id})

    days = set(DirtyDay.objects.values_list("date", flat=True))
    assert days == {datetime.date(2026, 3, 1), datetime.date(2026, 3, 10)}
