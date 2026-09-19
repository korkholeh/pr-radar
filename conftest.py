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


@pytest.fixture(autouse=True)
def _metrics_cache(settings, tmp_path):
    from django.core.cache import caches

    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
            "LOCATION": str(tmp_path / "metrics-cache"),
            "OPTIONS": {"MAX_ENTRIES": 5000},
        }
    }
    caches["default"].clear()
    yield
    caches["default"].clear()


@pytest.fixture(scope="session")
def _large_scale_seed_refcount():
    return {"count": 0}


@pytest.fixture(scope="module")
def large_scale_seed(django_db_setup, django_db_blocker, _large_scale_seed_refcount):
    """`manage.py seed_demo --scale large` (50 repositories / 20,000 pull requests, ~4 minutes),
    shared across every module that depends on it via a session-scoped reference count so the seed
    runs once for the whole `pytest` session no matter how many modules need it, and is torn down
    only after the last of them finishes (round 1 review MINOR:
    `apps/dashboards/tests/test_seed_demo_scale.py` and `tests/test_performance.py` used to each own
    an independent module-scoped seed of the same volume, roughly tripling the plan's own
    "~3 minutes" budget for the added gate cost). Committed directly (`unblock()`), not inside a
    per-test transaction, so it survives across the modules that share it — and is reset via
    `reset_large_scale_data()` once the reference count reaches zero, so no later, unrelated module
    in the same session sees leftover rows."""
    from django.core.management import call_command

    from apps.dashboards.management.commands.seed_demo import reset_large_scale_data

    refcount = _large_scale_seed_refcount
    if refcount["count"] == 0:
        with django_db_blocker.unblock():
            call_command("seed_demo", scale="large", reset=True)
    refcount["count"] += 1

    yield

    refcount["count"] -= 1
    if refcount["count"] == 0:
        with django_db_blocker.unblock():
            reset_large_scale_data()


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
