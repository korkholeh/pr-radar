"""The sync orchestrator (spec §5.3): one connection-grouped pass over active repositories, one
transaction per PR, per-connection rate budgets, and a global lock so only one run is ever in
flight. Connections and sync runs are read here rather than through a selectors.py — they are
global admin-scoped configuration, not per-project data (CLAUDE.md's scope_for_user() rule stays
absolute for the tables phase 8 actually scopes)."""

import datetime
import logging
import time
from collections import deque
from collections.abc import Callable, Iterable
from functools import partial
from itertools import groupby
from typing import Any

from cryptography.fernet import InvalidToken
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.catalog.models import Repository
from apps.catalog.services import get_int
from apps.connections.auth import ConnectionNotUsableError, auth_for_connection
from apps.connections.models import GitHubConnection
from apps.github_sync.client import GitHubClient
from apps.github_sync.errors import GitHubAuthError, GitHubError, GitHubSSOError, require
from apps.github_sync.models import SyncLock, SyncRun
from apps.github_sync.pipeline import process_pull_request
from apps.github_sync.queries import (
    PR_COMMITS_QUERY,
    PR_FILES_QUERY,
    PR_REVIEW_THREADS_QUERY,
    PR_REVIEWS_QUERY,
    PR_TIMELINE_QUERY,
    PULL_REQUESTS_QUERY,
)
from apps.github_sync.rate_limit import RateBudgetRegistry
from apps.github_sync.upserts import upsert_pull_request
from apps.metrics.rollups import rebuild_dirty
from apps.metrics.services import bump_data_version
from config.security import mask_secrets

logger = logging.getLogger(__name__)

LOCK_NAME = "global"

_EMPTY_STATS = {
    "repositories": 0,
    "pull_requests": 0,
    "commits": 0,
    "reviews": 0,
    "review_comments": 0,
    "files": 0,
    "check_statuses": 0,
    "errors": 0,
}


class SyncAlreadyRunning(Exception):
    pass


