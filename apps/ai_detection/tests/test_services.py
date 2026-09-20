from datetime import UTC, datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import (
    CommitFactory,
    PRFileFactory,
    PullRequestCommitFactory,
    PullRequestFactory,
    ReviewFactory,
)
from apps.activity.models import AIDisclosure, AIStatus, PullRequest
from apps.ai_detection.factories import DetectionRuleFactory
from apps.ai_detection.models import AISignal, Confidence, DetectionRule, Detector, Tool
from apps.ai_detection.services import detect_pull_request, detect_pull_requests, dry_run_rule
from apps.catalog.factories import IdentityFactory, ProjectFactory, RepositoryFactory
from apps.catalog.models import Identity


@pytest.mark.django_db
def test_matching_rule_writes_one_signal_with_the_right_fields():
    rule = DetectionRuleFactory(
        detector=Detector.PR_BODY_FOOTER,
        pattern="Generated with Claude Code",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )
    pr = PullRequestFactory(body="Fixes bug\n\nGenerated with Claude Code")

    detect_pull_request(pr.pk)

    signal = AISignal.objects.get(pull_request=pr)
    assert signal.rule_id == rule.pk
    assert signal.tool == Tool.CLAUDE_CODE
    assert signal.confidence == Confidence.HIGH
    assert "Generated with Claude Code" in signal.evidence

    pr.refresh_from_db()
    assert pr.ai_status == AIStatus.AI_EXPLICIT


@pytest.mark.django_db
def test_second_detection_run_creates_no_new_signal():
    DetectionRuleFactory(
        detector=Detector.PR_BODY_FOOTER,
        pattern="Generated with Claude Code",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )
    pr = PullRequestFactory(body="Generated with Claude Code")

    detect_pull_request(pr.pk)
    first = AISignal.objects.get(pull_request=pr)

    detect_pull_request(pr.pk)

    assert AISignal.objects.filter(pull_request=pr).count() == 1
    second = AISignal.objects.get(pull_request=pr)
    assert second.pk == first.pk
    assert second.detected_at == first.detected_at


@pytest.mark.django_db
def test_deactivating_a_rule_removes_its_signals_and_resets_status():
    rule = DetectionRuleFactory(
        detector=Detector.PR_BODY_FOOTER,
        pattern="Generated with Claude Code",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )
    pr = PullRequestFactory(body="Generated with Claude Code")
    detect_pull_request(pr.pk)
    assert AISignal.objects.filter(pull_request=pr).count() == 1

    rule.is_active = False
    rule.save(update_fields=["is_active"])
    detect_pull_request(pr.pk)

    assert AISignal.objects.filter(pull_request=pr).count() == 0
    pr.refresh_from_db()
    assert pr.ai_status == AIStatus.UNKNOWN


@pytest.mark.django_db
def test_editing_a_pattern_so_it_no_longer_matches_deletes_the_stale_signal():
    rule = DetectionRuleFactory(
        detector=Detector.PR_BODY_FOOTER,
        pattern="Generated with Claude Code",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )
    pr = PullRequestFactory(body="Generated with Claude Code")
    detect_pull_request(pr.pk)
    assert AISignal.objects.filter(pull_request=pr).count() == 1

    rule.pattern = "Something else entirely"
    rule.save(update_fields=["pattern"])
    detect_pull_request(pr.pk)

    assert AISignal.objects.filter(pull_request=pr).count() == 0


