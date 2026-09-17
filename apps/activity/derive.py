"""Derived PR fields (spec §4.2, §8.2, §15). Every rule reads only stored rows and settings
already written by sync and identity resolution, so `derive_pull_request()` is a total,
idempotent function of them: re-running it with the same inputs writes the same outputs. Absent
input is `None`, never `0` (spec §15)."""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

from django.db import transaction
from django.db.models import QuerySet

from apps.activity.models import Commit, PRFile, PullRequest, Review
from apps.catalog.globs import compile_globs, matches_any
from apps.catalog.models import Identity
from apps.catalog.services import get_dict, get_int, get_list

_HOTFIX_TITLE_RE = re.compile(r"^\s*(hotfix|fix)\b", re.IGNORECASE)
_HOTFIX_REF_RE = re.compile(r"^(hotfix|fix)/", re.IGNORECASE)
_REVERT_NUMBER_RE = re.compile(r"[Rr]everts\s+([\w.-]+)/([\w.-]+)#(\d+)")
_REVERT_COMMIT_RE = re.compile(r"[Tt]his reverts commit ([0-9a-fA-F]{7,40})")
_REVERT_TITLE_RE = re.compile(r'^\s*Revert\s+"(.+)"\s*$')

_SIZE_BUCKET_ORDER = ("XS", "S", "M", "L")


@dataclass(frozen=True)
class FileDerivedFields:
    pk: int
    is_excluded: bool
    is_test: bool


@dataclass(frozen=True)
class DerivedFields:
    effective_additions: int | None
    effective_deletions: int | None
    has_test_changes: bool
    size_bucket: str | None
    ready_for_review_at: datetime.datetime | None
    first_commit_at: datetime.datetime | None
    first_review_at: datetime.datetime | None
    first_approval_at: datetime.datetime | None
    last_activity_at: datetime.datetime | None
    review_rounds: int
    commits_after_first_review: int | None
    is_rubber_stamp: bool
    is_self_merged: bool
    is_hotfix: bool
    is_revert: bool
    reverts_pr_id: int | None
    files: tuple[FileDerivedFields, ...]


def _person_id(identity: Identity | None) -> int | None:
    return identity.person_id if identity is not None else None


def _is_bot(identity: Identity | None) -> bool:
    return bool(identity is not None and identity.person is not None and identity.person.is_bot)


def _derive_files(pr: PullRequest) -> tuple[FileDerivedFields, ...]:
    excluded_globs = compile_globs(get_list("EXCLUDED_PATH_GLOBS"))
    test_globs = compile_globs(get_list("TEST_PATH_GLOBS"))
    return tuple(
        FileDerivedFields(
            pk=pr_file.pk,
            is_excluded=matches_any(pr_file.path, excluded_globs),
            is_test=matches_any(pr_file.path, test_globs),
        )
        for pr_file in pr.files.all()
    )


def _effective_lines(pr: PullRequest, files: tuple[FileDerivedFields, ...]) -> tuple[int | None, int | None]:
    if not files:
        return None, None
    excluded_pks = {f.pk for f in files if f.is_excluded}
    additions = sum(f.additions or 0 for f in pr.files.all() if f.pk not in excluded_pks)
    deletions = sum(f.deletions or 0 for f in pr.files.all() if f.pk not in excluded_pks)
    return additions, deletions


def _size_bucket(effective_lines: int | None) -> str | None:
    if effective_lines is None:
        return None
    buckets = get_dict("PR_SIZE_BUCKETS")
    for name in _SIZE_BUCKET_ORDER:
        if effective_lines < buckets[name]:
            return name
    return "XL"


def _ready_for_review_at(pr: PullRequest) -> datetime.datetime | None:
    if pr.ready_for_review_at is not None:
        return pr.ready_for_review_at
    if pr.is_draft:
        return None
    return pr.created_at


def _first_commit_at(pr: PullRequest) -> datetime.datetime | None:
    timestamps = [
        ts
        for ts in (
            (prc.commit.authored_at or prc.commit.committed_at) for prc in pr.pull_request_commits.all()
        )
        if ts is not None
    ]
    return min(timestamps) if timestamps else None


def _non_author_non_bot_reviews(pr: PullRequest) -> list[Review]:
    return [r for r in pr.reviews.all() if r.reviewer_id != pr.author_id and not _is_bot(r.reviewer)]


def _non_author_non_bot_comments(pr: PullRequest) -> list:
    return [c for c in pr.review_comments.all() if c.author_id != pr.author_id and not _is_bot(c.author)]


