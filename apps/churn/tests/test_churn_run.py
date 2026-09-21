"""`apps/churn/services.py::run_churn` (T14): grouping by repository, thread-pool git work,
main-thread writes, per-repository failure isolation, `bump_data_version`."""

from __future__ import annotations

import pytest

from apps.catalog.factories import RepositoryFactory
from apps.churn.clones import ensure_clone as real_ensure_clone
from apps.churn.gitcmd import GitOperationError
from apps.churn.models import ChurnResult
from apps.churn.selectors import eligible_pull_requests
from apps.churn.services import _compute_repository, run_churn
from apps.churn.tests.conftest import make_merge_pr, make_squash_pr, make_zero_pr
from apps.connections.factories import GitHubConnectionFactory
from apps.connections.services import set_token

pytestmark = pytest.mark.django_db

TOKEN = "ghp_secrettokenvalue0123456789"


@pytest.fixture(autouse=True)
def _data_dir_in_tmp_path(settings, tmp_path):
    settings.DATA_DIR = tmp_path


@pytest.fixture
def repository(git_origin, origin_remote):
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    return RepositoryFactory(connection=connection, default_branch="main")


def test_writes_one_row_per_computed_pr_and_a_ratio_less_row_for_zero_lines_at_merge(repository, git_origin):
    """The zero-lines-at-merge PR still gets a settled `ChurnResult` (status=ok, churn_ratio=None)
    -- not no row at all -- so it does not stay eligible and get re-cloned/re-blamed forever."""
    squash_pr = make_squash_pr(repository, git_origin)
    zero_pr = make_zero_pr(repository, git_origin)

    result = run_churn(window_days=21)

    assert result.computed == 1
    assert result.skipped == 1
    assert ChurnResult.objects.filter(pull_request=squash_pr, window_days=21).exists()
    zero_row = ChurnResult.objects.get(pull_request=zero_pr, window_days=21)
    assert zero_row.status == ChurnResult.Status.OK
    assert zero_row.lines_at_merge == 0
    assert zero_row.churn_ratio is None

    # And it settles: a second run finds nothing left to do for it.
    assert not eligible_pull_requests(21).filter(pk=zero_pr.pk).exists()


def test_a_stale_error_row_is_overwritten_when_the_pr_later_settles(repository, git_origin):
    zero_pr = make_zero_pr(repository, git_origin)
    ChurnResult.objects.create(
        pull_request=zero_pr, window_days=21, status=ChurnResult.Status.ERROR, error="blame_failed: old"
    )

    run_churn(window_days=21)

    row = ChurnResult.objects.get(pull_request=zero_pr, window_days=21)
    assert row.status == ChurnResult.Status.OK
    assert row.error == ""


def test_second_run_is_idempotent(repository, git_origin):
    make_squash_pr(repository, git_origin)

    run_churn(window_days=21)
    row = ChurnResult.objects.get(window_days=21)
    computed_at_first = row.computed_at

    second = run_churn(window_days=21)

    assert second.computed == 0  # already settled, no longer eligible
    assert ChurnResult.objects.count() == 1
    assert ChurnResult.objects.get(window_days=21).computed_at == computed_at_first


def test_unusable_connection_yields_connection_unusable_error(git_origin, origin_remote):
    connection = GitHubConnectionFactory(is_active=False)
    repository = RepositoryFactory(connection=connection, default_branch="main")
    pr = make_squash_pr(repository, git_origin)

    result = run_churn(window_days=21)

    assert result.errors == 1
    row = ChurnResult.objects.get(pull_request=pr, window_days=21)
    assert row.status == ChurnResult.Status.ERROR
    assert row.error.startswith("connection_unusable")


def test_a_repository_with_a_failing_fetch_does_not_stop_the_other_repository(
    monkeypatch, git_origin, tmp_path
):
    good_connection = GitHubConnectionFactory()
    set_token(good_connection, TOKEN)
    good_repo = RepositoryFactory(connection=good_connection, full_name="acme/good", default_branch="main")

    bad_connection = GitHubConnectionFactory()
    set_token(bad_connection, TOKEN)
    bad_repo = RepositoryFactory(connection=bad_connection, full_name="acme/bad", default_branch="main")

    def remote_url_for(repository):
        if repository.id == good_repo.id:
            return f"file://{git_origin.path}"
        return f"file://{tmp_path}/does-not-exist"

    monkeypatch.setattr("apps.churn.clones.remote_url_for", remote_url_for)

    good_pr = make_squash_pr(good_repo, git_origin)
    bad_pr = make_squash_pr(bad_repo, git_origin)

    result = run_churn(window_days=21)

    assert result.computed == 1
    assert result.errors == 1
    assert ChurnResult.objects.get(pull_request=good_pr, window_days=21).status == ChurnResult.Status.OK
    bad_row = ChurnResult.objects.get(pull_request=bad_pr, window_days=21)
    assert bad_row.status == ChurnResult.Status.ERROR
    assert bad_row.error.startswith("fetch_failed")


