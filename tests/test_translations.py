from pathlib import Path

import polib
import pytest
from django.urls import reverse

BASE_DIR = Path(__file__).resolve().parent.parent
PO_FILES = [
    BASE_DIR / "locale/uk/LC_MESSAGES/django.po",
    BASE_DIR / "locale/uk/LC_MESSAGES/djangojs.po",
]

CANARY_ENGLISH_STRINGS = ["Log in", "Log out", "Overview", "Password", "Username"]


def _entry_key(entry):
    return entry.msgstr, tuple(sorted(entry.msgstr_plural.items()))


@pytest.mark.parametrize("po_path", PO_FILES, ids=[p.name for p in PO_FILES])
def test_mo_catalog_matches_committed_po_source(po_path):
    """The committed .mo is what Django actually loads at runtime (see CLAUDE.md: generated
    files under version control need a freshness test). A `.po` edited without a following
    `make messages` would otherwise ship a stale/English catalogue with no gate noticing."""
    mo_path = po_path.with_suffix(".mo")
    assert mo_path.exists(), f"{mo_path.name} is missing — run `make messages` and commit it"

    po = polib.pofile(str(po_path))
    mo = polib.mofile(str(mo_path))
    po_entries = {e.msgid: _entry_key(e) for e in po if not e.obsolete}
    mo_entries = {e.msgid: _entry_key(e) for e in mo}

    missing = sorted(set(po_entries) - set(mo_entries))
    assert not missing, f"{mo_path.name} is missing entries compiled from {po_path.name}: {missing}"

    stale = sorted(msgid for msgid, key in po_entries.items() if mo_entries.get(msgid) != key)
    assert not stale, f"{mo_path.name} is stale relative to {po_path.name} for: {stale} — run `make messages`"


@pytest.mark.parametrize("po_path", PO_FILES, ids=[p.name for p in PO_FILES])
def test_po_has_no_empty_or_fuzzy_entries(po_path):
    po = polib.pofile(str(po_path))
    empty = [e.msgid for e in po if not e.obsolete and not e.msgstr and not e.msgstr_plural]
    fuzzy = [e.msgid for e in po if e.fuzzy]
    assert not empty, f"{po_path.name} has empty msgstr for: {empty}"
    assert not fuzzy, f"{po_path.name} has fuzzy entries for: {fuzzy}"


@pytest.mark.parametrize("po_path", PO_FILES, ids=[p.name for p in PO_FILES])
def test_po_placeholders_match_between_msgid_and_msgstr(po_path):
    import re

    placeholder_re = re.compile(r"%\([a-zA-Z_]+\)[sd]|%[sd]")

    po = polib.pofile(str(po_path))
    mismatches = []
    for entry in po:
        if entry.obsolete:
            continue
        if entry.msgid_plural:
            id_placeholders = set(placeholder_re.findall(entry.msgid)) | set(
                placeholder_re.findall(entry.msgid_plural)
            )
            for plural_form, text in entry.msgstr_plural.items():
                if set(placeholder_re.findall(text)) != id_placeholders:
                    mismatches.append((entry.msgid, plural_form))
        else:
            id_placeholders = set(placeholder_re.findall(entry.msgid))
            str_placeholders = set(placeholder_re.findall(entry.msgstr))
            if id_placeholders != str_placeholders:
                mismatches.append((entry.msgid, entry.msgstr))
    assert not mismatches, f"{po_path.name} placeholder mismatches: {mismatches}"


@pytest.mark.django_db
def test_language_switch_renders_uk(client, lead_user):
    client.force_login(lead_user)
    client.post(reverse("accounts:set_language"), {"language": "uk", "next": "/"})
    response = client.get(reverse("dashboards:overview"))
    content = response.content.decode()
    assert "Огляд" in content


@pytest.mark.django_db
def test_invalid_preference_value_error_is_translated_to_uk(client, lead_user):
    client.force_login(lead_user)
    client.post(reverse("accounts:set_language"), {"language": "uk", "next": "/"})

    response = client.post(reverse("accounts:set_theme"), {"theme": "purple", "next": "/"})
    assert response.status_code == 400
    assert "Некоректне значення теми." in response.content.decode()

    response = client.post(reverse("accounts:set_language"), {"language": "xx", "next": "/"})
    assert response.status_code == 400
    assert "Некоректне значення мови." in response.content.decode()


@pytest.mark.django_db
def test_language_choice_survives_reload_and_new_session(client, django_user_model):
    user = django_user_model.objects.create_user(username="bob", password="bob-pass")
    client.force_login(user)
    client.post(reverse("accounts:set_language"), {"language": "uk", "next": "/"})

    from django.test import Client

    fresh_client = Client()
    fresh_client.force_login(user)
    response = fresh_client.get(reverse("dashboards:overview"))
    content = response.content.decode()
    assert "Огляд" in content


@pytest.mark.django_db
def test_canary_english_strings_absent_from_uk_render(client, lead_user):
    client.force_login(lead_user)
    client.post(reverse("accounts:set_language"), {"language": "uk", "next": "/"})
    response = client.get(reverse("accounts:login"))
    content = response.content.decode()
    for canary in CANARY_ENGLISH_STRINGS:
        assert canary not in content, f"untranslated English string leaked into uk render: {canary}"
