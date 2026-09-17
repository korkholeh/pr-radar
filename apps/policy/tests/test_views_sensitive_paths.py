import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import AuditEntry
from apps.catalog.factories import ProjectFactory
from apps.policy.factories import SensitivePathRuleFactory, SensitivePathRuleForProjectFactory
from apps.policy.models import SensitivePathRule

pytestmark = pytest.mark.django_db

URL_NAMES = ["policy:sensitive_paths", "policy:sensitive_path_create"]

VALID_POST = {
    "glob": "**/secrets/**",
    "ai_mode": SensitivePathRule.AiMode.FORBIDDEN,
    "description": "Secrets directory",
    "is_active": "on",
}


@pytest.mark.parametrize("name", URL_NAMES)
def test_lead_gets_403(client, lead_user, name):
    client.force_login(lead_user)
    assert client.get(reverse(name)).status_code == 403


@pytest.mark.parametrize("name", URL_NAMES)
def test_admin_gets_200(client, admin_user, name):
    client.force_login(admin_user)
    assert client.get(reverse(name)).status_code == 200


def test_create_persists_global_rule_and_writes_audit(client, admin_user):
    client.force_login(admin_user)
    response = client.post(reverse("policy:sensitive_path_create"), VALID_POST)

    assert response.status_code == 302
    rule = SensitivePathRule.objects.get(glob="**/secrets/**")
    assert rule.project_id is None
    assert AuditEntry.objects.filter(action="sensitive_path_rule.create", object_id=str(rule.pk)).exists()


def test_create_persists_project_scoped_rule(client, admin_user):
    project = ProjectFactory()
    client.force_login(admin_user)

    payload = {**VALID_POST, "project": project.pk}
    response = client.post(reverse("policy:sensitive_path_create"), payload)

    assert response.status_code == 302
    rule = SensitivePathRule.objects.get(glob="**/secrets/**")
    assert rule.project_id == project.pk


def test_edit_updates_and_writes_audit(client, admin_user):
    rule = SensitivePathRuleFactory(glob="**/old/**")
    client.force_login(admin_user)

    payload = {**VALID_POST, "glob": "**/new/**"}
    response = client.post(reverse("policy:sensitive_path_edit", args=[rule.pk]), payload)

    assert response.status_code == 302
    rule.refresh_from_db()
    assert rule.glob == "**/new/**"
    assert AuditEntry.objects.filter(action="sensitive_path_rule.update", object_id=str(rule.pk)).exists()


def test_toggle_flips_is_active_and_writes_audit(client, admin_user):
    rule = SensitivePathRuleFactory(is_active=True)
    client.force_login(admin_user)

    response = client.post(reverse("policy:sensitive_path_toggle", args=[rule.pk]))

    assert response.status_code == 302
    rule.refresh_from_db()
    assert rule.is_active is False
    assert AuditEntry.objects.filter(action="sensitive_path_rule.toggle", object_id=str(rule.pk)).exists()


def test_bad_glob_renders_visible_field_error(client, admin_user):
    client.force_login(admin_user)
    payload = {**VALID_POST, "glob": ""}

    response = client.post(reverse("policy:sensitive_path_create"), payload)

    assert response.status_code == 200
    assert b'role="alert"' in response.content
    assert SensitivePathRule.objects.count() == 0


def test_htmx_request_returns_fragment_normal_request_returns_full_page(client, admin_user):
    SensitivePathRuleForProjectFactory()
    client.force_login(admin_user)

    full_page = client.get(reverse("policy:sensitive_paths"))
    fragment = client.get(reverse("policy:sensitive_paths"), HTTP_HX_REQUEST="true")

    assert b"<html" in full_page.content
    assert b"<html" not in fragment.content
    assert b"Sensitive paths" in fragment.content


def test_list_query_count_is_bounded(client, admin_user):
    for i in range(5):
        SensitivePathRuleFactory(glob=f"**/rule-{i}/**")
    client.force_login(admin_user)

    with CaptureQueriesContext(connection) as ctx:
        response = client.get(reverse("policy:sensitive_paths"))
    assert response.status_code == 200
    assert len(ctx.captured_queries) < 10
