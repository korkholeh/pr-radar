"""T14: `apps.dashboards.reviews` — `reviewer_load()`, `author_reviewer_matrix()` (with the
"Other" fold, axis counts, flags and self-review on the diagonal) and `prs_waiting_for_review()`.
Hand-computed counts, self-review excluded from workload, bots excluded, an empty scope returns
empty structures, and query counts stay constant as rows grow."""

from __future__ import annotations

import datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory, ReviewFactory
from apps.catalog.factories import IdentityFactory, PersonFactory, RepositoryFactory
from apps.dashboards.params import DashboardParams
from apps.dashboards.pr_filters import PRFilters
from apps.dashboards.reviews import (
    author_reviewer_matrix,
    heat_map_grid,
    prs_waiting_for_review,
    reviewer_load,
)
from apps.metrics.models import ScopeType
from apps.metrics.types import Scope

DATE_FROM = datetime.date(2026, 6, 1)
DATE_TO = datetime.date(2026, 6, 30)
SUBMITTED = datetime.datetime(2026, 6, 15, tzinfo=datetime.UTC)


def _scope() -> Scope:
    return Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=ScopeFilter(unrestricted=True))


def _params() -> DashboardParams:
    return DashboardParams(
        mode="period",
        preset="custom",
        date_from=DATE_FROM,
        date_to=DATE_TO,
        day=DATE_TO,
        granularity="week",
        granularity_is_auto=False,
        cohort="all",
        project_ids=(),
        repository_ids=(),
        q="",
        sort="",
        page=1,
        table="",
        pr_filters=PRFilters(),
    )


@pytest.mark.django_db
def test_reviewer_load_hand_computed_counts():
    repository = RepositoryFactory()
    author = IdentityFactory()
    reviewer_person = PersonFactory(display_name="Rae")
    reviewer = IdentityFactory(person=reviewer_person)
    for _index in range(3):
        pr = PullRequestFactory(repository=repository, author=author, created_at=SUBMITTED)
        ReviewFactory(pull_request=pr, reviewer=reviewer, submitted_at=SUBMITTED)

    result = reviewer_load(_scope(), _params())

    assert [(load.person, load.reviews_given, load.pull_requests_reviewed) for load in result] == [
        (reviewer_person, 3, 3)
    ]


@pytest.mark.django_db
def test_reviewer_load_counts_repeat_rounds_on_one_pr_once_as_a_pull_request():
    """The point of the second series on the Reviews chart: three rounds on one pull request is
    three reviews but one pull request reviewed, so the two counts have to come apart."""
    repository = RepositoryFactory()
    author = IdentityFactory()
    reviewer_person = PersonFactory(display_name="Rae")
    reviewer = IdentityFactory(person=reviewer_person)
    pr = PullRequestFactory(repository=repository, author=author, created_at=SUBMITTED)
    for _index in range(3):
        ReviewFactory(pull_request=pr, reviewer=reviewer, submitted_at=SUBMITTED)

    result = reviewer_load(_scope(), _params())

    assert len(result) == 1
    assert result[0].reviews_given == 3
    assert result[0].pull_requests_reviewed == 1


@pytest.mark.django_db
def test_reviewer_load_excludes_self_review():
    repository = RepositoryFactory()
    person = PersonFactory()
    identity = IdentityFactory(person=person)
    pr = PullRequestFactory(repository=repository, author=identity, created_at=SUBMITTED)
    ReviewFactory(pull_request=pr, reviewer=identity, submitted_at=SUBMITTED)

    result = reviewer_load(_scope(), _params())

    assert result == []


@pytest.mark.django_db
def test_reviewer_load_excludes_bots():
    repository = RepositoryFactory()
    author = IdentityFactory()
    bot_reviewer = IdentityFactory(person=PersonFactory(is_bot=True))
    pr = PullRequestFactory(repository=repository, author=author, created_at=SUBMITTED)
    ReviewFactory(pull_request=pr, reviewer=bot_reviewer, submitted_at=SUBMITTED)

    result = reviewer_load(_scope(), _params())

    assert result == []


@pytest.mark.django_db
def test_reviewer_load_empty_scope_returns_empty_list():
    assert reviewer_load(_scope(), _params()) == []


