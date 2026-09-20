import logging
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.policy.models import SensitivePathRule
from apps.policy.sensitive_paths import DEFAULT_SENSITIVE_PATHS_PATH, load_sensitive_path_definitions

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Seeds the PLANEKS risk table as advisory SensitivePathRule rows from "
        "fixtures/sensitive_paths.yaml. Creates rules missing by glob; never overwrites a rule an "
        "operator has edited unless --update is passed. Seeded rules are advisory: they classify a "
        "path's risk and raise no violation of their own, so seeding changes no pull request's "
        "compliance until a risk-reading check is switched on."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--path",
            type=Path,
            default=DEFAULT_SENSITIVE_PATHS_PATH,
            help="Path to the sensitive-path definitions YAML file.",
        )
        parser.add_argument(
            "--update",
            action="store_true",
            help="Overwrite risk_level/description of an already-seeded global rule with the file's.",
        )

    def handle(self, *args, **options) -> None:
        definitions = load_sensitive_path_definitions(options["path"])
        created = 0
        updated = 0

        for definition in definitions:
            # Keyed on the glob, and only against global rules: a project-scoped rule with the same
            # glob is somebody's deliberate override and is left alone.
            row = SensitivePathRule.objects.filter(glob=definition.glob, project__isnull=True).first()
            if row is None:
                SensitivePathRule.objects.create(
                    glob=definition.glob,
                    ai_mode=SensitivePathRule.AiMode.ADVISORY,
                    risk_level=definition.risk_level,
                    description=definition.description,
                    is_active=True,
                )
                created += 1
            elif options["update"]:
                row.risk_level = definition.risk_level
                row.description = definition.description
                row.save(update_fields=["risk_level", "description"])
                updated += 1

        self.stdout.write(f"Seeded sensitive-path rules: {created} created, {updated} updated.")
        if created:
            self.stdout.write(
                "All of them are advisory: they classify risk and raise no violation on their own. "
                "Turn on a check that reads risk (a high-risk plan requirement, a minimum number of "
                "approvals, a size limit per risk level) in Settings -> AI policy."
            )
