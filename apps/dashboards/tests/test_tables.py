"""T17: `apps/dashboards/tables.py` — sorting, search and pagination over `TABLE_SPECS` row
dicts, and the django-tables2 `Table` classes generated from `ExportColumn` lists."""

from __future__ import annotations

import datetime

import pytest

from apps.accounts.selectors import scope_for_user
from apps.activity.factories import PullRequestFactory
from apps.activity.models import AIStatus
from apps.catalog.factories import IdentityFactory, PersonFactory, ProjectFactory, RepositoryFactory
from apps.dashboards.exports.columns import TABLE_SPECS, ExportColumn
from apps.dashboards.params import DashboardParams
from apps.dashboards.tables import _cell_html, build_table_context, paginate_rows, search_rows, sort_rows
from apps.metrics.models import ScopeType
from apps.metrics.rollups import rebuild
from apps.metrics.services import compute_many
from apps.metrics.types import Scope


def _params(**overrides: object) -> DashboardParams:
    base = dict(
        mode="period",
        preset="custom",
        date_from=datetime.date(2026, 8, 1),
        date_to=datetime.date(2026, 8, 31),
        day=datetime.date(2026, 8, 31),
        granularity="day",
        granularity_is_auto=False,
        cohort="all",
        project_ids=(),
        repository_ids=(),
        q="",
        sort="",
        page=1,
        table="",
    )
    base.update(overrides)
    return DashboardParams(**base)


def _scope() -> Scope:
    return Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=scope_for_user(None))


# -- pure sort/search helpers -------------------------------------------------------------------


def test_sort_rows_ascending_and_descending():
    rows = [{"name": "b", "count": 2}, {"name": "a", "count": 3}, {"name": "c", "count": 1}]
    keys = frozenset({"name", "count"})
    assert [row["name"] for row in sort_rows(rows, "count", keys)] == ["c", "b", "a"]
    assert [row["name"] for row in sort_rows(rows, "-count", keys)] == ["a", "b", "c"]


def test_sort_rows_none_values_sort_last_ascending():
    rows = [{"count": 2}, {"count": None}, {"count": 1}]
    ordered = sort_rows(rows, "count", frozenset({"count"}))
    assert [row["count"] for row in ordered] == [1, 2, None]


def test_sort_rows_unknown_key_leaves_order_unchanged():
    rows = [{"name": "b"}, {"name": "a"}]
    assert sort_rows(rows, "not_a_column", frozenset({"name"})) == rows
    assert sort_rows(rows, "", frozenset({"name"})) == rows


def test_search_rows_narrows_by_substring_case_insensitive():
    rows = [{"name": "Alpha Repo"}, {"name": "Beta Repo"}, {"name": "Gamma"}]
    assert [row["name"] for row in search_rows(rows, "repo", ("name",))] == ["Alpha Repo", "Beta Repo"]
    assert search_rows(rows, "", ("name",)) == rows
    assert search_rows(rows, "   ", ("name",)) == rows


@pytest.mark.django_db
def test_paginate_rows_splits_by_the_configured_page_size():
    rows = [{"name": f"row-{i}"} for i in range(30)]
    page = paginate_rows(rows, 1)
    assert page.paginator.per_page == 25
    assert len(page.object_list) == 25
    second_page = paginate_rows(rows, 2)
    assert len(second_page.object_list) == 5


# -- build_table_context, DB-backed -------------------------------------------------------------


@pytest.mark.django_db
def test_people_table_defaults_to_name_order_not_a_ranking():
    """RISKS row 1: the people table's default order is by name, never by a metric."""
    charlie = PersonFactory(display_name="Charlie")
    alice = PersonFactory(display_name="Alice")
    bob = PersonFactory(display_name="Bob")
    for person in (charlie, alice, bob):
        IdentityFactory(person=person)

    ctx = build_table_context("people", _scope(), _params(table=""))
    names = [row["name"] for row in ctx.page.object_list]
    assert names == sorted(names)


