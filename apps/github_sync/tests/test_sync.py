import datetime
import json
from pathlib import Path

import httpx
import pytest
from django.utils import timezone

from apps.activity.models import CheckStatus, Commit, PRFile, PullRequest, Review
from apps.catalog.factories import RepositoryFactory
from apps.connections.factories import GitHubConnectionFactory
from apps.connections.services import set_token
from apps.github_sync.models import SyncLock, SyncRun
from apps.github_sync.services import SyncAlreadyRunning, _watermark, run_sync
from apps.github_sync.tests.conftest import mock_graphql_responses, mock_graphql_sequence
from apps.metrics.models import DailyRollup
from apps.metrics.services import data_version
from apps.metrics.timeframe import day_of

TOKEN = "ghp_secrettokenvalue0123456789"


def _rate_limit_block():
    return {"remaining": 4900, "resetAt": "2026-01-01T01:00:00Z", "cost": 1}


def _pr_node(number, updated_at):
    return {
        "id": f"PR_{number}",
        "number": number,
        "title": "A pull request",
        "body": "",
        "state": "OPEN",
        "isDraft": False,
        "baseRefName": "main",
        "headRefName": f"feature/{number}",
        "createdAt": "2026-01-01T09:00:00Z",
        "updatedAt": updated_at,
        "mergedAt": None,
        "closedAt": None,
        "additions": 1,
        "deletions": 0,
        "changedFiles": 1,
        "mergeCommit": None,
        "labels": {"nodes": []},
        "author": {"login": "octocat"},
        "mergedBy": None,
    }


def _pr_list_page_from_nodes(nodes, *, has_next=False, end_cursor=None):
    return {
        "data": {
            "repository": {
                "pullRequests": {
                    "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
                    "nodes": nodes,
                }
            },
            "rateLimit": _rate_limit_block(),
        }
    }


def _pr_list_page(number, updated_at, *, has_next=False, end_cursor=None):
    return _pr_list_page_from_nodes([_pr_node(number, updated_at)], has_next=has_next, end_cursor=end_cursor)


def _empty_nested_page(container_key):
    return {
        "data": {
            "node": {container_key: {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}},
            "rateLimit": _rate_limit_block(),
        }
    }


def _commits_page(shas):
    nodes = [
        {
            "commit": {
                "oid": sha,
                "message": f"commit {sha}",
                "authoredDate": "2026-01-01T09:00:00Z",
                "committedDate": "2026-01-01T09:00:00Z",
                "additions": 1,
                "deletions": 0,
                "author": {"user": {"login": "octocat"}, "email": "octocat@example.com"},
                "committer": {"user": {"login": "octocat"}, "email": "octocat@example.com"},
                "statusCheckRollup": {"state": "SUCCESS"},
            }
        }
        for sha in shas
    ]
    return {
        "data": {
            "node": {"commits": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}},
            "rateLimit": _rate_limit_block(),
        }
    }


def _reviews_page():
    return {
        "data": {
            "node": {
                "reviews": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {
                            "id": "PRR_1",
                            "author": {"login": "reviewer-1"},
                            "state": "APPROVED",
                            "submittedAt": "2026-01-01T10:00:00Z",
                            "body": "ok",
                            "comments": {"totalCount": 0},
                        }
                    ],
                }
            },
            "rateLimit": _rate_limit_block(),
        }
    }


def _files_page():
    return {
        "data": {
            "node": {
                "files": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [{"path": "a.py", "additions": 1, "deletions": 0, "changeType": "MODIFIED"}],
                }
            },
            "rateLimit": _rate_limit_block(),
        }
    }


_FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "github"


def _tooling_page(name="repository_tree_no_tooling"):
    """`sync_repository` probes the repository's tree before its pull requests (phase 12, stage
    3), so every ordered response sequence starts with one of these. The default fixture is a
    repository with no agent tooling at all, which costs exactly one request: nothing in it is a
    directory any configured glob names, so no second-level probe follows."""
    return json.loads((_FIXTURES_DIR / f"{name}.json").read_text())


def _one_pr_sequence(number=1, updated_at="2026-01-05T09:00:00Z", sha="sha0001"):
    """The tooling probe, then PULL_REQUESTS_QUERY, then commits/reviews/threads/files/timeline
    for the single PR."""
    return [
        _tooling_page(),
        _pr_list_page(number, updated_at),
        _commits_page([sha]),
        _reviews_page(),
        _empty_nested_page("reviewThreads"),
        _files_page(),
        _empty_nested_page("timelineItems"),
    ]


