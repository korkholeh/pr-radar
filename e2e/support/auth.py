"""Shared login helper. Personas are real, shared database rows reused across specs, so every
login resets language to English through the product's own switcher — never behind the app's back —
keeping specs that don't touch language independent of the ones that do. Theme is left alone: the
theme spec relies on the persona's theme surviving a fresh login, and it is the only spec that
touches theme, so no reset is needed there."""

from playwright.sync_api import Page, expect

from e2e.support.personas import Persona


def log_in(page: Page, persona: Persona) -> None:
    page.goto("/accounts/login/")
    page.get_by_label("Username").fill(persona.username)
    page.get_by_label("Password").fill(persona.password)
    page.get_by_role("button", name="Log in").click()
    page.wait_for_url(lambda url: "/accounts/login/" not in url)
    page.locator("#language-select").select_option("en")
    expect(page.locator("#language-select")).to_have_value("en")
