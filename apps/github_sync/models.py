import datetime

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.metrics.timeframe import day_of


class SyncRun(models.Model):
    class Trigger(models.TextChoices):
        CLI = "cli", _("CLI")
        UI = "ui", _("UI")
        SCHEDULE = "schedule", _("Schedule")
        BACKFILL = "backfill", _("Backfill")

    class Status(models.TextChoices):
        RUNNING = "running", _("Running")
        SUCCESS = "success", _("Success")
        PARTIAL = "partial", _("Partial")
        FAILED = "failed", _("Failed")

    started_at = models.DateTimeField(_("started at"), default=timezone.now)
    finished_at = models.DateTimeField(_("finished at"), null=True, blank=True)
    trigger = models.CharField(_("trigger"), max_length=10, choices=Trigger.choices)
    since = models.DateTimeField(
        _("since"),
        null=True,
        blank=True,
        help_text=_("The watermark override this run used, if any (a backfill sets it)."),
    )
    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.RUNNING)
    repositories = models.ManyToManyField(
        "catalog.Repository", related_name="sync_runs", blank=True, verbose_name=_("repositories")
    )
    stats = models.JSONField(_("stats"), default=dict, blank=True)
    stats_by_connection = models.JSONField(_("stats by connection"), default=dict, blank=True)
    error_log = models.TextField(_("error log"), blank=True)

    class Meta:
        verbose_name = _("sync run")
        verbose_name_plural = _("sync runs")
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["started_at"])]

    def __str__(self) -> str:
        return f"sync {self.started_at:%Y-%m-%d %H:%M} ({self.status})"

    @property
    def since_date(self) -> datetime.date | None:
        """The stored UTC watermark as the REPORT_TIMEZONE calendar day the operator picked — the
        inverse of the `day_start()` the backfill form put in. Rendering `since` directly would
        show the previous day for every date chosen east of UTC."""
        return day_of(self.since) if self.since else None


class SyncLock(models.Model):
    """A unique-row mutex: select_for_update() is a silent no-op on SQLite (ADR 0001), so
    acquisition is objects.create(name=...) and an IntegrityError means another run holds it.
    A row older than SYNC_LOCK_STALE_MINUTES is stolen so a killed worker cannot wedge the tool."""

    name = models.CharField(_("name"), max_length=100, unique=True)
    acquired_at = models.DateTimeField(_("acquired at"), default=timezone.now)
    sync_run = models.ForeignKey(
        SyncRun, on_delete=models.CASCADE, related_name="locks", verbose_name=_("sync run")
    )

    class Meta:
        verbose_name = _("sync lock")
        verbose_name_plural = _("sync locks")

    def __str__(self) -> str:
        return self.name
