"""Review round 2 BLOCKER fix: the filter bar's project/repository multi-select (spec §10.1) must
actually narrow the page, not just round-trip through the query string
(`.autodev/phases/08-dashboards-and-charts/REVIEW-r2.md`). `narrow_scope()` reuses `ScopeFilter`
(the same mechanism `compute()`/`compute_many()`/every scoped selector already honour) to turn a
selected project/repository into a real restriction on `scope.access`."""

from __future__ import annotations

import datetime
import re

import pytest
from django.http import QueryDict
from django.urls import reverse

from apps.accounts.selectors import ScopeFilter, scope_for_user
from apps.activity.factories import PullRequestFactory
from apps.catalog.factories import IdentityFactory, PersonFactory, ProjectFactory, RepositoryFactory
from apps.catalog.models import Project, Repository
from apps.dashboards.params import DashboardParams, narrow_scope, parse
from apps.dashboards.services import build_dashboard
from apps.metrics.models import ScopeType
from apps.metrics.rollups import rebuild
from apps.metrics.types import Scope

DATE_FROM = datetime.date(2026, 8, 1)
DATE_TO = datetime.date(2026, 8, 31)


def _parse(query: dict, **kwargs) -> DashboardParams:
    qd = QueryDict(mutable=True)
    for key, value in query.items():
        if isinstance(value, (list, tuple)):
            qd.setlist(key, value)
        else:
            qd[key] = value
    return parse(qd, today=DATE_TO, **kwargs)


def _global_scope(access: ScopeFilter) -> Scope:
    return Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=access)


def test_narrow_scope_is_a_noop_without_any_filter():
    access = scope_for_user(None)
    scope = _global_scope(access)
    params = _parse({})

    assert narrow_scope(scope, params) is scope


def test_narrow_scope_sets_project_ids_for_an_unrestricted_caller():
    scope = _global_scope(scope_for_user(None))
    params = _parse({"project": "5"}, projects=Project.objects.none())
    params = params.replace(project_ids=(5,))

    narrowed = narrow_scope(scope, params)

    assert narrowed.access.unrestricted is False
    assert narrowed.access.project_ids == frozenset({5})
    assert narrowed.access.repository_ids is None


def test_narrow_scope_intersects_project_ids_with_an_already_restricted_access():
    """A future restricted lead (phase 9) picking a project they can see must not regain access
    to a project outside `scope_for_user()` — the filter only tightens, never widens."""
    access = ScopeFilter(unrestricted=False, project_ids=frozenset({1, 2, 3}))
    scope = _global_scope(access)
    params = _parse({}).replace(project_ids=(2, 99))

    narrowed = narrow_scope(scope, params)

    assert narrowed.access.project_ids == frozenset({2})


def test_narrow_scope_sets_repository_ids_independently_of_project_ids():
    scope = _global_scope(scope_for_user(None))
    params = _parse({}).replace(repository_ids=(7,))

    narrowed = narrow_scope(scope, params)

    assert narrowed.access.unrestricted is False
    assert narrowed.access.project_ids is None
    assert narrowed.access.repository_ids == frozenset({7})


@pytest.mark.django_db
def test_project_filter_narrows_overview_kpis_and_tables():
    project_a = ProjectFactory()
    project_b = ProjectFactory()
    repo_a = RepositoryFactory()
    repo_b = RepositoryFactory()
    project_a.repositories.add(repo_a)
    project_b.repositories.add(repo_b)
    author = IdentityFactory(person=PersonFactory())

    PullRequestFactory(
        repository=repo_a,
        author=author,
        state="merged",
        created_at=datetime.datetime(2026, 8, 5, tzinfo=datetime.UTC),
        merged_at=datetime.datetime(2026, 8, 6, tzinfo=datetime.UTC),
    )
    for offset in range(3):
        PullRequestFactory(
            repository=repo_b,
            author=author,
            state="merged",
            created_at=datetime.datetime(2026, 8, 10 + offset, tzinfo=datetime.UTC),
            merged_at=datetime.datetime(2026, 8, 12 + offset, tzinfo=datetime.UTC),
        )
    rebuild(DATE_FROM, DATE_TO)

    access = scope_for_user(None)
    scope = _global_scope(access)
    projects_queryset = Project.objects.filter(pk__in=[project_a.pk, project_b.pk])
    common_query = {"preset": "custom", "from": "2026-08-01", "to": "2026-08-31"}

    unfiltered_params = _parse(common_query, projects=projects_queryset)
    project_a_params = _parse({**common_query, "project": str(project_a.pk)}, projects=projects_queryset)

    unfiltered_prs_merged = build_dashboard(narrow_scope(scope, unfiltered_params), unfiltered_params)[
        "kpi_rows"
    ][1][0].result.value
    project_a_prs_merged = build_dashboard(narrow_scope(scope, project_a_params), project_a_params)[
        "kpi_rows"
    ][1][0].result.value

    assert unfiltered_prs_merged == 4
    assert project_a_prs_merged == 1
    assert project_a_prs_merged != unfiltered_prs_merged


