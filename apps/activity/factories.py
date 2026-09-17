import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

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


class PullRequestFactory(DjangoModelFactory):
    class Meta:
        model = PullRequest

    repository = factory.SubFactory(RepositoryFactory)
    number = factory.Sequence(lambda n: n + 1)
    github_id = factory.Sequence(lambda n: f"PR_{n}")
    state = PullRequest.State.OPEN
    created_at = factory.LazyFunction(timezone.now)


class CommitFactory(DjangoModelFactory):
    class Meta:
        model = Commit

    repository = factory.SubFactory(RepositoryFactory)
    sha = factory.Sequence(lambda n: f"{n:040x}")


class PullRequestCommitFactory(DjangoModelFactory):
    class Meta:
        model = PullRequestCommit

    pull_request = factory.SubFactory(PullRequestFactory)
    commit = factory.SubFactory(CommitFactory)
    position = 0


class PRFileFactory(DjangoModelFactory):
    class Meta:
        model = PRFile

    pull_request = factory.SubFactory(PullRequestFactory)
    path = factory.Sequence(lambda n: f"file-{n}.py")


class ReviewFactory(DjangoModelFactory):
    class Meta:
        model = Review

    pull_request = factory.SubFactory(PullRequestFactory)
    state = Review.State.APPROVED
    github_id = factory.Sequence(lambda n: f"RVW_{n}")


class ReviewCommentFactory(DjangoModelFactory):
    class Meta:
        model = ReviewComment

    pull_request = factory.SubFactory(PullRequestFactory)
    github_id = factory.Sequence(lambda n: f"RC_{n}")


class CheckStatusFactory(DjangoModelFactory):
    class Meta:
        model = CheckStatus

    pull_request = factory.SubFactory(PullRequestFactory)
    commit_sha = factory.Sequence(lambda n: f"{n:040x}")
    rollup_state = CheckStatus.RollupState.SUCCESS
