"""T5: `/people/` — the lead-facing People index, `views.people_index`. Reuses the `people`
`TableSpec` exactly like `projects_index`/`repositories_index` (filter bar, search, sort,
pagination, CSV/XLSX export for free)."""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory
from apps.catalog.factories import IdentityFactory, PersonFactory, RepositoryFactory
from apps.dashboards import rows
from apps.dashboards.params import DashboardParams
from apps.metrics.models import ScopeType
from apps.metrics.rollups import rebuild
from apps.metrics.services import bump_data_version
from apps.metrics.types import Scope

DATE_FROM = datetime.date(2026, 8, 1)
DATE_TO = datetime.date(2026, 8, 31)
PERIOD_QS = "preset=custom&from=2026-08-01&to=2026-08-31&granularity=week"


def _seed_people() -> list[str]:
    repository = RepositoryFactory()
    names = ["Zoe", "Amir", "Mona"]
    for name in names:
        identity = IdentityFactory(person=PersonFactory(display_name=name))
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
    return names


@pytest.mark.django_db
def test_people_index_returns_200_for_a_lead(client, lead_user):
    _seed_people()
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:people_index") + f"?{PERIOD_QS}")

    assert response.status_code == 200
    for name in ("Zoe", "Amir", "Mona"):
        assert name in response.content.decode()


@pytest.mark.django_db
def test_people_table_defaults_to_name_order(client, lead_user):
    names = _seed_people()
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:people_index") + f"?{PERIOD_QS}")
    body = response.content.decode()
    positions = [body.index(name) for name in sorted(names)]

    assert positions == sorted(positions)

    scope = Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=ScopeFilter(unrestricted=True))
    params = DashboardParams(
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
    )
    built_rows = rows.people_rows(scope, params)
    assert [row["name"] for row in built_rows] == sorted(names)


@pytest.mark.django_db
def test_people_index_query_count_is_bounded(client, lead_user, django_assert_num_queries):
    """Pinned, not derived: `PEOPLE_METRIC_KEYS` includes distribution/state metrics that cost one
    query per person (documented, accepted cost — see
    `test_query_counts.py::test_people_table_query_count_grows_by_the_known_per_person_cost`), so
    this bound moves with the fixture's person count, not with an unrelated N+1."""
    _seed_people()
    client.force_login(lead_user)

    with django_assert_num_queries(131):
        client.get(reverse("dashboards:people_index") + f"?{PERIOD_QS}")
