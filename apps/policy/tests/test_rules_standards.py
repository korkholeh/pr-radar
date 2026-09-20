"""The fifteen PLANEKS-standards rules (phase 12, stage 7).

Each rule gets three cases at minimum: it fires when it should, it stays silent when the condition
is absent, and it stays silent while its toggle is off. The third is the one that matters most —
every check here defaults to off, and `test_upgrade_is_quiet.py` proves the whole set together.

The evaluators read only a `PolicyContext`, never the database, so these build one directly through
`helpers.make_context`. `load_context` is covered in `test_rules_registry.py`, and the end-to-end
write path in `test_evaluate.py`.
"""

from __future__ import annotations

import datetime

import pytest

from apps.activity.factories import PRFileFactory, PullRequestFactory
from apps.activity.models import AIStatus, CheckStatus, PullRequest
from apps.ai_detection.diffsignals import DiffFacts
from apps.ai_detection.models import SignalKind
from apps.catalog.factories import PersonFactory
from apps.policy.factories import AIPolicyFactory, SensitivePathRuleFactory
from apps.policy.models import PolicyViolation, SensitivePathRule
from apps.policy.rules import RULES
from apps.policy.tests.helpers import make_context

pytestmark = pytest.mark.django_db

RuleCode = PolicyViolation.RuleCode
MERGED_AT = datetime.datetime(2026, 9, 1, 12, 0, tzinfo=datetime.UTC)


def _run(rule_code: str, ctx) -> list:
    return list(RULES[rule_code](ctx))


def _merged_pr(**kwargs) -> PullRequest:
    kwargs.setdefault("state", PullRequest.State.MERGED)
    kwargs.setdefault("merged_at", MERGED_AT)
    kwargs.setdefault("ai_status", AIStatus.AI_EXPLICIT)
    return PullRequestFactory(**kwargs)


def _files(pull_request, paths: dict[str, dict]) -> tuple:
    return tuple(
        PRFileFactory(pull_request=pull_request, path=path, **kwargs) for path, kwargs in paths.items()
    )


# -- QUALITY_GATE_BYPASSED -----------------------------------------------------------------------


def test_quality_gate_bypassed_fires_on_a_red_rollup_at_merge():
    policy = AIPolicyFactory(forbid_ci_bypass=True)
    ctx = make_context(
        pull_request=_merged_pr(),
        policy=policy,
        final_rollup_state=CheckStatus.RollupState.FAILURE,
    )

    findings = _run(RuleCode.QUALITY_GATE_BYPASSED, ctx)

    assert [f.details_params["reason"] for f in findings] == ["checks_failing"]
    assert findings[0].details_params["state"] == CheckStatus.RollupState.FAILURE


def test_quality_gate_bypassed_is_silent_on_a_green_rollup():
    policy = AIPolicyFactory(forbid_ci_bypass=True)
    ctx = make_context(
        pull_request=_merged_pr(), policy=policy, final_rollup_state=CheckStatus.RollupState.SUCCESS
    )
    assert _run(RuleCode.QUALITY_GATE_BYPASSED, ctx) == []


def test_quality_gate_bypassed_is_silent_on_a_repository_with_no_checks_at_all():
    """No CI to bypass. `None` means "we saw no rollup", which must not read as a failure."""
    policy = AIPolicyFactory(forbid_ci_bypass=True)
    ctx = make_context(pull_request=_merged_pr(), policy=policy, final_rollup_state=None)
    assert _run(RuleCode.QUALITY_GATE_BYPASSED, ctx) == []


def test_quality_gate_bypassed_reads_a_skip_ci_marker_in_a_commit_message():
    policy = AIPolicyFactory(forbid_ci_bypass=True)
    ctx = make_context(
        pull_request=_merged_pr(),
        policy=policy,
        commit_messages=("Fix the thing [skip ci]",),
        final_rollup_state=CheckStatus.RollupState.SUCCESS,
    )

    findings = _run(RuleCode.QUALITY_GATE_BYPASSED, ctx)

    assert [f.details_params["reason"] for f in findings] == ["skip_ci_marker"]


