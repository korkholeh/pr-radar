import logging
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.ai_detection.models import SignalRule
from apps.ai_detection.rules import DEFAULT_SIGNAL_RULES_PATH, load_signal_rule_definitions

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Seeds SignalRule rows from fixtures/signal_rules.yaml. Creates rules missing by name; never "
        "overwrites an existing row's kind/params/tool/confidence/notes unless --update is passed, "
        "since thresholds are operator-owned data once a lead has tuned them. Every rule is created "
        "DEACTIVATED: unlike a vendor's commit trailer, a threshold has no right answer across teams."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--path",
            type=Path,
            default=DEFAULT_SIGNAL_RULES_PATH,
            help="Path to the signal rule definitions YAML file.",
        )
        parser.add_argument(
            "--update",
            action="store_true",
            help="Overwrite kind/params/tool/confidence/notes of an already-seeded rule with the file's.",
        )

    def handle(self, *args, **options) -> None:
        definitions = load_signal_rule_definitions(options["path"])
        created = 0
        updated = 0

        for definition in definitions:
            row = SignalRule.objects.filter(name=definition.name).first()
            if row is None:
                # `is_active` is written only here, on creation, and always False. It is a lead's
                # switch afterwards, so neither a re-seed nor --update ever touches it again.
                SignalRule.objects.create(
                    name=definition.name,
                    kind=definition.kind,
                    params=definition.params,
                    tool=definition.tool,
                    confidence=definition.confidence,
                    notes=definition.notes,
                    is_active=False,
                )
                created += 1
            elif options["update"]:
                row.kind = definition.kind
                row.params = definition.params
                row.tool = definition.tool
                row.confidence = definition.confidence
                row.notes = definition.notes
                row.save(update_fields=["kind", "params", "tool", "confidence", "notes", "updated_at"])
                updated += 1

        self.stdout.write(f"Seeded structural signal rules: {created} created, {updated} updated.")
        if created:
            self.stdout.write(
                f"All {created} were created deactivated. A threshold has no right answer across "
                f"teams: dry-run each one in Settings -> Structural signals against your own pull "
                f"requests, adjust the numbers, then switch it on. See docs/user/tune-ai-detection.md."
            )