@pytest.mark.django_db
def test_repository_filter_narrows_overview_kpis():
    repo_a = RepositoryFactory()
    repo_b = RepositoryFactory()
    author = IdentityFactory(person=PersonFactory())

    PullRequestFactory(
        repository=repo_a,
        author=author,
        state="merged",
        created_at=datetime.datetime(2026, 8, 5, tzinfo=datetime.UTC),
        merged_at=datetime.datetime(2026, 8, 6, tzinfo=datetime.UTC),
    )
    for offset in range(3):
        PullRequestFactory(
            repository=repo_b,
            author=author,
            state="merged",
            created_at=datetime.datetime(2026, 8, 10 + offset, tzinfo=datetime.UTC),
            merged_at=datetime.datetime(2026, 8, 12 + offset, tzinfo=datetime.UTC),
        )
    rebuild(DATE_FROM, DATE_TO)

    access = scope_for_user(None)
    scope = _global_scope(access)
    repositories_queryset = Repository.objects.filter(pk__in=[repo_a.pk, repo_b.pk])
    common_query = {"preset": "custom", "from": "2026-08-01", "to": "2026-08-31"}

    unfiltered_params = _parse(common_query, repositories=repositories_queryset)
    repo_a_params = _parse({**common_query, "repository": str(repo_a.pk)}, repositories=repositories_queryset)

    unfiltered_value = build_dashboard(narrow_scope(scope, unfiltered_params), unfiltered_params)["kpi_rows"][
        1
    ][0].result.value
    repo_a_value = build_dashboard(narrow_scope(scope, repo_a_params), repo_a_params)["kpi_rows"][1][
        0
    ].result.value

    assert unfiltered_value == 4
    assert repo_a_value == 1
    assert repo_a_value != unfiltered_value


@pytest.mark.django_db
def test_project_filter_round_trips_through_the_query_string(client, lead_user):
    project = ProjectFactory()
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:overview"), {"project": str(project.pk)})

    assert response.status_code == 200
    body = response.content.decode()
    assert f'value="{project.pk}" selected' in body


def _kpi_value(html: bytes, metric_key: str) -> str:
    """Pulls the rendered `data-testid="kpi-value"` text out of the card carrying
    `data-metric-key="<metric_key>"` (`partials/kpi_card.html`)."""
    match = re.search(
        rf'data-metric-key="{metric_key}"[\s\S]*?data-testid="kpi-value">\s*([^<]+?)\s*<', html.decode()
    )
    assert match, f"metric {metric_key!r} not found in rendered page"
    return match.group(1).strip()


@pytest.mark.django_db
def test_project_filter_narrows_overview_view_kpi(client, lead_user):
    """Round 2 audit MAJOR: `test_project_filter_narrows_overview_kpis_and_tables` above calls
    `narrow_scope()`/`build_dashboard()` directly, so it stays green even if `views.dashboard()`'s
    own `scope = params_module.narrow_scope(scope, params)` line (the actual blocker call site) is
    deleted. This drives the real view through `client.get()` instead."""
    project_a = ProjectFactory()
    project_b = ProjectFactory()
    repo_a = RepositoryFactory()
    repo_b = RepositoryFactory()
    project_a.repositories.add(repo_a)
    project_b.repositories.add(repo_b)
    author = IdentityFactory(person=PersonFactory())

    PullRequestFactory(
        repository=repo_a,
        author=author,
        state="merged",
        created_at=datetime.datetime(2026, 8, 5, tzinfo=datetime.UTC),
        merged_at=datetime.datetime(2026, 8, 6, tzinfo=datetime.UTC),
    )
    for offset in range(3):
        PullRequestFactory(
            repository=repo_b,
            author=author,
            state="merged",
            created_at=datetime.datetime(2026, 8, 10 + offset, tzinfo=datetime.UTC),
            merged_at=datetime.datetime(2026, 8, 12 + offset, tzinfo=datetime.UTC),
        )
    rebuild(DATE_FROM, DATE_TO)
    client.force_login(lead_user)
    common_query = {"preset": "custom", "from": "2026-08-01", "to": "2026-08-31"}

    unfiltered = client.get(reverse("dashboards:overview"), common_query)
    filtered = client.get(reverse("dashboards:overview"), {**common_query, "project": str(project_a.pk)})

    assert unfiltered.status_code == filtered.status_code == 200
    assert _kpi_value(unfiltered.content, "prs_merged") == "4"
    assert _kpi_value(filtered.content, "prs_merged") == "1"