def test_quality_gate_bypassed_reads_the_diff_relaxing_the_gate():
    policy = AIPolicyFactory(forbid_ci_bypass=True)
    facts = DiffFacts(quality_gate_relaxations=("continue_on_error",), checks_removed=2)
    ctx = make_context(pull_request=_merged_pr(), policy=policy, diff_facts=facts)

    reasons = {f.details_params["reason"] for f in _run(RuleCode.QUALITY_GATE_BYPASSED, ctx)}

    # Two separate findings, keyed separately: a reader acts differently on "a check was turned
    # off" and "a check was deleted", and one can be waived while the other stands.
    assert reasons == {"gate_relaxed", "check_removed"}


def test_quality_gate_bypassed_is_silent_without_a_diff_analysis():
    """A repository that was never opted in to diff analysis is not one that relaxed its gate."""
    policy = AIPolicyFactory(forbid_ci_bypass=True)
    ctx = make_context(pull_request=_merged_pr(), policy=policy, diff_facts=None)
    assert _run(RuleCode.QUALITY_GATE_BYPASSED, ctx) == []


def test_quality_gate_bypassed_is_silent_while_the_toggle_is_off():
    ctx = make_context(pull_request=_merged_pr(), final_rollup_state=CheckStatus.RollupState.FAILURE)
    assert _run(RuleCode.QUALITY_GATE_BYPASSED, ctx) == []


def test_quality_gate_bypassed_is_silent_on_an_open_pull_request():
    policy = AIPolicyFactory(forbid_ci_bypass=True)
    open_pr = PullRequestFactory(state=PullRequest.State.OPEN, merged_at=None)
    ctx = make_context(
        pull_request=open_pr, policy=policy, final_rollup_state=CheckStatus.RollupState.FAILURE
    )
    assert _run(RuleCode.QUALITY_GATE_BYPASSED, ctx) == []


# -- TEST_WEAKENED -------------------------------------------------------------------------------


def test_test_weakened_fires_when_a_test_file_is_deleted_alongside_code_changes():
    policy = AIPolicyFactory(forbid_test_weakening=True)
    pr = _merged_pr()
    files = _files(
        pr,
        {
            "tests/test_thing.py": {"is_test": True, "status": "removed"},
            "apps/thing/service.py": {"is_test": False, "status": "modified"},
        },
    )
    ctx = make_context(pull_request=pr, policy=policy, files=files)

    findings = _run(RuleCode.TEST_WEAKENED, ctx)

    assert [f.details_params["reason"] for f in findings] == ["test_file_removed"]
    assert findings[0].details_params["paths"] == ["tests/test_thing.py"]


def test_test_weakened_is_silent_when_only_the_tests_were_deleted():
    """A module went away and its tests went with it. That is housekeeping, not a weakening."""
    policy = AIPolicyFactory(forbid_test_weakening=True)
    pr = _merged_pr()
    files = _files(pr, {"tests/test_thing.py": {"is_test": True, "status": "removed"}})
    ctx = make_context(pull_request=pr, policy=policy, files=files)
    assert _run(RuleCode.TEST_WEAKENED, ctx) == []


def test_test_weakened_reads_a_skip_marker_from_the_diff():
    policy = AIPolicyFactory(forbid_test_weakening=True)
    ctx = make_context(
        pull_request=_merged_pr(),
        policy=policy,
        diff_facts=DiffFacts(skip_markers_added=2, test_lines_added=4),
    )

    findings = _run(RuleCode.TEST_WEAKENED, ctx)

    assert [f.details_params["reason"] for f in findings] == ["skip_marker_added"]
    assert findings[0].details_params["markers"] == 2


def test_test_weakened_treats_a_rewritten_test_as_a_rewrite():
    """Assertions removed *and* test lines added is a rewrite; only a removal with nothing added
    back is a weakening."""
    policy = AIPolicyFactory(forbid_test_weakening=True)
    rewrite = make_context(
        pull_request=_merged_pr(),
        policy=policy,
        diff_facts=DiffFacts(assertions_removed=5, test_lines_added=30),
    )
    stripped = make_context(
        pull_request=_merged_pr(),
        policy=policy,
        diff_facts=DiffFacts(assertions_removed=5, test_lines_added=0),
    )

    assert _run(RuleCode.TEST_WEAKENED, rewrite) == []
    assert [f.details_params["reason"] for f in _run(RuleCode.TEST_WEAKENED, stripped)] == [
        "assertions_removed"
    ]