def _first_review_at(pr: PullRequest) -> datetime.datetime | None:
    timestamps = [r.submitted_at for r in _non_author_non_bot_reviews(pr) if r.submitted_at is not None]
    timestamps += [c.created_at for c in _non_author_non_bot_comments(pr) if c.created_at is not None]
    return min(timestamps) if timestamps else None


def _first_approval_at(pr: PullRequest) -> datetime.datetime | None:
    timestamps = [
        r.submitted_at
        for r in _non_author_non_bot_reviews(pr)
        if r.state == Review.State.APPROVED and r.submitted_at is not None
    ]
    return min(timestamps) if timestamps else None


def _last_activity_at(pr: PullRequest) -> datetime.datetime | None:
    candidates = [pr.created_at, pr.updated_at_github, pr.merged_at, pr.closed_at]
    candidates += [prc.commit.committed_at for prc in pr.pull_request_commits.all()]
    candidates += [r.submitted_at for r in pr.reviews.all()]
    candidates += [c.created_at for c in pr.review_comments.all()]
    present = [c for c in candidates if c is not None]
    return max(present) if present else None


def _review_rounds(pr: PullRequest) -> int:
    changes_requested = [
        r
        for r in pr.reviews.all()
        if r.reviewer_id != pr.author_id and r.state == Review.State.CHANGES_REQUESTED
    ]
    return len(changes_requested) + 1


def _commits_after_first_review(pr: PullRequest, first_review_at: datetime.datetime | None) -> int | None:
    if first_review_at is None:
        return None
    return sum(
        1
        for prc in pr.pull_request_commits.all()
        if prc.commit.committed_at is not None and prc.commit.committed_at > first_review_at
    )


def _is_rubber_stamp(
    pr: PullRequest,
    size_bucket: str | None,
    ready_for_review_at: datetime.datetime | None,
    first_approval_at: datetime.datetime | None,
) -> bool:
    if size_bucket not in ("L", "XL"):
        return False
    if ready_for_review_at is None or first_approval_at is None:
        return False
    max_minutes = get_int("RUBBER_STAMP_MAX_MINUTES")
    if first_approval_at - ready_for_review_at >= datetime.timedelta(minutes=max_minutes):
        return False
    if any(pr.review_comments.all()):
        return False
    if any((r.body_length or 0) > 0 for r in pr.reviews.all()):
        return False
    return True


def _is_self_merged(pr: PullRequest) -> bool:
    if pr.merged_by_id is None or pr.author_id is None:
        return False
    if pr.merged_by_id == pr.author_id:
        return True
    author_person_id = _person_id(pr.author)
    merged_by_person_id = _person_id(pr.merged_by)
    return author_person_id is not None and author_person_id == merged_by_person_id


def _is_hotfix(pr: PullRequest) -> bool:
    if _HOTFIX_TITLE_RE.match(pr.title or ""):
        return True
    if _HOTFIX_REF_RE.match(pr.head_ref or ""):
        return True
    return any("hotfix" in (label or "").casefold() for label in pr.labels)


def _revert_target_by_number(pr: PullRequest, owner: str, repo: str, number: str) -> PullRequest | None:
    return (
        PullRequest.objects.filter(repository__full_name=f"{owner}/{repo}", number=int(number))
        .exclude(pk=pr.pk)
        .first()
    )


def _revert_target_by_commit(pr: PullRequest, sha: str) -> PullRequest | None:
    commit = (
        Commit.objects.filter(repository_id=pr.repository_id, sha__startswith=sha.lower())
        .order_by("sha")
        .first()
    )
    if commit is not None:
        candidates = PullRequest.objects.filter(pull_request_commits__commit=commit).exclude(pk=pr.pk)
        target = (
            candidates.filter(state=PullRequest.State.MERGED).order_by("-merged_at").first()
            or candidates.order_by("-merged_at").first()
        )
        if target is not None:
            return target
    return (
        PullRequest.objects.filter(repository_id=pr.repository_id, merge_commit_sha__startswith=sha.lower())
        .exclude(pk=pr.pk)
        .order_by("-merged_at")
        .first()
    )


def _revert_target_by_title(pr: PullRequest, original_title: str) -> PullRequest | None:
    return (
        PullRequest.objects.filter(
            repository_id=pr.repository_id,
            title=original_title,
            state=PullRequest.State.MERGED,
            merged_at__lt=pr.created_at,
        )
        .exclude(pk=pr.pk)
        .order_by("-merged_at")
        .first()
    )