@pytest.mark.django_db
def test_project_filter_narrows_chart_json_endpoint(client, lead_user):
    """Round 2 audit MAJOR: the second untested `narrow_scope()` call site, in
    `views.chart_json()`."""
    project_a = ProjectFactory()
    project_b = ProjectFactory()
    repo_a = RepositoryFactory()
    repo_b = RepositoryFactory()
    project_a.repositories.add(repo_a)
    project_b.repositories.add(repo_b)
    author = IdentityFactory(person=PersonFactory())

    PullRequestFactory(
        repository=repo_a,
        author=author,
        state="merged",
        created_at=datetime.datetime(2026, 8, 5, tzinfo=datetime.UTC),
        merged_at=datetime.datetime(2026, 8, 6, tzinfo=datetime.UTC),
    )
    for offset in range(3):
        PullRequestFactory(
            repository=repo_b,
            author=author,
            state="merged",
            created_at=datetime.datetime(2026, 8, 10 + offset, tzinfo=datetime.UTC),
            merged_at=datetime.datetime(2026, 8, 12 + offset, tzinfo=datetime.UTC),
        )
    rebuild(DATE_FROM, DATE_TO)
    client.force_login(lead_user)
    common_query = {"preset": "custom", "from": "2026-08-01", "to": "2026-08-31"}

    def _total(response) -> float:
        payload = response.json()
        return sum(value for dataset in payload["datasets"] for value in dataset["data"] if value is not None)

    unfiltered = client.get(reverse("dashboards:chart_json", args=["throughput"]), common_query)
    filtered = client.get(
        reverse("dashboards:chart_json", args=["throughput"]),
        {**common_query, "project": str(project_a.pk)},
    )

    assert unfiltered.status_code == filtered.status_code == 200
    assert _total(unfiltered) == 4
    assert _total(filtered) == 1


@pytest.mark.django_db
def test_project_filter_narrows_export_view(client, lead_user):
    """Round 2 audit MAJOR: the fourth untested `narrow_scope()` call site, in
    `views.export_table()` (acceptance criterion #5's filtering must include the project/
    repository filter bar, not just `q`/`sort`)."""
    project_a = ProjectFactory()
    project_b = ProjectFactory()
    repo_a = RepositoryFactory()
    repo_b = RepositoryFactory()
    project_a.repositories.add(repo_a)
    project_b.repositories.add(repo_b)
    author = IdentityFactory(person=PersonFactory())

    PullRequestFactory(
        repository=repo_a,
        author=author,
        state="merged",
        created_at=datetime.datetime(2026, 8, 5, tzinfo=datetime.UTC),
        merged_at=datetime.datetime(2026, 8, 6, tzinfo=datetime.UTC),
    )
    for offset in range(3):
        PullRequestFactory(
            repository=repo_b,
            author=author,
            state="merged",
            created_at=datetime.datetime(2026, 8, 10 + offset, tzinfo=datetime.UTC),
            merged_at=datetime.datetime(2026, 8, 12 + offset, tzinfo=datetime.UTC),
        )
    rebuild(DATE_FROM, DATE_TO)
    client.force_login(lead_user)
    common_query = {"preset": "custom", "from": "2026-08-01", "to": "2026-08-31"}

    def _row_count(response) -> int:
        body = b"".join(response.streaming_content).decode("utf-8-sig")
        return len(body.splitlines()) - 1

    unfiltered = client.get(reverse("dashboards:export", args=["repositories", "csv"]), common_query)
    filtered = client.get(
        reverse("dashboards:export", args=["repositories", "csv"]),
        {**common_query, "project": str(project_a.pk)},
    )

    assert unfiltered.status_code == filtered.status_code == 200
    assert _row_count(unfiltered) == 2
    assert _row_count(filtered) == 1
