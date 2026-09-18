"""T21-T24: `ExportJob` (plan §6) — its defaults, the background flow (`services.run_export_job`,
`services.cleanup_exports`), "My exports" and the author-only download."""

from __future__ import annotations

import datetime
from unittest import mock

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.factories import UserProjectAccessFactory
from apps.activity.factories import PullRequestFactory
from apps.catalog.factories import IdentityFactory, ProjectFactory, RepositoryFactory
from apps.catalog.services import set_setting
from apps.dashboards import tasks
from apps.dashboards.factories import ExportJobFactory
from apps.dashboards.models import ExportJob, compute_expires_at
from apps.dashboards.services import CleanupResult, cleanup_exports, run_export_job

# --- ExportJob model (T21) ----------------------------------------------------------------------


@pytest.mark.django_db
def test_export_job_defaults():
    job = ExportJobFactory()
    assert job.status == ExportJob.Status.PENDING
    assert job.rows is None
    assert not job.file
    assert job.error_code == ""
    assert job.started_at is None
    assert job.finished_at is None


@pytest.mark.django_db
def test_compute_expires_at_uses_the_retention_setting():
    set_setting("EXPORT_RETENTION_DAYS", 7)
    created = timezone.now()
    assert compute_expires_at(created) == created + datetime.timedelta(days=7)


# --- Background job run (T22) -------------------------------------------------------------------


@pytest.mark.django_db
def test_export_above_sync_cap_returns_job_and_enqueues(client, lead_user):
    """Acceptance criterion #9: an export of more rows than `EXPORT_SYNC_MAX_ROWS` returns an
    `ExportJob` instead of a file, and the enqueue is asserted by patching the task — with huey's
    `immediate=True` test setting, calling the real task would run the job inline and defeat the
    point of this test."""
    client.force_login(lead_user)
    set_setting("EXPORT_SYNC_MAX_ROWS", 2)
    for index in range(5):
        ProjectFactory(name=f"Radar Project {index:02d}")

    with mock.patch("apps.dashboards.views.tasks.export_job_task") as mock_task:
        response = client.get(reverse("dashboards:export", args=["projects", "xlsx"]))

    assert response.status_code == 302
    job = ExportJob.objects.get()
    assert job.status == ExportJob.Status.PENDING
    assert job.kind == ExportJob.Kind.TABLE_XLSX
    mock_task.assert_called_once_with(job.id)


@pytest.mark.django_db
def test_an_export_at_the_sync_cap_still_streams_synchronously(client, lead_user):
    client.force_login(lead_user)
    set_setting("EXPORT_SYNC_MAX_ROWS", 3)
    for index in range(3):
        ProjectFactory(name=f"Radar Project {index:02d}")

    with mock.patch("apps.dashboards.views.tasks.export_job_task") as mock_task:
        response = client.get(reverse("dashboards:export", args=["projects", "csv"]))

    assert response.status_code == 200
    assert ExportJob.objects.count() == 0
    mock_task.assert_not_called()


@pytest.mark.django_db
def test_export_above_sync_cap_never_materialises_the_full_row_set(client, lead_user):
    """Round 2 review MINOR: `views.export_table` must decide the sync-vs-background threshold
    from a cheap `.count()` query (`rows.pull_request_row_count`), never by building every row
    dict first just to discard it — `full_rows()` is mocked to fail if called at all here."""
    client.force_login(lead_user)
    set_setting("EXPORT_SYNC_MAX_ROWS", 2)
    repository = RepositoryFactory()
    identity = IdentityFactory()
    for _ in range(5):
        PullRequestFactory(repository=repository, author=identity, created_at=timezone.now())

    with (
        mock.patch("apps.dashboards.views.tasks.export_job_task"),
        mock.patch(
            "apps.dashboards.views.full_rows",
            side_effect=AssertionError("full_rows must not be called above the cap"),
        ) as mock_full_rows,
    ):
        response = client.get(reverse("dashboards:export", args=["pull_requests", "csv"]))

    assert response.status_code == 302
    mock_full_rows.assert_not_called()
    assert ExportJob.objects.get().status == ExportJob.Status.PENDING


