from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class GitHubConnection(models.Model):
    class Kind(models.TextChoices):
        FINE_GRAINED_PAT = "fine_grained_pat", _("Fine-grained personal access token")
        CLASSIC_PAT = "classic_pat", _("Classic personal access token")
        GITHUB_APP = "github_app", _("GitHub App")

    class Status(models.TextChoices):
        UNVERIFIED = "unverified", _("Unverified")
        OK = "ok", _("OK")
        DEGRADED = "degraded", _("Degraded")
        INVALID = "invalid", _("Invalid")
        EXPIRED = "expired", _("Expired")

    name = models.CharField(_("name"), max_length=200, unique=True)
    kind = models.CharField(_("kind"), max_length=20, choices=Kind.choices)
    owner_login = models.CharField(_("owner login"), max_length=200, blank=True)

    token_encrypted = models.BinaryField(_("encrypted token"), null=True, blank=True)
    token_last4 = models.CharField(_("token last 4 characters"), max_length=4, blank=True)
    token_login = models.CharField(_("token login"), max_length=200, blank=True)
    expires_at = models.DateTimeField(_("expires at"), null=True, blank=True)

    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.UNVERIFIED)
    last_checked_at = models.DateTimeField(_("last checked at"), null=True, blank=True)
    last_check_result = models.JSONField(_("last check result"), default=dict, blank=True)
    rate_limit_remaining = models.IntegerField(_("rate limit remaining"), null=True, blank=True)
    rate_limit_reset_at = models.DateTimeField(_("rate limit reset at"), null=True, blank=True)

    is_active = models.BooleanField(_("is active"), default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_connections",
        verbose_name=_("created by"),
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    # Reserved for the github_app kind; unused by fine_grained_pat and classic_pat.
    app_id = models.CharField(_("app id"), max_length=100, null=True, blank=True)  # noqa: DJ001
    installation_id = models.CharField(  # noqa: DJ001
        _("installation id"), max_length=100, null=True, blank=True
    )
    private_key_encrypted = models.BinaryField(_("encrypted private key"), null=True, blank=True)

    class Meta:
        verbose_name = _("GitHub connection")
        verbose_name_plural = _("GitHub connections")

    def __str__(self) -> str:
        return self.name
