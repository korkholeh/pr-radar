"""Nightly author-baseline recomputation (ADR 0005: every huey task is also a management command
-- `manage.py compute_baselines`).

Runs at 01:00, before `compute_churn_task` at 02:00 and `cleanup_exports_task` at 03:00. It has
to be a scheduled job rather than part of the sync: a baseline verdict is about an author's whole
recent history, so it changes when *other* pull requests arrive, and a verdict computed while
syncing one pull request would be stale the moment the next one lands.
"""

import logging

from huey import crontab
from huey.contrib.djhuey import db_periodic_task

from apps.ai_detection.services import run_baselines

logger = logging.getLogger(__name__)


@db_periodic_task(crontab(hour="1", minute="0"))
def compute_baselines_task() -> None:
    result = run_baselines()
    logger.info(
        "Baseline signals: %s author(s), %s created, %s deleted, %s pull request(s) restatused.",
        result.authors,
        result.created,
        result.deleted,
        result.pull_requests_restatused,
    )
