"""Re-applies `BOT_LOGINS` / `BOT_LOGIN_SUFFIXES` to `Person` rows that already exist.

`Person.is_bot` is decided once, when identity resolution first creates the person
(`apps.catalog.identity.person_for_login`). Widening the bot settings afterwards therefore does
nothing for people already in the database — an AI reviewer synced before its login was known
keeps counting as a human in every review metric. This command closes that gap.

It only ever flips `False -> True`. The opposite direction is a lead's decision, taken in
Settings -> People ("mark bot"), and a settings change must never silently undo it.

Nothing is written without `--apply`; the default run prints what it would change.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.catalog.identity import is_bot_login
from apps.catalog.models import Identity, Person


class Command(BaseCommand):
    help = (
        "Re-evaluates Person.is_bot against the current BOT_LOGINS/BOT_LOGIN_SUFFIXES settings. "
        "Only marks people as bots, never unmarks them. Prints the changes; writes only with --apply."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the changes. Without it the command only reports what it would do.",
        )

    def handle(self, *args, **options) -> None:
        candidates: dict[int, tuple[Person, list[str]]] = {}

        identities = (
            Identity.objects.filter(kind=Identity.Kind.GITHUB_LOGIN, person__isnull=False)
            .select_related("person")
            .order_by("value")
        )
        for identity in identities:
            person = identity.person
            if person is None or person.is_bot:
                continue
            if not is_bot_login(identity.value):
                continue
            _, logins = candidates.setdefault(person.pk, (person, []))
            logins.append(identity.value)

        if not candidates:
            self.stdout.write("No person needs reclassifying.")
            return

        for person, logins in candidates.values():
            self.stdout.write(f"  {person.display_name} (id={person.pk}) matches {', '.join(logins)}")

        if not options["apply"]:
            self.stdout.write(
                f"Would mark {len(candidates)} person(s) as bots. Re-run with --apply to write, "
                f"then run `manage.py recompute` so the metrics pick it up."
            )
            return

        with transaction.atomic():
            for person, _logins in candidates.values():
                person.is_bot = True
                person.save(update_fields=["is_bot"])

        self.stdout.write(
            f"Marked {len(candidates)} person(s) as bots. Run `manage.py recompute` to refresh the metrics."
        )
