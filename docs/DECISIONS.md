# Decisions

Notable choices made while building the app, for the next person who wonders "why is it done this way?". Larger,
structural decisions get a full ADR under `docs/dev/adr/`; this file covers narrower, phase-level calls. The
complete, terse decision log the autonomous build kept as it went is `.autodev/DECISIONS.md` — this page is the
curated, human-facing subset.

## Phase 1

**`apps/dashboards` exists from phase 1**, with a single placeholder Overview page at `/`. `base.html`, the
navigation, the post-login redirect target and the e2e login spec all need one real page to point at; creating it
anywhere else would just mean moving it once phase 8 (Dashboards, charts, themes) arrives. The navigation renders
only the items whose pages already exist — a full spec-§10.1 nav with dead links would 404 instead of redirecting
an anonymous visitor to login, and would mislead an operator into clicking something that isn't built yet.

**Language persistence ignores the browser.** `Accept-Language` detection is deliberately off:
`apps.accounts.middleware.UserLanguageMiddleware` copies `UserPreference.language` into the cookie
`LocaleMiddleware` reads, so a lead's chosen language follows them to a new device rather than being silently
overridden by that device's browser locale. The middleware writes to `request.COOKIES` rather than a session key,
because this Django version (5.2.17) no longer has session-based i18n storage — `LocaleMiddleware` resolves
language from the cookie, `Accept-Language`, and `LANGUAGE_CODE` only.

**Colour literals live in exactly one file.** `static/css/tokens.css` defines every colour for both themes; a test
greps the rest of the codebase (templates, Python, `static/js/`, and CSS other than `app.css`/`vendor/`) for
`#hex`/`rgb(`/`hsl(` and fails the build on a hit. Dark mode is bound to `[data-theme="dark"]` written by the
server, not to `prefers-color-scheme`, so the three-state preference (system/light/dark) can be represented
without JavaScript owning the source of truth.

**Duration formatting has two implementations, proven equal by a shared fixture** rather than one implementation
called from both runtimes: `apps/dashboards/formatting.py::format_duration` (Python, `ngettext`-driven) and
`static/js/formatting.js::window.prRadar.formatDuration` (hand-written pluralisation, since there is no Node
build in this project). `tests/fixtures/duration_cases.json` is asserted against both — a pytest test for Python,
a Playwright spec for JS — because the project has no other JS test runtime, and the e2e browser already is one.

**No live GitHub call is possible, enforced, not just intended.** The root `conftest.py` wraps every unit/
integration test in `respx.mock(assert_all_mocked=True)`, so an unmocked outbound HTTP request raises immediately
instead of silently reaching the network. This exists before `github_sync` (phase 3) writes a single line of
client code, because the guarantee needs to hold from the first test onward, not be retrofitted.

**e2e personas are shared, persistent database rows**, not recreated per test (`manage.py seed_e2e` is
idempotent). Specs that mutate a persona's state (language, theme) restore it — through the product's own
switcher, never a direct database write — so the suite stays order-independent across repeated
`make e2e-up && pytest e2e -q; make e2e-down` cycles.