@pytest.mark.django_db
def test_identical_evidence_on_two_commits_attributes_the_signal_to_the_earlier_one():
    DetectionRuleFactory(
        detector=Detector.COMMIT_TRAILER,
        pattern="Co-Authored-By: Claude",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )
    pr = PullRequestFactory()
    trailers = {"Co-Authored-By": ["Claude <noreply@anthropic.com>"]}
    earlier_commit = CommitFactory(
        repository=pr.repository,
        trailers=trailers,
        committed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    later_commit = CommitFactory(
        repository=pr.repository,
        trailers=trailers,
        committed_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    PullRequestCommitFactory(pull_request=pr, commit=later_commit, position=1)
    PullRequestCommitFactory(pull_request=pr, commit=earlier_commit, position=0)

    detect_pull_request(pr.pk)

    signal = AISignal.objects.get(pull_request=pr)
    assert signal.commit_id == earlier_commit.pk

    detect_pull_request(pr.pk)
    signal.refresh_from_db()
    assert signal.commit_id == earlier_commit.pk


@pytest.mark.django_db
def test_ai_tools_is_the_union_of_signal_tools_and_disclosed_tools():
    DetectionRuleFactory(
        detector=Detector.PR_BODY_FOOTER,
        pattern="Generated with Cursor",
        tool=Tool.CURSOR,
        confidence=Confidence.LOW,
    )
    body = "Generated with Cursor\n\n### AI assistance\n- [x] Partial\n\n### AI tools used\nWindsurf\n"
    pr = PullRequestFactory(body=body)

    detect_pull_request(pr.pk)

    pr.refresh_from_db()
    assert pr.ai_tools == ["cursor", "windsurf"]


@pytest.mark.django_db
def test_signal_evidence_is_within_the_limit():
    DetectionRuleFactory(
        detector=Detector.PR_BODY_FOOTER,
        pattern="Generated with Claude Code",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )
    pr = PullRequestFactory(body=("x" * 3000) + "Generated with Claude Code" + ("y" * 3000))

    detect_pull_request(pr.pk)

    signal = AISignal.objects.get(pull_request=pr)
    assert len(signal.evidence) <= 200


@pytest.mark.django_db
def test_invalid_pattern_rule_is_skipped_and_other_rules_still_run():
    DetectionRule.objects.create(
        name="broken rule",
        detector=Detector.PR_BODY_FOOTER,
        pattern="[unclosed",
        tool=Tool.OTHER,
        confidence=Confidence.LOW,
    )
    DetectionRuleFactory(
        detector=Detector.PR_BODY_FOOTER,
        pattern="Generated with Claude Code",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )
    pr = PullRequestFactory(body="Generated with Claude Code")

    detect_pull_request(pr.pk)

    assert AISignal.objects.filter(pull_request=pr).count() == 1
    pr.refresh_from_db()
    assert pr.ai_status == AIStatus.AI_EXPLICIT


@pytest.mark.django_db
def test_pr_with_no_rules_gets_unknown_and_missing():
    pr = PullRequestFactory(body="Just a normal PR with nothing special.")

    detect_pull_request(pr.pk)

    pr.refresh_from_db()
    assert pr.ai_status == AIStatus.UNKNOWN
    assert pr.ai_disclosure == AIDisclosure.MISSING
    assert AISignal.objects.filter(pull_request=pr).count() == 0


@pytest.mark.django_db
def test_detect_pull_requests_returns_the_processed_count():
    DetectionRuleFactory(
        detector=Detector.PR_BODY_FOOTER,
        pattern="Generated with Claude Code",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )
    PullRequestFactory(body="Generated with Claude Code")
    PullRequestFactory(body="Nothing here")

    count = detect_pull_requests(PullRequest.objects.all())

    assert count == 2


@pytest.mark.django_db
def test_detect_pull_requests_does_not_reload_rules_and_settings_per_pr():
    """`detect_pull_requests` must load the compiled rules and the `DisclosureConfig` once, not
    once per PR (1 rules query + 6 settings queries = 7 queries repeated per PR would make this
    grow with the batch size)."""
    DetectionRuleFactory(
        detector=Detector.PR_BODY_FOOTER,
        pattern="Generated with Claude Code",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )
    small_batch = [PullRequestFactory(body="Generated with Claude Code") for _ in range(3)]
    large_batch = [PullRequestFactory(body="Generated with Claude Code") for _ in range(20)]

    with CaptureQueriesContext(connection) as small:
        detect_pull_requests(PullRequest.objects.filter(pk__in=[pr.pk for pr in small_batch]))
    with CaptureQueriesContext(connection) as large:
        detect_pull_requests(PullRequest.objects.filter(pk__in=[pr.pk for pr in large_batch]))

    # Each PR's own detect_pull_request call costs ~7 queries regardless (PR fetch, commit
    # prefetch, existing-signal fetch, insert, update, plus the two savepoint statements) — that
    # part scales with the batch size legitimately. Reloading the rules and the settings inside
    # the loop would add another 7 queries per PR on top of that, so the regression this guards
    # against would push growth to ~14; comfortably below that but above the inherent ~7 leaves
    # room for either without a flaky test.
    per_pr_growth = (len(large.captured_queries) - len(small.captured_queries)) / (
        len(large_batch) - len(small_batch)
    )
    assert per_pr_growth < 10


# --- dry_run_rule (T15) ----------------------------------------------------


@pytest.mark.django_db
def test_dry_run_rule_finds_matches_without_writing_signals():
    PullRequestFactory(body="Generated with Claude Code")
    PullRequestFactory(body="Nothing here")
    unsaved_rule = DetectionRule(
        detector=Detector.PR_BODY_FOOTER,
        pattern="Generated with Claude Code",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )
    scope = ScopeFilter(unrestricted=True, project_ids=None)

    matches = dry_run_rule(unsaved_rule, scope, limit=50)

    assert len(matches) == 1
    assert "Generated with Claude Code" in matches[0].evidence
    assert AISignal.objects.count() == 0


@pytest.mark.django_db
def test_dry_run_rule_invalid_pattern_raises_value_error():
    unsaved_rule = DetectionRule(
        detector=Detector.PR_BODY_FOOTER,
        pattern="[unclosed",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )
    scope = ScopeFilter(unrestricted=True, project_ids=None)

    with pytest.raises(ValueError):
        dry_run_rule(unsaved_rule, scope, limit=50)


@pytest.mark.django_db
def test_dry_run_rule_restricted_scope_sees_only_its_own_prs():
    project = ProjectFactory()
    own_repository = RepositoryFactory()
    project.repositories.add(own_repository)
    other_repository = RepositoryFactory()

    PullRequestFactory(repository=own_repository, body="Generated with Claude Code")
    PullRequestFactory(repository=other_repository, body="Generated with Claude Code")

    unsaved_rule = DetectionRule(
        detector=Detector.PR_BODY_FOOTER,
        pattern="Generated with Claude Code",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )
    scope = ScopeFilter(unrestricted=False, project_ids=frozenset({project.pk}))

    matches = dry_run_rule(unsaved_rule, scope, limit=50)

    assert len(matches) == 1


# --- the detectors phase 12 added ----------------------------------------------------------------


@pytest.mark.django_db
def test_a_tool_written_file_in_the_diff_resolves_the_pull_request_to_ai_explicit():
    DetectionRuleFactory(
        detector=Detector.FILE_PATH,
        pattern=r"(^|/)\.aider\.(chat|input)\.history\.md$",
        tool=Tool.AIDER,
        confidence=Confidence.HIGH,
    )
    pr = PullRequestFactory()
    PRFileFactory(pull_request=pr, path="apps/sync/client.py")
    PRFileFactory(pull_request=pr, path=".aider.chat.history.md")

    detect_pull_request(pr.pk)

    signal = AISignal.objects.get(pull_request=pr)
    assert signal.evidence == ".aider.chat.history.md"
    assert signal.tool == Tool.AIDER
    pr.refresh_from_db()
    assert pr.ai_status == AIStatus.AI_EXPLICIT


@pytest.mark.django_db
def test_a_file_path_rule_writes_one_signal_for_a_pull_request_with_many_matching_files():
    DetectionRuleFactory(
        detector=Detector.FILE_PATH,
        pattern=r"^\.claude/",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )
    pr = PullRequestFactory()
    for index in range(400):
        PRFileFactory(pull_request=pr, path=f".claude/agents/agent-{index:03d}.md")

    detect_pull_request(pr.pk)

    assert AISignal.objects.filter(pull_request=pr).count() == 1


@pytest.mark.django_db
def test_an_excluded_file_produces_no_signal():
    DetectionRuleFactory(
        detector=Detector.FILE_PATH,
        pattern=r"^\.claude/",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )
    pr = PullRequestFactory()
    PRFileFactory(pull_request=pr, path=".claude/settings.local.json", is_excluded=True)

    detect_pull_request(pr.pk)

    assert AISignal.objects.filter(pull_request=pr).count() == 0


@pytest.mark.django_db
def test_a_stylometric_rule_alone_never_resolves_a_pull_request_to_ai_explicit():
    """Every stylometric rule ships `low` + `disputed`, and prose habits are not proof. The most
    a match may claim on its own is `ai_suspected`."""
    DetectionRuleFactory(
        detector=Detector.PR_BODY_FOOTER,
        pattern=r"\b(comprehensive|robust|seamless|meticulous)(ly)?\b",
        tool=Tool.OTHER,
        confidence=Confidence.LOW,
    )
    pr = PullRequestFactory(body="This adds comprehensive coverage of the sync path.")

    detect_pull_request(pr.pk)

    assert AISignal.objects.filter(pull_request=pr).count() == 1
    pr.refresh_from_db()
    assert pr.ai_status == AIStatus.AI_SUSPECTED


@pytest.mark.django_db
def test_a_reviewer_bot_is_detected_once_however_many_reviews_it_left():
    DetectionRuleFactory(
        detector=Detector.REVIEWER_IDENTITY,
        pattern=r"^coderabbitai(\[bot\])?$",
        tool=Tool.OTHER,
        confidence=Confidence.LOW,
    )
    pr = PullRequestFactory()
    reviewer = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="coderabbitai[bot]")
    for _ in range(3):
        ReviewFactory(pull_request=pr, reviewer=reviewer, submitted_at=timezone.now())

    detect_pull_request(pr.pk)

    signals = AISignal.objects.filter(pull_request=pr)
    assert signals.count() == 1
    assert signals.get().evidence == "coderabbitai[bot]"


@pytest.mark.django_db
def test_a_title_rule_and_a_merged_by_rule_write_their_own_signals():
    DetectionRuleFactory(
        detector=Detector.PR_TITLE,
        pattern=r"^codex\s*:\s",
        tool=Tool.CODEX,
        confidence=Confidence.LOW,
    )
    DetectionRuleFactory(
        detector=Detector.MERGED_BY_IDENTITY,
        pattern=r"^devin-ai-integration(\[bot\])?$",
        tool=Tool.DEVIN,
        confidence=Confidence.LOW,
    )
    merger = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="devin-ai-integration[bot]")
    pr = PullRequestFactory(title="Codex: fix the flaky sync test", merged_by=merger)

    detect_pull_request(pr.pk)

    assert {signal.tool for signal in AISignal.objects.filter(pull_request=pr)} == {
        Tool.CODEX,
        Tool.DEVIN,
    }


