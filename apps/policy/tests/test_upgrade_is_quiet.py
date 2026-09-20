"""Upgrading to phase 12 stage 7 must raise no violation until a lead asks for one.

This is the acceptance criterion the whole stage rests on. Fifteen new checks arrived at once; if a
single one of them defaulted to on, an existing installation would open its console on hundreds of
violations nobody chose to be measured by, and the reasonable response to that is to stop reading
the console.

The pull request below is deliberately the worst case for every new rule at the same time: merged
with failing checks, a skip-ci commit, a deleted test, a committed `.env`, a changed `CLAUDE.md`, a
migration, a dependency manifest, an empty body, a bot-only approval, an unresolved AI thread and a
rubber-stamp approval. With default settings it must produce nothing at all.
"""

from __future__ import annotations

import datetime

import pytest

from apps.activity.factories import (
    CheckStatusFactory,
    CommitFactory,
    PRFileFactory,
    PullRequestCommitFactory,
    PullRequestFactory,
    ReviewCommentFactory,
    ReviewFactory,
)
from apps.activity.models import AIStatus, CheckStatus, PullRequest, Review
from apps.catalog.factories import IdentityFactory, PersonFactory
from apps.policy.factories import AIPolicyFactory
from apps.policy.models import PolicyViolation
from apps.policy.services import evaluate_pull_request

pytestmark = pytest.mark.django_db

RuleCode = PolicyViolation.RuleCode
CREATED_AT = datetime.datetime(2026, 8, 1, 9, 0, tzinfo=datetime.UTC)
MERGED_AT = datetime.datetime(2026, 8, 2, 9, 0, tzinfo=datetime.UTC)


@pytest.fixture
def awful_pull_request():
    """Every new check's condition, on one merged pull request."""
    bot_person = PersonFactory(display_name="copilot", is_bot=True)
    bot_identity = IdentityFactory(value="copilot", person=bot_person)

    pull_request = PullRequestFactory(
        state=PullRequest.State.MERGED,
        created_at=CREATED_AT,
        merged_at=MERGED_AT,
        ai_status=AIStatus.AI_EXPLICIT,
        body="",
        is_rubber_stamp=True,
        effective_additions=900,
        effective_deletions=100,
    )
    commit = CommitFactory(repository=pull_request.repository, message="Ship it [skip ci]")
    PullRequestCommitFactory(pull_request=pull_request, commit=commit, position=0)
    CheckStatusFactory(
        pull_request=pull_request,
        commit_sha=commit.sha,
        rollup_state=CheckStatus.RollupState.FAILURE,
    )
    for path, kwargs in {
        "tests/test_thing.py": {"is_test": True, "status": "removed"},
        "apps/thing/service.py": {},
        "apps/thing/migrations/0002_x.py": {"is_excluded": True},
        "pyproject.toml": {},
        "CLAUDE.md": {},
        ".env": {},
    }.items():
        PRFileFactory(pull_request=pull_request, path=path, **kwargs)
    ReviewFactory(
        pull_request=pull_request, reviewer=bot_identity, state=Review.State.APPROVED, body_length=0
    )
    ReviewCommentFactory(
        pull_request=pull_request, author=bot_identity, is_review_thread=True, is_resolved=False
    )
    return pull_request


def test_default_policy_raises_no_violation_on_the_worst_possible_pull_request(awful_pull_request):
    AIPolicyFactory(effective_from=CREATED_AT - datetime.timedelta(days=1))

    evaluate_pull_request(awful_pull_request.pk)

    assert PolicyViolation.objects.count() == 0


def test_turning_one_toggle_on_raises_exactly_that_one_violation(awful_pull_request):
    """And nothing else moves: a lead switching on the quality-gate check gets the quality-gate
    check, not a console full of things they did not ask about."""
    AIPolicyFactory(
        effective_from=CREATED_AT - datetime.timedelta(days=1),
        forbid_ci_bypass=True,
    )

    evaluate_pull_request(awful_pull_request.pk)

    assert set(PolicyViolation.objects.values_list("rule_code", flat=True)) == {
        RuleCode.QUALITY_GATE_BYPASSED
    }


def test_a_second_evaluation_creates_no_second_row(awful_pull_request):
    AIPolicyFactory(
        effective_from=CREATED_AT - datetime.timedelta(days=1),
        forbid_ci_bypass=True,
        forbid_test_weakening=True,
        forbid_secret_artifacts=True,
        flag_agent_config_changes=True,
        require_risk_level=True,
        require_task_link=True,
        require_verification_note=True,
    )

    evaluate_pull_request(awful_pull_request.pk)
    first = set(PolicyViolation.objects.values_list("pk", flat=True))
    evaluate_pull_request(awful_pull_request.pk)

    assert set(PolicyViolation.objects.values_list("pk", flat=True)) == first
    assert first


def test_switching_a_toggle_back_off_auto_resolves_its_violation(awful_pull_request):
    policy = AIPolicyFactory(
        effective_from=CREATED_AT - datetime.timedelta(days=1), forbid_secret_artifacts=True
    )
    evaluate_pull_request(awful_pull_request.pk)
    violation = PolicyViolation.objects.get(rule_code=RuleCode.SECRET_ARTIFACT_COMMITTED)
    assert violation.status == PolicyViolation.Status.OPEN

    policy.forbid_secret_artifacts = False
    policy.save(update_fields=["forbid_secret_artifacts"])
    evaluate_pull_request(awful_pull_request.pk)

    violation.refresh_from_db()
    assert violation.status == PolicyViolation.Status.RESOLVED
    assert violation.resolved_automatically is True


def test_no_violation_stores_rendered_text(awful_pull_request):
    """CLAUDE.md's rule, re-asserted for the new codes: a violation stores a code plus parameters,
    and the sentence is built in the reader's language at read time."""
    AIPolicyFactory(
        effective_from=CREATED_AT - datetime.timedelta(days=1),
        forbid_ci_bypass=True,
        forbid_test_weakening=True,
        forbid_secret_artifacts=True,
        flag_agent_config_changes=True,
        require_risk_level=True,
    )

    evaluate_pull_request(awful_pull_request.pk)

    assert PolicyViolation.objects.exists()
    for violation in PolicyViolation.objects.all():
        for key, value in violation.details_params.items():
            if isinstance(value, str):
                # Codes, paths and levels are fine; a sentence is not. Nothing stored may contain a
                # space-separated phrase ending in a full stop.
                assert not value.endswith("."), (violation.rule_code, key, value)
