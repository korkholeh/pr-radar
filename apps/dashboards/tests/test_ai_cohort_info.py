"""The dashboards' "how a pull request gets into the AI cohort" block (`dashboards.ai_cohort_info`):
its text follows the live configuration (`ai_detection.services.cohort_rules()`)."""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import translation

from apps.ai_detection.models import Confidence, DetectionRule, Tool
from apps.ai_detection.services import cohort_rules
from apps.catalog.services import set_setting

PERIOD_QS = "preset=custom&from=2026-08-01&to=2026-08-31&granularity=week"


def _rule(name: str, tool: str, confidence: str, is_active: bool = True) -> DetectionRule:
    return DetectionRule.objects.create(
        name=name,
        detector="commit_trailer",
        pattern="x",
        tool=tool,
        confidence=confidence,
        is_active=is_active,
    )


@pytest.mark.django_db
def test_cohort_rules_lists_the_named_tools_of_active_high_confidence_rules_only():
    DetectionRule.objects.all().delete()
    _rule("claude trailer", Tool.CLAUDE_CODE, Confidence.HIGH)
    _rule("claude footer", Tool.CLAUDE_CODE, Confidence.HIGH)
    _rule("cursor trailer", Tool.CURSOR, Confidence.HIGH)
    _rule("windsurf branch", Tool.WINDSURF, Confidence.LOW)
    _rule("devin, switched off", Tool.DEVIN, Confidence.HIGH, is_active=False)
    _rule("unnamed marker", Tool.OTHER, Confidence.HIGH)

    with translation.override("en"):
        rules = cohort_rules()

    assert rules.high_confidence_tools == ("Claude Code", "Cursor")


@pytest.mark.django_db
def test_cohort_rules_reads_the_suspected_settings():
    set_setting("AI_COHORT_INCLUDE_SUSPECTED", False)
    set_setting("AI_SUSPECTED_MIN_STRUCTURAL_KINDS", 3)

    rules = cohort_rules()

    assert rules.include_suspected is False
    assert rules.min_structural_kinds == 3


@pytest.mark.django_db
def test_dashboard_explains_the_cohort_and_says_suspected_is_in_by_default(client, lead_user):
    client.force_login(lead_user)

    body = client.get(reverse("dashboards:overview") + f"?{PERIOD_QS}").content.decode()

    assert 'data-testid="ai-cohort-info"' in body
    assert "How a pull request gets into the AI cohort" in body
    assert "In the AI cohort: this installation counts suspected pull requests." in body
    # Only a lead who can edit the rules gets the links to them.
    start = body.index('data-testid="ai-cohort-info"')
    assert reverse("ai_detection:rules") not in body[start : body.index("</details>", start)]


@pytest.mark.django_db
def test_dashboard_says_suspected_is_out_when_the_setting_is_off(client, lead_user):
    set_setting("AI_COHORT_INCLUDE_SUSPECTED", False)
    client.force_login(lead_user)

    body = client.get(reverse("dashboards:overview") + f"?{PERIOD_QS}").content.decode()

    assert "Not in the AI cohort: it counts as non-AI." in body


@pytest.mark.django_db
def test_admin_gets_links_to_the_rules(client, django_user_model):
    admin = django_user_model.objects.create_superuser(username="admin", password="pw")
    client.force_login(admin)

    body = client.get(reverse("dashboards:overview") + f"?{PERIOD_QS}").content.decode()
    start = body.index('data-testid="ai-cohort-info"')
    block = body[start : body.index("</details>", start)]

    assert reverse("ai_detection:rules") in block
    assert reverse("ai_detection:signal_rules") in block