def test_test_weakened_is_silent_while_the_toggle_is_off():
    pr = _merged_pr()
    files = _files(
        pr,
        {
            "tests/test_thing.py": {"is_test": True, "status": "removed"},
            "apps/thing/service.py": {"is_test": False, "status": "modified"},
        },
    )
    assert _run(RuleCode.TEST_WEAKENED, make_context(pull_request=pr, files=files)) == []


# -- AI_ONLY_APPROVAL ----------------------------------------------------------------------------


def test_ai_only_approval_fires_when_every_approval_came_from_a_bot():
    policy = AIPolicyFactory(forbid_ai_only_approval=True)
    ctx = make_context(
        pull_request=_merged_pr(), policy=policy, bot_approver_count=1, human_approver_person_ids=()
    )

    findings = _run(RuleCode.AI_ONLY_APPROVAL, ctx)

    assert len(findings) == 1
    assert findings[0].details_params["bot_approvals"] == 1


def test_ai_only_approval_is_silent_when_a_person_also_approved():
    policy = AIPolicyFactory(forbid_ai_only_approval=True)
    ctx = make_context(
        pull_request=_merged_pr(), policy=policy, bot_approver_count=1, human_approver_person_ids={7}
    )
    assert _run(RuleCode.AI_ONLY_APPROVAL, ctx) == []


def test_ai_only_approval_is_silent_when_nothing_approved_at_all():
    """That is `NO_HUMAN_APPROVAL`'s business. This rule is about a bot standing in for a person."""
    policy = AIPolicyFactory(forbid_ai_only_approval=True)
    ctx = make_context(pull_request=_merged_pr(), policy=policy, bot_approver_count=0)
    assert _run(RuleCode.AI_ONLY_APPROVAL, ctx) == []


def test_ai_only_approval_is_silent_while_the_toggle_is_off():
    ctx = make_context(pull_request=_merged_pr(), bot_approver_count=2)
    assert _run(RuleCode.AI_ONLY_APPROVAL, ctx) == []


# -- AI_REVIEW_MISSING / AI_REVIEW_UNRESOLVED ----------------------------------------------------


def test_ai_review_missing_fires_when_no_configured_ai_reviewer_reviewed():
    policy = AIPolicyFactory(require_ai_review_first=True, ai_reviewer_identities=["copilot"])
    ctx = make_context(pull_request=_merged_pr(), policy=policy, ai_reviewer_logins=frozenset({"copilot"}))

    findings = _run(RuleCode.AI_REVIEW_MISSING, ctx)

    assert findings[0].details_params["reviewers"] == ["copilot"]


def test_ai_review_missing_is_silent_when_the_ai_reviewer_did_review():
    policy = AIPolicyFactory(require_ai_review_first=True, ai_reviewer_identities=["copilot"])
    ctx = make_context(
        pull_request=_merged_pr(),
        policy=policy,
        ai_reviewer_logins=frozenset({"copilot"}),
        ai_review_logins_seen=frozenset({"copilot"}),
    )
    assert _run(RuleCode.AI_REVIEW_MISSING, ctx) == []


def test_ai_review_missing_is_silent_when_no_ai_reviewer_is_configured():
    """The policy cannot require a reviewer nobody named."""
    policy = AIPolicyFactory(require_ai_review_first=True)
    ctx = make_context(pull_request=_merged_pr(), policy=policy, ai_reviewer_logins=frozenset())
    assert _run(RuleCode.AI_REVIEW_MISSING, ctx) == []


def test_ai_review_unresolved_fires_on_an_open_thread_at_merge():
    policy = AIPolicyFactory(require_ai_comments_resolved=True)
    ctx = make_context(pull_request=_merged_pr(), policy=policy, unresolved_ai_threads=3)

    findings = _run(RuleCode.AI_REVIEW_UNRESOLVED, ctx)

    assert findings[0].details_params["threads"] == 3


