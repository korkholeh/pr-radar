import pytest
from django.urls import reverse

from apps.accounts.models import UserPreference


@pytest.mark.django_db
def test_theme_persisted_for_user(client, lead_user):
    client.force_login(lead_user)
    response = client.post(reverse("accounts:set_theme"), {"theme": "dark", "next": "/"})
    assert response.status_code == 302
    assert response.cookies["pr_radar_theme"].value == "dark"
    preference = UserPreference.objects.get(user=lead_user)
    assert preference.theme == "dark"

    page = client.get(reverse("dashboards:overview"))
    assert 'data-theme="dark"' in page.content.decode()


@pytest.mark.django_db
def test_anonymous_post_sets_only_the_cookie(client):
    response = client.post(reverse("accounts:set_theme"), {"theme": "dark", "next": "/"})
    assert response.status_code == 302
    assert response.cookies["pr_radar_theme"].value == "dark"
    assert not UserPreference.objects.exists()


@pytest.mark.django_db
def test_invalid_theme_value_returns_400_with_visible_message(client):
    response = client.post(reverse("accounts:set_theme"), {"theme": "purple", "next": "/"})
    assert response.status_code == 400
    assert b'role="alert"' in response.content


@pytest.mark.django_db
def test_system_preference_renders_theme_preference_attribute(client, lead_user):
    client.force_login(lead_user)
    page = client.get(reverse("dashboards:overview"))
    assert 'data-theme-preference="system"' in page.content.decode()


@pytest.mark.django_db
def test_theme_survives_logout_and_fresh_login(client, lead_user):
    client.force_login(lead_user)
    client.post(reverse("accounts:set_theme"), {"theme": "dark", "next": "/"})
    client.post(reverse("accounts:logout"))
    client.force_login(lead_user)
    page = client.get(reverse("dashboards:overview"))
    assert 'data-theme="dark"' in page.content.decode()


def test_inline_theme_script_precedes_stylesheet_link():
    from django.test import Client

    response = Client().get(reverse("accounts:login"))
    content = response.content.decode()
    script_index = content.index("<script>")
    stylesheet_index = content.index('rel="stylesheet"')
    assert script_index < stylesheet_index


@pytest.mark.django_db
def test_hx_headers_carries_csrf_token(client, lead_user):
    client.force_login(lead_user)
    page = client.get(reverse("dashboards:overview"))
    assert "X-CSRFToken" in page.content.decode()


def test_404_renders_themed_template_under_debug_false(client, settings):
    settings.DEBUG = False
    response = client.get("/this-page-does-not-exist/")
    assert response.status_code == 404
    assert b"data-theme=" in response.content