def _make_repository(**kwargs):
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    return RepositoryFactory(connection=connection, **kwargs)


@pytest.mark.django_db
def test_fixture_sync_creates_expected_rows_and_succeeds():
    repository = _make_repository(full_name="acme/widget")
    mock_graphql_sequence(*_one_pr_sequence())

    run = run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    run.refresh_from_db()
    assert run.status == SyncRun.Status.SUCCESS
    assert run.stats == {
        "repositories": 1,
        "pull_requests": 1,
        "commits": 1,
        "reviews": 1,
        "review_comments": 0,
        "files": 1,
        "check_statuses": 1,
        "errors": 0,
    }
    assert PullRequest.objects.count() == 1
    assert Commit.objects.count() == 1
    assert Review.objects.count() == 1
    assert PRFile.objects.count() == 1
    assert CheckStatus.objects.count() == 1
    repository.refresh_from_db()
    assert repository.last_synced_at == run.started_at


@pytest.mark.django_db(transaction=True)
def test_successful_sync_leaves_rollup_rows_for_the_synced_day_and_bumps_the_data_version():
    """transaction=True: the post-processing hook (mark_dirty included) only fires on a real
    commit, same requirement as apps/github_sync/tests/test_pipeline.py."""
    repository = _make_repository(full_name="acme/widget")
    mock_graphql_sequence(*_one_pr_sequence())
    version_before = data_version()

    run = run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    run.refresh_from_db()
    assert run.status == SyncRun.Status.SUCCESS
    pull_request = PullRequest.objects.get(repository=repository)
    expected_day = day_of(pull_request.created_at)
    assert DailyRollup.objects.filter(date=expected_day, metric_key="prs_opened").exists()
    assert data_version() == version_before + 1


@pytest.mark.django_db
def test_second_sync_is_idempotent():
    _make_repository(full_name="acme/widget")
    sequence = _one_pr_sequence() + _one_pr_sequence()
    mock_graphql_sequence(*sequence)

    run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])
    pr_ids_before = set(PullRequest.objects.values_list("id", flat=True))
    commit_count_before = Commit.objects.count()

    run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"], full=True)

    assert set(PullRequest.objects.values_list("id", flat=True)) == pr_ids_before
    assert Commit.objects.count() == commit_count_before


@pytest.mark.django_db
def test_pr_schema_error_is_recorded_and_repository_continues():
    repository = _make_repository(full_name="acme/widget")
    malformed_commits_page = {
        "data": {
            "node": {"commits": {"pageInfo": {"hasNextPage": True}, "nodes": []}},
            "rateLimit": _rate_limit_block(),
        }
    }
    mock_graphql_sequence(_tooling_page(), _pr_list_page(1, "2026-01-05T09:00:00Z"), malformed_commits_page)

    run = run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    run.refresh_from_db()
    assert run.status == SyncRun.Status.PARTIAL
    assert run.stats["errors"] == 1
    assert "acme/widget#1" in run.error_log
    assert PullRequest.objects.count() == 0
    repository.refresh_from_db()
    # Must NOT advance to run.started_at: that would put the failed PR below the next
    # incremental watermark forever (.autodev/DECISIONS.md line 18's permanent-gap concern).
    assert repository.last_synced_at == datetime.datetime(2026, 1, 5, 9, 0, tzinfo=datetime.UTC)


@pytest.mark.django_db
def test_older_pr_failure_does_not_advance_watermark_past_it():
    repository = _make_repository(full_name="acme/widget")
    malformed_commits_page = {
        "data": {
            "node": {"commits": {"pageInfo": {"hasNextPage": True}, "nodes": []}},
            "rateLimit": _rate_limit_block(),
        }
    }
    first_run_sequence = [
        _tooling_page(),
        _pr_list_page_from_nodes([_pr_node(2, "2026-01-05T09:00:00Z"), _pr_node(1, "2026-01-04T09:00:00Z")]),
        _commits_page(["sha0002"]),
        _reviews_page(),
        _empty_nested_page("reviewThreads"),
        _files_page(),
        _empty_nested_page("timelineItems"),
        malformed_commits_page,
    ]
    second_run_sequence = _one_pr_sequence(number=1, updated_at="2026-01-04T09:00:00Z", sha="sha0001")
    mock_graphql_sequence(*(first_run_sequence + second_run_sequence))

    run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    repository.refresh_from_db()
    assert repository.last_synced_at == datetime.datetime(2026, 1, 4, 9, 0, tzinfo=datetime.UTC)
    assert PullRequest.objects.filter(number=1).exists() is False
    assert PullRequest.objects.filter(number=2).exists() is True

    run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    assert PullRequest.objects.filter(number=1).exists() is True


