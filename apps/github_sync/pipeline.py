"""The post-processing hook the sync orchestrator calls once per synced PR, after its writing
transaction commits (spec §5.3 step 4). Phase 4 fills the first two stages: identity resolution,
then derived-field computation. AI detection (phase 5) and policy evaluation (phase 6) add the
third and fourth; dirty-day rollups (phase 7) add a further stage here without reopening the
orchestrator or its once-per-PR contract."""

import logging

from apps.activity.derive import derive_pull_request
from apps.activity.models import PullRequest
from apps.ai_detection.services import detect_pull_request
from apps.catalog.identity import resolve_identities_for_pull_request
from apps.metrics.services import mark_dirty
from apps.policy.services import evaluate_pull_request

logger = logging.getLogger(__name__)


def process_pull_request(pull_request_id: int) -> None:
    logger.debug("process_pull_request: pull_request_id=%s", pull_request_id)
    previous_reverts_pr_id = (
        PullRequest.objects.filter(id=pull_request_id).values_list("reverts_pr_id", flat=True).first()
    )
    resolve_identities_for_pull_request(pull_request_id)
    derive_pull_request(pull_request_id)
    detect_pull_request(pull_request_id)
    evaluate_pull_request(pull_request_id)
    mark_dirty(pull_request_id, previous_reverts_pr_id=previous_reverts_pr_id)
