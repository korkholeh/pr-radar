import datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.activity.derive import (
    compute_derived,
    derive_pull_request,
    derive_pull_requests,
    size_bucket_bounds,
)
from apps.activity.factories import (
    CommitFactory,
    PRFileFactory,
    PullRequestCommitFactory,
    PullRequestFactory,
    ReviewCommentFactory,
    ReviewFactory,
)
from apps.activity.models import PRFile, PullRequest, Review
from apps.catalog.factories import IdentityFactory, PersonFactory
from apps.catalog.models import Identity
from apps.catalog.services import set_setting

NOW = timezone.datetime(2026, 1, 10, tzinfo=datetime.UTC)


def _dt(**kwargs):
    return timezone.datetime(2026, 1, 1, tzinfo=datetime.UTC) + datetime.timedelta(**kwargs)


def _login(value, is_bot=False):
    identity = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value=value)
    identity.person = PersonFactory(display_name=value, is_bot=is_bot)
    identity.save(update_fields=["person"])
    return identity


@pytest.mark.django_db
class TestFilesAndSize:
    def test_normal_pr_sums_non_excluded_files(self):
        pr = PullRequestFactory()
        PRFileFactory(pull_request=pr, path="app.py", additions=5, deletions=2)
        PRFileFactory(pull_request=pr, path="uv.lock", additions=100, deletions=0)

        derived = compute_derived(pr)

        assert derived.effective_additions == 5
        assert derived.effective_deletions == 2
        assert derived.size_bucket == "XS"

    def test_excluded_only_pr_has_zero_effective_lines(self):
        pr = PullRequestFactory()
        PRFileFactory(pull_request=pr, path="uv.lock", additions=500, deletions=500)

        derived = compute_derived(pr)

        assert derived.effective_additions == 0
        assert derived.effective_deletions == 0
        assert derived.size_bucket == "XS"

    def test_pr_with_no_files_has_none_not_zero(self):
        pr = PullRequestFactory()

        derived = compute_derived(pr)

        assert derived.effective_additions is None
        assert derived.effective_deletions is None
        assert derived.size_bucket is None

    @pytest.mark.parametrize(
        "total,expected",
        [(9, "XS"), (10, "S"), (99, "S"), (100, "M"), (399, "M"), (400, "L"), (999, "L"), (1000, "XL")],
    )
    def test_size_bucket_boundaries(self, total, expected):
        pr = PullRequestFactory()
        PRFileFactory(pull_request=pr, path="app.py", additions=total, deletions=0)

        derived = compute_derived(pr)

        assert derived.size_bucket == expected

    def test_has_test_changes_is_true_for_a_non_excluded_test_file(self):
        pr = PullRequestFactory()
        PRFileFactory(pull_request=pr, path="tests/test_app.py", additions=1, deletions=0)

        derived = compute_derived(pr)

        assert derived.has_test_changes is True

    def test_has_test_changes_ignores_excluded_test_files(self):
        pr = PullRequestFactory()
        PRFileFactory(pull_request=pr, path="app/migrations/test_data.py", additions=1, deletions=0)

        derived = compute_derived(pr)

        assert derived.has_test_changes is False


@pytest.mark.django_db
class TestReadyForReviewAt:
    def test_never_drafted_pr_is_ready_at_creation(self):
        pr = PullRequestFactory(is_draft=False, ready_for_review_at=None, created_at=_dt())

        derived = compute_derived(pr)

        assert derived.ready_for_review_at == pr.created_at

    def test_drafted_pr_keeps_the_timeline_value(self):
        ready_at = _dt(hours=5)
        pr = PullRequestFactory(is_draft=False, ready_for_review_at=ready_at, created_at=_dt())

        derived = compute_derived(pr)

        assert derived.ready_for_review_at == ready_at

    def test_still_draft_pr_has_no_ready_time(self):
        pr = PullRequestFactory(is_draft=True, ready_for_review_at=None, created_at=_dt())

        derived = compute_derived(pr)

        assert derived.ready_for_review_at is None


