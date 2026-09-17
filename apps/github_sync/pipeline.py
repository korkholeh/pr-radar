"""The post-processing hook the sync orchestrator calls once per synced PR, after its writing
transaction commits (spec §5.3 step 4). Phase 4 fills the first two stages: identity resolution,
then derived-field computation. AI detection (phase 5), policy evaluation (phase 6) and dirty-day
rollups (phase 7) each add a further stage here without reopening the orchestrator or its
once-per-PR contract."""

import logging

from apps.activity.derive import derive_pull_request
from apps.catalog.identity import resolve_identities_for_pull_request

logger = logging.getLogger(__name__)


def process_pull_request(pull_request_id: int) -> None:
    logger.debug("process_pull_request: pull_request_id=%s", pull_request_id)
    resolve_identities_for_pull_request(pull_request_id)
    derive_pull_request(pull_request_id)
