"""update_or_create writers keyed on GitHub's own identifiers (spec §5.3 step 3), one transaction
per PR. Identity rows are get_or_create((kind, value)) with person=None — mapping identities to
people is phase 4's job, not sync's."""

from django.db import transaction

from apps.activity.models import (
    CheckStatus,
    Commit,
    PRFile,
    PullRequest,
    PullRequestCommit,
    Review,
    ReviewComment,
)
from apps.catalog.models import Identity, Repository
from apps.catalog.normalize import normalize_identity_value
from apps.github_sync import mappers


def _identity_for(kind: str, value: str | None) -> Identity | None:
    if not value:
        return None
    normalized = normalize_identity_value(kind, value)
    identity, _created = Identity.objects.get_or_create(
        kind=kind, value=normalized, defaults={"person": None}
    )
    return identity


def _identity_for_login(login: str | None) -> Identity | None:
    return _identity_for(Identity.Kind.GITHUB_LOGIN, login)


def _identity_for_email(email: str | None) -> Identity | None:
    return _identity_for(Identity.Kind.GIT_EMAIL, email)


@transaction.atomic
def upsert_pull_request(
    repository: Repository,
    *,
    pr_node: dict,
    commit_nodes: list[dict],
    review_nodes: list[dict],
    review_thread_comment_nodes: list[dict],
    file_nodes: list[dict],
    timeline_nodes: list[dict],
) -> PullRequest:
    fields = mappers.map_pull_request(pr_node)
    author = _identity_for_login(fields.pop("author_login"))
    merged_by = _identity_for_login(fields.pop("merged_by_login"))
    github_id = fields.pop("github_id")

    pull_request, _created = PullRequest.objects.update_or_create(
        github_id=github_id,
        defaults={
            **fields,
            "repository": repository,
            "author": author,
            "merged_by": merged_by,
            "ready_for_review_at": mappers.map_ready_for_review_at(timeline_nodes),
            "review_requested_at": mappers.map_review_requested_at(timeline_nodes),
        },
    )

    first_ci_sha: str | None = None
    for position, commit_node in enumerate(commit_nodes):
        commit_fields = mappers.map_commit(commit_node)
        author_login = commit_fields.pop("author_login")
        author_email = commit_fields.pop("author_email")
        committer_login = commit_fields.pop("committer_login")
        committer_email = commit_fields.pop("committer_email")
        commit_fields.pop("check_rollup_state")
        sha = commit_fields.pop("sha")

        author_login_identity = _identity_for_login(author_login)
        author_email_identity = _identity_for_email(author_email)

        commit, _ = Commit.objects.update_or_create(
            repository=repository,
            sha=sha,
            defaults={
                **commit_fields,
                "author_identity": author_login_identity or author_email_identity,
                "author_email_identity": author_email_identity,
                "committer_identity": _identity_for_login(committer_login)
                or _identity_for_email(committer_email),
            },
        )
        PullRequestCommit.objects.update_or_create(
            pull_request=pull_request, commit=commit, defaults={"position": position}
        )

        check_fields = mappers.map_check_status(commit_node)
        if check_fields is not None:
            CheckStatus.objects.update_or_create(
                pull_request=pull_request,
                commit_sha=check_fields["commit_sha"],
                defaults=check_fields,
            )
            if first_ci_sha is None:
                first_ci_sha = check_fields["commit_sha"]

    CheckStatus.objects.filter(pull_request=pull_request).update(is_first_ci_commit=False)
    if first_ci_sha is not None:
        CheckStatus.objects.filter(pull_request=pull_request, commit_sha=first_ci_sha).update(
            is_first_ci_commit=True
        )

    for review_node in review_nodes:
        review_fields = mappers.map_review(review_node)
        reviewer_login = review_fields.pop("reviewer_login")
        review_github_id = review_fields.pop("github_id")
        Review.objects.update_or_create(
            github_id=review_github_id,
            defaults={
                **review_fields,
                "pull_request": pull_request,
                "reviewer": _identity_for_login(reviewer_login),
            },
        )

    for comment_node in review_thread_comment_nodes:
        # The thread's `isResolved` travels with its first comment, attached by `sync_repository`
        # when it flattens the threads: a comment row is what this project stores, and whether the
        # conversation it opened was ever answered is a property of the thread around it.
        comment_fields = mappers.map_review_comment(
            comment_node,
            is_review_thread=True,
            is_resolved=comment_node.get("_thread_is_resolved"),
        )
        author_login = comment_fields.pop("author_login")
        comment_github_id = comment_fields.pop("github_id")
        ReviewComment.objects.update_or_create(
            github_id=comment_github_id,
            defaults={
                **comment_fields,
                "pull_request": pull_request,
                "author": _identity_for_login(author_login),
            },
        )

    for file_node in file_nodes:
        file_fields = mappers.map_pr_file(file_node)
        path = file_fields.pop("path")
        PRFile.objects.update_or_create(pull_request=pull_request, path=path, defaults=file_fields)

    return pull_request