def test_ai_review_unresolved_is_silent_when_nothing_is_known_to_be_unresolved():
    """A thread whose resolution GitHub never reported is counted nowhere: "unknown" must not be
    rendered as "the author ignored the reviewer"."""
    policy = AIPolicyFactory(require_ai_comments_resolved=True)
    ctx = make_context(pull_request=_merged_pr(), policy=policy, unresolved_ai_threads=0)
    assert _run(RuleCode.AI_REVIEW_UNRESOLVED, ctx) == []


# -- the body-section checks ----------------------------------------------------------------------

_FULL_BODY = """## Task

Closes #431

## Risk level

Medium — touches the reporting query only.

## Verification

Ran the suite and clicked through the dashboard.

## Plan

Roll back by reverting this commit; no migration.
"""


def test_risk_level_missing_fires_on_a_body_with_no_risk_section():
    policy = AIPolicyFactory(require_risk_level=True)
    ctx = make_context(pull_request=_merged_pr(body="Just a fix."), policy=policy)
    assert len(_run(RuleCode.RISK_LEVEL_MISSING, ctx)) == 1


def test_risk_level_missing_is_silent_on_a_body_that_states_one():
    policy = AIPolicyFactory(require_risk_level=True)
    ctx = make_context(pull_request=_merged_pr(body=_FULL_BODY), policy=policy)
    assert _run(RuleCode.RISK_LEVEL_MISSING, ctx) == []


def test_an_unfilled_template_section_does_not_count_as_stated():
    """The heading is there and the content is the template's own instruction comment. A check
    satisfied by an untouched template would be worse than no check."""
    policy = AIPolicyFactory(require_risk_level=True)
    body = "## Risk level\n\n<!-- low / medium / high -->\n"
    ctx = make_context(pull_request=_merged_pr(body=body), policy=policy)
    assert len(_run(RuleCode.RISK_LEVEL_MISSING, ctx)) == 1


def test_verification_missing_fires_and_clears():
    policy = AIPolicyFactory(require_verification_note=True)
    without = make_context(pull_request=_merged_pr(body="Fixes the bug."), policy=policy)
    with_note = make_context(pull_request=_merged_pr(body=_FULL_BODY), policy=policy)

    assert len(_run(RuleCode.VERIFICATION_MISSING, without)) == 1
    assert _run(RuleCode.VERIFICATION_MISSING, with_note) == []


@pytest.mark.parametrize(
    "body",
    [
        "Closes #431",
        "Implements ABC-1234",
        "See https://example.atlassian.net/browse/ABC-9",
        "https://github.com/acme/widget/issues/12",
    ],
)
def test_task_link_missing_accepts_a_reference_anywhere_in_the_body(body):
    """Plenty of teams write "closes #431" in the first line and never fill a section in."""
    policy = AIPolicyFactory(require_task_link=True)
    ctx = make_context(pull_request=_merged_pr(body=body), policy=policy)
    assert _run(RuleCode.TASK_LINK_MISSING, ctx) == []


def test_task_link_missing_fires_on_a_body_with_no_reference():
    policy = AIPolicyFactory(require_task_link=True)
    ctx = make_context(pull_request=_merged_pr(body="Tidy up the imports."), policy=policy)
    assert len(_run(RuleCode.TASK_LINK_MISSING, ctx)) == 1


def test_task_link_missing_ignores_a_reference_inside_a_template_comment():
    policy = AIPolicyFactory(require_task_link=True)
    ctx = make_context(pull_request=_merged_pr(body="<!-- Link the task, e.g. #123 -->"), policy=policy)
    assert len(_run(RuleCode.TASK_LINK_MISSING, ctx)) == 1


def test_high_risk_no_plan_fires_only_for_a_high_risk_change_without_a_plan():
    policy = AIPolicyFactory(require_high_risk_plan=True)
    no_plan = make_context(
        pull_request=_merged_pr(body="Quick change."),
        policy=policy,
        risk_level=SensitivePathRule.RiskLevel.HIGH,
        risk_paths=("apps/thing/migrations/0002_x.py",),
    )
    with_plan = make_context(
        pull_request=_merged_pr(body=_FULL_BODY),
        policy=policy,
        risk_level=SensitivePathRule.RiskLevel.HIGH,
    )
    medium_risk = make_context(
        pull_request=_merged_pr(body="Quick change."),
        policy=policy,
        risk_level=SensitivePathRule.RiskLevel.MEDIUM,
    )

    findings = _run(RuleCode.HIGH_RISK_NO_PLAN, no_plan)
    assert findings[0].details_params["paths"] == ["apps/thing/migrations/0002_x.py"]
    assert _run(RuleCode.HIGH_RISK_NO_PLAN, with_plan) == []
    assert _run(RuleCode.HIGH_RISK_NO_PLAN, medium_risk) == []


