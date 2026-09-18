import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.activity.models import (
    AIDisclosure,
    AIStatus,
    CheckStatus,
    Commit,
    PRFile,
    PullRequest,
    PullRequestCommit,
    Review,
    ReviewComment,
)
from apps.catalog.models import Organization, Repository
from apps.connections.models import GitHubConnection


@pytest.fixture
def repository(db):
    org = Organization.objects.create(login="acme", type=Organization.Type.ORG, github_id="O_1")
    connection = GitHubConnection.objects.create(name="conn", kind=GitHubConnection.Kind.FINE_GRAINED_PAT)
    return Repository.objects.create(
        organization=org, connection=connection, name="repo", full_name="acme/repo", github_id="R_1"
    )


@pytest.fixture
def pull_request(repository):
    return PullRequest.objects.create(
        repository=repository,
        number=1,
        github_id="PR_1",
        state=PullRequest.State.OPEN,
        created_at=timezone.now(),
    )


@pytest.mark.django_db
def test_duplicate_repository_number_raises(repository):
    PullRequest.objects.create(
        repository=repository,
        number=1,
        github_id="PR_1",
        state=PullRequest.State.OPEN,
        created_at=timezone.now(),
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        PullRequest.objects.create(
            repository=repository,
            number=1,
            github_id="PR_2",
            state=PullRequest.State.OPEN,
            created_at=timezone.now(),
        )


@pytest.mark.django_db
def test_duplicate_commit_sha_raises(repository):
    Commit.objects.create(repository=repository, sha="a" * 40)
    with pytest.raises(IntegrityError), transaction.atomic():
        Commit.objects.create(repository=repository, sha="a" * 40)


@pytest.mark.django_db
def test_duplicate_pr_file_path_raises(pull_request):
    PRFile.objects.create(pull_request=pull_request, path="a.py")
    with pytest.raises(IntegrityError), transaction.atomic():
        PRFile.objects.create(pull_request=pull_request, path="a.py")


@pytest.mark.django_db
def test_duplicate_check_status_commit_sha_raises(pull_request):
    CheckStatus.objects.create(
        pull_request=pull_request, commit_sha="a" * 40, rollup_state=CheckStatus.RollupState.SUCCESS
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        CheckStatus.objects.create(
            pull_request=pull_request, commit_sha="a" * 40, rollup_state=CheckStatus.RollupState.SUCCESS
        )


@pytest.mark.django_db
def test_duplicate_pull_request_commit_raises(pull_request, repository):
    commit = Commit.objects.create(repository=repository, sha="b" * 40)
    PullRequestCommit.objects.create(pull_request=pull_request, commit=commit, position=0)
    with pytest.raises(IntegrityError), transaction.atomic():
        PullRequestCommit.objects.create(pull_request=pull_request, commit=commit, position=1)


@pytest.mark.django_db
def test_ai_status_and_disclosure_defaults(pull_request):
    assert pull_request.ai_status == AIStatus.UNKNOWN
    assert pull_request.ai_disclosure == AIDisclosure.MISSING


@pytest.mark.django_db
def test_nullable_duration_source_fields_default_to_none(pull_request):
    assert pull_request.additions is None
    assert pull_request.first_review_at is None
    assert pull_request.review_rounds is None


@pytest.mark.django_db
def test_has_followup_fix_defaults_to_false(pull_request):
    assert pull_request.has_followup_fix is False


def test_review_comment_has_no_body_text_field():
    field_names = {f.name for f in ReviewComment._meta.get_fields()}
    assert "body" not in field_names
    assert "text" not in field_names


@pytest.mark.django_db
def test_review_can_be_created(pull_request):
    review = Review.objects.create(pull_request=pull_request, state=Review.State.APPROVED, github_id="RVW_1")
    assert review.reviewer is None


@pytest.mark.parametrize("model", [Commit, Review, ReviewComment])
def test_github_node_models_have_a_raw_payload_field(model):
    field_names = {f.name for f in model._meta.get_fields()}
    assert "raw" in field_names