@pytest.mark.django_db
def test_run_export_job_writes_file_rows_and_audit(lead_user):
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

    result = run_export_job(job.id)

    assert result.status == ExportJob.Status.DONE
    assert result.rows == 1
    assert result.file.name
    assert result.finished_at is not None
    from apps.accounts.models import AuditEntry

    assert AuditEntry.objects.filter(action="export.table", object_type="ExportJob").count() == 1


@pytest.mark.django_db
def test_run_export_job_writes_absolute_hyperlinks_when_a_base_url_was_stored(lead_user):
    """Round 2 review MINOR: a background XLSX table export must not silently lose the hyperlinks
    the synchronous export has — `views.export_table` stores the enqueuing request's `base_url` on
    the job, and `run_export_job()` must apply it before `write_xlsx()`."""
    import io

    import openpyxl

    project = ProjectFactory(name="Radar Project")
    job = ExportJobFactory(
        user=lead_user,
        kind=ExportJob.Kind.TABLE_XLSX,
        params={
            "scope_type": "global",
            "scope_id": None,
            "query_string": "table=projects",
            "table_key": "projects",
            "fmt": "xlsx",
            "base_url": "http://testserver/",
        },
    )

    result = run_export_job(job.id)

    assert result.status == ExportJob.Status.DONE
    workbook = openpyxl.load_workbook(io.BytesIO(result.file.read()))
    cell = workbook.active["A2"]
    assert cell.hyperlink is not None
    assert cell.hyperlink.target == f"http://testserver/projects/{project.id}/"


@pytest.mark.django_db
def test_run_export_job_reresolves_scope_for_user_at_run_time(lead_user):
    """RISKS row 3: `ExportJob.params` stores enough to rebuild the `Scope`, and the task
    re-resolves `scope_for_user(job.user)` at run time, not at enqueue time — a grant revoked
    between enqueue and run must be honoured."""
    granted = ProjectFactory(name="Granted Project")
    revoked = ProjectFactory(name="Revoked Project")
    access_a = UserProjectAccessFactory(user=lead_user, project=granted)
    access_b = UserProjectAccessFactory(user=lead_user, project=revoked)
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

    access_b.delete()  # the grant is revoked after the job was enqueued
    result = run_export_job(job.id)

    assert result.status == ExportJob.Status.DONE
    assert result.rows == 1  # only the still-granted project, not both
    access_a.delete()


@pytest.mark.django_db
def test_a_failing_job_ends_failed_with_an_error_code(lead_user):
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

    result = run_export_job(job.id)

    assert result.status == ExportJob.Status.FAILED
    assert result.error_code == "failed"
    assert result.finished_at is not None


# --- "My exports" and download (T23) ------------------------------------------------------------


@pytest.mark.django_db
def test_exports_list_shows_only_the_callers_own_jobs(client, lead_user, django_user_model):
    """Round 2 review MINOR: asserts on the rendered response, not just on factory state, so a
    view that switched to `ExportJob.objects.all()` would fail this test."""
    other_user = django_user_model.objects.create_user(username="other", password="pw")
    own_job = ExportJobFactory(user=lead_user, kind=ExportJob.Kind.TABLE_CSV, status=ExportJob.Status.DONE)
    other_job = ExportJobFactory(user=other_user, kind=ExportJob.Kind.TABLE_CSV, status=ExportJob.Status.DONE)
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:exports_index"))

    assert response.status_code == 200
    body = response.content.decode()
    assert body.count('data-testid="export-job-row"') == 1
    assert reverse("dashboards:export_download", args=[own_job.pk]) in body
    assert reverse("dashboards:export_download", args=[other_job.pk]) not in body


@pytest.mark.django_db
def test_download_of_another_users_export_is_forbidden(client, lead_user, django_user_model):
    other_user = django_user_model.objects.create_user(username="other", password="pw")
    job = ExportJobFactory(user=other_user, status=ExportJob.Status.DONE)
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:export_download", args=[job.pk]))

    assert response.status_code == 403


