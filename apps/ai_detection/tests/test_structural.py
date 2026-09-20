"""The per-PR structural kinds (phase 12, stage 4).

Each kind gets a firing case and a near-miss, because a heuristic that fires on everything is
worse than no heuristic at all: these rules read the shape of an honest developer's work as
readily as a machine's, which is why none of them may be `high` confidence and why two distinct
kinds are needed before a pull request's AI status moves.
"""

import ast
import datetime
import inspect

import pytest
from django.utils import timezone

from apps.activity.factories import (
    CommitFactory,
    PRFileFactory,
    PullRequestCommitFactory,
    PullRequestFactory,
    ReviewCommentFactory,
)
from apps.activity.models import PullRequest
from apps.ai_detection import baselines, diffsignals, structural
from apps.ai_detection.baselines import BASELINE_FUNCTIONS
from apps.ai_detection.diffsignals import DIFF_FUNCTIONS
from apps.ai_detection.evidence import EVIDENCE_MESSAGES
from apps.ai_detection.models import SignalFamily, SignalKind, kinds_in_family
from apps.ai_detection.structural import (
    SIGNAL_FUNCTIONS,
    StructuralCommit,
    StructuralContext,
    StructuralFile,
    load_structural_context,
    run_kind,
)
from apps.catalog.factories import IdentityFactory

NOW = datetime.datetime(2026, 9, 20, 12, 0, tzinfo=datetime.UTC)


def _ctx(**overrides) -> StructuralContext:
    defaults = dict(pull_request=None, commits=(), files=(), review_comment_times=())
    defaults.update(overrides)
    return StructuralContext(**defaults)


def _pr(**kwargs) -> PullRequest:
    return PullRequestFactory.build(**kwargs)


def _commit(minutes_after=0, lines=100) -> StructuralCommit:
    return StructuralCommit(
        committed_at=NOW + datetime.timedelta(minutes=minutes_after),
        additions=lines,
        deletions=0,
    )


def test_every_per_pr_kind_has_a_function_and_an_evidence_message():
    """This module owns the per-PR family only. The other two are covered by their own modules'
    tests (`test_baselines.py`, `test_diffsignals.py`), and `test_all_kinds_are_implemented_
    somewhere` below is what stops a kind from being declared and implemented nowhere at all."""
    per_pr_kinds = kinds_in_family(SignalFamily.PER_PR)
    assert set(SIGNAL_FUNCTIONS) == per_pr_kinds
    for kind in per_pr_kinds:
        assert kind in EVIDENCE_MESSAGES, kind


def test_all_kinds_are_implemented_somewhere():
    implemented = set(SIGNAL_FUNCTIONS) | set(BASELINE_FUNCTIONS) | set(DIFF_FUNCTIONS)
    assert implemented == set(SignalKind.values)


def test_an_unknown_kind_yields_nothing_rather_than_raising():
    """A row written by a future version of the app must not 500 the pull-request page."""
    assert list(run_kind("reads_minds", {}, _ctx())) == []


# -- fast_large_pr -------------------------------------------------------------------------------


def test_fast_large_pr_fires_and_reports_both_measurement_and_threshold():
    pr = _pr(
        first_commit_at=NOW,
        merged_at=NOW + datetime.timedelta(minutes=40),
        additions=500,
        deletions=100,
        changed_files=12,
    )
    matches = list(run_kind(SignalKind.FAST_LARGE_PR, {}, _ctx(pull_request=pr)))

    assert len(matches) == 1
    params = matches[0].params
    assert params["lines"] == 600
    assert params["files"] == 12
    assert params["hours"] == 0.7
    # The thresholds travel with the evidence: "40 minutes" means nothing to a reader who does
    # not know the rule said two hours.
    assert params["max_hours"] == 2


def test_fast_large_pr_does_not_fire_on_a_change_that_took_days():
    pr = _pr(
        first_commit_at=NOW,
        merged_at=NOW + datetime.timedelta(days=3),
        additions=500,
        deletions=100,
        changed_files=12,
    )
    assert list(run_kind(SignalKind.FAST_LARGE_PR, {}, _ctx(pull_request=pr))) == []


def test_fast_large_pr_does_not_fire_on_a_small_fast_change():
    pr = _pr(
        first_commit_at=NOW,
        merged_at=NOW + datetime.timedelta(minutes=5),
        additions=8,
        deletions=1,
        changed_files=1,
    )
    assert list(run_kind(SignalKind.FAST_LARGE_PR, {}, _ctx(pull_request=pr))) == []


def test_fast_large_pr_needs_both_ends_of_the_span():
    """An unmerged pull request, or one with no first commit recorded, has no span to measure."""
    pr = _pr(first_commit_at=NOW, merged_at=None, additions=900, changed_files=20)
    assert list(run_kind(SignalKind.FAST_LARGE_PR, {}, _ctx(pull_request=pr))) == []


