import logging
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.ai_detection.models import DetectionRule
from apps.ai_detection.rules import DEFAULT_RULES_PATH, load_rule_definitions

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Seeds DetectionRule rows from fixtures/detection_rules.yaml. Creates rules missing by name; "
        "never overwrites an existing row's pattern/tool/confidence/notes unless --update is passed, "
        "since a rule is operator-owned data once a lead has tuned it."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--path", type=Path, default=DEFAULT_RULES_PATH, help="Path to the rule definitions YAML file."
        )
        parser.add_argument(
            "--update",
            action="store_true",
            help="Overwrite pattern/tool/confidence/notes of an already-seeded rule with the file's values.",
        )

    def handle(self, *args, **options) -> None:
        definitions = load_rule_definitions(options["path"])
        created = 0
        updated = 0

        for definition in definitions:
            row = DetectionRule.objects.filter(name=definition.name).first()
            if row is None:
                DetectionRule.objects.create(
                    name=definition.name,
                    detector=definition.detector,
                    pattern=definition.pattern,
                    tool=definition.tool,
                    confidence=definition.confidence,
                    notes=definition.notes,
                )
                created += 1
            elif options["update"]:
                row.detector = definition.detector
                row.pattern = definition.pattern
                row.tool = definition.tool
                row.confidence = definition.confidence
                row.notes = definition.notes
                row.save(update_fields=["detector", "pattern", "tool", "confidence", "notes", "updated_at"])
                updated += 1

        self.stdout.write(f"Seeded detection rules: {created} created, {updated} updated.")