@pytest.mark.django_db
def test_detecting_a_batch_costs_the_same_whether_its_pull_requests_touch_2_files_or_40():
    """The widened prefetch must stay one query per relation per PR: an N+1 over files or
    reviews would make `manage.py recompute` unusable on a real repository."""
    DetectionRuleFactory(
        detector=Detector.FILE_PATH,
        pattern=r"^\.claude/",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )

    def _seed(prefix: str, file_count: int) -> None:
        for pr_index in range(20):
            pr = PullRequestFactory(title=f"{prefix}-{pr_index}")
            for file_index in range(file_count):
                PRFileFactory(pull_request=pr, path=f"{prefix}/{pr_index}/{file_index}.py")
                ReviewFactory(pull_request=pr)

    _seed("small", 2)
    small_queryset = PullRequest.objects.filter(title__startswith="small-")
    detect_pull_requests(small_queryset)  # warm the process-wide settings cache
    with CaptureQueriesContext(connection) as small_queries:
        assert detect_pull_requests(small_queryset) == 20

    _seed("big", 40)
    big_queryset = PullRequest.objects.filter(title__startswith="big-")
    with CaptureQueriesContext(connection) as big_queries:
        assert detect_pull_requests(big_queryset) == 20

    assert len(big_queries) == len(small_queries)
