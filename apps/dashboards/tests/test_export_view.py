"""T19: `views.export_table()` (plan §7)."""

from __future__ import annotations

import csv
import io
import logging

import openpyxl
import pytest
from django.urls import reverse

from apps.catalog.factories import ProjectFactory, RepositoryFactory
from apps.catalog.services import set_setting
from apps.dashboards.tables import paginate_rows
from apps.metrics.models import ScopeType


@pytest.mark.django_db
def test_csv_export_contains_every_matching_row_not_just_the_page(client, lead_user):
    """Acceptance criterion #5. More rows than `paginate_rows`' page size, filtered by `q` so the
    matching count is smaller than the total, but still bigger than one page — no, wider: this
    proves the export doesn't truncate at the page boundary at all."""
    client.force_login(lead_user)
    page_size = paginate_rows([], 1).paginator.per_page
    total = page_size + 5
    for index in range(total):
        ProjectFactory(name=f"Radar Project {index:02d}")

    response = client.get(
        reverse("dashboards:export", args=["projects", "csv"]),
        {"table": "projects", "q": "Radar Project"},
    )
    assert response.status_code == 200
    body = b"".join(response.streaming_content).decode("utf-8-sig")
    data_rows = list(csv.reader(io.StringIO(body)))[1:]
    assert len(data_rows) == total


@pytest.mark.django_db
def test_xlsx_export_of_the_same_request_has_the_same_row_count(client, lead_user):
    client.force_login(lead_user)
    page_size = paginate_rows([], 1).paginator.per_page
    total = page_size + 3
    for index in range(total):
        ProjectFactory(name=f"Radar Project {index:02d}")

    response = client.get(
        reverse("dashboards:export", args=["projects", "xlsx"]),
        {"table": "projects", "q": "Radar Project"},
    )
    assert response.status_code == 200
    workbook = openpyxl.load_workbook(io.BytesIO(response.content))
    sheet = workbook.active
    assert sheet.max_row - 1 == total  # minus the header row


@pytest.mark.django_db
def test_unknown_table_key_is_404(client, lead_user):
    client.force_login(lead_user)
    response = client.get(reverse("dashboards:export", args=["not_a_table", "csv"]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_unknown_format_is_404(client, lead_user):
    client.force_login(lead_user)
    response = client.get(reverse("dashboards:export", args=["projects", "pdf"]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_filename_is_ascii_in_a_uk_session(client, lead_user):
    client.force_login(lead_user)
    client.post(reverse("accounts:set_language"), {"language": "uk", "next": "/"})
    ProjectFactory(name="Проєкт")

    response = client.get(reverse("dashboards:export", args=["projects", "csv"]))
    disposition = response["Content-Disposition"]
    disposition.encode("ascii")  # raises if a non-ASCII character slipped in


@pytest.mark.django_db
def test_day_mode_export_filename_uses_the_day_not_a_range(client, lead_user):
    client.force_login(lead_user)
    response = client.get(
        reverse("dashboards:export", args=["recent_prs", "csv"]),
        {"mode": "day", "day": "2026-08-15"},
    )
    disposition = response["Content-Disposition"]
    assert "2026-08-15" in disposition
    assert "_2026-08-15_2026-08-15" not in disposition


@pytest.mark.django_db
def test_export_exceeding_the_configured_cap_is_logged_not_silently_uncapped(client, lead_user, caplog):
    """Round-1 review: `EXPORT_SYNC_MAX_ROWS` was read nowhere — the row count wasn't even
    recorded, despite the plan and docs implying it was. The background-job path stays phase 9;
    this only proves an operator gets a signal when the configured cap is exceeded."""
    client.force_login(lead_user)
    set_setting("EXPORT_SYNC_MAX_ROWS", 2)
    for index in range(5):
        ProjectFactory(name=f"Radar Project {index:02d}")

    with caplog.at_level(logging.WARNING, logger="apps.dashboards.views"):
        response = client.get(reverse("dashboards:export", args=["projects", "csv"]), {"q": "Radar Project"})
    assert response.status_code == 200
    assert any("EXPORT_SYNC_MAX_ROWS" in record.message for record in caplog.records)


@pytest.mark.django_db
def test_people_export_header_marks_metrics_as_org_wide(client, lead_user):
    """Round 2 review MAJOR fix: the people table's metric values are always organization-wide
    (`rows.py::people_rows`) — `partials/table.html` said so on screen, but the CSV/XLSX file
    carried no equivalent marker, which is exactly the artifact that gets pasted into a 1:1."""
    client.force_login(lead_user)
    repository = RepositoryFactory()

    response = client.get(
        reverse("dashboards:export", args=["people", "csv"]),
        {"scope_type": ScopeType.REPO, "scope_id": str(repository.pk)},
    )
    header = b"".join(response.streaming_content).decode("utf-8-sig").splitlines()[0]
    assert "PRs merged (org-wide)" in header


@pytest.mark.django_db
def test_people_export_header_does_not_mark_metrics_as_org_wide_when_filtered(client, lead_user):
    """Round 2 audit MAJOR: the BLOCKER fix made the filter bar's project/repository narrowing
    (`params.narrow_scope`) actually reach `compute_many(ScopeType.PERSON, ...)`, which falsifies
    the "(org-wide)" marker the moment such a filter is applied — even though the page's own
    `scope_type=repository` alone (with no `project=`/`repository=` filter) still leaves the
    values org-wide, as the previous test proves."""
    client.force_login(lead_user)
    project = ProjectFactory()
    repository = RepositoryFactory()

    response = client.get(
        reverse("dashboards:export", args=["people", "csv"]),
        {"scope_type": ScopeType.REPO, "scope_id": str(repository.pk), "project": str(project.pk)},
    )
    header = b"".join(response.streaming_content).decode("utf-8-sig").splitlines()[0]
    assert "(org-wide)" not in header
    assert "PRs merged" in header


def test_anonymous_user_is_redirected():
    from django.test import Client

    response = Client().get(reverse("dashboards:export", args=["projects", "csv"]))
    assert response.status_code == 302
