"""The huey task is a thin wrapper over run_sync (ADR 0005: every huey task is also a
management command — manage.py sync and this task both call apps.github_sync.services.run_sync)."""

import logging

from huey.contrib.djhuey import db_task

from apps.github_sync.models import SyncRun
from apps.github_sync.services import SyncAlreadyRunning, run_sync

logger = logging.getLogger(__name__)


@db_task()
def sync_task() -> None:
    try:
        run_sync(SyncRun.Trigger.UI)
    except SyncAlreadyRunning:
        logger.info("Sync already running; skipped this enqueued task.")
