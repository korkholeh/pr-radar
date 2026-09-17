import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import AuditEntry
from apps.policy.factories import AIPolicyFactory
from apps.policy.models import AIPolicy

pytestmark = pytest.mark.django_db

VALID_POST = {
    "allowed_tools": ["claude_code"],
    "require_disclosure": "on",
    "require_human_approval": "on",
    "min_human_approvals": 1,
    "require_tests_for_ai_prs": "on",
    "ai_pr_max_effective_lines": 500,
    "effective_from": "2026-01-01 00:00:00",
}


def test_lead_gets_403_admin_gets_200(client, lead_user, admin_user):
    client.force_login(lead_user)
    assert client.get(reverse("policy:policy_settings")).status_code == 403

    client.force_login(admin_user)
    assert client.get(reverse("policy:policy_settings")).status_code == 200


def test_lead_sees_no_nav_link_admin_does(client, lead_user, admin_user):
    client.force_login(lead_user)
    assert b"AI policy" not in client.get(reverse("dashboards:overview")).content

    client.force_login(admin_user)
    assert b"AI policy" in client.get(reverse("dashboards:overview")).content


def test_saving_creates_a_new_version_and_writes_audit(client, admin_user):
    previous = AIPolicyFactory(effective_from=timezone.make_aware(datetime.datetime(2025, 1, 1)))
    client.force_login(admin_user)

    response = client.post(reverse("policy:policy_settings"), VALID_POST)

    assert response.status_code == 302
    assert AIPolicy.objects.count() == 2
    newest = AIPolicy.objects.order_by("-effective_from").first()
    assert newest.pk != previous.pk
    assert newest.min_human_approvals == 1
    entry = AuditEntry.objects.get(action="ai_policy.update", object_id=str(newest.pk))
    assert entry.changes["before"]["effective_from"] == previous.effective_from.isoformat()
    assert entry.changes["after"]["min_human_approvals"] == 1


def test_history_lists_both_versions_newest_first(client, admin_user):
    older = AIPolicyFactory(
        effective_from=timezone.make_aware(datetime.datetime(2025, 1, 1)), min_human_approvals=3
    )
    newer = AIPolicyFactory(
        effective_from=timezone.make_aware(datetime.datetime(2025, 6, 1)), min_human_approvals=7
    )
    client.force_login(admin_user)

    response = client.get(reverse("policy:policy_settings"))

    content = response.content.decode()
    assert content.index(f'id="policy-version-{newer.pk}"') < content.index(f'id="policy-version-{older.pk}"')


def test_invalid_form_rerenders_with_visible_errors_and_creates_nothing(client, admin_user):
    client.force_login(admin_user)

    payload = {**VALID_POST, "min_human_approvals": -1}
    response = client.post(reverse("policy:policy_settings"), payload)

    assert response.status_code == 200
    assert b'role="alert"' in response.content
    assert AIPolicy.objects.count() == 0
