"""T25: every path that produces an export file writes exactly one `AuditEntry` recording the
user, kind, filters and row count (plan §7) — sync CSV, sync XLSX, sync report, and a completed
background job."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.accounts.models import AuditEntry
from apps.catalog.factories import ProjectFactory
from apps.dashboards.factories import ExportJobFactory
from apps.dashboards.models import ExportJob
from apps.dashboards.services import run_export_job


@pytest.mark.django_db
@pytest.mark.parametrize("fmt", ["csv", "xlsx"])
def test_sync_table_export_writes_one_audit_entry(client, lead_user, fmt):
    ProjectFactory(name="Radar Project")
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:export", args=["projects", fmt]))

    assert response.status_code == 200
    entry = AuditEntry.objects.get(action="export.table")
    assert entry.actor_id == lead_user.id
    assert entry.changes["after"]["table_key"] == "projects"
    assert entry.changes["after"]["format"] == fmt
    assert entry.changes["after"]["rows"] == 1
    assert "filters" in entry.changes["after"]


@pytest.mark.django_db
def test_sync_report_export_writes_one_audit_entry(client, lead_user):
    ProjectFactory(name="Radar Project")
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:export_report"))

    assert response.status_code == 200
    entry = AuditEntry.objects.get(action="export.report")
    assert entry.actor_id == lead_user.id
    assert entry.changes["after"]["scope_type"] == "global"
    assert "rows" in entry.changes["after"]


@pytest.mark.django_db
def test_completed_background_job_writes_one_audit_entry(lead_user):
    ProjectFactory(name="Radar Project")
    job = ExportJobFactory(
        user=lead_user,
        kind=ExportJob.Kind.TABLE_CSV,
        params={
            "scope_type": "global",
            "scope_id": None,
            "query_string": "table=projects",
            "table_key": "projects",
            "fmt": "csv",
        },
    )

    run_export_job(job.id)

    entry = AuditEntry.objects.get(action="export.table", object_type="ExportJob")
    assert entry.object_id == str(job.pk)
    assert entry.actor_id == lead_user.id
    assert entry.changes["after"]["rows"] == 1


@pytest.mark.django_db
def test_a_failed_background_job_writes_no_audit_entry(lead_user):
    job = ExportJobFactory(
        user=lead_user,
        kind=ExportJob.Kind.TABLE_CSV,
        params={
            "scope_type": "global",
            "scope_id": None,
            "query_string": "table=projects",
            "table_key": "not_a_real_table",
            "fmt": "csv",
        },
    )

    run_export_job(job.id)

    assert not AuditEntry.objects.filter(action="export.table").exists()