def test_a_prs_blame_failure_does_not_abort_the_rest_of_its_own_repository(
    monkeypatch, repository, git_origin
):
    """A `GitOperationError` from one PR's `_sum_surviving` call must not escape into
    `_compute_repository`/`run_churn`'s broad `except Exception` and turn every PR of that
    repository into `error`/`worker_failed` -- the *other* PR of the SAME repository (not just a
    different repository, already covered above) still gets a settled `ok` row (round 2 review
    MAJOR)."""
    import apps.churn.services as services_module

    squash_pr = make_squash_pr(repository, git_origin)
    merge_pr = make_merge_pr(repository, git_origin)

    real_sum_surviving = services_module._sum_surviving

    def flaky_sum_surviving(clone_dir, merge_sha, *args, **kwargs):
        if merge_sha == git_origin.merge_sha:
            raise GitOperationError("simulated corrupt clone", reason="git_error")
        return real_sum_surviving(clone_dir, merge_sha, *args, **kwargs)

    monkeypatch.setattr(services_module, "_sum_surviving", flaky_sum_surviving)

    run_churn(window_days=21)

    good_row = ChurnResult.objects.get(pull_request=squash_pr, window_days=21)
    assert good_row.status == ChurnResult.Status.OK
    bad_row = ChurnResult.objects.get(pull_request=merge_pr, window_days=21)
    assert bad_row.status == ChurnResult.Status.ERROR
    assert bad_row.error.startswith("blame_failed")


def test_bump_data_version_is_called_once(monkeypatch, repository, git_origin):
    make_squash_pr(repository, git_origin)
    calls = []
    monkeypatch.setattr("apps.churn.services.bump_data_version", lambda: calls.append(1))

    run_churn(window_days=21)

    assert len(calls) == 1


def test_no_eligible_pull_requests_still_bumps_data_version(monkeypatch):
    calls = []
    monkeypatch.setattr("apps.churn.services.bump_data_version", lambda: calls.append(1))

    result = run_churn(window_days=21)

    assert result.computed == 0
    assert len(calls) == 1


def test_repository_budget_is_measured_from_when_the_worker_starts_not_when_it_was_submitted(
    monkeypatch, repository, git_origin
):
    """`_compute_repository` must compute its deadline from `repo_budget` as its own first
    statement -- a repository that waited a long time in the executor's queue before its worker
    started must still get its full budget, not an already-expired one."""
    fake_clock = {"t": 0.0}
    monkeypatch.setattr("apps.churn.services.time.monotonic", lambda: fake_clock["t"])

    pr = make_squash_pr(repository, git_origin)

    # Simulate a long queueing delay between submission and this worker actually starting.
    fake_clock["t"] = 10_000.0

    work = _compute_repository(
        repository, [pr], window_days=21, repo_budget=600, max_files=50, git_timeout=None
    )

    assert len(work.churn) == 1
    assert work.churn[0].status == ChurnResult.Status.OK


def test_settings_are_read_once_on_the_main_thread_not_per_worker(monkeypatch, git_origin, origin_remote):
    """Worker threads must never open their own ORM connection to read an AppSetting (RISKS row
    14) -- `run_churn` reads every churn setting exactly once, regardless of how many
    repositories/PRs are processed."""
    import apps.churn.services as services_module

    real_get_int = services_module.get_int
    calls: list[str] = []

    def counting_get_int(key):
        calls.append(key)
        return real_get_int(key)

    monkeypatch.setattr(services_module, "get_int", counting_get_int)

    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    repo_a = RepositoryFactory(connection=connection, full_name="acme/settings-a", default_branch="main")
    repo_b = RepositoryFactory(connection=connection, full_name="acme/settings-b", default_branch="main")
    make_squash_pr(repo_a, git_origin)
    make_squash_pr(repo_b, git_origin)

    run_churn(window_days=21)

    assert calls.count("CHURN_MAX_FILES") == 1
    assert calls.count("CHURN_GIT_TIMEOUT_SECONDS") == 1
    assert calls.count("CHURN_REPO_TIME_BUDGET_SECONDS") == 1
    assert calls.count("CHURN_MAX_WORKERS") == 1


def test_unexpected_worker_exception_is_recorded_as_error_and_does_not_abort_other_repositories(
    monkeypatch, git_origin, origin_remote
):
    """An exception that is not a `GitOperationError` (e.g. an `OSError` from disk exhaustion)
    must not escape `run_churn` -- it must be recorded as an `error` outcome for that repository's
    PRs, leaving the other repositories' results and `bump_data_version()` intact."""
    good_connection = GitHubConnectionFactory()
    set_token(good_connection, TOKEN)
    good_repo = RepositoryFactory(
        connection=good_connection, full_name="acme/good-worker", default_branch="main"
    )

    bad_connection = GitHubConnectionFactory()
    set_token(bad_connection, TOKEN)
    bad_repo = RepositoryFactory(
        connection=bad_connection, full_name="acme/bad-worker", default_branch="main"
    )

    good_pr = make_squash_pr(good_repo, git_origin)
    bad_pr = make_squash_pr(bad_repo, git_origin)

    def flaky_ensure_clone(repository, credentials, *, timeout=None):
        if repository.id == bad_repo.id:
            raise OSError("simulated disk exhaustion")
        return real_ensure_clone(repository, credentials, timeout=timeout)

    monkeypatch.setattr("apps.churn.services.ensure_clone", flaky_ensure_clone)

    calls = []
    monkeypatch.setattr("apps.churn.services.bump_data_version", lambda: calls.append(1))

    result = run_churn(window_days=21)

    assert result.computed == 1
    assert result.errors == 1
    assert ChurnResult.objects.get(pull_request=good_pr, window_days=21).status == ChurnResult.Status.OK
    bad_row = ChurnResult.objects.get(pull_request=bad_pr, window_days=21)
    assert bad_row.status == ChurnResult.Status.ERROR
    assert bad_row.error.startswith("worker_failed")
    assert len(calls) == 1