@pytest.mark.django_db
def test_repositories_table_metric_columns_match_compute_many():
    project = ProjectFactory()
    repo_a = RepositoryFactory()
    repo_b = RepositoryFactory()
    project.repositories.add(repo_a, repo_b)
    author = IdentityFactory(person=PersonFactory())
    PullRequestFactory(
        repository=repo_a,
        author=author,
        state="merged",
        merged_at=datetime.datetime(2026, 8, 10, tzinfo=datetime.UTC),
        created_at=datetime.datetime(2026, 8, 9, tzinfo=datetime.UTC),
    )

    scope = _scope()
    params = _params()
    spec = TABLE_SPECS["repositories"]
    ctx = build_table_context("repositories", scope, params)
    rows_by_id = {row["id"]: row for row in ctx.page.object_list}

    expected = compute_many(
        list(spec.metric_keys),
        ScopeType.REPO,
        [repo_a.id, repo_b.id],
        scope.access,
        params.date_from,
        params.date_to,
        granularity=params.granularity,
    )
    for repo_id, result_set in expected.items():
        row = rows_by_id[repo_id]
        for key in spec.metric_keys:
            assert row[key] == result_set[key].value


@pytest.mark.django_db
def test_projects_table_honours_the_cohort_filter():
    """Round 2 review MINOR fix: switching the filter bar's cohort must move the metric tables,
    not just the KPI row and the charts."""
    project = ProjectFactory()
    repo = RepositoryFactory()
    project.repositories.add(repo)
    author = IdentityFactory(person=PersonFactory())
    PullRequestFactory(
        repository=repo,
        author=author,
        state="merged",
        ai_status=AIStatus.AI_EXPLICIT,
        created_at=datetime.datetime(2026, 8, 9, tzinfo=datetime.UTC),
        merged_at=datetime.datetime(2026, 8, 10, tzinfo=datetime.UTC),
    )
    PullRequestFactory(
        repository=repo,
        author=author,
        state="merged",
        ai_status=AIStatus.NO_AI,
        created_at=datetime.datetime(2026, 8, 11, tzinfo=datetime.UTC),
        merged_at=datetime.datetime(2026, 8, 12, tzinfo=datetime.UTC),
    )
    rebuild(datetime.date(2026, 8, 1), datetime.date(2026, 8, 31))

    scope = _scope()
    all_row = build_table_context("projects", scope, _params(cohort="all")).page.object_list[0]
    ai_row = build_table_context("projects", scope, _params(cohort="ai")).page.object_list[0]

    assert all_row["prs_merged"] == 2
    assert ai_row["prs_merged"] == 1


@pytest.mark.django_db
def test_search_and_sort_apply_only_to_the_active_table():
    project_a = ProjectFactory(name="Zeta Project")
    project_b = ProjectFactory(name="Alpha Project")

    ctx_inactive_search = build_table_context(
        "projects", _scope(), _params(table="people", q="Zeta", sort="name")
    )
    names = [row["name"] for row in ctx_inactive_search.page.object_list]
    assert {project_a.name, project_b.name}.issubset(set(names))

    ctx_active_search = build_table_context("projects", _scope(), _params(table="projects", q="Zeta"))
    names = [row["name"] for row in ctx_active_search.page.object_list]
    assert names == [project_a.name]


@pytest.mark.django_db
def test_projects_table_sort_by_name_reorders_rows():
    ProjectFactory(name="Zeta Project")
    ProjectFactory(name="Alpha Project")

    ctx = build_table_context("projects", _scope(), _params(table="projects", sort="name"))
    names = [row["name"] for row in ctx.page.object_list]
    assert names == sorted(names)

    ctx_desc = build_table_context("projects", _scope(), _params(table="projects", sort="-name"))
    names_desc = [row["name"] for row in ctx_desc.page.object_list]
    assert names_desc == sorted(names_desc, reverse=True)


def test_delta_cell_html_renders_in_the_metrics_own_unit_not_a_percent():
    """Round-1 BLOCKER regression: the table cell for a delta column must use the delta column's
    own type (fixed in `exports/columns.py`) — a count delta of +5 must render "5", not "500.0%",
    and a duration delta of -3600s must render as a duration, not "-360000.0%"."""
    count_delta = ExportColumn(key="prs_merged__delta", title="PRs merged (Δ)", type="int")
    duration_delta = ExportColumn(key="lead_time_p50__delta", title="Lead time (Δ)", type="duration")
    assert _cell_html(count_delta, 5.0) == "5"
    rendered_duration = str(_cell_html(duration_delta, -3600.0))
    assert "%" not in rendered_duration


@pytest.mark.django_db
def test_table_instance_has_a_column_per_export_column():
    ctx = build_table_context("recent_prs", _scope(), _params())
    spec = TABLE_SPECS["recent_prs"]
    column_names = {column.name for column in ctx.table.columns}
    assert column_names == {column.key for column in spec.columns}
