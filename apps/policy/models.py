from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

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

    class Severity(models.TextChoices):
        HIGH = "high", _("High")
        MEDIUM = "medium", _("Medium")
        LOW = "low", _("Low")

    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        ACKNOWLEDGED = "acknowledged", _("Acknowledged")
        WAIVED = "waived", _("Waived")
        RESOLVED = "resolved", _("Resolved")

    pull_request = models.ForeignKey(
        "activity.PullRequest",
        on_delete=models.CASCADE,
        related_name="violations",
        verbose_name=_("pull request"),
    )
    rule_code = models.CharField(_("rule code"), max_length=30, choices=RuleCode.choices)
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
