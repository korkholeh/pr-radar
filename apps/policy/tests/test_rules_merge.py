import pytest
from django.utils import timezone

from apps.activity.factories import PullRequestFactory, ReviewFactory
from apps.activity.models import PullRequest, Review
from apps.catalog.factories import IdentityFactory, PersonFactory
from apps.policy.factories import AIPolicyFactory
from apps.policy.rules import RULES, load_context
from apps.policy.tests.helpers import make_context

pytestmark = pytest.mark.django_db


def _merged_kwargs(**extra):
    return {"state": PullRequest.State.MERGED, "merged_at": timezone.now(), **extra}


# --- NO_HUMAN_APPROVAL ---


def test_no_human_approval_fires_when_below_required():
    policy = AIPolicyFactory(require_human_approval=True, min_human_approvals=2)
    ctx = make_context(policy=policy, human_approver_person_ids={1}, **_merged_kwargs())
    findings = list(RULES["NO_HUMAN_APPROVAL"](ctx))
    assert len(findings) == 1
    assert findings[0].details_params == {"approvals": 1, "required": 2}


def test_no_human_approval_does_not_fire_when_enough_approvals():
    policy = AIPolicyFactory(require_human_approval=True, min_human_approvals=1)
    ctx = make_context(policy=policy, human_approver_person_ids={1}, **_merged_kwargs())
    assert list(RULES["NO_HUMAN_APPROVAL"](ctx)) == []


def test_no_human_approval_does_not_fire_when_not_required():
    policy = AIPolicyFactory(require_human_approval=False, min_human_approvals=2)
    ctx = make_context(policy=policy, human_approver_person_ids=set(), **_merged_kwargs())
    assert list(RULES["NO_HUMAN_APPROVAL"](ctx)) == []


def test_no_human_approval_does_not_fire_on_open_pr():
    policy = AIPolicyFactory(require_human_approval=True, min_human_approvals=2)
    ctx = make_context(policy=policy, human_approver_person_ids=set(), state=PullRequest.State.OPEN)
    assert list(RULES["NO_HUMAN_APPROVAL"](ctx)) == []


# --- SELF_MERGE ---


def test_self_merge_fires_with_zero_other_approvals():
    ctx = make_context(human_approver_person_ids=set(), **_merged_kwargs(is_self_merged=True))
    findings = list(RULES["SELF_MERGE"](ctx))
    assert len(findings) == 1


def test_self_merge_does_not_fire_when_another_human_approved():
    ctx = make_context(human_approver_person_ids={1}, **_merged_kwargs(is_self_merged=True))
    assert list(RULES["SELF_MERGE"](ctx)) == []


def test_self_merge_does_not_fire_when_not_self_merged():
    ctx = make_context(human_approver_person_ids=set(), **_merged_kwargs(is_self_merged=False))
    assert list(RULES["SELF_MERGE"](ctx)) == []


def test_self_merge_does_not_fire_on_open_pr():
    ctx = make_context(human_approver_person_ids=set(), state=PullRequest.State.OPEN, is_self_merged=True)
    assert list(RULES["SELF_MERGE"](ctx)) == []


# --- NO_TESTS ---


def test_no_tests_fires_over_threshold_without_test_changes():
    policy = AIPolicyFactory(require_tests_for_ai_prs=True)
    pr_file = _pr_file(additions=15, deletions=10)
    ctx = make_context(policy=policy, files=[pr_file], no_tests_min_lines=20, has_test_changes=False)
    findings = list(RULES["NO_TESTS"](ctx))
    assert len(findings) == 1
    assert findings[0].details_params == {"non_test_lines": 25, "threshold": 20}


def test_no_tests_does_not_fire_when_has_test_changes():
    policy = AIPolicyFactory(require_tests_for_ai_prs=True)
    pr_file = _pr_file(additions=15, deletions=10)
    ctx = make_context(policy=policy, files=[pr_file], no_tests_min_lines=20, has_test_changes=True)
    assert list(RULES["NO_TESTS"](ctx)) == []


def test_no_tests_does_not_fire_under_threshold():
    policy = AIPolicyFactory(require_tests_for_ai_prs=True)
    pr_file = _pr_file(additions=5, deletions=5)
    ctx = make_context(policy=policy, files=[pr_file], no_tests_min_lines=20, has_test_changes=False)
    assert list(RULES["NO_TESTS"](ctx)) == []


