import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import AuditEntry
from apps.catalog.factories import IdentityFactory, PersonFactory
from apps.catalog.models import Identity, Person

URL_NAMES = ["catalog:people", "catalog:person_create", "catalog:identity_queue", "catalog:person_merge"]


@pytest.mark.django_db
@pytest.mark.parametrize("name", URL_NAMES)
def test_lead_gets_403(client, lead_user, name):
    client.force_login(lead_user)
    assert client.get(reverse(name)).status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("name", URL_NAMES)
def test_admin_gets_200(client, admin_user, name):
    client.force_login(admin_user)
    assert client.get(reverse(name)).status_code == 200


@pytest.mark.django_db
def test_person_edit_lead_403_admin_200(client, lead_user, admin_user):
    person = PersonFactory()

    client.force_login(lead_user)
    assert client.get(reverse("catalog:person_edit", args=[person.pk])).status_code == 403

    client.force_login(admin_user)
    assert client.get(reverse("catalog:person_edit", args=[person.pk])).status_code == 200


@pytest.mark.django_db
def test_person_create_persists_and_writes_audit(client, admin_user):
    client.force_login(admin_user)
    response = client.post(
        reverse("catalog:person_create"),
        {"display_name": "Ada Lovelace", "team": "Platform", "role_hint": "dev", "notes": ""},
    )
    assert response.status_code == 302
    person = Person.objects.get(display_name="Ada Lovelace")
    assert person.team == "Platform"
    assert AuditEntry.objects.filter(action="person.create", object_id=str(person.pk)).exists()


@pytest.mark.django_db
def test_person_edit_updates_and_writes_audit(client, admin_user):
    person = PersonFactory(display_name="Old Name")
    client.force_login(admin_user)

    response = client.post(
        reverse("catalog:person_edit", args=[person.pk]),
        {"display_name": "New Name", "team": "", "role_hint": "", "notes": ""},
    )

    assert response.status_code == 302
    person.refresh_from_db()
    assert person.display_name == "New Name"
    assert AuditEntry.objects.filter(action="person.update", object_id=str(person.pk)).exists()


@pytest.mark.django_db
def test_htmx_request_returns_fragment_normal_request_returns_full_page(client, admin_user):
    client.force_login(admin_user)

    full_page = client.get(reverse("catalog:people"))
    fragment = client.get(reverse("catalog:people"), HTTP_HX_REQUEST="true")

    assert b"<html" in full_page.content
    assert b"<html" not in fragment.content
    assert b"People" in fragment.content


@pytest.mark.django_db
def test_invalid_person_form_renders_visible_alert_fragment(client, admin_user):
    client.force_login(admin_user)
    response = client.post(reverse("catalog:person_create"), {"display_name": ""})
    assert response.status_code == 200
    assert b'role="alert"' in response.content


@pytest.mark.django_db
def test_people_list_query_count(client, admin_user):
    for i in range(5):
        person = PersonFactory()
        IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value=f"login-{i}", person=person)
    client.force_login(admin_user)

    with CaptureQueriesContext(connection) as ctx:
        response = client.get(reverse("catalog:people"))
    assert response.status_code == 200
    assert len(ctx.captured_queries) < 15


@pytest.mark.django_db
def test_queue_lists_the_unmatched_email(client, admin_user):
    IdentityFactory(kind=Identity.Kind.GIT_EMAIL, value="nobody@example.com")
    client.force_login(admin_user)

    response = client.get(reverse("catalog:identity_queue"))

    assert response.status_code == 200
    assert b"nobody@example.com" in response.content


@pytest.mark.django_db
def test_queue_count_matches_the_model_count(client, admin_user):
    for i in range(3):
        IdentityFactory(kind=Identity.Kind.GIT_EMAIL, value=f"a{i}@example.com")
    mapped = PersonFactory()
    IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="mapped-login", person=mapped)
    client.force_login(admin_user)

    response = client.get(reverse("catalog:identity_queue"))
    content = response.content.decode()

    assert content.count('id="identity-row-') == Identity.objects.filter(person=None).count() == 3


@pytest.mark.django_db
def test_queue_empty_state_renders(client, admin_user):
    client.force_login(admin_user)
    response = client.get(reverse("catalog:identity_queue"))
    assert response.status_code == 200
    assert "No unmapped identities" in response.content.decode()


@pytest.mark.django_db
def test_queue_query_count(client, admin_user):
    client.force_login(admin_user)

    def query_count(n):
        Identity.objects.all().delete()
        for i in range(n):
            IdentityFactory(kind=Identity.Kind.GIT_EMAIL, value=f"b{i}-{n}@example.com")
        client.get(reverse("catalog:identity_queue"))  # warm the process-wide settings cache
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(reverse("catalog:identity_queue"))
        assert response.status_code == 200
        return len(ctx.captured_queries)

    # Query count must not scale with row count — proves no per-row N+1.
    assert query_count(5) == query_count(25)


