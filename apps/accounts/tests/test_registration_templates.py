"""django.contrib.admin ships its own registration/{logged_out,password_reset_*}.html templates.
It is listed before apps.accounts in INSTALLED_APPS, so Django's app_directories loader picked
admin's unthemed, English-only templates over ours whenever both apps shipped a template of the
same name — the explicit `template_name=` in accounts/urls.py did not help, since it names exactly
the string both apps register a template for. The fix lives in the project's own `templates/`
directory (config/settings/base.py's `TEMPLATES[0]["DIRS"]`), which the loader checks first
regardless of INSTALLED_APPS order — verified here by asserting our own theme attribute is present
and the admin's page title is absent.
"""

import pytest
from django.urls import reverse


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name",
    [
        "accounts:password_reset",
        "accounts:password_reset_done",
        "accounts:password_reset_complete",
    ],
)
def test_registration_page_uses_app_theme_not_admin_template(client, name):
    response = client.get(reverse(name))
    content = response.content.decode()
    assert "Django site admin" not in content
    assert 'data-theme="' in content


@pytest.mark.django_db
def test_password_reset_confirm_uses_app_theme_not_admin_template(client):
    response = client.get(
        reverse("accounts:password_reset_confirm", kwargs={"uidb64": "MQ", "token": "set-password"})
    )
    content = response.content.decode()
    assert "Django site admin" not in content
    assert 'data-theme="' in content


@pytest.mark.django_db
def test_logout_uses_app_theme_not_admin_template(client, lead_user):
    client.force_login(lead_user)
    response = client.post(reverse("accounts:logout"))
    content = response.content.decode()
    assert "Django site admin" not in content
    assert 'data-theme="' in content