def test_no_tests_counts_only_non_test_non_excluded_lines():
    policy = AIPolicyFactory(require_tests_for_ai_prs=True)
    files = [
        _pr_file(additions=100, deletions=0, is_test=True),
        _pr_file(additions=100, deletions=0, is_excluded=True),
        _pr_file(additions=15, deletions=10),
    ]
    ctx = make_context(policy=policy, files=files, no_tests_min_lines=20, has_test_changes=False)
    findings = list(RULES["NO_TESTS"](ctx))
    assert len(findings) == 1
    assert findings[0].details_params["non_test_lines"] == 25


def test_no_tests_does_not_fire_when_not_required():
    policy = AIPolicyFactory(require_tests_for_ai_prs=False)
    pr_file = _pr_file(additions=100, deletions=0)
    ctx = make_context(policy=policy, files=[pr_file], no_tests_min_lines=20, has_test_changes=False)
    assert list(RULES["NO_TESTS"](ctx)) == []


def _pr_file(*, additions, deletions, is_test=False, is_excluded=False):
    from apps.activity.factories import PRFileFactory

    return PRFileFactory.build(
        additions=additions, deletions=deletions, is_test=is_test, is_excluded=is_excluded
    )


# --- AI_PR_TOO_LARGE ---


def test_ai_pr_too_large_fires_over_limit():
    policy = AIPolicyFactory(ai_pr_max_effective_lines=100)
    ctx = make_context(policy=policy, effective_additions=60, effective_deletions=50)
    findings = list(RULES["AI_PR_TOO_LARGE"](ctx))
    assert len(findings) == 1
    assert findings[0].details_params == {"effective_lines": 110, "limit": 100}


def test_ai_pr_too_large_does_not_fire_under_limit():
    policy = AIPolicyFactory(ai_pr_max_effective_lines=100)
    ctx = make_context(policy=policy, effective_additions=10, effective_deletions=10)
    assert list(RULES["AI_PR_TOO_LARGE"](ctx)) == []


def test_ai_pr_too_large_does_not_fire_when_limit_is_none():
    policy = AIPolicyFactory(ai_pr_max_effective_lines=None)
    ctx = make_context(policy=policy, effective_additions=1000, effective_deletions=1000)
    assert list(RULES["AI_PR_TOO_LARGE"](ctx)) == []


def test_ai_pr_too_large_does_not_fire_when_effective_lines_are_none():
    policy = AIPolicyFactory(ai_pr_max_effective_lines=1)
    ctx = make_context(policy=policy, effective_additions=None, effective_deletions=None)
    assert list(RULES["AI_PR_TOO_LARGE"](ctx)) == []


# --- human approver resolution (load_context, real Identity/Person rows) ---


def test_human_approver_person_ids_excludes_author_bot_and_dedupes():
    author_person = PersonFactory()
    author_identity = IdentityFactory(person=author_person)
    pr = PullRequestFactory(author=author_identity, **_merged_kwargs())

    # the author approving their own PR does not count
    ReviewFactory(pull_request=pr, reviewer=author_identity, state=Review.State.APPROVED)

    # a bot approving does not count
    bot_person = PersonFactory(is_bot=True)
    bot_identity = IdentityFactory(person=bot_person)
    ReviewFactory(pull_request=pr, reviewer=bot_identity, state=Review.State.APPROVED)

    # two approvals from the same human count once
    reviewer_person = PersonFactory()
    reviewer_identity = IdentityFactory(person=reviewer_person)
    ReviewFactory(pull_request=pr, reviewer=reviewer_identity, state=Review.State.APPROVED)
    ReviewFactory(pull_request=pr, reviewer=reviewer_identity, state=Review.State.APPROVED)

    # a non-approved review does not count
    other_person = PersonFactory()
    other_identity = IdentityFactory(person=other_person)
    ReviewFactory(pull_request=pr, reviewer=other_identity, state=Review.State.CHANGES_REQUESTED)

    policy = AIPolicyFactory()
    ctx = load_context(pr.pk, policy)
    assert ctx.human_approver_person_ids == {reviewer_person.pk}