# -- commit_burst --------------------------------------------------------------------------------


def test_commit_burst_fires_on_substantial_commits_seconds_apart():
    commits = tuple(_commit(minutes_after=index, lines=40) for index in range(5))
    matches = list(run_kind(SignalKind.COMMIT_BURST, {}, _ctx(commits=commits)))

    assert len(matches) == 1
    assert matches[0].params["commits"] == 5
    assert matches[0].params["gap_seconds"] == 60  # the widest gap observed, not the threshold


def test_commit_burst_ignores_a_run_of_tiny_fixup_commits():
    """A string of one-line fixups seconds apart is a normal human habit, not a burst."""
    commits = tuple(_commit(minutes_after=index, lines=2) for index in range(8))
    assert list(run_kind(SignalKind.COMMIT_BURST, {}, _ctx(commits=commits))) == []


def test_commit_burst_does_not_fire_when_the_commits_are_spread_out():
    commits = tuple(_commit(minutes_after=index * 60, lines=40) for index in range(6))
    assert list(run_kind(SignalKind.COMMIT_BURST, {}, _ctx(commits=commits))) == []


def test_commit_burst_needs_the_run_to_be_consecutive():
    """Four fast commits, a long pause, then two more is not a burst of five."""
    commits = (
        _commit(minutes_after=0, lines=40),
        _commit(minutes_after=1, lines=40),
        _commit(minutes_after=2, lines=40),
        _commit(minutes_after=3, lines=40),
        _commit(minutes_after=400, lines=40),
        _commit(minutes_after=401, lines=40),
    )
    assert list(run_kind(SignalKind.COMMIT_BURST, {}, _ctx(commits=commits))) == []


# -- single_large_commit -------------------------------------------------------------------------


def test_single_large_commit_fires_when_the_whole_change_is_one_commit():
    pr = _pr(additions=400, deletions=50, changed_files=9)
    ctx = _ctx(pull_request=pr, commits=(_commit(lines=450),))

    matches = list(run_kind(SignalKind.SINGLE_LARGE_COMMIT, {}, ctx))

    assert len(matches) == 1
    assert matches[0].params["lines"] == 450


def test_single_large_commit_does_not_fire_when_the_work_arrived_in_steps():
    pr = _pr(additions=400, deletions=50, changed_files=9)
    ctx = _ctx(pull_request=pr, commits=(_commit(0), _commit(60), _commit(120)))
    assert list(run_kind(SignalKind.SINGLE_LARGE_COMMIT, {}, ctx)) == []


def test_single_large_commit_does_not_fire_on_a_small_one_commit_change():
    pr = _pr(additions=12, deletions=1, changed_files=1)
    ctx = _ctx(pull_request=pr, commits=(_commit(lines=13),))
    assert list(run_kind(SignalKind.SINGLE_LARGE_COMMIT, {}, ctx)) == []


# -- mass_file_creation --------------------------------------------------------------------------


def test_mass_file_creation_fires_on_many_new_files_across_directories():
    files = tuple(
        StructuralFile(path=f"apps/mod{index % 4}/file{index}.py", status="added", additions=5, deletions=0)
        for index in range(12)
    )
    matches = list(run_kind(SignalKind.MASS_FILE_CREATION, {}, _ctx(files=files)))

    assert len(matches) == 1
    assert matches[0].params["added_files"] == 12
    assert matches[0].params["directories"] == 4


def test_mass_file_creation_ignores_modified_files():
    files = tuple(
        StructuralFile(
            path=f"apps/mod{index % 4}/file{index}.py", status="modified", additions=5, deletions=1
        )
        for index in range(12)
    )
    assert list(run_kind(SignalKind.MASS_FILE_CREATION, {}, _ctx(files=files))) == []


def test_mass_file_creation_does_not_fire_when_everything_lands_in_one_directory():
    files = tuple(
        StructuralFile(path=f"apps/one/file{index}.py", status="added", additions=5, deletions=0)
        for index in range(12)
    )
    assert list(run_kind(SignalKind.MASS_FILE_CREATION, {}, _ctx(files=files))) == []


# -- instant_review_response ---------------------------------------------------------------------


def test_instant_review_response_fires_on_repeated_immediate_commits():
    comments = tuple(NOW + datetime.timedelta(minutes=30 * index) for index in range(3))
    commits = tuple(_commit(minutes_after=30 * index + 2) for index in range(3))

    matches = list(
        run_kind(SignalKind.INSTANT_REVIEW_RESPONSE, {}, _ctx(commits=commits, review_comment_times=comments))
    )

    assert len(matches) == 1
    assert matches[0].params["occurrences"] == 3


def test_instant_review_response_does_not_fire_on_a_single_quick_answer():
    """Once is a reviewer catching a typo the author was already fixing."""
    ctx = _ctx(commits=(_commit(minutes_after=2),), review_comment_times=(NOW,))
    assert list(run_kind(SignalKind.INSTANT_REVIEW_RESPONSE, {}, ctx)) == []


