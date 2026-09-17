from django.db import models
from django.utils.translation import gettext_lazy as _


class ScopeType(models.TextChoices):
    GLOBAL = "global", _("Global")
    PROJECT = "project", _("Project")
    REPO = "repo", _("Repository")
    PERSON = "person", _("Person")


class Cohort(models.TextChoices):
    ALL = "all", _("All")
    AI = "ai", _("AI")
    NON_AI = "non_ai", _("Non-AI")


class DailyRollup(models.Model):
    date = models.DateField(_("date"))
    scope_type = models.CharField(_("scope type"), max_length=10, choices=ScopeType.choices)
    scope_id = models.PositiveIntegerField(_("scope id"), null=True, blank=True)
    cohort = models.CharField(_("cohort"), max_length=10, choices=Cohort.choices)
    metric_key = models.CharField(_("metric key"), max_length=100)
    value = models.FloatField(_("value"), null=True, blank=True)
    sample_size = models.PositiveIntegerField(_("sample size"), default=0)
    computed_at = models.DateTimeField(_("computed at"), auto_now=True)

    class Meta:
        verbose_name = _("daily rollup")
        verbose_name_plural = _("daily rollups")
        constraints = [
            models.UniqueConstraint(
                fields=["date", "scope_type", "scope_id", "cohort", "metric_key"],
                name="uniq_dailyrollup_scoped",
            ),
            models.UniqueConstraint(
                fields=["date", "scope_type", "cohort", "metric_key"],
                condition=models.Q(scope_id__isnull=True),
                name="uniq_dailyrollup_global",
            ),
        ]
        indexes = [
            models.Index(fields=["metric_key", "date"]),
            models.Index(fields=["scope_type", "scope_id", "date"]),
        ]

    def __str__(self) -> str:
        return f"{self.metric_key} {self.scope_type}:{self.scope_id} {self.date}"