@pytest.mark.django_db
class TestFirstReviewAndApproval:
    def test_self_review_is_ignored(self):
        author = _login("author")
        pr = PullRequestFactory(author=author)
        ReviewFactory(pull_request=pr, reviewer=author, state=Review.State.APPROVED, submitted_at=_dt())

        derived = compute_derived(pr)

        assert derived.first_review_at is None
        assert derived.first_approval_at is None

    def test_bot_review_is_ignored(self):
        author = _login("author")
        bot = _login("dependabot[bot]", is_bot=True)
        pr = PullRequestFactory(author=author)
        ReviewFactory(pull_request=pr, reviewer=bot, state=Review.State.APPROVED, submitted_at=_dt())

        derived = compute_derived(pr)

        assert derived.first_review_at is None

    def test_review_comment_earlier_than_review_wins(self):
        author = _login("author")
        reviewer = _login("reviewer")
        pr = PullRequestFactory(author=author)
        ReviewFactory(pull_request=pr, reviewer=reviewer, submitted_at=_dt(hours=5))
        ReviewCommentFactory(pull_request=pr, author=reviewer, created_at=_dt(hours=2))

        derived = compute_derived(pr)

        assert derived.first_review_at == _dt(hours=2)

    def test_no_reviews_means_first_review_at_is_none(self):
        pr = PullRequestFactory()

        derived = compute_derived(pr)

        assert derived.first_review_at is None


@pytest.mark.django_db
class TestTimestamps:
    def test_first_commit_at_falls_back_to_committed_at_when_authored_at_is_missing(self):
        pr = PullRequestFactory()
        early_commit = CommitFactory(authored_at=None, committed_at=_dt(hours=1))
        late_commit = CommitFactory(authored_at=_dt(hours=5), committed_at=_dt(hours=6))
        PullRequestCommitFactory(pull_request=pr, commit=early_commit, position=0)
        PullRequestCommitFactory(pull_request=pr, commit=late_commit, position=1)

        derived = compute_derived(pr)

        assert derived.first_commit_at == _dt(hours=1)

    def test_last_activity_at_is_the_latest_review_comment(self):
        author = _login("author")
        reviewer = _login("reviewer")
        pr = PullRequestFactory(
            author=author,
            created_at=_dt(),
            merged_at=_dt(hours=1),
            closed_at=_dt(hours=1),
        )
        PullRequestCommitFactory(pull_request=pr, commit=CommitFactory(committed_at=_dt(hours=1)), position=0)
        ReviewFactory(pull_request=pr, reviewer=reviewer, submitted_at=_dt(hours=2))
        ReviewCommentFactory(pull_request=pr, author=reviewer, created_at=_dt(hours=10))

        derived = compute_derived(pr)

        assert derived.last_activity_at == _dt(hours=10)

    def test_last_activity_at_falls_back_to_created_at_with_nothing_else(self):
        pr = PullRequestFactory(created_at=_dt(), merged_at=None, closed_at=None)

        derived = compute_derived(pr)

        assert derived.last_activity_at == pr.created_at


@pytest.mark.django_db
class TestReviewShape:
    def test_no_reviews_means_one_review_round(self):
        pr = PullRequestFactory()

        derived = compute_derived(pr)

        assert derived.review_rounds == 1

    def test_two_changes_requested_makes_three_rounds(self):
        author = _login("author")
        reviewer = _login("reviewer")
        pr = PullRequestFactory(author=author)
        ReviewFactory(pull_request=pr, reviewer=reviewer, state=Review.State.CHANGES_REQUESTED)
        ReviewFactory(pull_request=pr, reviewer=reviewer, state=Review.State.CHANGES_REQUESTED)

        derived = compute_derived(pr)

        assert derived.review_rounds == 3

    def test_commits_split_before_and_after_first_review(self):
        author = _login("author")
        reviewer = _login("reviewer")
        pr = PullRequestFactory(author=author)
        ReviewFactory(pull_request=pr, reviewer=reviewer, submitted_at=_dt(hours=10))
        before = CommitFactory(committed_at=_dt(hours=5))
        after = CommitFactory(committed_at=_dt(hours=15))
        PullRequestCommitFactory(pull_request=pr, commit=before, position=0)
        PullRequestCommitFactory(pull_request=pr, commit=after, position=1)

        derived = compute_derived(pr)

        assert derived.commits_after_first_review == 1

    def test_no_review_means_commits_after_first_review_is_none(self):
        pr = PullRequestFactory()
        commit = CommitFactory()
        PullRequestCommitFactory(pull_request=pr, commit=commit)

        derived = compute_derived(pr)

        assert derived.commits_after_first_review is None

    def test_self_merge_detected_via_shared_person(self):
        person = PersonFactory()
        author = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="author-login", person=person)
        merged_by = IdentityFactory(kind=Identity.Kind.GIT_EMAIL, value="author@example.com", person=person)
        pr = PullRequestFactory(author=author, merged_by=merged_by)

        derived = compute_derived(pr)

        assert derived.is_self_merged is True

    def test_not_self_merged_when_someone_else_merges(self):
        author = _login("author")
        merger = _login("merger")
        pr = PullRequestFactory(author=author, merged_by=merger)

        derived = compute_derived(pr)

        assert derived.is_self_merged is False


