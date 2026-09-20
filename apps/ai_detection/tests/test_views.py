import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import AuditEntry
from apps.activity.factories import PRFileFactory, PullRequestFactory, ReviewFactory
from apps.ai_detection.factories import DetectionRuleFactory
from apps.ai_detection.models import AISignal, Confidence, DetectionRule, Detector, Tool
from apps.catalog.factories import IdentityFactory
from apps.catalog.models import Identity

URL_NAMES = ["ai_detection:rules", "ai_detection:rule_create"]

VALID_RULE_POST = {
    "name": "new-rule",
    "detector": Detector.PR_BODY_FOOTER,
    "pattern": "Generated with Claude Code",
    "tool": Tool.CLAUDE_CODE,
    "confidence": Confidence.HIGH,
    "is_active": "on",
    "notes": "https://example.com/source",
}


@pytest.mark.django_db
@pytest.mark.parametrize("name", URL_NAMES)
def test_lead_gets_403(client, lead_user, name):
    client.force_login(lead_user)
    assert client.get(reverse(name)).status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("name", URL_NAMES)
def test_admin_gets_200(client, admin_user, name):
    client.force_login(admin_user)
    assert client.get(reverse(name)).status_code == 200


@pytest.mark.django_db
def test_lead_sees_no_nav_link_admin_does(client, lead_user, admin_user):
    client.force_login(lead_user)
    assert b"Detection rules" not in client.get(reverse("dashboards:overview")).content

    client.force_login(admin_user)
    assert b"Detection rules" in client.get(reverse("dashboards:overview")).content


@pytest.mark.django_db
def test_rule_edit_lead_403_admin_200(client, lead_user, admin_user):
    rule = DetectionRuleFactory()

    client.force_login(lead_user)
    assert client.get(reverse("ai_detection:rule_edit", args=[rule.pk])).status_code == 403

    client.force_login(admin_user)
    assert client.get(reverse("ai_detection:rule_edit", args=[rule.pk])).status_code == 200


@pytest.mark.django_db
def test_rule_create_persists_and_writes_audit(client, admin_user):
    client.force_login(admin_user)
    response = client.post(reverse("ai_detection:rule_create"), VALID_RULE_POST)
    assert response.status_code == 302
    rule = DetectionRule.objects.get(name="new-rule")
    assert rule.pattern == "Generated with Claude Code"
    assert AuditEntry.objects.filter(action="detection_rule.create", object_id=str(rule.pk)).exists()


@pytest.mark.django_db
def test_rule_edit_updates_and_writes_audit(client, admin_user):
    rule = DetectionRuleFactory(name="old-name")
    client.force_login(admin_user)

    payload = {**VALID_RULE_POST, "name": "renamed"}
    response = client.post(reverse("ai_detection:rule_edit", args=[rule.pk]), payload)

    assert response.status_code == 302
    rule.refresh_from_db()
    assert rule.name == "renamed"
    assert AuditEntry.objects.filter(action="detection_rule.update", object_id=str(rule.pk)).exists()


@pytest.mark.django_db
def test_invalid_pattern_renders_visible_field_error(client, admin_user):
    client.force_login(admin_user)
    payload = {**VALID_RULE_POST, "pattern": "[unclosed"}
    response = client.post(reverse("ai_detection:rule_create"), payload)
    assert response.status_code == 200
    assert b'role="alert"' in response.content
    assert not DetectionRule.objects.filter(name="new-rule").exists()


@pytest.mark.django_db
def test_toggle_flips_is_active_and_writes_audit(client, admin_user):
    rule = DetectionRuleFactory(is_active=True)
    client.force_login(admin_user)

    response = client.post(reverse("ai_detection:rule_toggle", args=[rule.pk]))

    assert response.status_code == 302
    rule.refresh_from_db()
    assert rule.is_active is False
    assert AuditEntry.objects.filter(action="detection_rule.toggle", object_id=str(rule.pk)).exists()


