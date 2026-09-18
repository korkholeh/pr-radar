"""`apps/churn/services.py::compute_churn_for_pull_request` (T13) against the hand-countable
`git_origin` fixture -- no bare clone needed here, `git_origin.path` is used directly as the
"clone dir" since the algorithm only ever runs `git` commands against it."""

from __future__ import annotations

import datetime

import pytest

from apps.activity.models import PullRequest
from apps.catalog.factories import RepositoryFactory
from apps.churn.gitcmd import GitOperationError
from apps.churn.models import ChurnResult
from apps.churn.services import compute_churn_for_pull_request
from apps.churn.tests.conftest import (
    make_merge_pr,
    make_mixed_delete_pr,
    make_rebase_pr,
    make_rename_pr,
    make_squash_pr,
    make_zero_pr,
)

pytestmark = pytest.mark.django_db

UTC = datetime.UTC


@pytest.fixture
def repository(git_origin):
    return RepositoryFactory(default_branch="main")


def test_squash_merge_ratio_matches_hand_counted_lines(repository, git_origin):
    pr = make_squash_pr(repository, git_origin)

    outcome = compute_churn_for_pull_request(pr, git_origin.path, window_days=21)

    assert outcome.status == ChurnResult.Status.OK
    assert outcome.lines_at_merge == 10
    assert outcome.lines_surviving == 6
    assert outcome.churn_ratio == pytest.approx(0.4)
    assert outcome.snapshot_sha


def test_merge_commit_ratio_matches_hand_counted_lines(repository, git_origin):
    pr = make_merge_pr(repository, git_origin)

    outcome = compute_churn_for_pull_request(pr, git_origin.path, window_days=21)

    assert outcome.status == ChurnResult.Status.OK
    assert outcome.lines_at_merge == 10
    assert outcome.lines_surviving == 5
    assert outcome.churn_ratio == pytest.approx(0.5)


def test_rename_survives_across_snapshot(repository, git_origin):
    pr = make_rename_pr(repository, git_origin)

    outcome = compute_churn_for_pull_request(pr, git_origin.path, window_days=21)

    assert outcome.status == ChurnResult.Status.OK
    assert outcome.lines_at_merge == 8
    assert outcome.lines_surviving == 8
    assert outcome.churn_ratio == pytest.approx(0.0)


def test_a_deleted_non_excluded_file_contributes_zero_rather_than_erroring(repository, git_origin):
    """A mixed PR (adds i.py, deletes h.py) must still compute a ratio -- h.py's path does not
    exist at `merge_commit_sha`, and previously made the whole PR return `status=error`."""
    pr = make_mixed_delete_pr(repository, git_origin)

    outcome = compute_churn_for_pull_request(pr, git_origin.path, window_days=21)

    assert outcome.status == ChurnResult.Status.OK
    assert outcome.lines_at_merge == 3
    assert outcome.lines_surviving == 3
    assert outcome.churn_ratio == pytest.approx(0.0)


def test_rebase_merge_is_unsupported_and_has_no_ratio(repository):
    pr = make_rebase_pr(repository)

    outcome = compute_churn_for_pull_request(pr, "/nonexistent", window_days=21)

    assert outcome.status == ChurnResult.Status.UNSUPPORTED_MERGE_METHOD
    assert outcome.churn_ratio is None


def test_zero_lines_at_merge_is_skipped(repository, git_origin):
    pr = make_zero_pr(repository, git_origin)

    outcome = compute_churn_for_pull_request(pr, git_origin.path, window_days=21)

    assert outcome.skip is True
    assert outcome.status == ChurnResult.Status.OK
    assert outcome.lines_at_merge == 0
    assert outcome.churn_ratio is None


def test_more_files_than_the_limit_is_too_large(repository, git_origin):
    pr = make_merge_pr(repository, git_origin)  # two non-excluded files

    outcome = compute_churn_for_pull_request(pr, git_origin.path, window_days=21, max_files=1)

    assert outcome.status == ChurnResult.Status.TOO_LARGE
    assert outcome.error == "1"  # the limit in force is recorded for the PR detail page (NIT)


def test_exactly_at_the_limit_is_computed(repository, git_origin):
    pr = make_squash_pr(repository, git_origin)  # one non-excluded file

    outcome = compute_churn_for_pull_request(pr, git_origin.path, window_days=21, max_files=1)

    assert outcome.status == ChurnResult.Status.OK


def test_unknown_merge_method_with_many_parents_is_treated_as_a_merge_commit(repository, git_origin):
    pr = make_merge_pr(repository, git_origin, merge_method=PullRequest.MergeMethod.UNKNOWN)

    outcome = compute_churn_for_pull_request(pr, git_origin.path, window_days=21)

    assert outcome.status == ChurnResult.Status.OK
    assert outcome.lines_at_merge == 10
    assert outcome.lines_surviving == 5


