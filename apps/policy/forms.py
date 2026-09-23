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

    Every switch carries a help text, and every group a sentence of its own: a lead deciding
    whether to turn something on needs to know which rule code it raises, at what severity, on
    which pull requests, and what else has to be true for it to fire at all. `docs/POLICY.md` is
    the long form of the same contract, and `tests/test_docs.py` holds it to the code; these are
    the two or three lines of it that belong next to the checkbox itself.
    """

    allowed_tools = forms.MultipleChoiceField(
        choices=Tool.choices,
        required=False,
        label=_("Allowed tools"),
        help_text=_(
            "Raises TOOL_NOT_ALLOWED (high) once per tool that a pull request declares or that "
            "detection names, and that is not selected here. Signals that name no tool (commit "
            "bursts, mass file creation and the like) never raise it, so Other applies only when "
            "the author declares it. An empty selection allows every tool: nothing is forbidden "
            "until something is allowed."
        ),
    )
    ai_reviewer_logins = forms.CharField(
        required=False,
        label=_("AI reviewer logins"),
        help_text=_(
            "One GitHub login per line, for example copilot-pull-request-reviewer. These logins "
            "are what the two checks above look for; while this box is empty, neither can fire."
        ),
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    designated_reviewers = forms.ModelMultipleChoiceField(
        queryset=Person.objects.none(),
        required=False,
        label=_("Designated reviewers"),
        help_text=_(
            "Raises MIGRATION_AI_INSUFFICIENT_REVIEW (high) on a merged AI pull request that "
            "carries a database migration none of these people approved. Naming nobody leaves "
            "the check switched off, because no approval could then satisfy it. Bots cannot be "
            "designated: the point of the designation is that a named person read the migration."
        ),
    )
    max_lines_low_risk = forms.IntegerField(
        required=False,
        min_value=1,
        label=_("Maximum effective lines, low risk"),
        help_text=_(
            "Replaces the limit above for a change whose sensitive paths put it at low risk. "
            "Empty means the flat limit applies."
        ),
    )
    max_lines_medium_risk = forms.IntegerField(
        required=False,
        min_value=1,
        label=_("Maximum effective lines, medium risk"),
        help_text=_(
            "Replaces the limit above for a medium-risk change. The PLANEKS standards suggest "
            "800 lines here; empty means the flat limit applies."
        ),
    )
    max_lines_high_risk = forms.IntegerField(
        required=False,
        min_value=1,
        label=_("Maximum effective lines, high risk"),
        help_text=_(
            "Replaces the limit above for a high-risk change. The PLANEKS standards suggest 400 "
            "lines here; empty means the flat limit applies."
        ),
    )

    _RISK_LIMIT_FIELDS = {
        "low": "max_lines_low_risk",
        "medium": "max_lines_medium_risk",
        "high": "max_lines_high_risk",
    }

    FIELDSETS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
        (
            _("Disclosure and tools"),
            _(
                "What a pull request has to say about the AI that helped write it, and which "
                "tools may be used at all. Detection runs either way: these switches decide "
                "whether a gap between what was declared and what was detected is written down "
                "as a violation."
            ),
            ("require_disclosure", "allowed_tools"),
        ),
        (
            _("Human review"),
            _(
                "How many people must have read an AI change before it merges, and whose "
                "approval counts. An approval from the author or from a bot account is never a "
                "human approval here."
            ),
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
            _(
                "The checks about the AI reviewer itself: that it ran, and that its comments "
                "were answered. Both need the reviewer logins below to be filled in, and both "
                "are judged only once a pull request has merged."
            ),
            ("require_ai_review_first", "require_ai_comments_resolved", "ai_reviewer_logins"),
        ),
        (
            _("What a description must state"),
            _(
                "Sections a pull request body has to carry. The headings that count are "
                "configurable in Settings; a section that exists but is empty counts as missing."
            ),
            (
                "require_risk_level",
                "require_verification_note",
                "require_task_link",
                "require_high_risk_plan",
            ),
        ),
        (
            _("Quality gates and tests"),
            _(
                "Whether the safety net around a change was left intact. These apply to every "
                "pull request, not only AI ones: a bypassed gate or a deleted test is no better "
                "for having been written by hand."
            ),
            (
                "forbid_ci_bypass",
                "forbid_test_weakening",
                "require_tests_for_ai_prs",
                "forbid_secret_artifacts",
            ),
        ),
        (
            _("Size and scope"),
            _(
                "How large an AI pull request may be and how far it may reach. Effective lines "
                "leave out generated and vendored files, so the limits count the code somebody "
                "actually has to review."
            ),
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
        # Each of these says the same four things: which rule code the switch raises, at what
        # severity, which pull requests it looks at, and what else has to be true before it can
        # fire at all. The severities are `rules.SEVERITY`; the long form is `docs/POLICY.md`.
        help_texts = {
            "require_disclosure": _(
                "Raises DISCLOSURE_MISSING (medium) on a pull request whose description neither "
                "states that AI helped nor states that it did not. Switched off, disclosures are "
                "still read and charted; nothing is raised. DISCLOSURE_MISMATCH — a description "
                "that says 'no AI' on a change detection is highly confident about — is raised "
                "either way, because a contradiction is always worth a look."
            ),
            "require_human_approval": _(
                "Raises NO_HUMAN_APPROVAL (high) on a merged AI pull request with fewer human "
                "approvals than the minimum below. Judged on merge only: an open pull request can "
                "still get its review."
            ),
            "min_human_approvals": _(
                "How many different people have to approve an AI pull request. A path a "
                "sensitive-path rule marks as needing extra review asks for one more than this."
            ),
            "high_risk_min_approvals": _(
                "Used instead of the minimum above when the change touches a path a "
                "sensitive-path rule marks high risk. It only ever raises the requirement: a "
                "lower number here than above changes nothing."
            ),
            "forbid_ai_only_approval": _(
                "Raises AI_ONLY_APPROVAL (high) when every approval on a merged pull request came "
                "from a bot account and none from a person. Applies to every pull request, not "
                "only AI ones."
            ),
            "forbid_rubber_stamp_approval": _(
                "Raises RUBBER_STAMP_ON_AI_PR (high) for a merged AI pull request approved with "
                "an empty review, no comments, within the rubber-stamp window configured in "
                "Settings."
            ),
            "require_ai_review_first": _(
                "Raises AI_REVIEW_MISSING (medium) when a merged pull request was never reviewed "
                "by any of the logins listed below. Silent while that list is empty: the policy "
                "cannot require a reviewer nobody named."
            ),
            "require_ai_comments_resolved": _(
                "Raises AI_REVIEW_UNRESOLVED (medium) when a pull request merged with a comment "
                "thread from an AI reviewer that GitHub reports as unresolved. A thread whose "
                "state GitHub never reported is not counted."
            ),
            "require_risk_level": _(
                "Raises RISK_LEVEL_MISSING (low) when the description carries no risk section "
                "under any of the recognised headings."
            ),
            "require_verification_note": _(
                "Raises VERIFICATION_MISSING (low) when the description says nothing about how "
                "the change was checked — tests run, manual steps, what was observed."
            ),
            "require_task_link": _(
                "Raises TASK_LINK_MISSING (low) when no task reference appears in the title or "
                "anywhere in the body. No section is needed: '[ENG-175]' in the title or "
                "'closes #431' on the first line of the body satisfies it."
            ),
            "require_high_risk_plan": _(
                "Raises HIGH_RISK_NO_PLAN (high) when a high-risk change states no plan, risks or "
                "rollback. Needs a sensitive-path rule with risk level High to match the change, "
                "so it stays silent until those rules are in place."
            ),
            "forbid_ci_bypass": _(
                "Raises QUALITY_GATE_BYPASSED (high), once per reason, on a pull request merged "
                "with red checks, with a skip-CI marker in a commit message, with the CI "
                "configuration relaxed, or with a CI step removed. The last two reasons need the "
                "repository to be opted in to diff analysis."
            ),
            "forbid_test_weakening": _(
                "Raises TEST_WEAKENED (high) when a test file is deleted while non-test code "
                "changes, a skip or xfail marker is added, or assertions are removed with none "
                "added back. Deleting a test on its own is housekeeping and passes."
            ),
            "require_tests_for_ai_prs": _(
                "Raises NO_TESTS (low) on an AI pull request that changes more non-test lines "
                "than the threshold configured in Settings and touches no test at all."
            ),
            "forbid_secret_artifacts": _(
                "Raises SECRET_ARTIFACT_COMMITTED (high) when a credential file, a private key or "
                "an agent's own chat history is committed. Decided from the path alone — the "
                "contents of the file are never read, logged or stored."
            ),
            "ai_pr_max_effective_lines": _(
                "Raises AI_PR_TOO_LARGE (low) when an AI pull request's effective additions plus "
                "deletions exceed this number. Leave it empty to switch the size check off; the "
                "per-risk limits below override it where they are set."
            ),
            "forbid_scope_creep": _(
                "Raises SCOPE_CREEP (medium) when an AI pull request mixes a wholesale reformat "
                "into a change, or reaches into top-level modules its own stated scope never "
                "mentions. A pull request that states no scope has nothing to exceed."
            ),
            "flag_new_dependencies_in_ai_prs": _(
                "Raises NEW_DEPENDENCY_AI (medium) when an AI pull request changes a dependency "
                "manifest or lockfile. A prompt to check that somebody chose the dependency, not "
                "an accusation."
            ),
            "flag_agent_config_changes": _(
                "Raises AGENT_CONFIG_CHANGED (low) when CLAUDE.md, AGENTS.md, .claude/ and their "
                "kin change. Low on purpose: it marks a change that alters how every later agent "
                "run behaves, so that somebody reads it."
            ),
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
        """`(legend, description, [bound field])` triples for the template. A field missing from
        `FIELDSETS` would vanish from the page, so the last group collects whatever was not named —
        a new toggle is then visible and misplaced rather than invisible and forgotten. That
        fallback group has no description, because nobody wrote one for a field nobody placed."""
        named = {name for _legend, _description, names in self.FIELDSETS for name in names}
        groups = [
            (legend, description, [self[name] for name in names if name in self.fields])
            for legend, description, names in self.FIELDSETS
        ]
        leftovers = [self[name] for name in self.fields if name not in named]
        if leftovers:
            groups.append((_("Other"), "", leftovers))
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