@pytest.mark.django_db
def test_author_reviewer_matrix_hand_computed_cells():
    repository = RepositoryFactory()
    author_person = PersonFactory(display_name="Author")
    author = IdentityFactory(person=author_person)
    reviewer_person = PersonFactory(display_name="Reviewer")
    reviewer = IdentityFactory(person=reviewer_person)
    pr = PullRequestFactory(repository=repository, author=author, created_at=SUBMITTED)
    ReviewFactory(pull_request=pr, reviewer=reviewer, submitted_at=SUBMITTED)
    ReviewFactory(pull_request=pr, reviewer=reviewer, submitted_at=SUBMITTED)

    heatmap = author_reviewer_matrix(_scope(), _params())

    assert [axis.id for axis in heatmap.authors] == [author_person.id]
    # The author never reviews, so they trail the reviewer axis as a zero column.
    assert [axis.id for axis in heatmap.reviewers] == [reviewer_person.id, author_person.id]
    # Two review rounds on one pull request: cells count distinct pull requests.
    assert heatmap.cells[author_person.id][reviewer_person.id] == 1
    assert heatmap.max_value == 1


@pytest.mark.django_db
def test_author_reviewer_matrix_empty_scope_returns_empty_heatmap():
    heatmap = author_reviewer_matrix(_scope(), _params())
    assert heatmap.authors == []
    assert heatmap.reviewers == []
    assert heatmap.cells == {}
    assert heatmap.max_value == 0


@pytest.mark.django_db
def test_author_reviewer_matrix_folds_the_rest_into_other():
    from apps.catalog.services import set_setting

    set_setting("REVIEW_HEATMAP_TOP_N", 1)
    repository = RepositoryFactory()
    author = IdentityFactory(person=PersonFactory(display_name="Author"))
    big_reviewer = IdentityFactory(person=PersonFactory(display_name="Big"))
    small_reviewer = IdentityFactory(person=PersonFactory(display_name="Small"))
    pr = PullRequestFactory(repository=repository, author=author, created_at=SUBMITTED)
    for _index in range(3):
        ReviewFactory(pull_request=pr, reviewer=big_reviewer, submitted_at=SUBMITTED)
    ReviewFactory(pull_request=pr, reviewer=small_reviewer, submitted_at=SUBMITTED)

    heatmap = author_reviewer_matrix(_scope(), _params())

    reviewer_ids = [axis.id for axis in heatmap.reviewers]
    assert big_reviewer.person_id in reviewer_ids
    assert None in reviewer_ids  # the folded "Other" bucket holds the small reviewer.


MERGED = datetime.datetime(2026, 6, 20, tzinfo=datetime.UTC)


def _merged_pr(repository, author):
    return PullRequestFactory(
        repository=repository,
        author=author,
        created_at=SUBMITTED,
        state="merged",
        merged_at=MERGED,
    )


@pytest.mark.django_db
def test_author_reviewer_matrix_shows_self_review_on_the_diagonal_outside_the_heat_scale():
    repository = RepositoryFactory()
    person = PersonFactory(display_name="Solo")
    identity = IdentityFactory(person=person)
    other = IdentityFactory(person=PersonFactory(display_name="Other person"))
    own_pr = PullRequestFactory(repository=repository, author=identity, created_at=SUBMITTED)
    ReviewFactory(pull_request=own_pr, reviewer=identity, submitted_at=SUBMITTED)
    ReviewFactory(pull_request=own_pr, reviewer=other, submitted_at=SUBMITTED)

    heatmap = author_reviewer_matrix(_scope(), _params())
    grid = heat_map_grid(heatmap)

    assert heatmap.cells[person.id][person.id] == 1
    diagonal = next(cell for cell in grid[0].cells if cell.reviewer.id == person.id)
    assert diagonal.same_person and diagonal.is_self_review
    assert diagonal.level == 0
    # Self-review is not "reviewed by someone else" and does not count as review activity.
    solo_column = next(axis for axis in heatmap.reviewers if axis.id == person.id)
    assert solo_column.pr_count == 0


@pytest.mark.django_db
def test_author_reviewer_matrix_same_person_without_self_review_is_muted_not_flagged():
    repository = RepositoryFactory()
    person = PersonFactory(display_name="Author")
    identity = IdentityFactory(person=person)
    reviewer = IdentityFactory(person=PersonFactory(display_name="Reviewer"))
    pr = PullRequestFactory(repository=repository, author=identity, created_at=SUBMITTED)
    ReviewFactory(pull_request=pr, reviewer=reviewer, submitted_at=SUBMITTED)

    grid = heat_map_grid(author_reviewer_matrix(_scope(), _params()))

    diagonal = next(cell for cell in grid[0].cells if cell.reviewer.id == person.id)
    assert diagonal.same_person
    assert not diagonal.is_self_review