@pytest.mark.django_db
def test_htmx_toggle_returns_the_rules_fragment(client, admin_user):
    rule = DetectionRuleFactory(is_active=True)
    client.force_login(admin_user)

    response = client.post(reverse("ai_detection:rule_toggle", args=[rule.pk]), HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    assert b"<html" not in response.content
    rule.refresh_from_db()
    assert rule.is_active is False


@pytest.mark.django_db
def test_get_on_toggle_is_405(client, admin_user):
    rule = DetectionRuleFactory()
    client.force_login(admin_user)
    assert client.get(reverse("ai_detection:rule_toggle", args=[rule.pk])).status_code == 405


@pytest.mark.django_db
def test_htmx_request_returns_fragment_normal_request_returns_full_page(client, admin_user):
    client.force_login(admin_user)

    full_page = client.get(reverse("ai_detection:rules"))
    fragment = client.get(reverse("ai_detection:rules"), HTTP_HX_REQUEST="true")

    assert b"<html" in full_page.content
    assert b"<html" not in fragment.content
    assert b"Detection rules" in fragment.content


@pytest.mark.django_db
def test_rules_list_query_count(client, admin_user):
    for i in range(5):
        DetectionRuleFactory(name=f"rule-{i}")
    client.force_login(admin_user)

    with CaptureQueriesContext(connection) as ctx:
        response = client.get(reverse("ai_detection:rules"))
    assert response.status_code == 200
    assert len(ctx.captured_queries) < 10


# --- Dry run (T15) ---------------------------------------------------------


@pytest.mark.django_db
def test_dry_run_reports_matches_for_the_last_n_prs(client, admin_user):
    PullRequestFactory(body="Generated with Claude Code")
    PullRequestFactory(body="No AI mention here")
    client.force_login(admin_user)

    response = client.post(
        reverse("ai_detection:rule_dry_run"),
        {
            "detector": Detector.PR_BODY_FOOTER,
            "pattern": "Generated with Claude Code",
            "tool": Tool.CLAUDE_CODE,
            "confidence": Confidence.HIGH,
        },
    )

    assert response.status_code == 200
    assert b"1 match" in response.content


@pytest.mark.django_db
def test_dry_run_writes_no_signal_rows_and_leaves_ai_status_untouched(client, admin_user):
    pr = PullRequestFactory(body="Generated with Claude Code")
    client.force_login(admin_user)
    before_status = pr.ai_status
    before_count = AISignal.objects.count()

    client.post(
        reverse("ai_detection:rule_dry_run"),
        {
            "detector": Detector.PR_BODY_FOOTER,
            "pattern": "Generated with Claude Code",
            "tool": Tool.CLAUDE_CODE,
            "confidence": Confidence.HIGH,
        },
    )

    assert AISignal.objects.count() == before_count
    pr.refresh_from_db()
    assert pr.ai_status == before_status


@pytest.mark.django_db
def test_dry_run_zero_matches_renders_empty_state(client, admin_user):
    PullRequestFactory(body="Nothing to see here")
    client.force_login(admin_user)

    response = client.post(
        reverse("ai_detection:rule_dry_run"),
        {
            "detector": Detector.PR_BODY_FOOTER,
            "pattern": "Generated with Claude Code",
            "tool": Tool.CLAUDE_CODE,
            "confidence": Confidence.HIGH,
        },
    )

    assert response.status_code == 200
    assert b"No matches." in response.content


@pytest.mark.django_db
def test_dry_run_invalid_regex_renders_error_fragment(client, admin_user):
    client.force_login(admin_user)

    response = client.post(
        reverse("ai_detection:rule_dry_run"),
        {
            "detector": Detector.PR_BODY_FOOTER,
            "pattern": "[unclosed",
            "tool": Tool.CLAUDE_CODE,
            "confidence": Confidence.HIGH,
        },
    )

    assert response.status_code == 200
    assert b'role="alert"' in response.content


CANARY_ENGLISH_STRINGS = [
    ">Detection rules<",
    "New rule",
    "No detection rules yet.",
    ">Detector<",
    ">Pattern<",
    ">Confidence<",
    "Dry run",
    ">Run<",
    "No matches.",
]


@pytest.mark.django_db
def test_uk_render_has_no_canary_english(client, admin_user):
    client.force_login(admin_user)
    client.post(reverse("accounts:set_language"), {"language": "uk", "next": "/"})
    DetectionRuleFactory()

    response = client.get(reverse("ai_detection:rules"))
    content = response.content.decode()
    for canary in CANARY_ENGLISH_STRINGS:
        assert canary not in content, f"untranslated English string {canary!r} leaked into uk render"


@pytest.mark.django_db
def test_dry_run_query_count_does_not_grow_per_pr(client, admin_user):
    for i in range(20):
        PullRequestFactory(body=f"PR body number {i}")
    client.force_login(admin_user)

    with CaptureQueriesContext(connection) as ctx:
        response = client.post(
            reverse("ai_detection:rule_dry_run"),
            {
                "detector": Detector.PR_BODY_FOOTER,
                "pattern": "Generated with Claude Code",
                "tool": Tool.CLAUDE_CODE,
                "confidence": Confidence.HIGH,
            },
        )
    assert response.status_code == 200
    assert len(ctx.captured_queries) < 15


@pytest.mark.django_db
def test_unsaved_rule_can_be_dry_run(client, admin_user):
    assert not DetectionRule.objects.filter(pattern="Generated with Claude Code").exists()
    client.force_login(admin_user)

    response = client.post(
        reverse("ai_detection:rule_dry_run"),
        {
            "detector": Detector.PR_BODY_FOOTER,
            "pattern": "Generated with Claude Code",
            "tool": Tool.CLAUDE_CODE,
            "confidence": Confidence.HIGH,
        },
    )

    assert response.status_code == 200
    assert not DetectionRule.objects.filter(pattern="Generated with Claude Code").exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("detector", "pattern"),
    [
        (Detector.FILE_PATH, r"^\.claude/settings\.local\.json$"),
        (Detector.PR_TITLE, r"^codex\s*:\s"),
        (Detector.REVIEWER_IDENTITY, r"^coderabbitai(\[bot\])?$"),
        (Detector.MERGED_BY_IDENTITY, r"^devin-ai-integration(\[bot\])?$"),
    ],
)
def test_dry_run_works_for_each_detector_added_in_phase_12(client, admin_user, detector, pattern):
    reviewer = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="coderabbitai[bot]")
    merger = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="devin-ai-integration[bot]")
    pr = PullRequestFactory(title="Codex: fix the flaky sync test", merged_by=merger)
    PRFileFactory(pull_request=pr, path=".claude/settings.local.json")
    ReviewFactory(pull_request=pr, reviewer=reviewer, submitted_at=timezone.now())
    client.force_login(admin_user)

    response = client.post(
        reverse("ai_detection:rule_dry_run"),
        {
            "detector": detector,
            "pattern": pattern,
            "tool": Tool.OTHER,
            "confidence": Confidence.LOW,
        },
    )

    assert response.status_code == 200
    assert b"1 match" in response.content
