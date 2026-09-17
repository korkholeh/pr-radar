"""Root fixtures. The respx guard here is the whole run's guarantee that no test can
reach a real GitHub (or any other) server: any unmocked outbound HTTP request raises."""

import json
from pathlib import Path

import pytest
import respx

FIXTURES_DIR = Path(__file__).resolve().parent / "tests" / "fixtures" / "github"


def _load_github_fixture(name: str) -> dict:
    path = FIXTURES_DIR / f"{name}.json"
    return json.loads(path.read_text())


@pytest.fixture(autouse=True)
def _no_live_http_requests():
    # `with respx.mock(**kwargs):` (called with arguments) branches to a *new*, unregistered
    # router instance distinct from the module-level singleton that respx.get()/post()/etc.
    # add routes to. Configuring the singleton directly and entering it bare (`with respx.mock:`)
    # keeps route registration and interception on the same instance.
    respx.mock._assert_all_mocked = True
    respx.mock._assert_all_called = False
    with respx.mock:
        yield


@pytest.fixture
def github_fixture():
    """Loads and parses a JSON fixture from tests/fixtures/github/<name>.json."""
    return _load_github_fixture


@pytest.fixture(scope="session", autouse=True)
def _field_encryption_keys():
    """Deterministic test keys so encryption tests do not depend on the developer's .env."""
    from django.conf import settings as django_settings

    original = django_settings.FIELD_ENCRYPTION_KEYS
    django_settings.FIELD_ENCRYPTION_KEYS = ["test-encryption-key-one", "test-encryption-key-two"]
    yield
    django_settings.FIELD_ENCRYPTION_KEYS = original


@pytest.fixture(autouse=True)
def _huey_immediate(settings):
    from huey.contrib.djhuey import HUEY as huey_instance

    settings.HUEY = {**settings.HUEY, "immediate": True}
    original_immediate = huey_instance.immediate
    huey_instance.immediate = True
    try:
        yield
    finally:
        huey_instance.immediate = original_immediate


@pytest.fixture
def lead_user(django_user_model):
    from django.contrib.auth.models import Group

    user = django_user_model.objects.create_user(username="lead", password="lead-pass")
    group, _ = Group.objects.get_or_create(name="lead")
    user.groups.add(group)
    return user


@pytest.fixture
def admin_user(django_user_model):
    from django.contrib.auth.models import Group

    user = django_user_model.objects.create_user(
        username="admin", password="admin-pass", is_staff=True, is_superuser=True
    )
    group, _ = Group.objects.get_or_create(name="admin")
    user.groups.add(group)
    return user
