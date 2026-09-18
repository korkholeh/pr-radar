"""T23: page smoke test (phase 8 acceptance criterion #1). Renders every dashboard page on
`seed_demo` data in both UI languages and both themes, asserting a 200 and a non-empty body, and
that no canary English string (`tests/test_translations.py::CANARY_ENGLISH_STRINGS`) leaks into
the Ukrainian render.

`seed_demo` takes several seconds, so it is seeded once for the whole module via the documented
pytest-django pattern (wrapping `django_db_setup`) rather than once per parametrized case.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from apps.catalog.models import Project, Repository
from apps.dashboards.tests.test_seed_demo import SHARED_REPO_FULL_NAME
from apps.metrics.timeframe import today
from tests.test_translations import CANARY_ENGLISH_STRINGS

LANGUAGES = ["en", "uk"]
THEMES = ["light", "dark"]


@pytest.fixture(scope="module")
def django_db_setup(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        call_command("seed_demo")
    yield


@pytest.fixture
def lead_client(django_user_model, django_db_setup):
    from django.contrib.auth.models import Group

    user = django_user_model.objects.create_user(username="smoke-lead", password="lead-pass")
    group, _ = Group.objects.get_or_create(name="lead")
    user.groups.add(group)

    client = Client()
    client.force_login(user)
    return client


def _prepare_client(lead_client: Client, language: str, theme: str) -> Client:
    lead_client.post(reverse("accounts:set_language"), {"language": language, "next": "/"})
    lead_client.post(reverse("accounts:set_theme"), {"theme": theme, "next": "/"})
    return lead_client


def _dashboard_urls() -> list[tuple[str, str]]:
    project_pk = Project.objects.filter(repositories__full_name=SHARED_REPO_FULL_NAME).first().pk
    repository_pk = Repository.objects.get(full_name=SHARED_REPO_FULL_NAME).pk
    day = today()
    return [
        ("overview", reverse("dashboards:overview")),
        ("projects_index", reverse("dashboards:projects_index")),
        ("repositories_index", reverse("dashboards:repositories_index")),
        ("project", reverse("dashboards:project", args=[project_pk])),
        ("repository", reverse("dashboards:repository", args=[repository_pk])),
        ("overview_day_mode", reverse("dashboards:overview") + f"?mode=day&day={day.isoformat()}"),
    ]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url_key,language,theme",
    [
        (url_key, language, theme)
        for url_key in [
            "overview",
            "projects_index",
            "repositories_index",
            "project",
            "repository",
            "overview_day_mode",
        ]
        for language in LANGUAGES
        for theme in THEMES
    ],
)
def test_every_dashboard_page_renders(lead_client, url_key, language, theme):
    _prepare_client(lead_client, language, theme)
    urls = dict(_dashboard_urls())

    response = lead_client.get(urls[url_key])

    assert response.status_code == 200
    assert response.content.strip()
    assert f'data-theme="{theme}"'.encode() in response.content


@pytest.mark.django_db
def test_no_canary_english_string_in_uk_render(lead_client):
    _prepare_client(lead_client, "uk", "light")
    urls = dict(_dashboard_urls())

    for url_key, url in urls.items():
        response = lead_client.get(url)
        content = response.content.decode()
        for canary in CANARY_ENGLISH_STRINGS:
            assert canary not in content, f"canary {canary!r} leaked into uk render of {url_key}"