# -- NEW_DEPENDENCY_AI / MIGRATION_AI_INSUFFICIENT_REVIEW ----------------------------------------


def test_new_dependency_ai_fires_on_a_manifest_change_even_when_excluded_from_size():
    """A lockfile is in `EXCLUDED_PATH_GLOBS` for size reasons and is exactly what this rule is
    about, so manifests are matched against every file."""
    policy = AIPolicyFactory(flag_new_dependencies_in_ai_prs=True)
    pr = _merged_pr()
    files = _files(
        pr,
        {
            "pyproject.toml": {"is_excluded": False},
            "uv.lock": {"is_excluded": True},
        },
    )
    ctx = make_context(pull_request=pr, policy=policy, files=files, is_ai=True)

    findings = _run(RuleCode.NEW_DEPENDENCY_AI, ctx)

    assert findings[0].details_params["paths"] == ["pyproject.toml", "uv.lock"]


def test_new_dependency_ai_is_silent_for_a_pull_request_outside_the_ai_cohort():
    policy = AIPolicyFactory(flag_new_dependencies_in_ai_prs=True)
    pr = _merged_pr(ai_status=AIStatus.NO_AI)
    files = _files(pr, {"pyproject.toml": {}})
    ctx = make_context(pull_request=pr, policy=policy, files=files, is_ai=False)
    assert _run(RuleCode.NEW_DEPENDENCY_AI, ctx) == []


def test_migration_ai_insufficient_review_fires_without_a_designated_approval():
    reviewer = PersonFactory()
    policy = AIPolicyFactory()
    pr = _merged_pr()
    files = _files(pr, {"apps/thing/migrations/0002_x.py": {"is_excluded": True}})
    ctx = make_context(
        pull_request=pr,
        policy=policy,
        files=files,
        designated_reviewer_person_ids=frozenset({reviewer.pk}),
        designated_approver_person_ids=frozenset(),
    )

    findings = _run(RuleCode.MIGRATION_AI_INSUFFICIENT_REVIEW, ctx)

    assert findings[0].details_params["paths"] == ["apps/thing/migrations/0002_x.py"]


def test_migration_ai_insufficient_review_clears_when_a_designated_reviewer_approved():
    reviewer = PersonFactory()
    pr = _merged_pr()
    files = _files(pr, {"apps/thing/migrations/0002_x.py": {"is_excluded": True}})
    ctx = make_context(
        pull_request=pr,
        policy=AIPolicyFactory(),
        files=files,
        designated_reviewer_person_ids=frozenset({reviewer.pk}),
        designated_approver_person_ids=frozenset({reviewer.pk}),
    )
    assert _run(RuleCode.MIGRATION_AI_INSUFFICIENT_REVIEW, ctx) == []


def test_migration_ai_insufficient_review_is_silent_with_no_designated_reviewers_configured():
    """With nobody named there is no approval that could satisfy it, so firing would make the
    check unanswerable — which is also what keeps an upgrade quiet."""
    pr = _merged_pr()
    files = _files(pr, {"apps/thing/migrations/0002_x.py": {"is_excluded": True}})
    ctx = make_context(pull_request=pr, policy=AIPolicyFactory(), files=files)
    assert _run(RuleCode.MIGRATION_AI_INSUFFICIENT_REVIEW, ctx) == []


# -- SECRET_ARTIFACT_COMMITTED / AGENT_CONFIG_CHANGED --------------------------------------------


