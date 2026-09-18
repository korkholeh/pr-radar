"""`ExportJob` (plan §6): a background CSV/XLSX/report export. `EXPORT_STORAGE` is a dedicated
`FileSystemStorage` under `DATA_DIR/exports/`, never `MEDIA_ROOT` — no URL should ever be able to
serve an export file directly, only the author-only download view (`views.export_download`, T23,
DECISIONS)."""

from __future__ import annotations

import datetime

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.catalog.services import get_int


def EXPORT_STORAGE() -> FileSystemStorage:  # noqa: N802 - `FileField(storage=...)`'s public name
    """A callable, not a bare instance: Django's migration serializer bakes an instantiated
    `FileSystemStorage`'s `location` kwarg into the migration file as a literal absolute path
    (this machine's `DATA_DIR`), which would break `makemigrations --check` and the file's actual
    location on every other machine/environment. A callable is instead recorded as an import
    reference and re-evaluated against `settings.DATA_DIR` wherever the app runs."""
    return FileSystemStorage(location=str(settings.DATA_DIR / "exports"))


def compute_expires_at(created_at: datetime.datetime) -> datetime.datetime:
    return created_at + datetime.timedelta(days=get_int("EXPORT_RETENTION_DAYS"))


class ExportJob(models.Model):
    class Kind(models.TextChoices):
        TABLE_CSV = "table_csv", _("Table (CSV)")
        TABLE_XLSX = "table_xlsx", _("Table (XLSX)")
        REPORT_XLSX = "report_xlsx", _("Report (XLSX)")

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        RUNNING = "running", _("Running")
        DONE = "done", _("Done")
        FAILED = "failed", _("Failed")

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="export_jobs", verbose_name=_("user")
    )
    kind = models.CharField(_("kind"), max_length=12, choices=Kind.choices)
    # The canonical query string plus scope_type/scope_id/table_key/fmt — enough for the task to
    # rebuild the Scope and re-resolve scope_for_user(job.user) at run time (RISKS row 3, plan §6).
    params = models.JSONField(_("params"), default=dict, blank=True)
    language = models.CharField(_("language"), max_length=8, default="en")
    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.PENDING)
    file = models.FileField(_("file"), storage=EXPORT_STORAGE, blank=True)
    rows = models.PositiveIntegerField(_("rows"), null=True, blank=True)
    # A code plus params, never a rendered message (CLAUDE.md) — "My exports" renders it in the
    # reader's own language.
    error_code = models.CharField(_("error code"), max_length=50, blank=True)
    error_params = models.JSONField(_("error params"), default=dict, blank=True)
    created_at = models.DateTimeField(_("created at"), default=timezone.now)
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    finished_at = models.DateTimeField(_("finished at"), null=True, blank=True)
    expires_at = models.DateTimeField(_("expires at"), null=True, blank=True)

    class Meta:
        verbose_name = _("export job")
        verbose_name_plural = _("export jobs")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} export #{self.pk} ({self.status})"
