from django.db import models
from django.utils.translation import gettext_lazy as _


class ChurnResult(models.Model):
    class Status(models.TextChoices):
        OK = "ok", _("OK")
        UNSUPPORTED_MERGE_METHOD = "unsupported_merge_method", _("Unsupported merge method")
        TOO_LARGE = "too_large", _("Too large")
        ERROR = "error", _("Error")

    pull_request = models.ForeignKey(
        "activity.PullRequest",
        on_delete=models.CASCADE,
        related_name="churn_results",
        verbose_name=_("pull request"),
    )
    window_days = models.PositiveIntegerField(_("window days"))
    lines_at_merge = models.PositiveIntegerField(_("lines at merge"), null=True, blank=True)
    lines_surviving = models.PositiveIntegerField(_("lines surviving"), null=True, blank=True)
    churn_ratio = models.FloatField(_("churn ratio"), null=True, blank=True)
    snapshot_sha = models.CharField(_("snapshot sha"), max_length=64, blank=True)
    computed_at = models.DateTimeField(_("computed at"), auto_now=True)
    status = models.CharField(_("status"), max_length=30, choices=Status.choices)
    error = models.TextField(_("error"), blank=True)

    class Meta:
        verbose_name = _("churn result")
        verbose_name_plural = _("churn results")
        constraints = [
            models.UniqueConstraint(fields=["pull_request", "window_days"], name="uniq_churn_pr_window"),
        ]
        indexes = [models.Index(fields=["status", "computed_at"])]

    def __str__(self) -> str:
        return f"{self.pull_request} churn @ {self.window_days}d"
