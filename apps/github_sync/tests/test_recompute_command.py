import datetime

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.activity.factories import PullRequestFactory
from apps.activity.models import AIStatus, PullRequest
from apps.ai_detection.factories import DetectionRuleFactory
from apps.ai_detection.models import AISignal, Confidence, Detector, Tool
from apps.catalog.factories import ProjectFactory, RepositoryFactory
from apps.metrics.models import DailyRollup
from apps.metrics.services import data_version
from apps.metrics.timeframe import day_of
from apps.policy.factories import AIPolicyFactory
from apps.policy.models import PolicyViolation


@pytest.mark.django_db
def test_recompute_re_detects_after_a_rule_is_added():
    pr = PullRequestFactory(body="Generated with Claude Code")
    call_command("recompute")
    pr.refresh_from_db()
    assert pr.ai_status == AIStatus.UNKNOWN

    DetectionRuleFactory(
        detector=Detector.PR_BODY_FOOTER,
        pattern="Generated with Claude Code",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )
    call_command("recompute")

    pr.refresh_from_db()
    assert pr.ai_status == AIStatus.AI_EXPLICIT
    assert AISignal.objects.filter(pull_request=pr).count() == 1


@pytest.mark.django_db
def test_from_to_narrows_the_set():
    DetectionRuleFactory(
        detector=Detector.PR_BODY_FOOTER,
        pattern="Generated with Claude Code",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )
    now = timezone.now()
    in_range = PullRequestFactory(
        created_at=now - datetime.timedelta(days=1), body="Generated with Claude Code"
    )
    out_of_range = PullRequestFactory(
        created_at=now - datetime.timedelta(days=30), body="Generated with Claude Code"
    )

    call_command(
        "recompute",
        **{"from": (now - datetime.timedelta(days=5)).date().isoformat(), "to": now.date().isoformat()},
    )

    in_range.refresh_from_db()
    out_of_range.refresh_from_db()
    assert in_range.ai_status == AIStatus.AI_EXPLICIT
    assert out_of_range.ai_status == AIStatus.UNKNOWN
    assert AISignal.objects.filter(pull_request=out_of_range).count() == 0


@pytest.mark.django_db
def test_from_boundary_uses_report_timezone_not_utc():
    DetectionRuleFactory(
        detector=Detector.PR_BODY_FOOTER,
        pattern="Generated with Claude Code",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )
    # 2025-12-31T23:00Z is 2026-01-01T01:00 in Europe/Kyiv (UTC+2 in January) — inside the Kyiv
    # day named by --from 2026-01-01, even though it falls on the previous UTC calendar day.
    early_kyiv_day = PullRequestFactory(
        created_at=datetime.datetime(2025, 12, 31, 23, 0, tzinfo=datetime.UTC),
        body="Generated with Claude Code",
    )

    call_command("recompute", **{"from": "2026-01-01"})

    early_kyiv_day.refresh_from_db()
    assert early_kyiv_day.ai_status == AIStatus.AI_EXPLICIT


@pytest.mark.django_db
def test_repo_and_project_filters_narrow_the_set():
    project = ProjectFactory()
    in_repo = RepositoryFactory()
    project.repositories.add(in_repo)
    other_repo = RepositoryFactory()

    DetectionRuleFactory(
        detector=Detector.PR_BODY_FOOTER,
        pattern="Generated with Claude Code",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )
    in_pr = PullRequestFactory(repository=in_repo, body="Generated with Claude Code")
    out_pr = PullRequestFactory(repository=other_repo, body="Generated with Claude Code")

    call_command("recompute", project=project.slug)

    in_pr.refresh_from_db()
    out_pr.refresh_from_db()
    assert in_pr.ai_status == AIStatus.AI_EXPLICIT
    assert out_pr.ai_status == AIStatus.UNKNOWN


@pytest.mark.django_db
def test_running_it_twice_is_a_noop_on_row_counts():
    DetectionRuleFactory(
        detector=Detector.PR_BODY_FOOTER,
        pattern="Generated with Claude Code",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )
    PullRequestFactory(body="Generated with Claude Code")

    call_command("recompute")
    count_after_first = AISignal.objects.count()

    call_command("recompute")

    assert AISignal.objects.count() == count_after_first


