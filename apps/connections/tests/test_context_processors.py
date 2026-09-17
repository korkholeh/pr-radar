import datetime

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.connections.context_processors import connection_alerts
from apps.connections.factories import GitHubConnectionFactory
from apps.connections.models import GitHubConnection

factory = RequestFactory()


def _request(user):
    request = factory.get("/")
    request.user = user
    return request


@pytest.mark.django_db
def test_admin_sees_invalid_expired_and_soon_to_expire_connections(admin_user):
    invalid = GitHubConnectionFactory(status=GitHubConnection.Status.INVALID)
    expired = GitHubConnectionFactory(status=GitHubConnection.Status.EXPIRED)
    expiring_soon = GitHubConnectionFactory(
        status=GitHubConnection.Status.OK,
        expires_at=timezone.now() + datetime.timedelta(days=13),
    )
    not_expiring_soon = GitHubConnectionFactory(
        status=GitHubConnection.Status.OK,
        expires_at=timezone.now() + datetime.timedelta(days=30),
    )

    alerts = connection_alerts(_request(admin_user))["connection_alerts"]

    assert invalid in alerts
    assert expired in alerts
    assert expiring_soon in alerts
    assert not_expiring_soon not in alerts


@pytest.mark.django_db
def test_lead_never_sees_the_banner(lead_user):
    GitHubConnectionFactory(status=GitHubConnection.Status.INVALID)

    context = connection_alerts(_request(lead_user))

    assert context == {}


@pytest.mark.django_db
def test_anonymous_costs_no_query(django_assert_max_num_queries):
    from django.contrib.auth.models import AnonymousUser

    with django_assert_max_num_queries(0):
        connection_alerts(_request(AnonymousUser()))


@pytest.mark.django_db
def test_processor_costs_a_fixed_low_query_count_for_admin_and_a_small_ceiling_for_lead(
    admin_user, lead_user, django_assert_max_num_queries
):
    """Two fixed queries for a superuser admin — has_perm() short-circuits on is_superuser (no
    query), then the TOKEN_EXPIRY_WARNING_DAYS setting lookup (uncached, same as every other
    apps.catalog.services.get_int() call site) and the one connections lookup — neither scaling
    with the number of connections. A lead costs a small, fixed number of queries for has_perm()'s
    permission lookup (user + group permissions) and stops there since they lack
    catalog.manage_settings."""
    GitHubConnectionFactory(status=GitHubConnection.Status.INVALID)
    GitHubConnectionFactory(status=GitHubConnection.Status.INVALID)
    GitHubConnectionFactory(status=GitHubConnection.Status.INVALID)

    with django_assert_max_num_queries(2):
        connection_alerts(_request(admin_user))

    with django_assert_max_num_queries(2):
        connection_alerts(_request(lead_user))


@pytest.mark.django_db
def test_non_staff_admin_group_member_sees_the_banner():
    """is_staff is not tied to admin-group membership anywhere in this app (docs/SETUP.md's
    onboarding flow only sets it for the bootstrap superuser); a non-staff user added to the
    admin group must still see the one banner that tells them a token has died."""
    from django.contrib.auth.models import Group

    user = get_user_model().objects.create_user(username="non-staff-admin", password="x")
    group, _ = Group.objects.get_or_create(name="admin")
    user.groups.add(group)
    GitHubConnectionFactory(status=GitHubConnection.Status.INVALID)

    context = connection_alerts(_request(user))

    assert context["connection_alerts"]


@pytest.mark.django_db
def test_banner_rendered_for_admin_and_absent_for_lead(client, admin_user, lead_user):
    GitHubConnectionFactory(status=GitHubConnection.Status.INVALID)

    client.force_login(admin_user)
    admin_content = client.get(reverse("dashboards:overview")).content.decode()
    assert 'role="alert"' in admin_content

    client.force_login(lead_user)
    lead_content = client.get(reverse("dashboards:overview")).content.decode()
    assert 'role="alert"' not in lead_content