def _detect_revert(pr: PullRequest) -> tuple[bool, int | None]:
    text = f"{pr.title}\n{pr.body}"

    number_match = _REVERT_NUMBER_RE.search(text)
    if number_match:
        target = _revert_target_by_number(pr, *number_match.groups())
        return True, target.pk if target else None

    commit_match = _REVERT_COMMIT_RE.search(text)
    if commit_match:
        target = _revert_target_by_commit(pr, commit_match.group(1))
        return True, target.pk if target else None

    title_match = _REVERT_TITLE_RE.match(pr.title or "")
    if title_match:
        target = _revert_target_by_title(pr, title_match.group(1))
        return True, target.pk if target else None

    return False, None


def compute_derived(pr: PullRequest) -> DerivedFields:
    files = _derive_files(pr)
    effective_additions, effective_deletions = _effective_lines(pr, files)
    effective_total = (
        None if effective_additions is None else effective_additions + (effective_deletions or 0)
    )
    size_bucket = _size_bucket(effective_total)
    has_test_changes = any(f.is_test and not f.is_excluded for f in files)
    ready_for_review_at = _ready_for_review_at(pr)
    first_review_at = _first_review_at(pr)
    first_approval_at = _first_approval_at(pr)
    is_revert, reverts_pr_id = _detect_revert(pr)

    return DerivedFields(
        effective_additions=effective_additions,
        effective_deletions=effective_deletions,
        has_test_changes=has_test_changes,
        size_bucket=size_bucket,
        ready_for_review_at=ready_for_review_at,
        first_commit_at=_first_commit_at(pr),
        first_review_at=first_review_at,
        first_approval_at=first_approval_at,
        last_activity_at=_last_activity_at(pr),
        review_rounds=_review_rounds(pr),
        commits_after_first_review=_commits_after_first_review(pr, first_review_at),
        is_rubber_stamp=_is_rubber_stamp(pr, size_bucket, ready_for_review_at, first_approval_at),
        is_self_merged=_is_self_merged(pr),
        is_hotfix=_is_hotfix(pr),
        is_revert=is_revert,
        reverts_pr_id=reverts_pr_id,
        files=files,
    )


_PR_UPDATE_FIELDS = [
    "effective_additions",
    "effective_deletions",
    "has_test_changes",
    "size_bucket",
    "ready_for_review_at",
    "first_commit_at",
    "first_review_at",
    "first_approval_at",
    "last_activity_at",
    "review_rounds",
    "commits_after_first_review",
    "is_rubber_stamp",
    "is_self_merged",
    "is_hotfix",
    "is_revert",
    "reverts_pr",
]


def _load_pull_request(pull_request_id: int) -> PullRequest:
    return (
        PullRequest.objects.select_related("author__person", "merged_by__person")
        .prefetch_related(
            "files",
            "reviews__reviewer__person",
            "review_comments__author__person",
            "pull_request_commits__commit",
        )
        .get(pk=pull_request_id)
    )


@transaction.atomic
def derive_pull_request(pull_request_id: int) -> None:
    """Loads a PR with its dependents, computes every derived field and saves them with an
    explicit `update_fields` list — a second call over unchanged rows writes the same values.
    `PRFile` flags and the PR row are written in one transaction so they never disagree."""
    pr = _load_pull_request(pull_request_id)
    derived = compute_derived(pr)

    if derived.files:
        PRFile.objects.bulk_update(
            [PRFile(pk=f.pk, is_excluded=f.is_excluded, is_test=f.is_test) for f in derived.files],
            ["is_excluded", "is_test"],
        )

    pr.effective_additions = derived.effective_additions
    pr.effective_deletions = derived.effective_deletions
    pr.has_test_changes = derived.has_test_changes
    pr.size_bucket = derived.size_bucket
    pr.ready_for_review_at = derived.ready_for_review_at
    pr.first_commit_at = derived.first_commit_at
    pr.first_review_at = derived.first_review_at
    pr.first_approval_at = derived.first_approval_at
    pr.last_activity_at = derived.last_activity_at
    pr.review_rounds = derived.review_rounds
    pr.commits_after_first_review = derived.commits_after_first_review
    pr.is_rubber_stamp = derived.is_rubber_stamp
    pr.is_self_merged = derived.is_self_merged
    pr.is_hotfix = derived.is_hotfix
    pr.is_revert = derived.is_revert
    pr.reverts_pr_id = derived.reverts_pr_id
    pr.save(update_fields=_PR_UPDATE_FIELDS)


def derive_pull_requests(queryset: QuerySet[PullRequest]) -> int:
    """Bulk entry point for `manage.py recompute` (phase 7): redrives every derived field for
    the given PRs instead of letting them drift."""
    count = 0
    for pk in queryset.values_list("pk", flat=True):
        derive_pull_request(pk)
        count += 1
    return count
