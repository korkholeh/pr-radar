import pytest
from django.utils import timezone
from freezegun import freeze_time

from apps.accounts.factories import UserFactory
from apps.activity.factories import PullRequestFactory
from apps.activity.models import AIDisclosure
from apps.policy.models import AIPolicy, PolicyViolation
from apps.policy.services import current_policy, details_hash, evaluate_pull_request, save_policy_version


def test_details_hash_stable_under_key_reordering():
    a = details_hash({"path": "foo.py", "lines": 12})
    b = details_hash({"lines": 12, "path": "foo.py"})
    assert a == b


def test_details_hash_differs_when_value_changes():
    a = details_hash({"path": "foo.py", "lines": 12})
    b = details_hash({"path": "foo.py", "lines": 13})
    assert a != b


@pytest.mark.django_db
@freeze_time("2026-01-01T00:00:00Z")
def test_current_policy_returns_newest_effective_row():
    now = timezone.now()
    older = AIPolicy.objects.create(effective_from=now - timezone.timedelta(days=10))
    newer = AIPolicy.objects.create(effective_from=now - timezone.timedelta(days=1))
    AIPolicy.objects.create(effective_from=now + timezone.timedelta(days=10))

    assert current_policy() == newer
    assert current_policy() != older


@pytest.mark.django_db
@freeze_time("2026-01-01T00:00:00Z")
def test_current_policy_returns_none_when_no_policy_applies():
    now = timezone.now()
    AIPolicy.objects.create(effective_from=now + timezone.timedelta(days=1))
    assert current_policy() is None


VALID_CLEANED_DATA = {
    "allowed_tools": [],
    "require_disclosure": True,
    "require_human_approval": False,
    "min_human_approvals": 1,
    "require_tests_for_ai_prs": False,
    "ai_pr_max_effective_lines": None,
}


@pytest.mark.django_db
@freeze_time("2026-06-01T00:00:00Z")
def test_first_policy_version_governs_pull_requests_synced_before_it_was_saved():
    """Regression for the round-2 review's major finding: stamping `effective_from = now()` on
    the very first saved version meant it governed no PR already in the database, because every
    synced PR's `created_at` is necessarily before the moment an admin gets around to configuring
    the policy."""
    old_pr = PullRequestFactory(
        ai_disclosure=AIDisclosure.MISSING, created_at=timezone.now() - timezone.timedelta(days=30)
    )

    policy = save_policy_version(UserFactory(), VALID_CLEANED_DATA)

    assert policy.effective_from == old_pr.created_at
    evaluate_pull_request(old_pr.pk)
    assert PolicyViolation.objects.filter(
        pull_request=old_pr, rule_code=PolicyViolation.RuleCode.DISCLOSURE_MISSING
    ).exists()


@pytest.mark.django_db
@freeze_time("2026-06-01T00:00:00Z")
def test_first_policy_version_defaults_to_now_with_no_pull_requests_synced():
    policy = save_policy_version(UserFactory(), VALID_CLEANED_DATA)
    assert policy.effective_from == timezone.now()


@pytest.mark.django_db
@freeze_time("2026-06-01T00:00:00Z")
def test_second_policy_version_is_stamped_at_save_time_not_the_earliest_pr():
    PullRequestFactory(created_at=timezone.now() - timezone.timedelta(days=30))
    save_policy_version(UserFactory(), VALID_CLEANED_DATA)

    with freeze_time("2026-06-02T00:00:00Z"):
        second = save_policy_version(UserFactory(), VALID_CLEANED_DATA)
        assert second.effective_from == timezone.now()
