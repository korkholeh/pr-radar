import pytest
from django.urls import reverse

from apps.accounts.models import AuditEntry
from apps.policy.factories import PolicyViolationFactory
from apps.policy.models import PolicyViolation

pytestmark = pytest.mark.django_db

Status = PolicyViolation.Status


def test_acknowledging_two_rows_updates_both_and_writes_two_audit_entries(client, lead_user):
    first = PolicyViolationFactory(status=Status.OPEN)
    second = PolicyViolationFactory(status=Status.OPEN)
    client.force_login(lead_user)

    response = client.post(
        reverse("policy:violation_bulk_action"),
        {"violation_ids": [first.pk, second.pk], "action": "acknowledge", "comment": "known issue"},
    )

    assert response.status_code == 200
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == Status.ACKNOWLEDGED
    assert second.status == Status.ACKNOWLEDGED
    assert AuditEntry.objects.filter(action="policy_violation.acknowledge").count() == 2
    assert b"Updated 2 violations." in response.content


def test_missing_comment_rerenders_with_visible_error_and_changes_nothing(client, lead_user):
    violation = PolicyViolationFactory(status=Status.OPEN)
    client.force_login(lead_user)

    response = client.post(
        reverse("policy:violation_bulk_action"),
        {"violation_ids": [violation.pk], "action": "acknowledge", "comment": ""},
    )

    assert response.status_code == 200
    assert b'role="alert"' in response.content
    violation.refresh_from_db()
    assert violation.status == Status.OPEN


def test_get_on_the_action_url_is_405(client, lead_user):
    client.force_login(lead_user)
    assert client.get(reverse("policy:violation_bulk_action")).status_code == 405


def test_resolved_row_in_selection_is_reported_as_skipped(client, lead_user):
    resolved = PolicyViolationFactory(status=Status.RESOLVED)
    open_violation = PolicyViolationFactory(status=Status.OPEN)
    client.force_login(lead_user)

    response = client.post(
        reverse("policy:violation_bulk_action"),
        {"violation_ids": [resolved.pk, open_violation.pk], "action": "waive", "comment": "accepted"},
    )

    assert response.status_code == 200
    resolved.refresh_from_db()
    open_violation.refresh_from_db()
    assert resolved.status == Status.RESOLVED
    assert open_violation.status == Status.WAIVED
    assert b"skipped 1 resolved violation" in response.content


def test_htmx_post_returns_fragment_normal_post_returns_full_page(client, lead_user):
    """CLAUDE.md: the same URL returns a full page for a normal request and a fragment for an
    htmx one. A plain POST here (e.g. a form submit with JS disabled) must not navigate to a
    bare table fragment with no `<html>`, nav or styling."""
    violation = PolicyViolationFactory(status=Status.OPEN)
    client.force_login(lead_user)
    payload = {"violation_ids": [violation.pk], "action": "acknowledge", "comment": "known issue"}

    fragment = client.post(reverse("policy:violation_bulk_action"), payload, HTTP_HX_REQUEST="true")
    violation.refresh_from_db()
    assert violation.status == Status.ACKNOWLEDGED
    assert b"<html" not in fragment.content

    violation.status = Status.OPEN
    violation.save(update_fields=["status"])
    full_page = client.post(reverse("policy:violation_bulk_action"), payload)
    violation.refresh_from_db()
    assert violation.status == Status.ACKNOWLEDGED
    assert b"<html" in full_page.content
    assert b"Updated 1 violation." in full_page.content


def test_out_of_scope_id_changes_nothing(client, lead_user):
    violation = PolicyViolationFactory(status=Status.OPEN)
    client.force_login(lead_user)

    response = client.post(
        reverse("policy:violation_bulk_action"),
        {"violation_ids": [violation.pk + 999], "action": "acknowledge", "comment": "known issue"},
    )

    assert response.status_code == 200
    assert b'role="alert"' in response.content
    violation.refresh_from_db()
    assert violation.status == Status.OPEN
