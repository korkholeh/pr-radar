import pytest
from django.utils import timezone
from freezegun import freeze_time

from apps.policy.models import AIPolicy
from apps.policy.services import current_policy, details_hash


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