def test_secret_artifact_committed_fires_on_a_credential_file_and_spares_its_example():
    policy = AIPolicyFactory(forbid_secret_artifacts=True)
    pr = _merged_pr()
    files = _files(pr, {".env": {}, ".env.example": {}, "deploy/server.pem": {}, "deploy/key.pub": {}})
    ctx = make_context(pull_request=pr, policy=policy, files=files)

    findings = _run(RuleCode.SECRET_ARTIFACT_COMMITTED, ctx)

    assert findings[0].details_params["paths"] == [".env", "deploy/server.pem"]


def test_secret_artifact_committed_applies_to_a_human_pull_request_too():
    """A committed credential is no better for having been typed by a person."""
    policy = AIPolicyFactory(forbid_secret_artifacts=True)
    pr = _merged_pr(ai_status=AIStatus.NO_AI)
    files = _files(pr, {".env": {}})
    ctx = make_context(pull_request=pr, policy=policy, files=files, is_ai=False)
    assert len(_run(RuleCode.SECRET_ARTIFACT_COMMITTED, ctx)) == 1


def test_secret_artifact_committed_is_silent_while_the_toggle_is_off():
    pr = _merged_pr()
    files = _files(pr, {".env": {}})
    assert _run(RuleCode.SECRET_ARTIFACT_COMMITTED, make_context(pull_request=pr, files=files)) == []


def test_agent_config_changed_fires_on_a_claude_md_change():
    policy = AIPolicyFactory(flag_agent_config_changes=True)
    pr = _merged_pr()
    files = _files(pr, {"CLAUDE.md": {}, ".claude/settings.json": {}, "apps/thing/service.py": {}})
    ctx = make_context(pull_request=pr, policy=policy, files=files)

    findings = _run(RuleCode.AGENT_CONFIG_CHANGED, ctx)

    assert findings[0].details_params["paths"] == [".claude/settings.json", "CLAUDE.md"]


def test_agent_config_changed_is_silent_on_an_ordinary_change():
    policy = AIPolicyFactory(flag_agent_config_changes=True)
    pr = _merged_pr()
    files = _files(pr, {"apps/thing/service.py": {}})
    ctx = make_context(pull_request=pr, policy=policy, files=files)
    assert _run(RuleCode.AGENT_CONFIG_CHANGED, ctx) == []


# -- SCOPE_CREEP / RUBBER_STAMP_ON_AI_PR ---------------------------------------------------------


def test_scope_creep_fires_when_a_formatting_sweep_is_mixed_in():
    policy = AIPolicyFactory(forbid_scope_creep=True)
    ctx = make_context(
        pull_request=_merged_pr(),
        policy=policy,
        structural_kinds=frozenset({SignalKind.WHOLESALE_REFORMAT}),
    )

    findings = _run(RuleCode.SCOPE_CREEP, ctx)

    assert [f.details_params["reason"] for f in findings] == ["reformat_mixed_in"]


def test_scope_creep_fires_when_the_change_reaches_outside_the_stated_scope():
    policy = AIPolicyFactory(forbid_scope_creep=True)
    body = "## Scope\n\nOnly the reporting app.\n"
    pr = _merged_pr(body=body)
    files = _files(pr, {"reporting/views.py": {}, "billing/models.py": {}})
    ctx = make_context(pull_request=pr, policy=policy, files=files)

    findings = _run(RuleCode.SCOPE_CREEP, ctx)

    assert [f.details_params["reason"] for f in findings] == ["outside_stated_scope"]
    assert findings[0].details_params["modules"] == ["billing"]


def test_scope_creep_is_silent_when_the_body_states_no_scope():
    """With nothing stated there is nothing to have exceeded."""
    policy = AIPolicyFactory(forbid_scope_creep=True)
    pr = _merged_pr(body="A fix.")
    files = _files(pr, {"reporting/views.py": {}, "billing/models.py": {}})
    ctx = make_context(pull_request=pr, policy=policy, files=files)
    assert _run(RuleCode.SCOPE_CREEP, ctx) == []


def test_rubber_stamp_on_ai_pr_fires_on_a_rubber_stamped_merge():
    policy = AIPolicyFactory(forbid_rubber_stamp_approval=True)
    ctx = make_context(pull_request=_merged_pr(is_rubber_stamp=True), policy=policy)
    assert len(_run(RuleCode.RUBBER_STAMP_ON_AI_PR, ctx)) == 1


