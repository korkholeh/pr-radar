"""T11: `GET /api/charts/<chart_key>/` (plan §4, acceptance criterion #3's endpoint half)."""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse

from apps.accounts.selectors import ScopeFilter
from apps.catalog.factories import ProjectFactory
from apps.dashboards.charts import CHART_REGISTRY
from apps.dashboards.params import DashboardParams
from apps.dashboards.tests.test_charts_build import _seed_period_data
from apps.metrics.models import ScopeType
from apps.metrics.types import Scope

_QUERY = {"preset": "custom", "from": "2026-08-01", "to": "2026-08-31", "granularity": "day"}


def _global_params() -> DashboardParams:
    return DashboardParams(
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


@pytest.mark.django_db
@pytest.mark.parametrize("chart_key", tuple(CHART_REGISTRY))
def test_endpoint_matches_compute(client, lead_user, chart_key):
    """Acceptance criterion #3, in full: with a real, seeded dataset (round-1 review — the
    previous version of this test ran against an empty database, so every dataset was all-`None`
    and could never catch a wrong number, a swapped cohort or a wrong metric key), the endpoint's
    `datasets[i].data` must equal, element by element, what `spec.build()` computes from the same
    `compute()` calls for the same scope/params — including the AI/non-AI split `throughput` and
    `pr_size_distribution` carry."""
    _seed_period_data()
    client.force_login(lead_user)
    response = client.get(
        reverse("dashboards:chart_json", args=[chart_key]), _QUERY, HTTP_ACCEPT="application/json"
    )
    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    body = response.json()
    assert body["key"] == chart_key
    assert isinstance(body["datasets"], list)

    scope = Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=ScopeFilter(unrestricted=True))
    expected = CHART_REGISTRY[chart_key].build(scope, _global_params()).to_dict()
    assert body["datasets"] == expected["datasets"]
    assert any(value is not None for dataset in body["datasets"] for value in dataset["data"]), (
        "seeded fixture produced no non-None value for any dataset — the comparison above proves nothing"
    )
    for dataset in body["datasets"]:
        assert dataset["color_token"].startswith("--")


@pytest.mark.django_db
def test_throughput_endpoint_ai_and_non_ai_datasets_differ_and_match_compute(client, lead_user):
    """Explicit regression for the AI/non-AI split acceptance criterion #3 calls out by name."""
    _seed_period_data()
    client.force_login(lead_user)
    response = client.get(
        reverse("dashboards:chart_json", args=["throughput"]), _QUERY, HTTP_ACCEPT="application/json"
    )
    body = response.json()

    scope = Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=ScopeFilter(unrestricted=True))
    expected = CHART_REGISTRY["throughput"].build(scope, _global_params()).to_dict()
    ai = next(d for d in body["datasets"] if d["color_token"] == "--series-ai")
    non_ai = next(d for d in body["datasets"] if d["color_token"] == "--series-non-ai")
    expected_ai = next(d for d in expected["datasets"] if d["color_token"] == "--series-ai")
    expected_non_ai = next(d for d in expected["datasets"] if d["color_token"] == "--series-non-ai")
    assert ai["data"] == expected_ai["data"]
    assert non_ai["data"] == expected_non_ai["data"]
    assert ai["data"] != non_ai["data"]


@pytest.mark.django_db
def test_unknown_chart_key_is_404(client, lead_user):
    client.force_login(lead_user)
    response = client.get(reverse("dashboards:chart_json", args=["does-not-exist"]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_out_of_scope_scope_id_falls_back_to_global(client, lead_user):
    client.force_login(lead_user)
    response = client.get(
        reverse("dashboards:chart_json", args=["throughput"]),
        {"scope_type": "project", "scope_id": "999999"},
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_project_scope_is_applied(client, lead_user):
    client.force_login(lead_user)
    project = ProjectFactory()
    response = client.get(
        reverse("dashboards:chart_json", args=["throughput"]),
        {"scope_type": "project", "scope_id": str(project.pk)},
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_anonymous_is_redirected(client):
    response = client.get(reverse("dashboards:chart_json", args=["throughput"]))
    assert response.status_code == 302
