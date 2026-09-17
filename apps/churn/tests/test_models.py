import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.activity.models import PullRequest
from apps.catalog.models import Organization, Repository
from apps.churn.models import ChurnResult
from apps.connections.models import GitHubConnection


@pytest.fixture
def pull_request(db):
    org = Organization.objects.create(login="acme", type=Organization.Type.ORG, github_id="O_1")
    connection = GitHubConnection.objects.create(name="conn", kind=GitHubConnection.Kind.FINE_GRAINED_PAT)
    repository = Repository.objects.create(
        organization=org, connection=connection, name="repo", full_name="acme/repo", github_id="R_1"
    )
    return PullRequest.objects.create(
        repository=repository,
        number=1,
        github_id="PR_1",
        state=PullRequest.State.OPEN,
        created_at=timezone.now(),
    )


@pytest.mark.django_db
def test_duplicate_pull_request_window_raises(pull_request):
    ChurnResult.objects.create(pull_request=pull_request, window_days=21, status=ChurnResult.Status.OK)
    with pytest.raises(IntegrityError), transaction.atomic():
        ChurnResult.objects.create(pull_request=pull_request, window_days=21, status=ChurnResult.Status.OK)


@pytest.mark.django_db
def test_two_windows_for_one_pr_coexist(pull_request):
    ChurnResult.objects.create(pull_request=pull_request, window_days=21, status=ChurnResult.Status.OK)
    ChurnResult.objects.create(pull_request=pull_request, window_days=60, status=ChurnResult.Status.OK)
    assert ChurnResult.objects.filter(pull_request=pull_request).count() == 2


def test_status_choices_cover_the_four_spec_values():
    values = {choice.value for choice in ChurnResult.Status}
    assert values == {"ok", "unsupported_merge_method", "too_large", "error"}


@pytest.mark.django_db
def test_error_blank_by_default(pull_request):
    result = ChurnResult.objects.create(
        pull_request=pull_request, window_days=21, status=ChurnResult.Status.OK
    )
    assert result.error == ""
