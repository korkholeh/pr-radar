"""Fixtures local to `apps/dashboards/tests/`."""

from __future__ import annotations

import pytest
from django.core.files.storage import FileSystemStorage


@pytest.fixture(autouse=True)
def _export_storage_uses_tmp_dir(tmp_path):
    """`ExportJob.file`'s storage (`models.EXPORT_STORAGE`) is resolved to a real
    `FileSystemStorage` once, at `FileField.__init__` time (Django evaluates callable storage
    eagerly at field construction, not per access) — so a test that actually runs an export job
    (`run_export_job`) writes a real file under the developer's `DATA_DIR/exports/` unless the
    field's storage is swapped directly. Re-binding `settings.DATA_DIR` alone would not help,
    since the already-constructed storage instance does not re-read it (round 2 review MINOR)."""
    from apps.dashboards.models import ExportJob

    field = ExportJob._meta.get_field("file")
    original_storage = field.storage
    field.storage = FileSystemStorage(location=str(tmp_path / "exports"))
    try:
        yield
    finally:
        field.storage = original_storage
