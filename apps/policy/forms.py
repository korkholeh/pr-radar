from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.ai_detection.models import Tool
from apps.catalog.models import Project, Repository
from apps.policy.models import AIPolicy, PolicyViolation, SensitivePathRule
from config.forms import date_widget


class _LenientChoiceField(forms.ChoiceField):
    """A value outside `choices` is silently dropped by the owning form's `clean_<field>`,
    rather than becoming a form error — RISKS row 3's rule for a query-string filter."""

    def validate(self, value):
        pass


class _LenientMultipleChoiceField(forms.MultipleChoiceField):
    """A value outside `choices` is silently dropped by the owning form's `clean_<field>`,
    rather than becoming a form error — RISKS row 3's rule for a query-string filter."""

    def validate(self, value):
        pass


class _LenientModelChoiceField(forms.ModelChoiceField):
    """A pk outside the queryset (or scope) is dropped silently instead of becoming a form
    error — RISKS row 3's rule for a query-string filter."""

    def to_python(self, value):
        try:
            return super().to_python(value)
        except forms.ValidationError:
            return None


class ViolationFilterForm(forms.Form):
    """Parses `request.GET` for the Policy console (RISKS row 3: nothing is read from a query
    string without a form). An unknown or out-of-scope id is dropped, not a validation error, so
    a stale bookmark never breaks the page."""

    rule_code = _LenientChoiceField(
        choices=[("", _("All rules")), *PolicyViolation.RuleCode.choices], required=False
    )
    severity = _LenientChoiceField(
        choices=[("", _("All severities")), *PolicyViolation.Severity.choices], required=False
    )
    status = _LenientMultipleChoiceField(choices=PolicyViolation.Status.choices, required=False)
    project = _LenientModelChoiceField(
        queryset=Project.objects.order_by("name"), required=False, empty_label=_("All projects")
    )
    repository = _LenientModelChoiceField(
        queryset=Repository.objects.order_by("full_name"), required=False, empty_label=_("All repositories")
    )
    date_from = forms.DateField(required=False, widget=date_widget())
    date_to = forms.DateField(required=False, widget=date_widget())
    q = forms.CharField(required=False)

    def __init__(self, *args, projects=None, repositories=None, **kwargs):
        """`projects`/`repositories` let a caller pass an already-scoped queryset (the view does,
        via `policy.selectors.projects_in_scope`/`repositories_in_scope`); omitted, the field
        keeps its class-level unscoped default, which is what direct construction in tests uses."""
        super().__init__(*args, **kwargs)
        if projects is not None:
            self.fields["project"].queryset = projects
        if repositories is not None:
            self.fields["repository"].queryset = repositories

    def clean_rule_code(self) -> str:
        value = self.cleaned_data.get("rule_code", "")
        return value if value in PolicyViolation.RuleCode.values else ""

    def clean_severity(self) -> str:
        value = self.cleaned_data.get("severity", "")
        return value if value in PolicyViolation.Severity.values else ""

    def clean_status(self) -> list[str]:
        values = [v for v in self.cleaned_data.get("status") or [] if v in PolicyViolation.Status.values]
        return values or [PolicyViolation.Status.OPEN]

    def clean(self) -> dict:
        cleaned = super().clean()
        date_from = cleaned.get("date_from")
        date_to = cleaned.get("date_to")
        if date_from and date_to and date_from > date_to:
            cleaned["date_from"], cleaned["date_to"] = date_to, date_from
        return cleaned


class BulkViolationActionForm(forms.Form):
    """`violation_ids` is bound to `violations_in_scope(scope)`, so an id outside the caller's
    scope is a validation error (refused), unlike `ViolationFilterForm`'s ids, which are
    read-only and safe to drop."""

    ACTION_CHOICES = [
        ("acknowledge", _("Acknowledge")),
        ("waive", _("Waive")),
    ]

    violation_ids = forms.ModelMultipleChoiceField(
        queryset=PolicyViolation.objects.none(), label=_("Violations")
    )
    action = forms.ChoiceField(choices=ACTION_CHOICES, label=_("Action"))
    comment = forms.CharField(
        label=_("Comment"),
        min_length=3,
        widget=forms.Textarea,
        error_messages={"min_length": _("A comment of at least 3 characters is required.")},
    )

    def __init__(self, *args, violations_queryset, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["violation_ids"].queryset = violations_queryset


class AIPolicyForm(forms.ModelForm):
    """`effective_from` is deliberately not a field here: a version always takes effect at the
    moment it is saved (`services.save_policy_version` stamps `timezone.now()`), so an admin can
    neither backdate a policy over already-judged PRs nor schedule one silently for the future."""

    allowed_tools = forms.MultipleChoiceField(choices=Tool.choices, required=False, label=_("Allowed tools"))

    class Meta:
        model = AIPolicy
        fields = [
            "allowed_tools",
            "require_disclosure",
            "require_human_approval",
            "min_human_approvals",
            "require_tests_for_ai_prs",
            "ai_pr_max_effective_lines",
        ]
        labels = {
            "require_disclosure": _("Require disclosure"),
            "require_human_approval": _("Require human approval"),
            "min_human_approvals": _("Minimum human approvals"),
            "require_tests_for_ai_prs": _("Require tests for AI PRs"),
            "ai_pr_max_effective_lines": _("Maximum effective lines for an AI PR"),
        }


class SensitivePathRuleForm(forms.ModelForm):
    class Meta:
        model = SensitivePathRule
        fields = ["project", "glob", "ai_mode", "description", "is_active"]
        labels = {
            "project": _("Project"),
            "glob": _("Path glob"),
            "ai_mode": _("AI mode"),
            "description": _("Description"),
            "is_active": _("Is active"),
        }
