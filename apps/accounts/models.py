from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class UserPreference(models.Model):
    class Theme(models.TextChoices):
        SYSTEM = "system", _("System")
        LIGHT = "light", _("Light")
        DARK = "dark", _("Dark")

    class Language(models.TextChoices):
        EN = "en", _("English")
        UK = "uk", _("Ukrainian")

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="preference",
        verbose_name=_("user"),
    )
    theme = models.CharField(_("theme"), max_length=10, choices=Theme.choices, default=Theme.SYSTEM)
    language = models.CharField(_("language"), max_length=10, choices=Language.choices, default=Language.EN)

    class Meta:
        verbose_name = _("user preference")
        verbose_name_plural = _("user preferences")

    def __str__(self) -> str:
        return f"{self.user} preference"


class AuditEntry(models.Model):
    """English-only audit trail: a code plus structured before/after JSON, never a rendered message."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="audit_entries",
        verbose_name=_("actor"),
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    action = models.CharField(_("action"), max_length=100)
    object_type = models.CharField(_("object type"), max_length=100)
    object_id = models.CharField(_("object id"), max_length=100)
    changes = models.JSONField(_("changes"), default=dict)

    class Meta:
        verbose_name = _("audit entry")
        verbose_name_plural = _("audit entries")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.action} on {self.object_type}:{self.object_id}"
