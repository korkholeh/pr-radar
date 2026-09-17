from pathlib import Path

import pytest
from django.db import IntegrityError, transaction

from apps.catalog.models import SERIES_COLOR_CHOICES, Identity, Organization, Person, Project, Repository
from apps.connections.models import GitHubConnection


def _make_repository(connection=None, full_name="acme/repo", github_id="R_1"):
    org = Organization.objects.create(login="acme", type=Organization.Type.ORG, github_id=f"O_{full_name}")
    connection = connection or GitHubConnection.objects.create(
        name=f"conn-{full_name}", kind=GitHubConnection.Kind.FINE_GRAINED_PAT
    )
    return Repository.objects.create(
        organization=org,
        connection=connection,
        name=full_name.split("/")[-1],
        full_name=full_name,
        github_id=github_id,
    )


@pytest.mark.django_db
def test_duplicate_identity_rejected():
    Identity.objects.create(kind=Identity.Kind.GITHUB_LOGIN, value="octocat")
    with pytest.raises(IntegrityError), transaction.atomic():
        Identity.objects.create(kind=Identity.Kind.GITHUB_LOGIN, value="octocat")


@pytest.mark.django_db
def test_identity_value_is_normalised_before_uniqueness():
    Identity.objects.create(kind=Identity.Kind.GITHUB_LOGIN, value="Foo")
    assert Identity.objects.get().value == "foo"
    with pytest.raises(IntegrityError), transaction.atomic():
        Identity.objects.create(kind=Identity.Kind.GITHUB_LOGIN, value="foo")


def test_identity_full_clean_normalises_value():
    # clean() is the choke point full_clean() calls, independent of save() — so a caller that
    # validates before a bulk write (which bypasses save()) still gets a normalised value.
    identity = Identity(kind=Identity.Kind.GITHUB_LOGIN, value="Foo")
    identity.clean()
    assert identity.value == "foo"


@pytest.mark.django_db
def test_repository_full_name_unique():
    _make_repository(full_name="acme/repo", github_id="R_1")
    with pytest.raises(IntegrityError), transaction.atomic():
        _make_repository(full_name="acme/repo", github_id="R_2")


@pytest.mark.django_db
def test_deleting_connection_with_repository_is_protected():
    from django.db.models import ProtectedError

    connection = GitHubConnection.objects.create(name="conn", kind=GitHubConnection.Kind.FINE_GRAINED_PAT)
    _make_repository(connection=connection)
    with pytest.raises(ProtectedError):
        connection.delete()


@pytest.mark.django_db
def test_repository_can_belong_to_two_projects():
    repo = _make_repository()
    project_a = Project.objects.create(name="A", slug="a")
    project_b = Project.objects.create(name="B", slug="b")
    project_a.repositories.add(repo)
    project_b.repositories.add(repo)
    assert set(repo.projects.all()) == {project_a, project_b}


@pytest.mark.django_db
def test_person_defaults():
    person = Person.objects.create(display_name="Alice")
    assert person.is_active is True
    assert person.is_bot is False
    assert person.exclude_from_metrics is False


def test_project_colors_are_declared_tokens():
    tokens_css = Path(__file__).resolve().parents[3] / "static" / "css" / "tokens.css"
    content = tokens_css.read_text()
    for value, _label in SERIES_COLOR_CHOICES:
        assert f"--{value}:" in content, f"missing token declaration for {value}"
