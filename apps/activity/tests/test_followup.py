import datetime

import pytest
from django.utils import timezone

from apps.activity.factories import PRFileFactory, PullRequestFactory
from apps.activity.followup import (
    compute_has_followup_fix,
    update_followup_fixes,
    update_followup_fixes_for,
)
from apps.activity.models import PullRequest
from apps.catalog.factories import RepositoryFactory

MERGED_AT = timezone.now()


def _original(repository, paths, *, merged_at=MERGED_AT):
    pr = PullRequestFactory(
        repository=repository, state=PullRequest.State.MERGED, merged_at=merged_at, title="Add feature"
    )
    for path in paths:
        PRFileFactory(pull_request=pr, path=path)
    return pr


def _fix(repository, paths, *, merged_at, is_hotfix_title=True, excluded_paths=()):
    title = "fix: patch it up" if is_hotfix_title else "Add another feature"
    pr = PullRequestFactory(
        repository=repository, state=PullRequest.State.MERGED, merged_at=merged_at, title=title
    )
    for path in paths:
        PRFileFactory(pull_request=pr, path=path)
    for path in excluded_paths:
        PRFileFactory(pull_request=pr, path=path, is_excluded=True)
    pr.is_hotfix = title.lower().startswith("fix")
    pr.save(update_fields=["is_hotfix"])
    return pr


@pytest.mark.django_db
def test_positive_fix_within_window_and_overlap():
    repo = RepositoryFactory()
    original = _original(repo, ["a.py", "b.py", "c.py", "d.py", "e.py"])
    _fix(repo, ["a.py", "b.py", "c.py"], merged_at=MERGED_AT + datetime.timedelta(days=10))

    assert compute_has_followup_fix(original) is True


@pytest.mark.django_db
def test_negative_by_window():
    repo = RepositoryFactory()
    original = _original(repo, ["a.py", "b.py", "c.py", "d.py", "e.py"])
    _fix(repo, ["a.py", "b.py", "c.py"], merged_at=MERGED_AT + datetime.timedelta(days=15))

    assert compute_has_followup_fix(original) is False


@pytest.mark.django_db
def test_negative_by_overlap():
    repo = RepositoryFactory()
    original = _original(repo, ["a.py", "b.py", "c.py", "d.py", "e.py"])
    _fix(repo, ["a.py", "b.py"], merged_at=MERGED_AT + datetime.timedelta(days=10))

    assert compute_has_followup_fix(original) is False


@pytest.mark.django_db
def test_negative_by_pattern_not_hotfix():
    repo = RepositoryFactory()
    original = _original(repo, ["a.py", "b.py", "c.py", "d.py", "e.py"])
    _fix(
        repo,
        ["a.py", "b.py", "c.py"],
        merged_at=MERGED_AT + datetime.timedelta(days=10),
        is_hotfix_title=False,
    )

    assert compute_has_followup_fix(original) is False


@pytest.mark.django_db
def test_boundary_exactly_14_days_counts():
    repo = RepositoryFactory()
    original = _original(repo, ["a.py", "b.py", "c.py", "d.py"])
    _fix(repo, ["a.py", "b.py"], merged_at=MERGED_AT + datetime.timedelta(days=14))

    assert compute_has_followup_fix(original) is True


@pytest.mark.django_db
def test_boundary_exactly_50_percent_overlap_counts():
    repo = RepositoryFactory()
    original = _original(repo, ["a.py", "b.py", "c.py", "d.py"])
    _fix(repo, ["a.py", "b.py"], merged_at=MERGED_AT + datetime.timedelta(days=10))

    assert compute_has_followup_fix(original) is True


@pytest.mark.django_db
def test_excluded_files_ignored_on_both_sides():
    repo = RepositoryFactory()
    original = _original(repo, ["a.py", "b.py"])
    PRFileFactory(pull_request=original, path="vendor/x.py", is_excluded=True)
    _fix(
        repo,
        ["a.py", "b.py"],
        merged_at=MERGED_AT + datetime.timedelta(days=5),
        excluded_paths=["vendor/x.py"],
    )

    assert compute_has_followup_fix(original) is True


@pytest.mark.django_db
def test_zero_file_pr_is_false():
    repo = RepositoryFactory()
    original = PullRequestFactory(repository=repo, state=PullRequest.State.MERGED, merged_at=MERGED_AT)
    _fix(repo, ["a.py"], merged_at=MERGED_AT + datetime.timedelta(days=5))

    assert compute_has_followup_fix(original) is False


@pytest.mark.django_db
def test_cross_repository_fix_ignored():
    repo = RepositoryFactory()
    other_repo = RepositoryFactory()
    original = _original(repo, ["a.py", "b.py"])
    _fix(other_repo, ["a.py", "b.py"], merged_at=MERGED_AT + datetime.timedelta(days=5))

    assert compute_has_followup_fix(original) is False


@pytest.mark.django_db
def test_update_followup_fixes_is_idempotent_and_flips_false_when_fix_deleted():
    repo = RepositoryFactory()
    original = _original(repo, ["a.py", "b.py"])
    fix_pr = _fix(repo, ["a.py", "b.py"], merged_at=MERGED_AT + datetime.timedelta(days=5))

    changed_first = update_followup_fixes(fix_pr.pk)
    original.refresh_from_db()
    assert original.has_followup_fix is True
    assert original.pk in changed_first

    changed_second = update_followup_fixes(fix_pr.pk)
    assert changed_second == set()

    fix_pr.delete()
    changed_third = update_followup_fixes(original.pk)
    original.refresh_from_db()
    assert original.has_followup_fix is False
    assert original.pk in changed_third


@pytest.mark.django_db
def test_update_followup_fixes_query_count_does_not_scale_with_originals_in_window(
    django_assert_max_num_queries,
):
    """The cascade over `originals` batches its `PRFile` reads into one grouped query -- syncing
    one hotfix PR must not issue a query per candidate per original in its window (a repository
    merging ~100 PRs/fortnight would otherwise cost thousands of queries per sync)."""
    repo = RepositoryFactory()
    for i in range(20):
        _original(
            repo, [f"orig-{i}-a.py", f"orig-{i}-b.py"], merged_at=MERGED_AT + datetime.timedelta(hours=i)
        )
    fix_pr = _fix(repo, ["orig-0-a.py", "orig-0-b.py"], merged_at=MERGED_AT + datetime.timedelta(days=10))

    with django_assert_max_num_queries(10):
        update_followup_fixes(fix_pr.pk)


@pytest.mark.django_db
def test_update_followup_fixes_for_batch_entry_point():
    repo = RepositoryFactory()
    original = _original(repo, ["a.py", "b.py"])
    _fix(repo, ["a.py", "b.py"], merged_at=MERGED_AT + datetime.timedelta(days=5))

    count = update_followup_fixes_for(PullRequest.objects.filter(repository=repo))
    original.refresh_from_db()
    assert count == 2
    assert original.has_followup_fix is True
