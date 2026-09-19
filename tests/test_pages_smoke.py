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

from apps.activity.models import PullRequest
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


@pytest.fixture
def admin_client_(django_user_model, django_db_setup):
    """Named `admin_client_` (trailing underscore) to avoid clashing with pytest-django's own
    `admin_client` fixture, which logs in Django's `admin_user` (no `catalog.manage_settings`
    group) rather than this project's `admin` group."""
    from django.contrib.auth.models import Group

    user = django_user_model.objects.create_user(
        username="smoke-admin", password="admin-pass", is_staff=True, is_superuser=True
    )
    group, _ = Group.objects.get_or_create(name="admin")
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
    sample_pr = (
        PullRequest.objects.filter(repository__full_name=SHARED_REPO_FULL_NAME, author__isnull=False)
        .select_related("author__person")
        .first()
    )
    return [
        ("overview", reverse("dashboards:overview")),
        ("projects_index", reverse("dashboards:projects_index")),
        ("repositories_index", reverse("dashboards:repositories_index")),
        ("project", reverse("dashboards:project", args=[project_pk])),
        ("repository", reverse("dashboards:repository", args=[repository_pk])),
        ("overview_day_mode", reverse("dashboards:overview") + f"?mode=day&day={day.isoformat()}"),
        ("people_index", reverse("dashboards:people_index")),
        ("person", reverse("dashboards:person", args=[sample_pr.author.person_id])),
        ("pull_requests_index", reverse("dashboards:pull_requests_index")),
        ("pull_request_detail", reverse("dashboards:pull_request_detail", args=[sample_pr.pk])),
        ("reviews", reverse("dashboards:reviews")),
        ("exports_index", reverse("dashboards:exports_index")),
    ]


def _admin_urls() -> list[tuple[str, str]]:
    """Named pages outside `apps/dashboards` (settings consoles, sync, connections), gated by
    `catalog.manage_settings` and so rendered here as `admin_client_`, not `lead_client`. PK-addressed
    pages (edit forms, a specific connection/rule) are out of scope, matching T3's own reasoning: no
    object exists to address on the `seed_demo` fixture predictably enough to assert against, and
    their own view tests already cover the object-specific render."""
    return [
        ("ai_detection_rules", reverse("ai_detection:rules")),
        ("catalog_people", reverse("catalog:people")),
        ("catalog_identity_queue", reverse("catalog:identity_queue")),
        ("connections_list", reverse("connections:list")),
        ("connections_create", reverse("connections:create")),
        ("connections_discover", reverse("connections:discover")),
        ("github_sync", reverse("github_sync:sync")),
        ("policy_console", reverse("policy:console")),
        ("policy_settings", reverse("policy:policy_settings")),
        ("policy_sensitive_paths", reverse("policy:sensitive_paths")),
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
            "people_index",
            "person",
            "pull_requests_index",
            "pull_request_detail",
            "reviews",
            "exports_index",
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
@pytest.mark.parametrize(
    "url_key,language,theme",
    [
        (url_key, language, theme)
        for url_key in [
            "ai_detection_rules",
            "catalog_people",
            "catalog_identity_queue",
            "connections_list",
            "connections_create",
            "connections_discover",
            "github_sync",
            "policy_console",
            "policy_settings",
            "policy_sensitive_paths",
        ]
        for language in LANGUAGES
        for theme in THEMES
    ],
)
def test_every_named_page_renders_for_admin(admin_client_, url_key, language, theme):
    _prepare_client(admin_client_, language, theme)
    urls = dict(_admin_urls())

    response = admin_client_.get(urls[url_key])

    assert response.status_code == 200
    assert response.content.strip()
    assert f'data-theme="{theme}"'.encode() in response.content


@pytest.mark.django_db
def test_no_canary_english_string_in_uk_render(lead_client, admin_client_):
    _prepare_client(lead_client, "uk", "light")
    _prepare_client(admin_client_, "uk", "light")
    urls = dict(_dashboard_urls())
    admin_urls = dict(_admin_urls())

    for url_key, url in urls.items():
        response = lead_client.get(url)
        content = response.content.decode()
        for canary in CANARY_ENGLISH_STRINGS:
            assert canary not in content, f"canary {canary!r} leaked into uk render of {url_key}"

    for url_key, url in admin_urls.items():
        response = admin_client_.get(url)
        content = response.content.decode()
        for canary in CANARY_ENGLISH_STRINGS:
            assert canary not in content, f"canary {canary!r} leaked into uk render of {url_key}"
