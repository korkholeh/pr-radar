"""T8: private notes card + `POST /people/<pk>/notes/`."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.accounts.factories import UserProjectAccessFactory
from apps.accounts.models import AuditEntry
from apps.catalog.factories import PersonFactory, ProjectFactory


@pytest.mark.django_db
def test_saving_notes_round_trips(client, lead_user):
    person = PersonFactory()
    client.force_login(lead_user)

    url = reverse("dashboards:person_notes", args=[person.pk])
    response = client.post(url, {"notes": "Promoted to senior in July."})

    assert response.status_code == 302
    person.refresh_from_db()
    assert person.notes == "Promoted to senior in July."


@pytest.mark.django_db
def test_htmx_post_returns_notes_fragment(client, lead_user):
    person = PersonFactory()
    client.force_login(lead_user)

    url = reverse("dashboards:person_notes", args=[person.pk])
    response = client.post(url, {"notes": "Some private note."}, HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    body = response.content.decode()
    assert "Some private note." in body
    assert "<html" not in body.lower()


@pytest.mark.django_db
def test_saving_notes_writes_audit_entry_without_note_text(client, lead_user):
    person = PersonFactory(notes="old note")
    client.force_login(lead_user)

    url = reverse("dashboards:person_notes", args=[person.pk])
    client.post(url, {"notes": "brand new secret note"})

    entry = AuditEntry.objects.get(action="person.notes")
    assert entry.actor == lead_user
    assert entry.object_id == str(person.pk)
    assert entry.changes["before"] == {"length": len("old note")}
    assert entry.changes["after"] == {"length": len("brand new secret note")}
    serialized = str(entry.changes)
    assert "brand new secret note" not in serialized
    assert "old note" not in serialized


@pytest.mark.django_db
def test_out_of_scope_person_notes_is_404(client, lead_user):
    person = PersonFactory()
    other_project = ProjectFactory()
    UserProjectAccessFactory(user=lead_user, project=other_project)
    client.force_login(lead_user)

    url = reverse("dashboards:person_notes", args=[person.pk])
    response = client.post(url, {"notes": "nope"})

    assert response.status_code == 404


@pytest.mark.django_db
def test_anonymous_post_redirects_to_login(client):
    person = PersonFactory()

    url = reverse("dashboards:person_notes", args=[person.pk])
    response = client.post(url, {"notes": "nope"})

    assert response.status_code == 302
    assert "/login" in response.url or "login" in response.url
