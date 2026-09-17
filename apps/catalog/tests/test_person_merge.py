import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import AuditEntry
from apps.activity.factories import PullRequestFactory
from apps.catalog.factories import IdentityFactory, PersonFactory
from apps.catalog.identity import merge_people
from apps.catalog.models import Identity, Person


@pytest.mark.django_db
def test_merge_repoints_every_identity():
    source = PersonFactory(display_name="Source")
    target = PersonFactory(display_name="Target")
    identities = [
        IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value=f"login-{i}", person=source) for i in range(3)
    ]

    merge_people(source, target, actor=None)

    for identity in identities:
        identity.refresh_from_db()
        assert identity.person_id == target.id


@pytest.mark.django_db
def test_merge_leaves_no_orphan_identity_or_pr():
    source = PersonFactory(display_name="Source")
    target = PersonFactory(display_name="Target")
    identity = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="octocat", person=source)
    pull_request = PullRequestFactory(author=identity)
    identity_count_before = Identity.objects.count()
    unmapped_before = Identity.objects.filter(person=None).count()

    merge_people(source, target, actor=None)

    assert Identity.objects.count() == identity_count_before
    assert Identity.objects.filter(person=None).count() == unmapped_before
    assert not Person.objects.filter(pk=source.pk).exists()
    pull_request.refresh_from_db()
    assert pull_request.author.person_id == target.id


@pytest.mark.django_db
def test_merge_preserves_notes():
    source = PersonFactory(display_name="Source", notes="source note")
    target = PersonFactory(display_name="Target", notes="target note")

    merge_people(source, target, actor=None)

    target.refresh_from_db()
    assert "source note" in target.notes
    assert "target note" in target.notes


@pytest.mark.django_db
def test_merge_writes_an_audit_entry(admin_user):
    source = PersonFactory(display_name="Source")
    target = PersonFactory(display_name="Target")

    merge_people(source, target, actor=admin_user)

    assert AuditEntry.objects.filter(action="person.merge", object_id=str(target.pk)).exists()


@pytest.mark.django_db
def test_merging_a_person_into_itself_is_refused():
    person = PersonFactory()
    with pytest.raises(ValidationError):
        merge_people(person, person, actor=None)
