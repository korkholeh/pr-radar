"""T19: `views.export_report()` + `/report.xlsx` (plan §5/§7)."""

from __future__ import annotations

import datetime
import io

import openpyxl
import pytest
from django.urls import reverse

from apps.activity.factories import PullRequestFactory
from apps.catalog.factories import IdentityFactory, ProjectFactory, RepositoryFactory
from apps.metrics.models import ScopeType

PERIOD_QS = "preset=custom&from=2026-08-01&to=2026-08-31&granularity=week"


@pytest.mark.django_db
def test_overview_report_is_a_valid_xlsx_with_ascii_filename(client, lead_user):
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:export_report") + f"?{PERIOD_QS}")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    disposition = response["Content-Disposition"]
    assert disposition.isascii()
    assert disposition.startswith("attachment; filename=")
    workbook = openpyxl.load_workbook(io.BytesIO(response.content))
    assert workbook.sheetnames[0] == "Summary"


@pytest.mark.django_db
def test_project_and_person_scope_reports_200(client, lead_user):
    client.force_login(lead_user)
    repository = RepositoryFactory()
    project = ProjectFactory()
    project.repositories.add(repository)

    response = client.get(
        reverse("dashboards:export_report")
        + f"?scope_type={ScopeType.PROJECT}&scope_id={project.id}&{PERIOD_QS}"
    )

    assert response.status_code == 200
    workbook = openpyxl.load_workbook(io.BytesIO(response.content))
    assert "Repositories" in workbook.sheetnames


@pytest.mark.django_db
def test_unknown_scope_type_is_404(client, lead_user):
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:export_report") + "?scope_type=not_a_level")

    assert response.status_code == 404


@pytest.mark.django_db
def test_day_mode_report_uses_the_day_in_the_filename(client, lead_user):
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:export_report") + "?mode=day&day=2026-08-15")

    assert response.status_code == 200
    assert "2026-08-15" in response["Content-Disposition"]


@pytest.mark.django_db
def test_day_mode_report_only_contains_that_days_prs(client, lead_user):
    """Round 2 review MAJOR (rejected as a real bug, see DECISIONS): `views.export_report()`
    builds `params` via `params_module.parse()`, which already forces
    `date_from == date_to == day` in day mode (`apps/dashboards/params.py`) before `build_report()`
    ever sees it — so a day-mode report is already narrowed to that single day, not the surrounding
    default 30-day period, through the real request flow (not just when hand-constructing
    `DashboardParams` directly, which is what `test_reports.py`'s own `_params()` helper does)."""
    client.force_login(lead_user)
    repository = RepositoryFactory()
    identity = IdentityFactory()
    day_a_pr = PullRequestFactory(
        repository=repository,
        author=identity,
        number=101,
        created_at=datetime.datetime(2026, 8, 15, 12, tzinfo=datetime.UTC),
    )
    day_b_pr = PullRequestFactory(
        repository=repository,
        author=identity,
        number=202,
        created_at=datetime.datetime(2026, 8, 20, 12, tzinfo=datetime.UTC),
    )

    response = client.get(reverse("dashboards:export_report") + "?mode=day&day=2026-08-15")

    assert response.status_code == 200
    workbook = openpyxl.load_workbook(io.BytesIO(response.content))
    prs_sheet = workbook["PRs"]
    numbers = {row[1].value for row in prs_sheet.iter_rows(min_row=2, max_col=2) if row[1].value is not None}
    assert numbers == {str(day_a_pr.number)}
    assert str(day_b_pr.number) not in numbers


@pytest.mark.django_db
def test_report_link_appears_on_the_overview_page(client, lead_user):
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:overview") + f"?{PERIOD_QS}")

    assert response.status_code == 200
    assert b'data-testid="download-report-link"' in response.content
