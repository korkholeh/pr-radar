import pytest
from django.core.management import call_command

from apps.ai_detection.models import Confidence, DetectionRule
from apps.ai_detection.rules import load_rule_definitions


@pytest.mark.django_db
def test_seeding_creates_the_full_set():
    call_command("seed_detection_rules")
    definitions = load_rule_definitions()
    assert DetectionRule.objects.count() == len(definitions)
    for definition in definitions:
        assert DetectionRule.objects.filter(name=definition.name).exists()


@pytest.mark.django_db
def test_seeding_twice_creates_nothing_new():
    call_command("seed_detection_rules")
    count_after_first = DetectionRule.objects.count()

    call_command("seed_detection_rules")

    assert DetectionRule.objects.count() == count_after_first


@pytest.mark.django_db
def test_an_admin_edited_pattern_survives_a_reseed():
    call_command("seed_detection_rules")
    definitions = load_rule_definitions()
    rule = DetectionRule.objects.get(name=definitions[0].name)
    rule.pattern = "an admin fixed this false positive"
    rule.save(update_fields=["pattern"])

    call_command("seed_detection_rules")

    rule.refresh_from_db()
    assert rule.pattern == "an admin fixed this false positive"


@pytest.mark.django_db
def test_update_flag_restores_the_seeded_pattern():
    call_command("seed_detection_rules")
    definitions = load_rule_definitions()
    target = definitions[0]
    rule = DetectionRule.objects.get(name=target.name)
    rule.pattern = "an admin fixed this false positive"
    rule.save(update_fields=["pattern"])

    call_command("seed_detection_rules", update=True)

    rule.refresh_from_db()
    assert rule.pattern == target.pattern


@pytest.mark.django_db
def test_a_deactivated_rule_stays_deactivated():
    call_command("seed_detection_rules")
    definitions = load_rule_definitions()
    rule = DetectionRule.objects.get(name=definitions[0].name)
    rule.is_active = False
    rule.save(update_fields=["is_active"])

    call_command("seed_detection_rules")
    rule.refresh_from_db()
    assert rule.is_active is False

    call_command("seed_detection_rules", update=True)
    rule.refresh_from_db()
    assert rule.is_active is False


@pytest.mark.django_db
def test_every_disputed_seeded_rule_ships_low_confidence():
    call_command("seed_detection_rules")
    definitions = load_rule_definitions()
    disputed_names = {d.name for d in definitions if d.disputed}
    assert disputed_names, "fixture should have at least one disputed rule to make this test meaningful"
    for name in disputed_names:
        assert DetectionRule.objects.get(name=name).confidence == Confidence.LOW