@pytest.mark.django_db
def test_recompute_evaluates_a_policy_added_after_the_pr_was_synced():
    pr = PullRequestFactory(created_at=timezone.now() - datetime.timedelta(days=10), body="")
    call_command("recompute")
    assert PolicyViolation.objects.filter(pull_request=pr).count() == 0

    AIPolicyFactory(require_disclosure=True, effective_from=timezone.now() - datetime.timedelta(days=30))
    call_command("recompute")

    violation = PolicyViolation.objects.get(
        pull_request=pr, rule_code=PolicyViolation.RuleCode.DISCLOSURE_MISSING
    )
    assert violation.status == PolicyViolation.Status.OPEN


@pytest.mark.django_db
def test_second_recompute_changes_no_policy_violation_row_counts():
    AIPolicyFactory(require_disclosure=True, effective_from=timezone.now() - datetime.timedelta(days=30))
    PullRequestFactory(created_at=timezone.now() - datetime.timedelta(days=10), body="")

    call_command("recompute")
    count_after_first = PolicyViolation.objects.count()

    call_command("recompute")

    assert PolicyViolation.objects.count() == count_after_first


@pytest.mark.django_db
def test_from_to_rebuilds_only_that_range():
    now = timezone.now()
    in_range = PullRequestFactory(
        state=PullRequest.State.MERGED, created_at=now - datetime.timedelta(days=1), merged_at=now
    )
    out_of_range = PullRequestFactory(
        state=PullRequest.State.MERGED,
        created_at=now - datetime.timedelta(days=30),
        merged_at=now - datetime.timedelta(days=30),
    )

    call_command(
        "recompute",
        **{
            "from": day_of(now - datetime.timedelta(days=5)).isoformat(),
            "to": day_of(now).isoformat(),
        },
    )

    assert DailyRollup.objects.filter(date=day_of(in_range.merged_at)).exists()
    assert not DailyRollup.objects.filter(date=day_of(out_of_range.merged_at)).exists()


@pytest.mark.django_db
def test_rollups_only_skips_derive_detect_evaluate():
    DetectionRuleFactory(
        detector=Detector.PR_BODY_FOOTER,
        pattern="Generated with Claude Code",
        tool=Tool.CLAUDE_CODE,
        confidence=Confidence.HIGH,
    )
    pr = PullRequestFactory(body="Generated with Claude Code")

    call_command("recompute", **{"rollups_only": True})

    pr.refresh_from_db()
    assert pr.ai_status == AIStatus.UNKNOWN
    assert AISignal.objects.filter(pull_request=pr).count() == 0


@pytest.mark.django_db
def test_skip_rollups_leaves_no_rollup_rows():
    now = timezone.now()
    PullRequestFactory(state=PullRequest.State.MERGED, created_at=now, merged_at=now)

    call_command("recompute", **{"skip_rollups": True})

    assert DailyRollup.objects.count() == 0


@pytest.mark.django_db
def test_command_bumps_the_data_version():
    PullRequestFactory()
    version_before = data_version()

    call_command("recompute")

    assert data_version() == version_before + 1


@pytest.mark.django_db
def test_running_recompute_twice_is_idempotent_at_the_rollup_row_level():
    now = timezone.now()
    PullRequestFactory(state=PullRequest.State.MERGED, created_at=now, merged_at=now)

    call_command("recompute")
    rows_after_first = list(
        DailyRollup.objects.order_by("metric_key", "scope_type", "scope_id", "cohort", "date").values(
            "metric_key", "scope_type", "scope_id", "cohort", "date", "value", "sample_size"
        )
    )

    call_command("recompute")
    rows_after_second = list(
        DailyRollup.objects.order_by("metric_key", "scope_type", "scope_id", "cohort", "date").values(
            "metric_key", "scope_type", "scope_id", "cohort", "date", "value", "sample_size"
        )
    )

    assert rows_after_first == rows_after_second