@pytest.mark.django_db
def test_polling_stops_once_every_job_is_terminal(client, lead_user):
    client.force_login(lead_user)
    ExportJobFactory(user=lead_user, status=ExportJob.Status.RUNNING)

    running_response = client.get(reverse("dashboards:exports_index"), HTTP_HX_REQUEST="true")
    assert b"hx-trigger" in running_response.content

    ExportJob.objects.update(status=ExportJob.Status.DONE)
    done_response = client.get(reverse("dashboards:exports_index"), HTTP_HX_REQUEST="true")
    assert b"hx-trigger" not in done_response.content


@pytest.mark.django_db
def test_failed_job_shows_a_translated_reason(client, lead_user):
    client.force_login(lead_user)
    ExportJobFactory(user=lead_user, status=ExportJob.Status.FAILED, error_code="failed")

    response = client.get(reverse("dashboards:exports_index"))

    assert response.status_code == 200
    assert "This export failed" in response.content.decode()


@pytest.mark.django_db
def test_exports_index_query_count(client, lead_user, django_assert_max_num_queries):
    client.force_login(lead_user)
    for _ in range(5):
        ExportJobFactory(user=lead_user)

    with django_assert_max_num_queries(8):
        client.get(reverse("dashboards:exports_index"))


def test_anonymous_user_is_redirected_from_exports_index():
    from django.test import Client

    response = Client().get(reverse("dashboards:exports_index"))
    assert response.status_code == 302


# --- Cleanup (T24) --------------------------------------------------------------------------


@pytest.mark.django_db
def test_cleanup_deletes_expired_file_and_row(lead_user):
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
    job.refresh_from_db()
    assert job.file
    file_path = job.file.path
    job.expires_at = timezone.now() - datetime.timedelta(seconds=1)
    job.save(update_fields=["expires_at"])

    import os

    result = cleanup_exports()

    assert result == CleanupResult(deleted=1, stuck_swept=0)
    assert not ExportJob.objects.filter(pk=job.pk).exists()
    assert not os.path.exists(file_path)


@pytest.mark.django_db
def test_cleanup_leaves_a_fresh_job_alone():
    job = ExportJobFactory(expires_at=timezone.now() + datetime.timedelta(days=7))

    result = cleanup_exports()

    assert result.deleted == 0
    assert ExportJob.objects.filter(pk=job.pk).exists()


@pytest.mark.django_db
def test_cleanup_sweeps_a_stuck_running_job_to_failed():
    job = ExportJobFactory(
        status=ExportJob.Status.RUNNING,
        started_at=timezone.now() - datetime.timedelta(hours=2),
        expires_at=timezone.now() + datetime.timedelta(days=7),
    )

    result = cleanup_exports()

    job.refresh_from_db()
    assert result.stuck_swept == 1
    assert job.status == ExportJob.Status.FAILED
    assert job.error_code == "stuck"


@pytest.mark.django_db
def test_cleanup_does_not_sweep_a_recently_started_running_job():
    job = ExportJobFactory(
        status=ExportJob.Status.RUNNING,
        started_at=timezone.now() - datetime.timedelta(minutes=5),
        expires_at=timezone.now() + datetime.timedelta(days=7),
    )

    result = cleanup_exports()

    job.refresh_from_db()
    assert result.stuck_swept == 0
    assert job.status == ExportJob.Status.RUNNING


@pytest.mark.django_db
def test_export_job_task_calls_run_export_job():
    job = ExportJobFactory()
    with mock.patch("apps.dashboards.tasks.run_export_job") as mock_run:
        tasks.export_job_task.call_local(job.id)
    mock_run.assert_called_once_with(job.id)


@pytest.mark.django_db
def test_cleanup_exports_task_calls_cleanup_exports():
    with mock.patch("apps.dashboards.tasks.cleanup_exports") as mock_cleanup:
        tasks.cleanup_exports_task.call_local()
    mock_cleanup.assert_called_once_with()
