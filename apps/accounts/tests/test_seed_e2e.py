import pytest
from django.core.management import call_command

from apps.catalog.identity import create_person_from_identity, merge_people
from apps.catalog.models import Identity, Person


@pytest.mark.django_db
def test_seed_e2e_is_idempotent_after_the_ui_actions_it_seeds_for():
    """Regression test: the Playwright suite may run against a surface it did not tear down and
    re-seed in between (the orchestrator can retry `pytest e2e` without a fresh `make e2e-up`).
    Re-running seed_e2e after the actions the queue cases drive must land on the same starting
    state as a first run, not accumulate duplicates or leave targets already consumed."""
    call_command("seed_e2e")

    Person.objects.create(display_name="E2E UI Created Person", team="Growth")

    assign_identity = Identity.objects.get(value="e2e-assign-target@example.com")
    assign_identity.person = Person.objects.get(display_name="E2E Assign Target")
    assign_identity.save(update_fields=["person"])

    mark_bot_identity = Identity.objects.get(value="e2e-mark-bot-target@example.com")
    create_person_from_identity(mark_bot_identity, is_bot=True)

    merge_source = Person.objects.get(display_name="E2E Merge Source")
    merge_target = Person.objects.get(display_name="E2E Merge Target")
    merge_people(merge_source, merge_target, actor=None)

    call_command("seed_e2e")

    assert Person.objects.filter(display_name="E2E UI Created Person").count() == 0

    reset_assign_identity = Identity.objects.get(value="e2e-assign-target@example.com")
    assert reset_assign_identity.person_id is None
    assert Person.objects.filter(display_name="E2E Assign Target").count() == 1

    reset_mark_bot_identity = Identity.objects.get(value="e2e-mark-bot-target@example.com")
    assert reset_mark_bot_identity.person_id is None
    assert not Person.objects.filter(display_name="e2e-mark-bot-target@example.com").exists()

    reset_merge_source = Person.objects.get(display_name="E2E Merge Source")
    reset_merge_source_identity = Identity.objects.get(value="e2e-merge-source-login")
    assert reset_merge_source_identity.person_id == reset_merge_source.pk
    assert Person.objects.filter(display_name="E2E Merge Target").count() == 1