@pytest.mark.django_db
class TestRubberStamp:
    def _large_pr(self, ready_at):
        author = _login("author")
        reviewer = _login("reviewer")
        pr = PullRequestFactory(
            author=author, is_draft=False, ready_for_review_at=ready_at, created_at=ready_at
        )
        PRFileFactory(pull_request=pr, path="app.py", additions=500, deletions=0)
        return pr, reviewer

    def test_rubber_stamp_boundary_is_exclusive(self):
        ready_at = _dt()
        pr, reviewer = self._large_pr(ready_at)
        ReviewFactory(
            pull_request=pr,
            reviewer=reviewer,
            state=Review.State.APPROVED,
            submitted_at=ready_at + datetime.timedelta(minutes=10),
            body_length=0,
        )
        assert compute_derived(pr).is_rubber_stamp is False

        pr2, reviewer2 = self._large_pr(ready_at)
        ReviewFactory(
            pull_request=pr2,
            reviewer=reviewer2,
            state=Review.State.APPROVED,
            submitted_at=ready_at + datetime.timedelta(minutes=9, seconds=59),
            body_length=0,
        )
        assert compute_derived(pr2).is_rubber_stamp is True

    def test_review_comment_disqualifies_rubber_stamp(self):
        ready_at = _dt()
        pr, reviewer = self._large_pr(ready_at)
        ReviewFactory(
            pull_request=pr,
            reviewer=reviewer,
            state=Review.State.APPROVED,
            submitted_at=ready_at + datetime.timedelta(minutes=1),
            body_length=0,
        )
        ReviewCommentFactory(pull_request=pr, author=reviewer, created_at=ready_at)

        assert compute_derived(pr).is_rubber_stamp is False

    def test_medium_pr_fast_approval_is_not_a_rubber_stamp(self):
        author = _login("author")
        reviewer = _login("reviewer")
        ready_at = _dt()
        pr = PullRequestFactory(
            author=author, is_draft=False, ready_for_review_at=ready_at, created_at=ready_at
        )
        PRFileFactory(pull_request=pr, path="app.py", additions=50, deletions=0)
        ReviewFactory(
            pull_request=pr,
            reviewer=reviewer,
            state=Review.State.APPROVED,
            submitted_at=ready_at + datetime.timedelta(minutes=1),
            body_length=0,
        )

        assert compute_derived(pr).is_rubber_stamp is False


@pytest.mark.django_db
class TestHotfix:
    def test_hotfix_branch(self):
        pr = PullRequestFactory(head_ref="hotfix/urgent-thing", title="Something")
        assert compute_derived(pr).is_hotfix is True

    def test_fix_title(self):
        pr = PullRequestFactory(title="Fix: broken thing", head_ref="feature/x")
        assert compute_derived(pr).is_hotfix is True

    def test_hotfix_label(self):
        pr = PullRequestFactory(title="Something", head_ref="feature/x", labels=["needs-hotfix"])
        assert compute_derived(pr).is_hotfix is True

    def test_prefix_fix_does_not_count(self):
        pr = PullRequestFactory(title="prefix-fix: something", head_ref="feature/prefix-fix")
        assert compute_derived(pr).is_hotfix is False