def test_instant_review_response_counts_one_commit_once():
    """A single commit answering five comments is one occurrence, not five."""
    comments = tuple(NOW + datetime.timedelta(seconds=index) for index in range(5))
    ctx = _ctx(commits=(_commit(minutes_after=1),), review_comment_times=comments)
    assert list(run_kind(SignalKind.INSTANT_REVIEW_RESPONSE, {}, ctx)) == []


def test_instant_review_response_ignores_a_commit_that_predates_the_comment():
    comments = tuple(NOW + datetime.timedelta(minutes=30 * index) for index in range(3))
    commits = tuple(_commit(minutes_after=30 * index - 2) for index in range(3))
    ctx = _ctx(commits=commits, review_comment_times=comments)
    assert list(run_kind(SignalKind.INSTANT_REVIEW_RESPONSE, {}, ctx)) == []


# -- the context ---------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_context_excludes_files_the_settings_exclude():
    pr = PullRequestFactory()
    PRFileFactory(pull_request=pr, path="apps/sync/client.py", status="added")
    PRFileFactory(pull_request=pr, path="vendor/lib.js", status="added", is_excluded=True)

    ctx = load_structural_context(PullRequest.objects.get(pk=pr.pk))

    assert [pr_file.path for pr_file in ctx.files] == ["apps/sync/client.py"]


@pytest.mark.django_db
def test_the_context_ignores_comments_the_pull_requests_own_author_left():
    author = IdentityFactory()
    pr = PullRequestFactory(author=author)
    ReviewCommentFactory(pull_request=pr, author=author, created_at=timezone.now())
    ReviewCommentFactory(pull_request=pr, author=IdentityFactory(), created_at=timezone.now())

    ctx = load_structural_context(PullRequest.objects.get(pk=pr.pk))

    assert len(ctx.review_comment_times) == 1


@pytest.mark.django_db
def test_an_unresolved_comment_author_does_not_discard_every_comment():
    """A bare `author_id != pr.author_id` would compare NULL with NULL and drop the lot, which is
    how `instant_review_response` silently produced nothing on unmapped identities."""
    pr = PullRequestFactory(author=None)
    ReviewCommentFactory(pull_request=pr, author=None, created_at=timezone.now())

    ctx = load_structural_context(PullRequest.objects.get(pk=pr.pk))

    assert len(ctx.review_comment_times) == 1


@pytest.mark.django_db
def test_the_context_orders_commits_by_time():
    pr = PullRequestFactory()
    late = CommitFactory(committed_at=NOW + datetime.timedelta(hours=2), additions=5)
    early = CommitFactory(committed_at=NOW, additions=5)
    PullRequestCommitFactory(pull_request=pr, commit=late, position=0)
    PullRequestCommitFactory(pull_request=pr, commit=early, position=1)

    ctx = load_structural_context(PullRequest.objects.get(pk=pr.pk))

    assert [commit.committed_at for commit in ctx.commits] == [NOW, NOW + datetime.timedelta(hours=2)]


# -- PLAN D7: outcome data is never a detector input ---------------------------------------------


def test_no_structural_kind_reads_churn_revert_or_follow_up_fix_data():
    """Churn, reverts and follow-up fixes are what this project *measures for* the AI cohort.
    Using them to decide who is in that cohort would make the AI-quality dashboards self-proving:
    the cohort would be built from bad outcomes and then shown to have bad outcomes.

    Checked over the parsed identifiers rather than the raw text, so a docstring may still explain
    the rule (and `unused_new_dependency` may say where stage 6 gets its bytes from) while an
    actual read of `ctx.churn_ratio` fails the build.
    """
    forbidden = {"churn", "churn_ratio", "is_revert", "has_followup_fix", "followup_fix"}
    kind_functions = []
    for module in (structural, baselines, diffsignals):
        tree = ast.parse(inspect.getsource(module))
        kind_functions += [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name.lstrip("_") in set(SignalKind.values)
        ]
    # Every declared kind is checked, wherever it lives — including the diff family, which runs
    # inside the churn job and therefore sits closest of the three to the outcome data it must
    # never read.
    assert {node.name.lstrip("_") for node in kind_functions} == set(SignalKind.values)

    for function in kind_functions:
        identifiers = {node.attr for node in ast.walk(function) if isinstance(node, ast.Attribute)} | {
            node.id for node in ast.walk(function) if isinstance(node, ast.Name)
        }
        literals = {
            node.value
            for node in ast.walk(function)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        # Constants are checked too: a kind could reach outcome data through a params key or a
        # queryset field name rather than an attribute access.
        used = identifiers | {value for value in literals if value in forbidden}
        assert not (used & forbidden), (function.name, used & forbidden)