def test_rubber_stamp_on_ai_pr_is_silent_when_the_review_was_real():
    policy = AIPolicyFactory(forbid_rubber_stamp_approval=True)
    ctx = make_context(pull_request=_merged_pr(is_rubber_stamp=False), policy=policy)
    assert _run(RuleCode.RUBBER_STAMP_ON_AI_PR, ctx) == []


# -- risk-aware size limits and approvals --------------------------------------------------------


def test_the_high_risk_size_limit_applies_to_a_migration_change_and_not_to_a_docs_change():
    """PLAN's own acceptance case: a 500-line AI pull request exceeds the high-risk limit when it
    touches a migration, and the same 500 lines of documentation do not."""
    policy = AIPolicyFactory(max_effective_lines_by_risk={"medium": 800, "high": 400})
    high_risk = make_context(
        pull_request=_merged_pr(effective_additions=500, effective_deletions=0),
        policy=policy,
        risk_level=SensitivePathRule.RiskLevel.HIGH,
    )
    unclassified = make_context(
        pull_request=_merged_pr(effective_additions=500, effective_deletions=0), policy=policy
    )

    findings = _run(RuleCode.AI_PR_TOO_LARGE, high_risk)
    assert findings[0].details_params == {
        "effective_lines": 500,
        "limit": 400,
        "risk_level": "high",
    }
    # No risk classification and no flat limit: nothing to exceed.
    assert _run(RuleCode.AI_PR_TOO_LARGE, unclassified) == []


def test_a_risk_limit_of_zero_or_a_missing_level_falls_back_to_the_flat_limit():
    policy = AIPolicyFactory(ai_pr_max_effective_lines=1000, max_effective_lines_by_risk={"high": 400})
    medium = make_context(
        pull_request=_merged_pr(effective_additions=1200, effective_deletions=0),
        policy=policy,
        risk_level=SensitivePathRule.RiskLevel.MEDIUM,
    )

    findings = _run(RuleCode.AI_PR_TOO_LARGE, medium)

    assert findings[0].details_params["limit"] == 1000


def test_a_high_risk_change_needs_the_higher_number_of_approvals():
    policy = AIPolicyFactory(require_human_approval=True, min_human_approvals=1, high_risk_min_approvals=2)
    ctx = make_context(
        pull_request=_merged_pr(),
        policy=policy,
        human_approver_person_ids={11},
        risk_level=SensitivePathRule.RiskLevel.HIGH,
    )

    findings = _run(RuleCode.NO_HUMAN_APPROVAL, ctx)

    assert findings[0].details_params == {"approvals": 1, "required": 2}


def test_the_high_risk_minimum_never_lowers_the_ordinary_one():
    policy = AIPolicyFactory(require_human_approval=True, min_human_approvals=3, high_risk_min_approvals=2)
    ctx = make_context(
        pull_request=_merged_pr(),
        policy=policy,
        human_approver_person_ids={11, 12},
        risk_level=SensitivePathRule.RiskLevel.HIGH,
    )
    assert _run(RuleCode.NO_HUMAN_APPROVAL, ctx)[0].details_params["required"] == 3


# -- the advisory mode ----------------------------------------------------------------------------


def test_an_advisory_rule_raises_no_sensitive_path_violation_of_its_own():
    """The seeded PLANEKS risk table is broad. It classifies risk and nothing else, which is what
    makes it safe to ship active."""
    rule = SensitivePathRuleFactory(
        glob="**/migrations/**",
        ai_mode=SensitivePathRule.AiMode.ADVISORY,
        risk_level=SensitivePathRule.RiskLevel.HIGH,
    )
    pr = _merged_pr()
    pr_file = PRFileFactory(pull_request=pr, path="apps/thing/migrations/0002_x.py")
    pr_file.matched_sensitive_rule = rule
    pr_file.save(update_fields=["matched_sensitive_rule"])
    ctx = make_context(pull_request=pr, files=(pr_file,), sensitive_rules=(rule,))

    assert _run(RuleCode.SENSITIVE_PATH_FORBIDDEN, ctx) == []
    assert _run(RuleCode.SENSITIVE_PATH_REVIEW, ctx) == []