@pytest.mark.django_db
def test_author_reviewer_matrix_flags_an_author_nobody_else_reviews():
    repository = RepositoryFactory()
    lonely = PersonFactory(display_name="Lonely")
    lonely_identity = IdentityFactory(person=lonely)
    for _index in range(5):
        _merged_pr(repository, lonely_identity)

    heatmap = author_reviewer_matrix(_scope(), _params())

    author = next(axis for axis in heatmap.authors if axis.id == lonely.id)
    assert author.pr_count == 5
    assert author.reviewed_by_others == 0
    assert author.flagged


@pytest.mark.django_db
def test_author_reviewer_matrix_does_not_flag_an_author_below_min_sample():
    repository = RepositoryFactory()
    person = PersonFactory(display_name="New")
    identity = IdentityFactory(person=person)
    for _index in range(4):
        _merged_pr(repository, identity)

    heatmap = author_reviewer_matrix(_scope(), _params())

    assert not heatmap.authors[0].flagged


@pytest.mark.django_db
def test_author_reviewer_matrix_flags_a_very_low_activity_reviewer():
    repository = RepositoryFactory()
    author = IdentityFactory(person=PersonFactory(display_name="Author"))
    busy = [IdentityFactory(person=PersonFactory(display_name=f"Busy {n}")) for n in range(3)]
    idle_person = PersonFactory(display_name="Idle")
    idle = IdentityFactory(person=idle_person)
    for index in range(8):
        pr = _merged_pr(repository, author)
        for reviewer in busy:
            ReviewFactory(pull_request=pr, reviewer=reviewer, submitted_at=SUBMITTED)
        if index == 0:
            ReviewFactory(pull_request=pr, reviewer=idle, submitted_at=SUBMITTED)

    heatmap = author_reviewer_matrix(_scope(), _params())

    by_id = {axis.id: axis for axis in heatmap.reviewers}
    assert by_id[idle_person.id].pr_count == 1
    assert by_id[idle_person.id].flagged  # 1 < 25% of the median 8
    assert not any(by_id[reviewer.person_id].flagged for reviewer in busy)
    # The author never reviews: a zero column, flagged as well.
    assert by_id[author.person_id].pr_count == 0
    assert by_id[author.person_id].flagged


@pytest.mark.django_db
def test_prs_waiting_for_review_excludes_reviewed_draft_and_closed():
    repository = RepositoryFactory()
    identity = IdentityFactory()
    waiting = PullRequestFactory(
        repository=repository,
        author=identity,
        state="open",
        is_draft=False,
        ready_for_review_at=SUBMITTED,
        first_review_at=None,
    )
    PullRequestFactory(  # already reviewed
        repository=repository,
        author=identity,
        state="open",
        is_draft=False,
        ready_for_review_at=SUBMITTED,
        first_review_at=SUBMITTED,
    )
    PullRequestFactory(  # draft
        repository=repository,
        author=identity,
        state="open",
        is_draft=True,
        ready_for_review_at=None,
        first_review_at=None,
    )
    PullRequestFactory(  # closed
        repository=repository,
        author=identity,
        state="closed",
        is_draft=False,
        ready_for_review_at=SUBMITTED,
        first_review_at=None,
    )

    result = prs_waiting_for_review(_scope(), _params())

    assert [entry.pull_request.id for entry in result] == [waiting.id]
    assert result[0].hours_waited is not None
    assert result[0].hours_waited >= 0


@pytest.mark.django_db
def test_reviewer_load_query_count_stays_constant_as_rows_grow():
    repository = RepositoryFactory()
    author = IdentityFactory()
    for _index in range(3):
        reviewer = IdentityFactory(person=PersonFactory())
        pr = PullRequestFactory(repository=repository, author=author, created_at=SUBMITTED)
        ReviewFactory(pull_request=pr, reviewer=reviewer, submitted_at=SUBMITTED)

    with CaptureQueriesContext(connection) as first:
        reviewer_load(_scope(), _params())

    for _index in range(3):
        reviewer = IdentityFactory(person=PersonFactory())
        pr = PullRequestFactory(repository=repository, author=author, created_at=SUBMITTED)
        ReviewFactory(pull_request=pr, reviewer=reviewer, submitted_at=SUBMITTED)

    with CaptureQueriesContext(connection) as second:
        reviewer_load(_scope(), _params())

    assert len(second.captured_queries) == len(first.captured_queries)