@pytest.mark.django_db
class TestRevert:
    def test_revert_chain_links_reverts_pr(self):
        original = PullRequestFactory(state=PullRequest.State.MERGED, merged_at=_dt(), title="Add widget")
        commit = CommitFactory(repository=original.repository, sha="a" * 40)
        PullRequestCommitFactory(pull_request=original, commit=commit)

        reverting = PullRequestFactory(
            repository=original.repository,
            title='Revert "Add widget"',
            body=f"This reverts commit {commit.sha}.",
            created_at=_dt(hours=1),
        )

        derived = compute_derived(reverting)

        assert derived.is_revert is True
        assert derived.reverts_pr_id == original.pk

    def test_reverts_by_number(self):
        original = PullRequestFactory(state=PullRequest.State.MERGED, merged_at=_dt(), number=1)
        reverting = PullRequestFactory(
            repository=original.repository,
            title="Revert something",
            body=f"Reverts {original.repository.full_name}#{original.number}",
        )

        derived = compute_derived(reverting)

        assert derived.is_revert is True
        assert derived.reverts_pr_id == original.pk

    def test_unresolvable_revert_has_no_target(self):
        pr = PullRequestFactory(body="This reverts commit " + "f" * 40)

        derived = compute_derived(pr)

        assert derived.is_revert is True
        assert derived.reverts_pr_id is None

    def test_pr_cannot_revert_itself(self):
        pr = PullRequestFactory(body="placeholder")
        commit = CommitFactory(repository=pr.repository, sha="b" * 40)
        PullRequestCommitFactory(pull_request=pr, commit=commit)
        pr.body = f"This reverts commit {commit.sha}."
        pr.save(update_fields=["body"])

        derived = compute_derived(pr)

        assert derived.is_revert is True
        assert derived.reverts_pr_id is None


@pytest.mark.django_db
class TestDeriveWriterIdempotency:
    def _build_pr(self):
        author = _login("author")
        reviewer = _login("reviewer")
        pr = PullRequestFactory(author=author, created_at=_dt(), is_draft=False)
        PRFileFactory(pull_request=pr, path="app.py", additions=20, deletions=5)
        PRFileFactory(pull_request=pr, path="tests/test_app.py", additions=5, deletions=0)
        commit = CommitFactory(committed_at=_dt(hours=1))
        PullRequestCommitFactory(pull_request=pr, commit=commit)
        ReviewFactory(
            pull_request=pr, reviewer=reviewer, state=Review.State.APPROVED, submitted_at=_dt(hours=2)
        )
        return pr

    def test_second_derive_pass_changes_nothing(self):
        pr = self._build_pr()

        derive_pull_request(pr.pk)
        pr.refresh_from_db()
        snapshot = {f.name: getattr(pr, f.name) for f in PullRequest._meta.fields}
        file_snapshot = {f.pk: (f.is_excluded, f.is_test) for f in PRFile.objects.filter(pull_request=pr)}

        derive_pull_request(pr.pk)
        pr.refresh_from_db()
        second_snapshot = {f.name: getattr(pr, f.name) for f in PullRequest._meta.fields}
        second_file_snapshot = {
            f.pk: (f.is_excluded, f.is_test) for f in PRFile.objects.filter(pull_request=pr)
        }

        assert snapshot == second_snapshot
        assert file_snapshot == second_file_snapshot

    def test_derive_query_count(self):
        pr = self._build_pr()

        with CaptureQueriesContext(connection) as ctx:
            derive_pull_request(pr.pk)

        assert len(ctx.captured_queries) <= 15

    def test_derive_pull_requests_returns_processed_count(self):
        pr1 = self._build_pr()
        pr2 = self._build_pr()

        count = derive_pull_requests(PullRequest.objects.filter(pk__in=[pr1.pk, pr2.pk]))

        assert count == 2


@pytest.mark.django_db
@pytest.mark.parametrize(
    "bucket,bounds",
    [
        ("XS", (0, 10)),
        ("S", (10, 100)),
        ("M", (100, 400)),
        ("L", (400, 1000)),
        ("XL", (1000, None)),
        ("XXL", None),
    ],
)
def test_size_bucket_bounds_follow_the_default_boundaries(bucket, bounds):
    assert size_bucket_bounds(bucket) == bounds


@pytest.mark.django_db
def test_size_bucket_bounds_follow_the_setting():
    set_setting("PR_SIZE_BUCKETS", {"XS": 5, "S": 50, "M": 200, "L": 800})

    assert size_bucket_bounds("M") == (50, 200)
    assert size_bucket_bounds("XL") == (800, None)
