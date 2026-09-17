import pytest

from apps.activity.models import (
    CheckStatus,
    Commit,
    PRFile,
    PullRequest,
    PullRequestCommit,
    Review,
    ReviewComment,
)
from apps.catalog.factories import RepositoryFactory
from apps.github_sync.errors import GitHubSchemaError
from apps.github_sync.upserts import upsert_pull_request


def _thread_comment_nodes(n):
    return [
        {
            "id": f"RC_{i}",
            "author": {"login": f"commenter-{i}"},
            "createdAt": "2026-01-02T10:00:00Z",
            "bodyText": "a comment",
        }
        for i in range(n)
    ]


@pytest.fixture
def full_pr_kwargs(github_fixture):
    pr_node = github_fixture("pull_requests_page1")["data"]["repository"]["pullRequests"]["nodes"][0]
    commit_nodes = github_fixture("pr_commits_page2")["data"]["node"]["commits"]["nodes"]
    review_nodes = github_fixture("pr_reviews_page2")["data"]["node"]["reviews"]["nodes"]
    file_nodes = github_fixture("pr_files_page2")["data"]["node"]["files"]["nodes"]
    return {
        "pr_node": pr_node,
        "commit_nodes": commit_nodes,
        "review_nodes": review_nodes,
        "review_thread_comment_nodes": _thread_comment_nodes(3),
        "file_nodes": file_nodes,
        "timeline_nodes": [{"__typename": "ReadyForReviewEvent", "createdAt": "2026-01-01T09:30:00Z"}],
    }


@pytest.mark.django_db
def test_full_pull_request_is_written_with_expected_fields(full_pr_kwargs):
    repository = RepositoryFactory()
    pull_request = upsert_pull_request(repository, **full_pr_kwargs)

    assert pull_request.github_id == "PR_kwDOA1"
    assert pull_request.repository_id == repository.id
    assert pull_request.author.value == "octocat"
    assert pull_request.merged_by.value == "octocat"

    assert Commit.objects.filter(repository=repository).count() == 50
    assert PullRequestCommit.objects.filter(pull_request=pull_request).count() == 50
    assert Review.objects.filter(pull_request=pull_request).count() == 50
    assert ReviewComment.objects.filter(pull_request=pull_request, is_review_thread=True).count() == 3
    assert PRFile.objects.filter(pull_request=pull_request).count() == 50
    check_statuses = CheckStatus.objects.filter(pull_request=pull_request)
    assert check_statuses.count() == 50
    first = check_statuses.get(commit_sha="commit0100")
    assert first.is_first_ci_commit is True
    assert check_statuses.exclude(pk=first.pk).filter(is_first_ci_commit=True).count() == 0


@pytest.mark.django_db
def test_upsert_twice_creates_no_rows(full_pr_kwargs):
    repository = RepositoryFactory()
    upsert_pull_request(repository, **full_pr_kwargs)
    ids_before = set(PullRequest.objects.values_list("id", flat=True))
    commit_count_before = Commit.objects.count()
    review_count_before = Review.objects.count()

    upsert_pull_request(repository, **full_pr_kwargs)

    assert set(PullRequest.objects.values_list("id", flat=True)) == ids_before
    assert Commit.objects.count() == commit_count_before
    assert Review.objects.count() == review_count_before


@pytest.mark.django_db
def test_changed_title_updates_in_place(full_pr_kwargs):
    repository = RepositoryFactory()
    pull_request = upsert_pull_request(repository, **full_pr_kwargs)
    pk = pull_request.pk

    changed_kwargs = dict(full_pr_kwargs)
    changed_kwargs["pr_node"] = {**full_pr_kwargs["pr_node"], "title": "A new title"}
    updated = upsert_pull_request(repository, **changed_kwargs)

    assert updated.pk == pk
    assert PullRequest.objects.count() == 1
    updated.refresh_from_db()
    assert updated.title == "A new title"


@pytest.mark.django_db
def test_mapper_failure_on_last_file_leaves_no_partial_rows(full_pr_kwargs):
    repository = RepositoryFactory()
    broken_kwargs = dict(full_pr_kwargs)
    broken_kwargs["file_nodes"] = [*full_pr_kwargs["file_nodes"][:2], {"additions": 1, "deletions": 0}]

    with pytest.raises(GitHubSchemaError):
        upsert_pull_request(repository, **broken_kwargs)

    assert PullRequest.objects.count() == 0
    assert Commit.objects.count() == 0
    assert Review.objects.count() == 0
    assert PRFile.objects.count() == 0
