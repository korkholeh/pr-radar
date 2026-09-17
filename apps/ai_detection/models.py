from django.db import models
from django.utils.translation import gettext_lazy as _


class Detector(models.TextChoices):
    COMMIT_TRAILER = "commit_trailer", _("Commit trailer")
    COMMIT_AUTHOR = "commit_author", _("Commit author")
    PR_AUTHOR = "pr_author", _("PR author")
    PR_BODY_FOOTER = "pr_body_footer", _("PR body footer")
    HTML_COMMENT = "html_comment", _("HTML comment")
    LABEL = "label", _("Label")
    BRANCH_PATTERN = "branch_pattern", _("Branch pattern")
    COMMIT_MESSAGE = "commit_message", _("Commit message")


class Tool(models.TextChoices):
    CLAUDE_CODE = "claude_code", _("Claude Code")
    COPILOT = "copilot", _("Copilot")
    CURSOR = "cursor", _("Cursor")
    CODEX = "codex", _("Codex")
    DEVIN = "devin", _("Devin")
    GEMINI = "gemini", _("Gemini")
    AIDER = "aider", _("Aider")
    WINDSURF = "windsurf", _("Windsurf")
    CHATGPT = "chatgpt", _("ChatGPT")
    OTHER = "other", _("Other")


class Confidence(models.TextChoices):
    HIGH = "high", _("High")
    MEDIUM = "medium", _("Medium")
    LOW = "low", _("Low")


class DetectionRule(models.Model):
    name = models.CharField(_("name"), max_length=200)
    detector = models.CharField(_("detector"), max_length=20, choices=Detector.choices)
    pattern = models.CharField(_("pattern"), max_length=500)
    tool = models.CharField(_("tool"), max_length=20, choices=Tool.choices)
    confidence = models.CharField(_("confidence"), max_length=10, choices=Confidence.choices)
    is_active = models.BooleanField(_("is active"), default=True)
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("detection rule")
        verbose_name_plural = _("detection rules")
        indexes = [models.Index(fields=["is_active", "detector"])]

    def __str__(self) -> str:
        return self.name


class AISignal(models.Model):
    pull_request = models.ForeignKey(
        "activity.PullRequest",
        on_delete=models.CASCADE,
        related_name="ai_signals",
        verbose_name=_("pull request"),
    )
    commit = models.ForeignKey(
        "activity.Commit",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="ai_signals",
        verbose_name=_("commit"),
    )
    rule = models.ForeignKey(
        DetectionRule, on_delete=models.PROTECT, related_name="signals", verbose_name=_("rule")
    )
    tool = models.CharField(_("tool"), max_length=20, choices=Tool.choices)
    confidence = models.CharField(_("confidence"), max_length=10, choices=Confidence.choices)
    evidence = models.CharField(_("evidence"), max_length=200)
    detected_at = models.DateTimeField(_("detected at"), auto_now_add=True)

    class Meta:
        verbose_name = _("AI signal")
        verbose_name_plural = _("AI signals")
        indexes = [models.Index(fields=["pull_request", "confidence"])]

    def __str__(self) -> str:
        return f"{self.rule} on {self.pull_request}"
