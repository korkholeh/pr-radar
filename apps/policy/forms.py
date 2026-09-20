from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.ai_detection.models import Tool
from apps.catalog.models import Person, Project, Repository
from apps.policy.models import AIPolicy, PolicyViolation, SensitivePathRule
from apps.policy.services import STANDARDS_BOOLEAN_FIELDS
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
    neither backdate a policy over already-judged PRs nor schedule one silently for the future.

    Phase 12, stage 7 brought the form to twenty-odd switches, which is more than a flat list can
    carry, so `fieldsets` groups them the way a lead thinks about them: disclosure, review,
    description, quality gates, risk. The three risk limits and the reviewer logins are separate
    inputs rather than raw JSON — a lead configuring a policy should not have to type a dict.
    """

    allowed_tools = forms.MultipleChoiceField(choices=Tool.choices, required=False, label=_("Allowed tools"))
    ai_reviewer_logins = forms.CharField(
        required=False,
        label=_("AI reviewer logins"),
        help_text=_("One GitHub login per line, for example copilot-pull-request-reviewer."),
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    designated_reviewers = forms.ModelMultipleChoiceField(
        queryset=Person.objects.none(),
        required=False,
        label=_("Designated reviewers"),
        help_text=_("Their approval satisfies the extra review a migration needs."),
    )
    max_lines_low_risk = forms.IntegerField(
        required=False, min_value=1, label=_("Maximum effective lines, low risk")
    )
    max_lines_medium_risk = forms.IntegerField(
        required=False, min_value=1, label=_("Maximum effective lines, medium risk")
    )
    max_lines_high_risk = forms.IntegerField(
        required=False, min_value=1, label=_("Maximum effective lines, high risk")
    )

    _RISK_LIMIT_FIELDS = {
        "low": "max_lines_low_risk",
        "medium": "max_lines_medium_risk",
        "high": "max_lines_high_risk",
    }

    FIELDSETS: tuple[tuple[str, tuple[str, ...]], ...] = (
        (
            _("Disclosure and tools"),
            ("require_disclosure", "allowed_tools"),
        ),
        (
            _("Human review"),
            (
                "require_human_approval",
                "min_human_approvals",
                "high_risk_min_approvals",
                "forbid_ai_only_approval",
                "forbid_rubber_stamp_approval",
                "designated_reviewers",
            ),
        ),
        (
            _("AI review"),
            ("require_ai_review_first", "require_ai_comments_resolved", "ai_reviewer_logins"),
        ),
        (
            _("What a description must state"),
            (
                "require_risk_level",
                "require_verification_note",
                "require_task_link",
                "require_high_risk_plan",
            ),
        ),
        (
            _("Quality gates and tests"),
            (
                "forbid_ci_bypass",
                "forbid_test_weakening",
                "require_tests_for_ai_prs",
                "forbid_secret_artifacts",
            ),
        ),
        (
            _("Size and scope"),
            (
                "ai_pr_max_effective_lines",
                "max_lines_low_risk",
                "max_lines_medium_risk",
                "max_lines_high_risk",
                "forbid_scope_creep",
                "flag_new_dependencies_in_ai_prs",
                "flag_agent_config_changes",
            ),
        ),
    )

    class Meta:
        model = AIPolicy
        fields = [
            "allowed_tools",
            "require_disclosure",
            "require_human_approval",
            "min_human_approvals",
            "require_tests_for_ai_prs",
            "ai_pr_max_effective_lines",
            *STANDARDS_BOOLEAN_FIELDS,
            "high_risk_min_approvals",
        ]
        labels = {
            "require_disclosure": _("Require disclosure"),
            "require_human_approval": _("Require human approval"),
            "min_human_approvals": _("Minimum human approvals"),
            "require_tests_for_ai_prs": _("Require tests for AI PRs"),
            "ai_pr_max_effective_lines": _("Maximum effective lines for an AI PR"),
            "require_ai_review_first": _("Require a review from an AI reviewer"),
            "forbid_ai_only_approval": _("Forbid merging with only bot approvals"),
            "require_ai_comments_resolved": _("Require AI review threads to be resolved"),
            "require_risk_level": _("Require a stated risk level"),
            "require_task_link": _("Require a linked task"),
            "require_verification_note": _("Require a note on how the change was verified"),
            "require_high_risk_plan": _("Require a plan for high-risk changes"),
            "forbid_ci_bypass": _("Forbid bypassing the quality gate"),
            "forbid_test_weakening": _("Forbid weakening tests"),
            "forbid_secret_artifacts": _("Forbid committing credential files"),
            "forbid_scope_creep": _("Forbid scope creep in AI PRs"),
            "forbid_rubber_stamp_approval": _("Forbid rubber-stamp approval of AI PRs"),
            "flag_agent_config_changes": _("Flag agent configuration changes"),
            "flag_new_dependencies_in_ai_prs": _("Flag new dependencies in AI PRs"),
            "high_risk_min_approvals": _("Minimum approvals for a high-risk change"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Bots cannot be designated reviewers: the whole point of the designation is that a named
        # person looked at the migration.
        self.fields["designated_reviewers"].queryset = Person.objects.filter(
            is_active=True, is_bot=False
        ).order_by("display_name")

        instance = kwargs.get("instance") or getattr(self, "instance", None)
        if instance is not None and instance.pk:
            self.fields["ai_reviewer_logins"].initial = "\n".join(instance.ai_reviewer_identities or [])
            limits = instance.max_effective_lines_by_risk or {}
            for level, field_name in self._RISK_LIMIT_FIELDS.items():
                self.fields[field_name].initial = limits.get(level)

    def fieldsets(self):
        """`(legend, [bound field])` pairs for the template. A field missing from `FIELDSETS` would
        vanish from the page, so the last group collects whatever was not named — a new toggle is
        then visible and misplaced rather than invisible and forgotten."""
        named = {name for _legend, names in self.FIELDSETS for name in names}
        groups = [
            (legend, [self[name] for name in names if name in self.fields])
            for legend, names in self.FIELDSETS
        ]
        leftovers = [self[name] for name in self.fields if name not in named]
        if leftovers:
            groups.append((_("Other"), leftovers))
        return groups

    def clean(self) -> dict:
        cleaned = super().clean()
        logins = [
            line.strip().lstrip("@")
            for line in (cleaned.get("ai_reviewer_logins") or "").replace(",", "\n").splitlines()
            if line.strip()
        ]
        cleaned["ai_reviewer_identities"] = logins
        cleaned["max_effective_lines_by_risk"] = {
            level: cleaned.get(field_name)
            for level, field_name in self._RISK_LIMIT_FIELDS.items()
            if cleaned.get(field_name)
        }
        return cleaned


class SensitivePathRuleForm(forms.ModelForm):
    class Meta:
        model = SensitivePathRule
        fields = ["project", "glob", "ai_mode", "risk_level", "description", "is_active"]
        labels = {
            "project": _("Project"),
            "glob": _("Path glob"),
            "ai_mode": _("AI mode"),
            "risk_level": _("Risk level"),
            "description": _("Description"),
            "is_active": _("Is active"),
        }
        help_texts = {
            "ai_mode": _("Advisory classifies the path's risk without raising a violation of its own."),
            "risk_level": _(
                "A pull request's risk is the highest of the paths it touches. Leave empty for a "
                "rule that says nothing about risk."
            ),
        }
