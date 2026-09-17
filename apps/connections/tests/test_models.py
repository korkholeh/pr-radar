import pytest
from django.db import IntegrityError, transaction

from apps.connections.models import GitHubConnection


@pytest.mark.django_db
def test_duplicate_name_raises_integrity_error():
    GitHubConnection.objects.create(name="primary", kind=GitHubConnection.Kind.FINE_GRAINED_PAT)
    with pytest.raises(IntegrityError), transaction.atomic():
        GitHubConnection.objects.create(name="primary", kind=GitHubConnection.Kind.FINE_GRAINED_PAT)


@pytest.mark.django_db
def test_status_defaults_to_unverified():
    connection = GitHubConnection.objects.create(name="primary", kind=GitHubConnection.Kind.FINE_GRAINED_PAT)
    assert connection.status == GitHubConnection.Status.UNVERIFIED


@pytest.mark.django_db
def test_last_check_result_defaults_to_empty_dict():
    connection = GitHubConnection.objects.create(name="primary", kind=GitHubConnection.Kind.FINE_GRAINED_PAT)
    assert connection.last_check_result == {}


@pytest.mark.django_db
def test_str_contains_name_and_not_token():
    connection = GitHubConnection.objects.create(
        name="primary",
        kind=GitHubConnection.Kind.FINE_GRAINED_PAT,
        token_encrypted=b"super-secret-token-bytes",
    )
    rendered = str(connection)
    assert rendered == "primary"
    assert b"super-secret-token-bytes" not in rendered.encode()


@pytest.mark.django_db
def test_github_app_fields_are_nullable():
    connection = GitHubConnection.objects.create(name="primary", kind=GitHubConnection.Kind.GITHUB_APP)
    assert connection.app_id is None
    assert connection.installation_id is None
    assert connection.private_key_encrypted is None