@pytest.mark.django_db
def test_repository_level_error_is_recorded_and_run_continues_with_other_repositories():
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    repo_a = RepositoryFactory(connection=connection, full_name="acme/broken")
    repo_b = RepositoryFactory(connection=connection, full_name="acme/widget")

    responses = [httpx.Response(502, json={"message": "bad gateway"}) for _ in range(6)]
    responses += [httpx.Response(200, json=body) for body in _one_pr_sequence()]
    mock_graphql_responses(*responses)

    run = run_sync(
        SyncRun.Trigger.CLI,
        repo_full_names=["acme/broken", "acme/widget"],
        sleep=lambda seconds: None,
    )

    run.refresh_from_db()
    assert run.status == SyncRun.Status.PARTIAL
    assert run.stats["errors"] == 1
    assert repo_a.full_name in run.error_log
    assert run.stats["repositories"] == 1
    repo_a.refresh_from_db()
    repo_b.refresh_from_db()
    assert repo_a.last_synced_at is None
    assert repo_b.last_synced_at == run.started_at


@pytest.mark.django_db
def test_second_concurrent_run_raises_sync_already_running():
    run = SyncRun.objects.create(trigger=SyncRun.Trigger.CLI, status=SyncRun.Status.RUNNING)
    SyncLock.objects.create(name="global", sync_run=run)

    with pytest.raises(SyncAlreadyRunning):
        run_sync(SyncRun.Trigger.CLI, repo_full_names=[])

    # The contending run must not leave a second, permanently "running" SyncRun row behind —
    # that row would have no lock to ever release it, and the UI polls forever for it.
    assert SyncRun.objects.filter(status=SyncRun.Status.RUNNING).count() == 1
    assert SyncRun.objects.count() == 1


@pytest.mark.django_db
def test_stale_lock_is_stolen():
    stale_run = SyncRun.objects.create(trigger=SyncRun.Trigger.CLI, status=SyncRun.Status.RUNNING)
    stale_at = timezone.now() - datetime.timedelta(hours=7)
    SyncLock.objects.create(name="global", sync_run=stale_run, acquired_at=stale_at)

    run = run_sync(SyncRun.Trigger.CLI, repo_full_names=[])

    run.refresh_from_db()
    stale_run.refresh_from_db()
    assert run.status == SyncRun.Status.SUCCESS
    assert not SyncLock.objects.filter(sync_run=stale_run).exists()
    # The killed run's own SyncRun must not be left at status=running forever: that would poll
    # the Sync page every 2s and disable "Sync now" with no in-app recovery.
    assert stale_run.status == SyncRun.Status.FAILED
    assert stale_run.finished_at is not None
    assert SyncRun.objects.filter(status=SyncRun.Status.RUNNING).count() == 0


