"""The post-processing hook the sync orchestrator calls once per synced PR, after its writing
transaction commits (spec §5.3 step 4). Ships empty in this phase: AI detection (phase 5), policy
evaluation (phase 6) and dirty-day rollups (phase 7) each add a stage here without reopening the
orchestrator or its once-per-PR contract."""

import logging

logger = logging.getLogger(__name__)


def process_pull_request(pull_request_id: int) -> None:
    logger.debug("process_pull_request: pull_request_id=%s", pull_request_id)