def test_unknown_merge_method_with_one_parent_is_treated_as_a_squash(repository, git_origin):
    pr = make_squash_pr(repository, git_origin, merge_method=PullRequest.MergeMethod.UNKNOWN)

    outcome = compute_churn_for_pull_request(pr, git_origin.path, window_days=21)

    assert outcome.status == ChurnResult.Status.OK
    assert outcome.lines_at_merge == 10
    assert outcome.lines_surviving == 6


def test_missing_snapshot_results_in_error_with_reason(repository, git_origin):
    pr = make_squash_pr(repository, git_origin, merged_at=datetime.datetime(2019, 1, 1, tzinfo=UTC))

    outcome = compute_churn_for_pull_request(pr, git_origin.path, window_days=1)

    assert outcome.status == ChurnResult.Status.ERROR
    assert outcome.error.startswith("no_snapshot")


def test_git_timeout_is_passed_to_every_git_call(monkeypatch, repository, git_origin):
    """`blame`, `rev-list` and `log` must all be bounded by `CHURN_GIT_TIMEOUT_SECONDS` -- a
    worker thread stuck on a pathological `git blame` call would otherwise block the whole
    nightly run's `ThreadPoolExecutor.__exit__` indefinitely."""
    import subprocess as subprocess_module

    captured_timeouts: list[float | None] = []
    real_run = subprocess_module.run

    def spy(argv, **kwargs):
        captured_timeouts.append(kwargs.get("timeout"))
        return real_run(argv, **kwargs)

    monkeypatch.setattr("apps.churn.gitcmd.subprocess.run", spy)

    pr = make_merge_pr(repository, git_origin, merge_method=PullRequest.MergeMethod.UNKNOWN)
    outcome = compute_churn_for_pull_request(pr, git_origin.path, window_days=21, git_timeout=42)

    assert outcome.status == ChurnResult.Status.OK
    assert len(captured_timeouts) >= 2  # at least one commit-set query and one blame call
    assert all(timeout == 42 for timeout in captured_timeouts)


def test_churn_ratio_is_clamped_to_zero_when_surviving_exceeds_at_merge(repository, git_origin, monkeypatch):
    monkeypatch.setattr("apps.churn.services._sum_surviving", lambda *args, **kwargs: 20)
    pr = make_squash_pr(repository, git_origin)

    outcome = compute_churn_for_pull_request(pr, git_origin.path, window_days=21)

    assert outcome.status == ChurnResult.Status.OK
    assert outcome.churn_ratio == 0.0


def test_blame_timeout_is_not_swallowed_and_yields_a_retryable_error(repository, git_origin, monkeypatch):
    """A `git blame` timeout (`CHURN_GIT_TIMEOUT_SECONDS`) must not be treated as a missing path --
    the previous behaviour silently under-counted `lines_at_merge` and settled the PR as a
    terminal `ok`/`skip` row that a nightly re-run never retries (round 2 review MAJOR)."""

    def raise_timeout(*args, **kwargs):
        raise GitOperationError("blame timed out", reason="timeout")

    monkeypatch.setattr("apps.churn.services.blame_counts", raise_timeout)
    pr = make_squash_pr(repository, git_origin)

    outcome = compute_churn_for_pull_request(pr, git_origin.path, window_days=21)

    assert outcome.status == ChurnResult.Status.ERROR
    assert outcome.error.startswith("blame_failed")
    assert outcome.skip is False


def test_blame_failure_for_an_existing_path_is_not_swallowed_as_a_missing_path(
    repository, git_origin, monkeypatch
):
    """A non-timeout `git_error` for a path that genuinely exists at `rev` (e.g. a corrupt object)
    must be treated as a real failure, not conflated with the legitimate deleted-path case that
    continues as 0 (see `test_a_deleted_non_excluded_file_contributes_zero_rather_than_erroring`)."""

    def raise_git_error(*args, **kwargs):
        raise GitOperationError("corrupt object", reason="git_error")

    monkeypatch.setattr("apps.churn.services.blame_counts", raise_git_error)
    pr = make_squash_pr(repository, git_origin)  # a.py genuinely exists at squash_sha

    outcome = compute_churn_for_pull_request(pr, git_origin.path, window_days=21)

    assert outcome.status == ChurnResult.Status.ERROR
    assert outcome.error.startswith("blame_failed")


def test_surviving_blame_failure_yields_a_retryable_error_not_an_unhandled_exception(
    repository, git_origin, monkeypatch
):
    """`_sum_surviving` (step 6) was previously unwrapped, so its `GitOperationError` escaped
    `compute_churn_for_pull_request` entirely (round 2 review MAJOR)."""

    def raise_error(*args, **kwargs):
        raise GitOperationError("simulated corrupt clone", reason="git_error")

    monkeypatch.setattr("apps.churn.services._sum_surviving", raise_error)
    pr = make_squash_pr(repository, git_origin)

    outcome = compute_churn_for_pull_request(pr, git_origin.path, window_days=21)

    assert outcome.status == ChurnResult.Status.ERROR
    assert outcome.error.startswith("blame_failed")
