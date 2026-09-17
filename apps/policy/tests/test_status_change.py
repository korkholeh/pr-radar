import pytest

from apps.accounts.factories import UserFactory
from apps.accounts.models import AuditEntry
from apps.policy.factories import PolicyViolationFactory
from apps.policy.models import PolicyViolation
from apps.policy.services import (
    BulkStatusChangeResult,
    ViolationResolvedError,
    apply_bulk_status_change,
    apply_status_change,
)

pytestmark = pytest.mark.django_db

Status = PolicyViolation.Status


def test_acknowledge_persists_status_resolver_and_comment():
    user = UserFactory()
    violation = PolicyViolationFactory(status=Status.OPEN)

    apply_status_change(user, violation, "acknowledge", "known issue")

    violation.refresh_from_db()
    assert violation.status == Status.ACKNOWLEDGED
    assert violation.resolved_by == user
    assert violation.resolution_comment == "known issue"
    assert violation.resolved_automatically is False


def test_waive_persists_status_resolver_and_comment():
    user = UserFactory()
    violation = PolicyViolationFactory(status=Status.OPEN)

    apply_status_change(user, violation, "waive", "accepted risk")

    violation.refresh_from_db()
    assert violation.status == Status.WAIVED
    assert violation.resolved_by == user
    assert violation.resolution_comment == "accepted risk"


def test_acknowledge_writes_one_audit_entry_with_actor_and_before_after():
    user = UserFactory()
    violation = PolicyViolationFactory(status=Status.OPEN)

    apply_status_change(user, violation, "acknowledge", "known issue")

    entries = AuditEntry.objects.filter(
        object_type="PolicyViolation", object_id=str(violation.pk), action="policy_violation.acknowledge"
    )
    assert entries.count() == 1
    entry = entries.get()
    assert entry.actor == user
    assert entry.created_at is not None
    assert entry.changes["before"]["status"] == Status.OPEN
    assert entry.changes["after"]["status"] == Status.ACKNOWLEDGED
    assert entry.changes["after"]["comment"] == "known issue"


def test_waive_writes_one_audit_entry():
    user = UserFactory()
    violation = PolicyViolationFactory(status=Status.OPEN)

    apply_status_change(user, violation, "waive", "accepted risk")

    assert (
        AuditEntry.objects.filter(
            object_type="PolicyViolation", object_id=str(violation.pk), action="policy_violation.waive"
        ).count()
        == 1
    )


def test_acknowledged_and_waived_switch_between_each_other():
    user = UserFactory()
    violation = PolicyViolationFactory(status=Status.ACKNOWLEDGED)

    apply_status_change(user, violation, "waive", "changed my mind")
    violation.refresh_from_db()
    assert violation.status == Status.WAIVED

    apply_status_change(user, violation, "acknowledge", "changed my mind again")
    violation.refresh_from_db()
    assert violation.status == Status.ACKNOWLEDGED


def test_resolved_violation_is_refused_not_silently_skipped():
    user = UserFactory()
    violation = PolicyViolationFactory(status=Status.RESOLVED)

    with pytest.raises(ViolationResolvedError):
        apply_status_change(user, violation, "acknowledge", "too late")

    violation.refresh_from_db()
    assert violation.status == Status.RESOLVED
    assert not AuditEntry.objects.filter(object_type="PolicyViolation", object_id=str(violation.pk)).exists()


def test_bulk_action_over_three_rows_writes_three_audit_entries():
    user = UserFactory()
    violations = [PolicyViolationFactory(status=Status.OPEN) for _ in range(3)]

    result = apply_bulk_status_change(user, violations, "acknowledge", "batch review")

    assert result == BulkStatusChangeResult(updated=3, skipped_resolved=0)
    for violation in violations:
        violation.refresh_from_db()
        assert violation.status == Status.ACKNOWLEDGED
    assert (
        AuditEntry.objects.filter(
            object_type="PolicyViolation", action="policy_violation.acknowledge"
        ).count()
        == 3
    )


def test_bulk_action_reports_a_resolved_row_as_skipped_and_still_updates_the_rest():
    user = UserFactory()
    open_violation = PolicyViolationFactory(status=Status.OPEN)
    resolved_violation = PolicyViolationFactory(status=Status.RESOLVED)

    result = apply_bulk_status_change(
        user, [open_violation, resolved_violation], "acknowledge", "batch review"
    )

    assert result == BulkStatusChangeResult(updated=1, skipped_resolved=1)
    open_violation.refresh_from_db()
    resolved_violation.refresh_from_db()
    assert open_violation.status == Status.ACKNOWLEDGED
    assert resolved_violation.status == Status.RESOLVED