def _parse_dt(value: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _steal_stale_locks(stale_before: datetime.datetime) -> None:
    """A lock older than SYNC_LOCK_STALE_MINUTES belonged to a killed process: deleting the
    lock row alone would free the next run while leaving the killed run's SyncRun stuck at
    status=running forever (the Sync page polls it and disables its own button indefinitely)."""
    stale_locks = list(SyncLock.objects.filter(name=LOCK_NAME, acquired_at__lt=stale_before))
    if not stale_locks:
        return
    stale_run_ids = [lock.sync_run_id for lock in stale_locks]
    SyncRun.objects.filter(id__in=stale_run_ids, status=SyncRun.Status.RUNNING).update(
        status=SyncRun.Status.FAILED,
        finished_at=timezone.now(),
    )
    SyncLock.objects.filter(id__in=[lock.id for lock in stale_locks]).delete()


def _acquire_lock(run: SyncRun) -> SyncLock:
    stale_before = timezone.now() - datetime.timedelta(minutes=get_int("SYNC_LOCK_STALE_MINUTES"))
    _steal_stale_locks(stale_before)
    try:
        # A savepoint: SQLite (and any DB under an outer transaction, e.g. tests) marks the
        # enclosing transaction unusable after an IntegrityError unless the failing statement
        # was inside its own atomic() block, which rolls back to the savepoint on exit.
        with transaction.atomic():
            return SyncLock.objects.create(name=LOCK_NAME, sync_run=run)
    except IntegrityError as exc:
        raise SyncAlreadyRunning("A sync is already running.") from exc


def _release_lock(run: SyncRun) -> None:
    SyncLock.objects.filter(name=LOCK_NAME, sync_run=run).delete()


def _select_repositories(
    *, repo_full_names: Iterable[str] | None, project_slug: str | None
) -> list[Repository]:
    queryset = Repository.objects.filter(is_active=True).select_related("connection", "organization")
    if repo_full_names:
        queryset = queryset.filter(full_name__in=list(repo_full_names))
    if project_slug:
        queryset = queryset.filter(projects__slug=project_slug)
    return list(queryset.order_by("connection_id", "id"))


def _watermark(
    repository: Repository, *, since: datetime.datetime | None, full: bool
) -> datetime.datetime | None:
    sync_since_dt = (
        datetime.datetime.combine(repository.sync_since, datetime.time.min, tzinfo=datetime.UTC)
        if repository.sync_since
        else None
    )
    if full:
        watermark = sync_since_dt
    else:
        overlap = datetime.timedelta(minutes=get_int("SYNC_OVERLAP_MINUTES"))
        watermark = repository.last_synced_at - overlap if repository.last_synced_at else None
        if sync_since_dt is not None and (watermark is None or watermark < sync_since_dt):
            watermark = sync_since_dt
    if since is not None:
        watermark = since
    return watermark


def _run_hook(
    pull_request_id: int, repo_full_name: str, error_lines: list[str], stats: dict[str, int]
) -> None:
    """Runs after the PR's writing transaction commits (spec §5.3 step 4). A raising hook must not
    undo the already-committed rows or abort the run; it is recorded like any other sync error."""
    try:
        process_pull_request(pull_request_id)
    except Exception as exc:
        logger.exception("process_pull_request hook failed for pull request %s", pull_request_id)
        error_lines.append(mask_secrets(f"{repo_full_name}: post-processing hook failed: {exc}"))
        stats["errors"] += 1


def sync_repository(
    client: GitHubClient,
    repository: Repository,
    run: SyncRun,
    stats: dict[str, int],
    error_lines: list[str],
    *,
    since: datetime.datetime | None = None,
    full: bool = False,
) -> None:
    watermark = _watermark(repository, since=since, full=full)
    owner, name = repository.full_name.split("/", 1)
    page_size = get_int("SYNC_PR_PAGE_SIZE")
    nested_page_size = get_int("SYNC_NESTED_PAGE_SIZE")
    earliest_failed_updated_at: datetime.datetime | None = None

    for pr_node in client.paginate(
        PULL_REQUESTS_QUERY,
        {"owner": owner, "name": name},
        page_path="repository.pullRequests",
        page_size=page_size,
    ):
        updated_at = _parse_dt(require(pr_node, "updatedAt"))
        if watermark is not None and updated_at < watermark:
            break

        pr_id = require(pr_node, "id")
        try:
            commit_nodes = list(
                client.paginate(
                    PR_COMMITS_QUERY, {"id": pr_id}, page_path="node.commits", page_size=nested_page_size
                )
            )
            review_nodes = list(
                client.paginate(
                    PR_REVIEWS_QUERY, {"id": pr_id}, page_path="node.reviews", page_size=nested_page_size
                )
            )
            thread_nodes = list(
                client.paginate(
                    PR_REVIEW_THREADS_QUERY,
                    {"id": pr_id},
                    page_path="node.reviewThreads",
                    page_size=nested_page_size,
                )
            )
            review_thread_comment_nodes = [
                thread["comments"]["nodes"][0]
                for thread in thread_nodes
                if thread.get("comments", {}).get("nodes")
            ]
            file_nodes = list(
                client.paginate(
                    PR_FILES_QUERY, {"id": pr_id}, page_path="node.files", page_size=nested_page_size
                )
            )
            timeline_nodes = list(
                client.paginate(
                    PR_TIMELINE_QUERY,
                    {"id": pr_id},
                    page_path="node.timelineItems",
                    page_size=nested_page_size,
                )
            )

            with transaction.atomic():
                pull_request = upsert_pull_request(
                    repository,
                    pr_node=pr_node,
                    commit_nodes=commit_nodes,
                    review_nodes=review_nodes,
                    review_thread_comment_nodes=review_thread_comment_nodes,
                    file_nodes=file_nodes,
                    timeline_nodes=timeline_nodes,
                )
                transaction.on_commit(
                    partial(_run_hook, pull_request.pk, repository.full_name, error_lines, stats)
                )
        except (GitHubAuthError, GitHubSSOError):
            raise
        except GitHubError as exc:
            error_lines.append(mask_secrets(f"{repository.full_name}#{require(pr_node, 'number')}: {exc}"))
            stats["errors"] += 1
            if earliest_failed_updated_at is None or updated_at < earliest_failed_updated_at:
                earliest_failed_updated_at = updated_at
            continue

        stats["pull_requests"] += 1
        stats["commits"] += len(commit_nodes)
        stats["reviews"] += len(review_nodes)
        stats["review_comments"] += len(review_thread_comment_nodes)
        stats["files"] += len(file_nodes)
        stats["check_statuses"] += pull_request.check_statuses.count()

    run.repositories.add(repository)
    if earliest_failed_updated_at is not None:
        # A PR failed to sync: stop the watermark just short of it (rather than run.started_at)
        # so the next incremental sync re-fetches it instead of skipping it forever (a page
        # below the new watermark is never revisited — .autodev/DECISIONS.md line 18).
        if repository.last_synced_at is None or earliest_failed_updated_at < repository.last_synced_at:
            repository.last_synced_at = earliest_failed_updated_at
            repository.save(update_fields=["last_synced_at"])
    else:
        repository.last_synced_at = run.started_at
        repository.save(update_fields=["last_synced_at"])
    stats["repositories"] += 1


def run_sync(
    trigger: str,
    *,
    repo_full_names: Iterable[str] | None = None,
    project_slug: str | None = None,
    since: datetime.datetime | None = None,
    full: bool = False,
    actor: Any | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SyncRun:
    run = SyncRun.objects.create(trigger=trigger, status=SyncRun.Status.RUNNING)
    try:
        _acquire_lock(run)
    except SyncAlreadyRunning:
        run.delete()
        raise

    try:
        repositories = _select_repositories(repo_full_names=repo_full_names, project_slug=project_slug)
        stats = dict(_EMPTY_STATS)
        stats_by_connection: dict[str, dict[str, Any]] = {}
        error_lines: list[str] = []
        budgets = RateBudgetRegistry()
        any_repository_completed = False
        min_remaining = get_int("RATE_LIMIT_MIN_REMAINING")

        connections: dict[int, GitHubConnection] = {}
        queues: dict[int, deque[Repository]] = {}
        clients: dict[int, GitHubClient] = {}
        order: list[int] = []

        for connection, repos_iter in groupby(repositories, key=lambda repo: repo.connection):
            repos = list(repos_iter)
            conn_stats = stats_by_connection.setdefault(
                str(connection.id),
                {
                    "name": connection.name,
                    "repositories": 0,
                    "skipped": 0,
                    "errors": 0,
                    "rate_limit_remaining": None,
                    "rate_limit_reset_at": None,
                    "rate_limit_waits": 0,
                    "status": connection.status,
                },
            )
            if not connection.is_active or connection.status == GitHubConnection.Status.INVALID:
                conn_stats["skipped"] += len(repos)
                continue

            try:
                auth = auth_for_connection(connection)
            except (ConnectionNotUsableError, InvalidToken, ImproperlyConfigured) as exc:
                conn_stats["skipped"] += len(repos)
                conn_stats["errors"] += 1
                stats["errors"] += 1
                error_lines.append(mask_secrets(f"{connection.name}: credentials unusable: {exc}"))
                continue

            budget = budgets.get(auth.rate_limit_key)
            connections[connection.id] = connection
            queues[connection.id] = deque(repos)
            clients[connection.id] = GitHubClient(auth, budget, sleep=sleep)
            order.append(connection.id)

        def process_one(conn_id: int) -> None:
            connection = connections[conn_id]
            queue = queues[conn_id]
            conn_stats = stats_by_connection[str(conn_id)]
            repo = queue.popleft()
            nonlocal any_repository_completed
            try:
                sync_repository(clients[conn_id], repo, run, stats, error_lines, since=since, full=full)
            except GitHubAuthError as exc:
                connection.status = GitHubConnection.Status.INVALID
                connection.save(update_fields=["status"])
                conn_stats["status"] = connection.status
                error_lines.append(mask_secrets(f"{repo.full_name}: authentication failed: {exc}"))
                stats["errors"] += 1
                conn_stats["errors"] += 1
                conn_stats["skipped"] += len(queue)
                queue.clear()
            except GitHubSSOError as exc:
                connection.status = GitHubConnection.Status.DEGRADED
                connection.save(update_fields=["status"])
                conn_stats["status"] = connection.status
                error_lines.append(mask_secrets(f"{repo.full_name}: SSO authorization required: {exc}"))
                stats["errors"] += 1
                conn_stats["errors"] += 1
                conn_stats["skipped"] += len(queue)
                queue.clear()
            except GitHubError as exc:
                error_lines.append(mask_secrets(f"{repo.full_name}: {exc}"))
                stats["errors"] += 1
                conn_stats["errors"] += 1
            except Exception as exc:
                logger.exception("Unexpected error syncing %s", repo.full_name)
                error_lines.append(mask_secrets(f"{repo.full_name}: unexpected error: {exc}"))
                stats["errors"] += 1
                conn_stats["errors"] += 1
            else:
                conn_stats["repositories"] += 1
                any_repository_completed = True

        # Round-robin across connections so a connection whose primary rate budget is low
        # yields to the others instead of blocking the run (ADR 0003 / PLAN.md §3): each round
        # tries every connection with remaining work once, skipping ones that would wait; only
        # when *every* remaining connection is waiting do we force one through (the client's own
        # budget wait then sleeps until reset).
        active = [conn_id for conn_id in order if queues[conn_id]]
        while active:
            made_progress = False
            waiting: list[int] = []
            for conn_id in active:
                budget = budgets.get(clients[conn_id].auth.rate_limit_key)
                if budget.should_wait(min_remaining):
                    waiting.append(conn_id)
                    continue
                made_progress = True
                process_one(conn_id)
            active = [conn_id for conn_id in active if queues[conn_id]]
            if not made_progress and active:
                for conn_id in waiting:
                    if conn_id in active:
                        stats_by_connection[str(conn_id)]["rate_limit_waits"] += 1
                forced = min(
                    active,
                    key=lambda conn_id: budgets.get(
                        clients[conn_id].auth.rate_limit_key
                    ).seconds_until_reset(),
                )
                process_one(forced)
                active = [conn_id for conn_id in active if queues[conn_id]]

        for conn_id in order:
            budget = budgets.get(clients[conn_id].auth.rate_limit_key)
            conn_stats = stats_by_connection[str(conn_id)]
            conn_stats["rate_limit_remaining"] = budget.remaining
            conn_stats["rate_limit_reset_at"] = budget.reset_at.isoformat() if budget.reset_at else None

        run.stats = stats
        run.stats_by_connection = stats_by_connection
        run.error_log = "\n".join(error_lines)
        if stats["errors"] == 0:
            run.status = SyncRun.Status.SUCCESS
        elif any_repository_completed:
            run.status = SyncRun.Status.PARTIAL
        else:
            run.status = SyncRun.Status.FAILED
        run.finished_at = timezone.now()
        run.save()
        rebuild_dirty()
        bump_data_version()
        return run
    except Exception as exc:
        # Anything that escapes the loop above (a bug, an unmasked exception type, a killed
        # process re-raising on the way out) must still leave a terminal status: a SyncRun stuck
        # at status=running polls the Sync page every 2s and disables "Sync now" forever.
        run.status = SyncRun.Status.FAILED
        run.finished_at = timezone.now()
        run.error_log = mask_secrets(f"{run.error_log}\n{exc}" if run.error_log else str(exc))
        run.save()
        # A partial sync (PRs already committed before the crash) still marked days dirty via
        # process_pull_request -> mark_dirty; rebuild them so those PRs are not left stale in
        # dashboards until the next successful sync happens to touch the same days. Best-effort:
        # the SyncRun row above already carries the terminal status, so a failure here must not
        # replace the original sync exception on its way out.
        try:
            rebuild_dirty()
            bump_data_version()
        except Exception:
            logger.exception("Post-sync rollup rebuild failed after a sync failure")
        raise
    finally:
        _release_lock(run)
