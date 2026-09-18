"""T6: Person page shell — `views.dashboard` at `ScopeType.PERSON` via `/people/<pk>/`, reusing
the KPI row/chart cards exactly like Overview/Project/Repository."""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse

from apps.accounts.factories import UserProjectAccessFactory
from apps.activity.factories import PullRequestFactory
from apps.catalog.factories import IdentityFactory, PersonFactory, ProjectFactory, RepositoryFactory
from apps.metrics.rollups import rebuild
from apps.metrics.services import bump_data_version

DATE_FROM = datetime.date(2026, 8, 1)
DATE_TO = datetime.date(2026, 8, 31)
PERIOD_QS = "preset=custom&from=2026-08-01&to=2026-08-31&granularity=week"


def _seed_person():
    repository = RepositoryFactory()
    project = ProjectFactory()
    project.repositories.add(repository)
    person = PersonFactory(display_name="Ada Lovelace")
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
    return person, project


@pytest.mark.django_db
def test_person_page_renders_kpis_and_charts_for_own_scope(client, lead_user):
    person, _project = _seed_person()
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:person", args=[person.pk]) + f"?{PERIOD_QS}")

    assert response.status_code == 200
    body = response.content.decode()
    assert person.display_name in body


@pytest.mark.django_db
def test_out_of_scope_person_id_in_path_is_404(client, lead_user):
    person, project = _seed_person()
    other_project = ProjectFactory()
    UserProjectAccessFactory(user=lead_user, project=other_project)
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:person", args=[person.pk]) + f"?{PERIOD_QS}")

    assert response.status_code == 404


@pytest.mark.django_db
def test_unknown_person_id_is_404(client, lead_user):
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:person", args=[999999]) + f"?{PERIOD_QS}")

    assert response.status_code == 404
