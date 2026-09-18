from __future__ import annotations

import datetime

import pytest
from freezegun import freeze_time

from apps.activity.factories import PullRequestFactory
from apps.activity.models import PullRequest
from apps.catalog.factories import ProjectFactory, RepositoryFactory
from apps.churn.factories import ChurnResultFactory
from apps.churn.models import ChurnResult
from apps.churn.selectors import eligible_pull_requests

pytestmark = pytest.mark.django_db

UTC = datetime.UTC
_NOW = datetime.datetime(2020, 2, 1, tzinfo=UTC)


@freeze_time(_NOW)
def test_unelapsed_window_is_excluded():
    PullRequestFactory(state=PullRequest.State.MERGED, merged_at=_NOW - datetime.timedelta(days=10))

    assert list(eligible_pull_requests(window_days=21)) == []


@freeze_time(_NOW)
def test_elapsed_window_is_included():
    pr = PullRequestFactory(state=PullRequest.State.MERGED, merged_at=_NOW - datetime.timedelta(days=21))

    assert list(eligible_pull_requests(window_days=21)) == [pr]


@pytest.mark.parametrize(
    "status",
    [ChurnResult.Status.OK, ChurnResult.Status.TOO_LARGE, ChurnResult.Status.UNSUPPORTED_MERGE_METHOD],
)
@freeze_time(_NOW)
def test_settled_result_excludes_the_pr(status):
    pr = PullRequestFactory(state=PullRequest.State.MERGED, merged_at=_NOW - datetime.timedelta(days=30))
    ChurnResultFactory(pull_request=pr, window_days=21, status=status)

    assert list(eligible_pull_requests(window_days=21)) == []


@freeze_time(_NOW)
def test_error_result_is_retried():
    pr = PullRequestFactory(state=PullRequest.State.MERGED, merged_at=_NOW - datetime.timedelta(days=30))
    ChurnResultFactory(pull_request=pr, window_days=21, status=ChurnResult.Status.ERROR)

    assert list(eligible_pull_requests(window_days=21)) == [pr]


@freeze_time(_NOW)
def test_open_pull_request_is_excluded():
    PullRequestFactory(state=PullRequest.State.OPEN, merged_at=None)

    assert list(eligible_pull_requests(window_days=21)) == []


@freeze_time(_NOW)
def test_repo_full_names_narrows_the_result():
    repo_a = RepositoryFactory(full_name="acme/a")
    repo_b = RepositoryFactory(full_name="acme/b")
    pr_a = PullRequestFactory(
        repository=repo_a, state=PullRequest.State.MERGED, merged_at=_NOW - datetime.timedelta(days=30)
    )
    PullRequestFactory(
        repository=repo_b, state=PullRequest.State.MERGED, merged_at=_NOW - datetime.timedelta(days=30)
    )

    result = list(eligible_pull_requests(window_days=21, repo_full_names=["acme/a"]))

    assert result == [pr_a]


@freeze_time(_NOW)
def test_project_slug_narrows_the_result():
    repo_a = RepositoryFactory(full_name="acme/a")
    repo_b = RepositoryFactory(full_name="acme/b")
    project = ProjectFactory(slug="team-x")
    project.repositories.add(repo_a)
    pr_a = PullRequestFactory(
        repository=repo_a, state=PullRequest.State.MERGED, merged_at=_NOW - datetime.timedelta(days=30)
    )
    PullRequestFactory(
        repository=repo_b, state=PullRequest.State.MERGED, merged_at=_NOW - datetime.timedelta(days=30)
    )

    result = list(eligible_pull_requests(window_days=21, project_slug="team-x"))

    assert result == [pr_a]


@freeze_time(_NOW)
def test_limit_caps_the_result():
    for _ in range(3):
        PullRequestFactory(state=PullRequest.State.MERGED, merged_at=_NOW - datetime.timedelta(days=30))

    result = list(eligible_pull_requests(window_days=21, limit=2))

    assert len(result) == 2
