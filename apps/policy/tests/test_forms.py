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


# -- phase 12, stage 7: the standards fields ------------------------------------------------------


@pytest.mark.django_db
def test_ai_policy_form_parses_reviewer_logins_one_per_line():
    form = AIPolicyForm(
        data={
            "min_human_approvals": 1,
            "high_risk_min_approvals": 2,
            "ai_reviewer_logins": "@Copilot\ncopilot-pull-request-reviewer, gemini-code-assist",
        }
    )

    assert form.is_valid(), form.errors
    # The leading `@` a lead pastes from a GitHub mention is dropped; commas work as well as
    # newlines, because both are what people actually type.
    assert form.cleaned_data["ai_reviewer_identities"] == [
        "Copilot",
        "copilot-pull-request-reviewer",
        "gemini-code-assist",
    ]


@pytest.mark.django_db
def test_ai_policy_form_assembles_the_risk_limits_from_three_inputs():
    """A lead configuring a size limit per risk level should not have to type JSON."""
    form = AIPolicyForm(
        data={"min_human_approvals": 1, "high_risk_min_approvals": 2, "max_lines_high_risk": 400}
    )

    assert form.is_valid(), form.errors
    assert form.cleaned_data["max_effective_lines_by_risk"] == {"high": 400}


@pytest.mark.django_db
def test_ai_policy_form_leaves_the_risk_limits_empty_when_nothing_is_entered():
    """The empty dict is what keeps an upgrade quiet: no limit means nothing to exceed."""
    form = AIPolicyForm(data={"min_human_approvals": 1, "high_risk_min_approvals": 2})

    assert form.is_valid(), form.errors
    assert form.cleaned_data["max_effective_lines_by_risk"] == {}


@pytest.mark.django_db
def test_every_ai_policy_field_appears_in_a_fieldset():
    """A field missing from `FIELDSETS` lands in "Other" rather than vanishing, so this test is
    what keeps a new toggle from being merely misplaced."""
    form = AIPolicyForm()
    grouped = {field.name for _legend, fields in form.fieldsets() for field in fields}

    assert grouped == set(form.fields)
    assert "Other" not in {str(legend) for legend, _fields in form.fieldsets()}


@pytest.mark.django_db
def test_a_bot_cannot_be_a_designated_reviewer():
    from apps.catalog.factories import PersonFactory

    person = PersonFactory(is_bot=False)
    bot = PersonFactory(is_bot=True)

    queryset = AIPolicyForm().fields["designated_reviewers"].queryset

    assert person in queryset
    assert bot not in queryset


@pytest.mark.django_db
def test_sensitive_path_rule_form_accepts_an_advisory_rule_with_a_risk_level():
    form = SensitivePathRuleForm(
        data={"glob": "**/migrations/**", "ai_mode": "advisory", "risk_level": "high", "is_active": True}
    )

    assert form.is_valid(), form.errors
    assert form.cleaned_data["risk_level"] == "high"


@pytest.mark.django_db
def test_sensitive_path_rule_form_allows_a_rule_with_no_risk_level():
    """A rule written to forbid a path says nothing about how risky the path is, and guessing
    `low` for it would quietly exempt it from every risk-based limit."""
    form = SensitivePathRuleForm(data={"glob": "secrets/**", "ai_mode": "forbidden", "is_active": True})

    assert form.is_valid(), form.errors
    assert form.cleaned_data["risk_level"] == ""