@pytest.mark.django_db
def test_lock_is_released_after_a_crashing_run(monkeypatch):
    import apps.github_sync.services as services_module

    def _boom(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(services_module, "_select_repositories", _boom)
    version_before = data_version()

    with pytest.raises(RuntimeError):
        run_sync(SyncRun.Trigger.CLI, repo_full_names=[])

    # A failed sync must still bump the version: any dirty days a partial run's committed PRs
    # left behind are rebuilt on the failure path too, so compute()'s cache must not keep serving
    # a pre-run answer for them.
    assert data_version() == version_before + 1
    assert not SyncLock.objects.filter(name="global").exists()
    # The crashed run's own SyncRun must get a terminal status too, or the Sync page polls it
    # forever and "Sync now" stays disabled with no recovery.
    assert SyncRun.objects.filter(status=SyncRun.Status.RUNNING).count() == 0
    crashed_run = SyncRun.objects.get(trigger=SyncRun.Trigger.CLI)
    assert crashed_run.status == SyncRun.Status.FAILED
    assert crashed_run.finished_at is not None
    assert "boom" in crashed_run.error_log


@pytest.mark.django_db
def test_a_failing_post_sync_rebuild_does_not_mask_the_original_sync_exception(monkeypatch):
    """The failure-path `rebuild_dirty()`/`bump_data_version()` calls are best-effort: the
    `SyncRun` row already carries a terminal status by the time they run, so if the rebuild itself
    raises (e.g. a concurrent `DirtyDay` write), the caller must still see the sync's own
    exception, not the rebuild's."""
    import apps.github_sync.services as services_module

    def _boom(**kwargs):
        raise RuntimeError("original sync failure")

    def _rebuild_boom():
        raise ValueError("rebuild also failed")

    monkeypatch.setattr(services_module, "_select_repositories", _boom)
    monkeypatch.setattr(services_module, "rebuild_dirty", _rebuild_boom)

    with pytest.raises(RuntimeError, match="original sync failure"):
        run_sync(SyncRun.Trigger.CLI, repo_full_names=[])

    crashed_run = SyncRun.objects.get(trigger=SyncRun.Trigger.CLI)
    assert crashed_run.status == SyncRun.Status.FAILED
    assert "original sync failure" in crashed_run.error_log


@pytest.mark.django_db
def test_no_repositories_matched_still_succeeds():
    run = run_sync(SyncRun.Trigger.CLI, repo_full_names=["nobody/nothing"])
    run.refresh_from_db()
    assert run.status == SyncRun.Status.SUCCESS
    assert run.stats["repositories"] == 0


@pytest.mark.django_db
def test_watermark_uses_overlap_and_never_precedes_sync_since():
    repository = RepositoryFactory(
        sync_since=datetime.date(2026, 1, 1),
        last_synced_at=datetime.datetime(2026, 1, 10, 12, 0, tzinfo=datetime.UTC),
    )
    watermark = _watermark(repository, since=None, full=False)
    assert watermark == datetime.datetime(2026, 1, 10, 11, 0, tzinfo=datetime.UTC)


@pytest.mark.django_db
def test_watermark_clamped_to_sync_since_when_overlap_would_precede_it():
    repository = RepositoryFactory(
        sync_since=datetime.date(2026, 1, 5),
        last_synced_at=datetime.datetime(2026, 1, 5, 0, 30, tzinfo=datetime.UTC),
    )
    watermark = _watermark(repository, since=None, full=False)
    assert watermark == datetime.datetime(2026, 1, 5, tzinfo=datetime.UTC)


@pytest.mark.django_db
def test_watermark_full_starts_from_sync_since():
    repository = RepositoryFactory(
        sync_since=datetime.date(2026, 1, 1),
        last_synced_at=datetime.datetime(2026, 1, 10, tzinfo=datetime.UTC),
    )
    assert _watermark(repository, since=None, full=True) == datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)


@pytest.mark.django_db
def test_watermark_since_overrides_everything():
    repository = RepositoryFactory(sync_since=datetime.date(2026, 1, 1))
    explicit = datetime.datetime(2026, 2, 1, tzinfo=datetime.UTC)
    assert _watermark(repository, since=explicit, full=False) == explicit


# -- phase 12, stage 7: thread resolution and the review-requested event --------------------------


@pytest.mark.django_db
def test_sync_stores_thread_resolution_and_the_review_request_time(github_fixture):
    """Both fields reach the database through the real sync path, and a thread whose resolution the
    server did not report stays `None` — "unknown", never "unresolved"."""
    _make_repository(full_name="acme/widget")
    sequence = [
        _tooling_page(),
        _pr_list_page(1, "2026-03-05T09:00:00Z"),
        _commits_page(["sha0001"]),
        _empty_nested_page("reviews"),
        github_fixture("pr_review_threads_resolution"),
        _files_page(),
        github_fixture("pr_timeline_review_requested"),
    ]
    mock_graphql_sequence(*sequence)

    run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    pull_request = PullRequest.objects.get(number=1)
    assert pull_request.review_requested_at == datetime.datetime(2026, 3, 1, 8, 30, tzinfo=datetime.UTC)
    resolutions = {comment.github_id: comment.is_resolved for comment in pull_request.review_comments.all()}
    assert resolutions == {
        "PRRC_resolved": True,
        "PRRC_unresolved": False,
        "PRRC_unknown": None,
    }
