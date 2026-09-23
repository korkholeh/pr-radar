from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from apps.catalog.globs import compile_globs


class AIPolicy(models.Model):
    allowed_tools = models.JSONField(_("allowed tools"), default=list, blank=True)
    require_disclosure = models.BooleanField(_("require disclosure"), default=False)
    require_human_approval = models.BooleanField(_("require human approval"), default=False)
    min_human_approvals = models.PositiveIntegerField(_("minimum human approvals"), default=1)
    require_tests_for_ai_prs = models.BooleanField(_("require tests for AI PRs"), default=False)
    ai_pr_max_effective_lines = models.PositiveIntegerField(
        _("maximum effective lines for an AI PR"), null=True, blank=True
    )
    # Phase 12, stage 7: the PLANEKS AI Engineering Standards as checks. Every one of these is off
    # — False, empty or null — so upgrading an existing installation produces no violation at all
    # until a lead turns something on. A test asserts exactly that.
    require_ai_review_first = models.BooleanField(_("require an AI review"), default=False)
    forbid_ai_only_approval = models.BooleanField(_("forbid AI-only approval"), default=False)
    require_ai_comments_resolved = models.BooleanField(
        _("require AI review comments to be resolved"), default=False
    )
    require_risk_level = models.BooleanField(_("require a stated risk level"), default=False)
    require_task_link = models.BooleanField(_("require a linked task"), default=False)
    require_verification_note = models.BooleanField(_("require a verification note"), default=False)
    require_high_risk_plan = models.BooleanField(_("require a plan for high-risk changes"), default=False)
    forbid_ci_bypass = models.BooleanField(_("forbid bypassing the quality gate"), default=False)
    forbid_test_weakening = models.BooleanField(_("forbid weakening tests"), default=False)
    forbid_secret_artifacts = models.BooleanField(_("forbid committing credential files"), default=False)
    forbid_scope_creep = models.BooleanField(_("forbid scope creep in AI PRs"), default=False)
    forbid_rubber_stamp_approval = models.BooleanField(
        _("forbid rubber-stamp approval of AI PRs"), default=False
    )
    flag_agent_config_changes = models.BooleanField(_("flag agent configuration changes"), default=False)
    flag_new_dependencies_in_ai_prs = models.BooleanField(_("flag new dependencies in AI PRs"), default=False)
    high_risk_min_approvals = models.PositiveIntegerField(_("minimum approvals for high risk"), default=2)
    # `{}` rather than the standards' own {"medium": 800, "high": 400}: a non-empty default would
    # raise AI_PR_TOO_LARGE across an installation's whole history the moment it upgraded. The
    # suggested numbers are in docs/POLICY.md, for a lead to paste in deliberately.
    max_effective_lines_by_risk = models.JSONField(
        _("maximum effective lines by risk level"), default=dict, blank=True
    )
    designated_reviewers = models.ManyToManyField(
        "catalog.Person",
        blank=True,
        related_name="designated_for_policies",
        verbose_name=_("designated reviewers"),
        help_text=_("People whose approval satisfies a migration's extra-review requirement."),
    )
    ai_reviewer_identities = models.JSONField(
        _("AI reviewer logins"),
        default=list,
        blank=True,
        help_text=_("GitHub logins of the AI reviewers a pull request is expected to have run."),
    )
    effective_from = models.DateTimeField(_("effective from"), unique=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("AI policy")
        verbose_name_plural = _("AI policies")
        ordering = ["-effective_from"]

    def __str__(self) -> str:
        return f"AI policy effective {self.effective_from:%Y-%m-%d}"


class SensitivePathRule(models.Model):
    class AiMode(models.TextChoices):
        FORBIDDEN = "forbidden", _("Forbidden")
        NEEDS_EXTRA_REVIEW = "needs_extra_review", _("Needs extra review")
        # Classification only: the rule contributes its `risk_level` and raises no violation of
        # its own. That is what lets the seeded PLANEKS risk table ship active — it has to be
        # active to classify anything — without flooding an installation with sensitive-path
        # violations the lead never asked for.
        ADVISORY = "advisory", _("Advisory (risk level only)")

    class RiskLevel(models.TextChoices):
        LOW = "low", _("Low")
        MEDIUM = "medium", _("Medium")
        HIGH = "high", _("High")

    project = models.ForeignKey(
        "catalog.Project",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="sensitive_path_rules",
        verbose_name=_("project"),
        help_text=_("Empty means the rule applies globally."),
    )
    glob = models.CharField(_("path glob"), max_length=400)
    ai_mode = models.CharField(_("AI mode"), max_length=30, choices=AiMode.choices)
    # Blank means "not classified": a rule written to forbid a path says nothing about how risky
    # the path is, and guessing `low` for it would quietly exempt it from every risk-based limit.
    risk_level = models.CharField(  # noqa: DJ001
        _("risk level"), max_length=10, choices=RiskLevel.choices, blank=True
    )
    description = models.CharField(_("description"), max_length=400, blank=True)
    is_active = models.BooleanField(_("is active"), default=True)

    class Meta:
        verbose_name = _("sensitive path rule")
        verbose_name_plural = _("sensitive path rules")
        ordering = ["project_id", "glob"]
        indexes = [models.Index(fields=["project", "is_active"])]
        constraints = [
            models.UniqueConstraint(fields=["project", "glob"], name="uniq_sensitive_path_project_glob"),
            models.UniqueConstraint(
                fields=["glob"],
                condition=Q(project__isnull=True),
                name="uniq_sensitive_path_global_glob",
            ),
        ]

    def __str__(self) -> str:
        return self.glob

    def clean(self) -> None:
        super().clean()
        if not self.glob or not self.glob.strip():
            raise ValidationError({"glob": _("A path glob is required.")})
        if not compile_globs([self.glob]):
            raise ValidationError({"glob": _("This glob could not be compiled.")})


class PolicyViolation(models.Model):
    class RuleCode(models.TextChoices):
        DISCLOSURE_MISSING = "DISCLOSURE_MISSING", _("Disclosure missing")
        DISCLOSURE_MISMATCH = "DISCLOSURE_MISMATCH", _("Disclosure mismatch")
        TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED", _("Tool not allowed")
        SENSITIVE_PATH_FORBIDDEN = "SENSITIVE_PATH_FORBIDDEN", _("Sensitive path forbidden")
        SENSITIVE_PATH_REVIEW = "SENSITIVE_PATH_REVIEW", _("Sensitive path needs review")
        NO_HUMAN_APPROVAL = "NO_HUMAN_APPROVAL", _("No human approval")
        SELF_MERGE = "SELF_MERGE", _("Self-merge")
        NO_TESTS = "NO_TESTS", _("No tests")
        AI_PR_TOO_LARGE = "AI_PR_TOO_LARGE", _("AI PR too large")
        # Phase 12, stage 7: the PLANEKS AI Engineering Standards. docs/POLICY.md maps each code
        # to the numbered rule or named section it comes from.
        QUALITY_GATE_BYPASSED = "QUALITY_GATE_BYPASSED", _("Quality gate bypassed")
        TEST_WEAKENED = "TEST_WEAKENED", _("Tests weakened")
        AI_ONLY_APPROVAL = "AI_ONLY_APPROVAL", _("Approved only by AI")
        AI_REVIEW_MISSING = "AI_REVIEW_MISSING", _("AI review missing")
        AI_REVIEW_UNRESOLVED = "AI_REVIEW_UNRESOLVED", _("AI review comments unresolved")
        HIGH_RISK_NO_PLAN = "HIGH_RISK_NO_PLAN", _("High-risk change with no plan")
        RISK_LEVEL_MISSING = "RISK_LEVEL_MISSING", _("Risk level not stated")
        VERIFICATION_MISSING = "VERIFICATION_MISSING", _("Verification not stated")
        TASK_LINK_MISSING = "TASK_LINK_MISSING", _("Task not linked")
        NEW_DEPENDENCY_AI = "NEW_DEPENDENCY_AI", _("New dependency in an AI PR")
        MIGRATION_AI_INSUFFICIENT_REVIEW = (
            "MIGRATION_AI_INSUFFICIENT_REVIEW",
            _("Migration in an AI PR without designated review"),
        )
        SECRET_ARTIFACT_COMMITTED = "SECRET_ARTIFACT_COMMITTED", _("Credential file committed")
        AGENT_CONFIG_CHANGED = "AGENT_CONFIG_CHANGED", _("Agent configuration changed")
        SCOPE_CREEP = "SCOPE_CREEP", _("Scope creep")
        RUBBER_STAMP_ON_AI_PR = "RUBBER_STAMP_ON_AI_PR", _("Rubber-stamp approval of an AI PR")

    class Severity(models.TextChoices):
        HIGH = "high", _("High")
        MEDIUM = "medium", _("Medium")
        LOW = "low", _("Low")

    class Status(models.TextChoices):
        # A violation's status is the lead's triage state, not the pull request's: "open" means
        # nobody has judged it yet. Its own context keeps the Ukrainian from reading like a
        # pull request that is still open, which the bare "Open" msgid (shared with
        # `PullRequest.State`) did on merged pull requests.
        OPEN = "open", pgettext_lazy("policy violation status", "Open")
        ACKNOWLEDGED = "acknowledged", _("Acknowledged")
        WAIVED = "waived", _("Waived")
        RESOLVED = "resolved", _("Resolved")

    pull_request = models.ForeignKey(
        "activity.PullRequest",
        on_delete=models.CASCADE,
        related_name="violations",
        verbose_name=_("pull request"),
    )
    rule_code = models.CharField(_("rule code"), max_length=40, choices=RuleCode.choices)
    severity = models.CharField(_("severity"), max_length=10, choices=Severity.choices)
    details_params = models.JSONField(_("details params"), default=dict, blank=True)
    details_hash = models.CharField(_("details hash"), max_length=64)
    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.OPEN)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_violations",
        verbose_name=_("resolved by"),
    )
    resolution_comment = models.TextField(_("resolution comment"), blank=True)
    resolved_automatically = models.BooleanField(_("resolved automatically"), default=False)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("policy violation")
        verbose_name_plural = _("policy violations")
        constraints = [
            models.UniqueConstraint(
                fields=["pull_request", "rule_code", "details_hash"], name="uniq_violation_pr_rule_hash"
            ),
        ]
        indexes = [
            models.Index(fields=["status", "severity"]),
            models.Index(fields=["rule_code", "created_at"]),
            models.Index(fields=["pull_request", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.rule_code} on {self.pull_request}"
