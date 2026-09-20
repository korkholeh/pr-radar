"""`manage.py reclassify_bots` — re-applying the bot settings to people who already exist.

The command's whole reason to exist is that `Person.is_bot` is decided once, at creation. These
cases pin the two properties that make it safe to run on a live database: it never writes without
`--apply`, and it never turns a bot back into a person.
"""

import pytest
from django.core.management import call_command

from apps.catalog.factories import IdentityFactory, PersonFactory
from apps.catalog.models import Identity
from apps.catalog.services import set_setting

pytestmark = pytest.mark.django_db


def _person_with_login(login: str, *, is_bot: bool = False):
    person = PersonFactory(display_name=login, is_bot=is_bot)
    IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value=login, person=person)
    return person


def test_default_bot_logins_cover_the_ai_reviewer_and_agent_accounts():
    """The three logins this command was written for carry no `[bot]` suffix, so only the exact
    list can catch them."""
    from apps.catalog.services import get_list

    logins = {value.casefold() for value in get_list("BOT_LOGINS")}
    assert {"copilot-pull-request-reviewer", "charliecreates", "charliehelps"} <= logins


def test_dry_run_reports_but_writes_nothing(capsys):
    person = _person_with_login("copilot-pull-request-reviewer")

    call_command("reclassify_bots")

    person.refresh_from_db()
    assert person.is_bot is False
    assert "copilot-pull-request-reviewer" in capsys.readouterr().out


def test_apply_marks_the_person_as_a_bot():
    person = _person_with_login("charliecreates")

    call_command("reclassify_bots", "--apply")

    person.refresh_from_db()
    assert person.is_bot is True


def test_a_person_who_is_not_a_bot_is_left_alone():
    person = _person_with_login("olehkorkh-planeks")

    call_command("reclassify_bots", "--apply")

    person.refresh_from_db()
    assert person.is_bot is False


def test_an_existing_bot_is_never_unmarked():
    """A lead's manual "mark bot" must survive a settings change that no longer matches."""
    person = _person_with_login("some-internal-agent", is_bot=True)
    set_setting("BOT_LOGINS", ["dependabot"])

    call_command("reclassify_bots", "--apply")

    person.refresh_from_db()
    assert person.is_bot is True


def test_one_person_with_two_matching_logins_is_reported_once(capsys):
    person = _person_with_login("charliecreates")
    IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="charliehelps", person=person)

    call_command("reclassify_bots")

    out = capsys.readouterr().out
    assert out.count(f"id={person.pk}") == 1
    assert "charliecreates" in out
    assert "charliehelps" in out


def test_a_clean_database_says_so(capsys):
    _person_with_login("olehkorkh-planeks")

    call_command("reclassify_bots", "--apply")

    assert "No person needs reclassifying." in capsys.readouterr().out