@pytest.mark.django_db
def test_assign_attaches_identity_and_removes_it_from_the_queue(client, admin_user):
    identity = IdentityFactory(kind=Identity.Kind.GIT_EMAIL, value="assignme@example.com")
    target = PersonFactory()
    client.force_login(admin_user)

    response = client.post(reverse("catalog:identity_assign", args=[identity.pk]), {"person": target.pk})

    assert response.status_code == 302
    identity.refresh_from_db()
    assert identity.person_id == target.pk
    assert AuditEntry.objects.filter(action="identity.assign").exists()
    response = client.get(reverse("catalog:identity_queue"))
    assert b"assignme@example.com" not in response.content


@pytest.mark.django_db
def test_mark_bot_creates_a_bot_person(client, admin_user):
    identity = IdentityFactory(kind=Identity.Kind.GIT_EMAIL, value="bot@example.com")
    client.force_login(admin_user)

    response = client.post(reverse("catalog:identity_mark_bot", args=[identity.pk]))

    assert response.status_code == 302
    identity.refresh_from_db()
    assert identity.person is not None
    assert identity.person.is_bot is True
    assert AuditEntry.objects.filter(action="identity.mark_bot").exists()


@pytest.mark.django_db
def test_exclude_sets_exclude_from_metrics(client, admin_user):
    identity = IdentityFactory(kind=Identity.Kind.GIT_EMAIL, value="excludeme@example.com")
    client.force_login(admin_user)

    response = client.post(reverse("catalog:identity_exclude", args=[identity.pk]))

    assert response.status_code == 302
    identity.refresh_from_db()
    assert identity.person is not None
    assert identity.person.exclude_from_metrics is True


@pytest.mark.django_db
def test_create_person_from_identity(client, admin_user):
    identity = IdentityFactory(kind=Identity.Kind.GIT_EMAIL, value="newperson@example.com")
    client.force_login(admin_user)

    response = client.post(reverse("catalog:identity_create_person", args=[identity.pk]))

    assert response.status_code == 302
    identity.refresh_from_db()
    assert identity.person is not None
    assert identity.person.is_bot is False
    assert AuditEntry.objects.filter(action="person.create").exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name",
    [
        "catalog:identity_assign",
        "catalog:identity_create_person",
        "catalog:identity_mark_bot",
        "catalog:identity_exclude",
    ],
)
def test_get_on_mutation_url_is_405(client, admin_user, name):
    identity = IdentityFactory(kind=Identity.Kind.GIT_EMAIL, value="getme@example.com")
    client.force_login(admin_user)
    response = client.get(reverse(name, args=[identity.pk]))
    assert response.status_code == 405


@pytest.mark.django_db
def test_merge_through_ui_repoints_identities_and_deletes_source(client, admin_user):
    source = PersonFactory()
    target = PersonFactory()
    IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="source-login", person=source)
    client.force_login(admin_user)

    response = client.post(reverse("catalog:person_merge"), {"source": source.pk, "target": target.pk})

    assert response.status_code == 302
    assert not Person.objects.filter(pk=source.pk).exists()
    identity = Identity.objects.get(value="source-login")
    assert identity.person_id == target.pk


@pytest.mark.django_db
def test_merge_into_self_is_refused_with_a_visible_error(client, admin_user):
    person = PersonFactory()
    client.force_login(admin_user)

    response = client.post(reverse("catalog:person_merge"), {"source": person.pk, "target": person.pk})

    assert response.status_code == 200
    assert b'role="alert"' in response.content
    assert Person.objects.filter(pk=person.pk).exists()


@pytest.mark.django_db
def test_merge_lead_is_refused(client, lead_user):
    source = PersonFactory()
    target = PersonFactory()
    client.force_login(lead_user)

    response = client.post(reverse("catalog:person_merge"), {"source": source.pk, "target": target.pk})

    assert response.status_code == 403
    assert Person.objects.filter(pk=source.pk).exists()


CANARY_ENGLISH_STRINGS = [
    ">People<",
    "New person",
    "Edit person",
    "Merge people",
    ">Save<",
    ">Edit<",
    ">Team<",
    ">Actions<",
    "No people yet.",
    "Unmapped identities",
    "No unmapped identities. Everyone is accounted for.",
    ">Assign<",
    "Mark as bot",
    ">Exclude<",
]


@pytest.mark.django_db
def test_uk_render_has_no_canary_english(client, admin_user):
    client.force_login(admin_user)
    client.post(reverse("accounts:set_language"), {"language": "uk", "next": "/"})
    person = PersonFactory()
    IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="uk-login", person=person)
    IdentityFactory(kind=Identity.Kind.GIT_EMAIL, value="uk-unmapped@example.com")

    for name in ["catalog:people", "catalog:person_create", "catalog:identity_queue", "catalog:person_merge"]:
        response = client.get(reverse(name))
        content = response.content.decode()
        for canary in CANARY_ENGLISH_STRINGS:
            assert canary not in content, (
                f"untranslated English string {canary!r} leaked into uk render of {name}"
            )
