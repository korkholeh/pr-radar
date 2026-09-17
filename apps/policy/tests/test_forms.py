import datetime

import pytest

from apps.catalog.factories import ProjectFactory
from apps.policy.factories import PolicyViolationFactory
from apps.policy.forms import (
    AIPolicyForm,
    BulkViolationActionForm,
    SensitivePathRuleForm,
    ViolationFilterForm,
)
from apps.policy.models import PolicyViolation

pytestmark = pytest.mark.django_db


def test_empty_comment_is_rejected():
    violation = PolicyViolationFactory()
    form = BulkViolationActionForm(
        data={"violation_ids": [violation.pk], "action": "acknowledge", "comment": ""},
        violations_queryset=PolicyViolation.objects.all(),
    )
    assert not form.is_valid()
    assert "comment" in form.errors


def test_whitespace_only_comment_is_rejected():
    violation = PolicyViolationFactory()
    form = BulkViolationActionForm(
        data={"violation_ids": [violation.pk], "action": "acknowledge", "comment": "   "},
        violations_queryset=PolicyViolation.objects.all(),
    )
    assert not form.is_valid()
    assert "comment" in form.errors


def test_out_of_scope_violation_id_is_refused():
    in_scope = PolicyViolationFactory()
    out_of_scope = PolicyViolationFactory()
    form = BulkViolationActionForm(
        data={"violation_ids": [out_of_scope.pk], "action": "acknowledge", "comment": "reviewed"},
        violations_queryset=PolicyViolation.objects.filter(pk=in_scope.pk),
    )
    assert not form.is_valid()
    assert "violation_ids" in form.errors


def test_unknown_rule_code_is_dropped_not_an_error():
    form = ViolationFilterForm(data={"rule_code": "NOT_A_REAL_CODE"})
    assert form.is_valid()
    assert form.cleaned_data["rule_code"] == ""


def test_out_of_scope_project_id_in_filter_is_dropped_not_an_error():
    form = ViolationFilterForm(data={"project": "999999"})
    assert form.is_valid()
    assert form.cleaned_data["project"] is None


def test_known_project_id_resolves():
    project = ProjectFactory()
    form = ViolationFilterForm(data={"project": str(project.pk)})
    assert form.is_valid()
    assert form.cleaned_data["project"] == project


def test_date_from_after_date_to_is_clamped():
    form = ViolationFilterForm(data={"date_from": "2026-06-10", "date_to": "2026-06-01"})
    assert form.is_valid()
    assert form.cleaned_data["date_from"] == datetime.date(2026, 6, 1)
    assert form.cleaned_data["date_to"] == datetime.date(2026, 6, 10)


def test_default_status_is_open_when_none_selected():
    form = ViolationFilterForm(data={})
    assert form.is_valid()
    assert form.cleaned_data["status"] == [PolicyViolation.Status.OPEN]


def test_ai_policy_form_has_no_editable_effective_from():
    # a version always takes effect when it is saved (services.save_policy_version stamps
    # timezone.now()); backdating or scheduling one is not offered on the form.
    form = AIPolicyForm()
    assert "effective_from" not in form.fields


def test_ai_policy_form_rejects_negative_min_human_approvals():
    form = AIPolicyForm(
        data={
            "allowed_tools": [],
            "require_disclosure": False,
            "require_human_approval": False,
            "min_human_approvals": -1,
            "require_tests_for_ai_prs": False,
        }
    )
    assert not form.is_valid()
    assert "min_human_approvals" in form.errors


def test_sensitive_path_rule_form_rejects_an_empty_glob():
    form = SensitivePathRuleForm(data={"glob": "", "ai_mode": "forbidden", "is_active": True})
    assert not form.is_valid()
    assert "glob" in form.errors
