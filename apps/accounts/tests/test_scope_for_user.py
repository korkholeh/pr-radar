import pytest
from django.contrib.auth.models import AnonymousUser

from apps.accounts.factories import UserProjectAccessFactory
from apps.accounts.selectors import scope_for_user
from apps.catalog.factories import ProjectFactory


@pytest.mark.django_db
def test_no_grant_rows_is_unrestricted(lead_user):
    scope = scope_for_user(lead_user)
    assert scope.unrestricted is True
    assert scope.project_ids is None


@pytest.mark.django_db
def test_grant_rows_restrict_to_exactly_those_projects(lead_user):
    granted = ProjectFactory()
    other = ProjectFactory()
    UserProjectAccessFactory(user=lead_user, project=granted)

    scope = scope_for_user(lead_user)

    assert scope.unrestricted is False
    assert scope.project_ids == frozenset({granted.id})
    assert other.id not in scope.project_ids


@pytest.mark.django_db
def test_anonymous_user_is_restricted_empty():
    scope = scope_for_user(AnonymousUser())
    assert scope.unrestricted is False
    assert scope.project_ids == frozenset()


@pytest.mark.django_db
def test_none_user_is_restricted_empty():
    scope = scope_for_user(None)
    assert scope.unrestricted is False
    assert scope.project_ids == frozenset()


@pytest.mark.django_db
def test_superuser_with_grant_rows_is_restricted(admin_user):
    granted = ProjectFactory()
    UserProjectAccessFactory(user=admin_user, project=granted)

    scope = scope_for_user(admin_user)

    assert scope.unrestricted is False
    assert scope.project_ids == frozenset({granted.id})


@pytest.mark.django_db
def test_scope_is_memoised_per_request(django_assert_num_queries, lead_user):
    granted = ProjectFactory()
    UserProjectAccessFactory(user=lead_user, project=granted)

    with django_assert_num_queries(1):
        scope_for_user(lead_user)
        scope_for_user(lead_user)
        scope_for_user(lead_user)
