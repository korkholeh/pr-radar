"""T20: `Person.notes` must appear in no exported file — every `TABLE_SPECS` CSV, every
`TABLE_SPECS` XLSX and every report level (RISKS row 13, acceptance criterion #8). `PEOPLE_COLUMNS`
excludes `notes` by construction (T8, DECISIONS); this test proves the absence in the actual
produced bytes rather than trusting the column list alone."""

from __future__ import annotations

import datetime
import io

import openpyxl
import pytest

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory
from apps.catalog.factories import IdentityFactory, PersonFactory, ProjectFactory, RepositoryFactory
from apps.dashboards.exports.columns import TABLE_SPECS
from apps.dashboards.exports.csv import stream_csv
from apps.dashboards.exports.reports import build_report
from apps.dashboards.exports.xlsx import write_xlsx
from apps.dashboards.params import DashboardParams
from apps.dashboards.pr_filters import PRFilters
from apps.metrics.models import ScopeType
from apps.metrics.rollups import rebuild
from apps.metrics.services import bump_data_version
from apps.metrics.types import Scope

DATE_FROM = datetime.date(2026, 8, 1)
DATE_TO = datetime.date(2026, 8, 31)
SENTINEL_NOTE = "SENTINEL-PRIVATE-NOTE-do-not-export-8f2c"


def _xlsx_cell_values(content: bytes) -> set[object]:
    """An `.xlsx` is a zip of DEFLATE-compressed XML — a raw substring search over the bytes
    would silently pass even when a sentinel string *is* present, so every xlsx assertion below
    reads it back with openpyxl instead."""
    workbook = openpyxl.load_workbook(io.BytesIO(content))
    return {
        cell.value
        for sheet in workbook.worksheets
        for row in sheet.iter_rows()
        for cell in row
        if cell.value is not None
    }


def _params() -> DashboardParams:
    return DashboardParams(
        mode="period",
        preset="custom",
        date_from=DATE_FROM,
        date_to=DATE_TO,
        day=DATE_TO,
        granularity="week",
        granularity_is_auto=False,
        cohort="all",
        project_ids=(),
        repository_ids=(),
        q="",
        sort="",
        page=1,
        table="",
        pr_filters=PRFilters(),
    )


@pytest.fixture
def seeded_org_with_notes(db):
    repository = RepositoryFactory()
    project = ProjectFactory()
    project.repositories.add(repository)
    person = PersonFactory(notes=SENTINEL_NOTE)
    identity = IdentityFactory(person=person)
    PullRequestFactory(
        repository=repository,
        author=identity,
        state="merged",
        created_at=datetime.datetime(2026, 8, 5, tzinfo=datetime.UTC),
        merged_at=datetime.datetime(2026, 8, 10, tzinfo=datetime.UTC),
        last_activity_at=datetime.datetime(2026, 8, 10, tzinfo=datetime.UTC),
    )
    rebuild(DATE_FROM, DATE_TO)
    bump_data_version()
    return {"project": project, "repository": repository, "person": person}


@pytest.mark.django_db
@pytest.mark.parametrize("table_key", sorted(TABLE_SPECS))
def test_table_csv_and_xlsx_never_contain_a_persons_notes(table_key, seeded_org_with_notes):
    scope = Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=ScopeFilter(unrestricted=True))
    spec = TABLE_SPECS[table_key]
    rows = spec.row_builder(scope, _params())

    csv_response = stream_csv(spec.columns, rows, "f.csv")
    csv_bytes = b"".join(csv_response.streaming_content)
    assert SENTINEL_NOTE.encode() not in csv_bytes

    xlsx_bytes = write_xlsx(list(spec.columns), rows, "f")
    assert SENTINEL_NOTE not in _xlsx_cell_values(xlsx_bytes)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "scope_type", [ScopeType.GLOBAL, ScopeType.PROJECT, ScopeType.REPO, ScopeType.PERSON]
)
def test_report_never_contains_a_persons_notes(scope_type, seeded_org_with_notes, django_user_model):
    scope_id = {
        ScopeType.GLOBAL: None,
        ScopeType.PROJECT: seeded_org_with_notes["project"].id,
        ScopeType.REPO: seeded_org_with_notes["repository"].id,
        ScopeType.PERSON: seeded_org_with_notes["person"].id,
    }[scope_type]
    scope = Scope(scope_type=scope_type, scope_id=scope_id, access=ScopeFilter(unrestricted=True))
    user = django_user_model.objects.create_user(username="lead", password="pw")

    content = build_report(scope, _params(), user=user, language="en")

    assert SENTINEL_NOTE not in _xlsx_cell_values(content)


def test_person_notes_is_not_in_the_people_columns_list():
    people_columns = TABLE_SPECS["people"].columns
    assert "notes" not in {column.key for column in people_columns}
