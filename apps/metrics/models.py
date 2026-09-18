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


class DataVersion(models.Model):
    """A singleton row (`id=1`): bumped with an atomic `F("version") + 1` whenever the underlying
    data changes, and stamped into `compute()`'s cache key so a stale cache entry can never be
    served (ARCHITECTURE.md). A DB row rather than a cache entry or an `AppSetting` because it
    must survive a cache wipe and be shared correctly between the web process and the huey worker."""

    version = models.PositiveIntegerField(_("version"), default=1)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("data version")
        verbose_name_plural = _("data version")

    def __str__(self) -> str:
        return f"v{self.version}"


class DirtyDay(models.Model):
    """A Kyiv calendar day a synced PR touched (`created_at`/`merged_at`/`closed_at`/a review's
    `submitted_at`), waiting for `rollups.rebuild_dirty()` to rebuild and delete it."""

    date = models.DateField(_("date"), unique=True)
    marked_at = models.DateTimeField(_("marked at"), auto_now_add=True)

    class Meta:
        verbose_name = _("dirty day")
        verbose_name_plural = _("dirty days")

    def __str__(self) -> str:
        return str(self.date)
