"""The huey tasks are thin wrappers over run_sync (ADR 0005: every huey task is also a
management command — manage.py sync and these tasks all call apps.github_sync.services.run_sync).

`scheduled_sync_task` runs at the top of every hour. It shares the global lock with a UI or CLI sync, so an
hour whose run finds another sync in progress is skipped rather than queued behind it."""

import datetime
import logging
from collections.abc import Sequence

from huey import crontab
from huey.contrib.djhuey import db_periodic_task, db_task

from apps.github_sync.models import SyncRun
from apps.github_sync.services import SyncAlreadyRunning, run_sync

logger = logging.getLogger(__name__)


@db_task()
def sync_task() -> None:
    try:
        run_sync(SyncRun.Trigger.UI)
    except SyncAlreadyRunning:
        logger.info("Sync already running; skipped this enqueued task.")


@db_periodic_task(crontab(minute="0"))
def scheduled_sync_task() -> None:
    try:
        run_sync(SyncRun.Trigger.SCHEDULE)
    except SyncAlreadyRunning:
        logger.info("Sync already running; skipped this scheduled run.")


@db_task()
def backfill_task(since: datetime.datetime, repo_full_names: Sequence[str] | None = None) -> None:
    """A backfill is an ordinary sync with the watermark forced back to `since`, so it shares the
    global lock, the per-connection rate budgets and the rollup rebuild with every other run."""
    try:
        run_sync(
            SyncRun.Trigger.BACKFILL,
            repo_full_names=list(repo_full_names) if repo_full_names else None,
            since=since,
        )
    except SyncAlreadyRunning:
        logger.info("Sync already running; skipped this enqueued backfill.")
