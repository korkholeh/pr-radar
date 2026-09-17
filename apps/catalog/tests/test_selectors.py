import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.selectors import scope_for_user
from apps.catalog.factories import IdentityFactory, PersonFactory
from apps.catalog.models import Identity
from apps.catalog.selectors import people_for_settings, unmapped_identities, unmapped_identity_count


@pytest.mark.django_db
def test_unmapped_identities_lists_only_unmapped_rows():
    mapped = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="mapped", person=PersonFactory())
    unmapped = IdentityFactory(kind=Identity.Kind.GIT_EMAIL, value="nobody@example.com")
    scope = scope_for_user(None)

    result = list(unmapped_identities(scope))

    assert unmapped in result
    assert mapped not in result


@pytest.mark.django_db
def test_unmapped_identity_count_matches_queryset():
    IdentityFactory(kind=Identity.Kind.GIT_EMAIL, value="a@example.com")
    IdentityFactory(kind=Identity.Kind.GIT_EMAIL, value="b@example.com")
    scope = scope_for_user(None)

    assert unmapped_identity_count(scope) == 2


@pytest.mark.django_db
def test_people_for_settings_query_count():
    for i in range(5):
        person = PersonFactory()
        IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value=f"login-{i}", person=person)

    scope = scope_for_user(None)
    with CaptureQueriesContext(connection) as ctx:
        for person in people_for_settings(scope):
            list(person.identities.all())

    assert len(ctx.captured_queries) <= 3
