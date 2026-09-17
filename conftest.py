"""Root fixtures. The respx guard here is the whole run's guarantee that no test can
reach a real GitHub (or any other) server: any unmocked outbound HTTP request raises."""

import pytest
import respx


@pytest.fixture(autouse=True)
def _no_live_http_requests():
    with respx.mock(assert_all_mocked=True, assert_all_called=False):
        yield


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
