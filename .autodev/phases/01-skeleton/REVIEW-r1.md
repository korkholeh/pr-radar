# Review — phase 1 round 1

**Verdict:** changes_requested

Phase 1 delivers a genuinely runnable, logged-in, bilingual, themed skeleton: I ran every gate myself and `uv run pytest -q` (95 tests), the full lint chain, a fresh `make css` (committed app.css is identical to a fresh build) and `make e2e-up && uv run pytest e2e -q; make e2e-down` (4 specs, exit 0, no leaked processes) are all green on this working tree, and the code structure follows CLAUDE.md closely (services/selectors split, ScopeFilter seam, mask_secrets before any token exists, tokens.css as the only colour file). But the acceptance criteria do not hold outside this developer's machine or outside these exact files: `*.mo` is gitignored and nothing in the install/test path compiles it, so on a clean checkout three translation tests fail — criterion 1 is not met. Four more gates are illusory rather than wrong-looking: the huey-immediate test asserts the fixture it just set while the real huey instance stays non-immediate, the SQLite-pragma test executes the pragma constants against a raw sqlite3 connection instead of the product's receiver (risk #14 unmitigated), `test_css_tokens.py` cannot fail because tokens.css is @imported wholesale into app.css (risk #15 unmitigated), and the JS side of i18n bypasses gettext entirely so djangojs.po is empty and the polib gate covers nothing for JS (risk #8 half-mitigated). Finally `STATICFILES_STORAGE` is a no-op setting on Django 5.2, so the whitenoise manifest/compressed storage prod thinks it has is not in effect — and a DECISIONS entry is justified on that false premise.

## [BLOCKER] Clean checkout fails the test gate: .mo files are gitignored and nothing compiles them
`.gitignore`

`.gitignore:5` ignores `*.mo` and only the `.po` files are tracked (`git ls-files locale` → django.po, djangojs.po). Neither `uv sync` nor `conftest.py` nor pytest compiles the catalogues; only `make messages` and `make e2e-up` do. I copied the tree to /tmp excluding `*.mo` and ran the suite with the project venv: `tests/test_translations.py::test_language_switch_renders_uk`, `::test_language_choice_survives_reload_and_new_session` and `::test_canary_english_strings_absent_from_uk_render` all FAIL (`AssertionError: untranslated English string leaked into uk render: Username`). Acceptance criterion 1 ("uv sync, uv run pytest -q ... succeed from the repository root on a clean checkout") is therefore not met, and docs/PROGRESS.md's "Everything below works from a clean checkout" is inaccurate.

**Fix:** Add a session-scoped autouse fixture in the root `conftest.py` that compiles `locale/uk/LC_MESSAGES/*.po` when the matching `.mo` is missing or older than the `.po` (call `django.core.management.call_command("compilemessages", "-l", "uk", "--ignore", ".venv")`, or `msgfmt` directly). That also closes the stale-.mo hole where an edited .po is never recompiled. Alternatively commit the `.mo` files and add a freshness test, and mention the compile step in docs/SETUP.md's Install section either way.

## [MAJOR] Huey-immediate guarantee is false and its test cannot fail
`tests/test_huey_config.py`

`conftest.py:14-16` mutates `settings.HUEY` after Django has already started, but `huey.contrib.djhuey` builds its HUEY instance at app load from the setting. Verified: `from huey.contrib.djhuey import HUEY; HUEY.immediate` → `False` under `config.settings.local`. So the architecture decision "Tests run with HUEY = {"immediate": True}" is not implemented — from phase 3 onward, a task enqueued inside a test will be written to `DATA_DIR/huey.sqlite3` and never executed, silently. `tests/test_huey_config.py:11-12` asserts `settings.HUEY["immediate"] is True`, i.e. it asserts the value the autouse fixture set one line earlier: it cannot fail and proves nothing about the queue.

**Fix:** Set immediacy where it takes effect — either a `config/settings/test.py` (used by `DJANGO_SETTINGS_MODULE` in `[tool.pytest.ini_options]`) with `HUEY["immediate"] = True`, or in `conftest.py` do `from huey.contrib.djhuey import HUEY; HUEY.immediate = True` in a session fixture. Then rewrite the test to assert on the instance (`HUEY.immediate is True`) plus a task that actually runs inline.

## [MAJOR] SQLite pragma test exercises sqlite3, not config/db.py — risk #14 is unmitigated
`apps/accounts/tests/test_settings.py`

`test_sqlite_pragmas_applied_on_connection` (lines 11-24) opens its own `sqlite3.connect()`, executes the `SQLITE_PRAGMAS` strings itself and then asserts sqlite honoured them. It never touches `config.db.apply_sqlite_pragmas`, the `connection_created` receiver, or Django's connection. Unregister the receiver, delete the `@receiver` decorator, or break the vendor guard and the test still passes — so PLAN.md's risk table claim ("WAL, busy_timeout=5000, foreign_keys=ON ... asserted by a test") is not backed. `OPTIONS["transaction_mode"] = "IMMEDIATE"` has no test at all.

**Fix:** Assert against the product: `from django.db import connection; connection.ensure_connection(); cursor.execute("PRAGMA busy_timeout")` → 5000 and `PRAGMA foreign_keys` → 1 (both hold for an in-memory test DB; use a file-based test DB or a fresh `sqlite3` connection routed through `connection_created.send()` if you also want `journal_mode=wal`). Add a test that `configure_sqlite_transaction_mode({"ENGINE": "django.db.backends.postgresql"})` leaves the dict untouched.

## [MAJOR] STATICFILES_STORAGE is a dead setting on Django 5.2 — whitenoise manifest/compression never applies
`config/settings/base.py`

`base.py:110` sets `STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"`. Django removed that transitional setting in 5.1 and this project pins 5.2.17; I verified at runtime: `settings.STORAGES` → `{'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}` and `staticfiles_storage.__class__` is `StaticFilesStorage`. Django issues no warning, so prod silently serves unhashed, uncompressed assets with no far-future caching, and `docs/CONFIGURATION.md`'s "prod.py — base + DEBUG=False, whitenoise" overstates what is configured. It also invalidates the recorded premise of a decision: the `p01-implement` DECISIONS entry justifies `page.add_script_tag(content=...)` in `e2e/web/test_duration_format.py` with "STATICFILES_STORAGE is ManifestStaticFilesStorage, so an unhashed /static/js/formatting.js URL 404s" — that storage is not in effect, so whatever caused the 404 was something else.

**Fix:** Replace the setting with `STORAGES = {"default": {...FileSystemStorage}, "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}}` (keep plain `StaticFilesStorage` in local/test so `runserver` needs no collectstatic). Once manifest storage is real, fix `templates/500.html:6`, which hardcodes `/static/css/app.css` instead of `{% static %}` and would then 404. Add a test asserting `settings.STORAGES["staticfiles"]["BACKEND"]` in prod, and correct the DECISIONS entry.

## [MAJOR] The app.css freshness test cannot fail — risk #15 is unmitigated
`tests/test_css_tokens.py`

`static/css/src/input.css:2` does `@import "../tokens.css"`, so Tailwind copies every token declaration verbatim into `app.css` (verified: `grep -- '--series-7' static/css/app.css` → `--series-7:#1b7c83` and the dark value, although no template uses a series token). The test's `missing = [name for name in token_names if f"--{name}" not in app_css]` is therefore empty by construction, whatever the state of the build. Concretely: add `bg-surface-2` to a template and never run `make css` — the test still passes and the page renders unstyled. The acceptance criterion is met literally, but the staleness guard PLAN.md and DECISIONS credit for risk #15 does not exist. (The committed app.css is in fact fresh right now: a fresh `.tools/tailwindcss` build is byte-identical.)

**Fix:** Keep this test, and add a real guard: write an inputs checksum (tokens.css + src/input.css + every scanned template/py/js) into a committed sidecar file during `make css` and assert it matches in a test; or extract the utility class names used in templates and assert each resolves to a rule in app.css.

## [MAJOR] JS i18n bypasses gettext: djangojs.po is empty, /jsi18n/ is unused, and fragments are concatenated
`static/js/formatting.js`

PLAN.md's design states both duration formatters are "driven by ngettext", but `formatting.js` ships its own `STRINGS` table with hardcoded en+uk literals (lines 4-21, 34, 43) and a hand-rolled `ukPluralForm`. Consequences: `locale/uk/LC_MESSAGES/djangojs.po` contains only a header — zero entries — so `tests/test_translations.py::test_po_has_no_empty_or_fuzzy_entries` and the placeholder test are vacuous for the JS domain; `JavaScriptCatalog` at `/jsi18n/` is referenced by no template (grep finds no use), so the whole i18n plumbing for JS is dead; and `days + dUnit + " " + hours + hUnit` (lines 68-70) concatenates translated fragments, which CLAUDE.md forbids explicitly. Risk #8 ("Ukrainian lags") is therefore unguarded on the JS side: a new JS string needs no .po change and no test notices a missing translation. The deviation is described in docs/TRANSLATIONS.md but has no DECISIONS.md entry and contradicts PLAN.md.

**Fix:** Use the catalog: load `{% url 'javascript-catalog' %}` in base.html and call `gettext`/`ngettext` in formatting.js with full-sentence msgids matching the Python side (`"%(days)dd %(hours)dh"`), so `makemessages -d djangojs` extracts them and the polib gate applies. Also delete the unused `second_one`/`second_other`/`minute_one`/`minute_other` keys, which are dead today.

## [MINOR] Python/JS duration parity breaks on half-minute values; the shared case table has no such case
`apps/dashboards/formatting.py`

Python `round(seconds / 60)` (line 21) uses banker's rounding; JS `Math.round` (formatting.js:59) rounds half up. Verified: `round(150/60)` → 2 and `round(270/60)` → 4, while JS gives 3 and 5 — so 150 s renders "2 minutes" in a table and "3 minutes" in a chart tooltip. `tests/fixtures/duration_cases.json` contains no x.5 case, so the parity suite that exists precisely to catch this cannot see it. Minor related edge: 3599 s renders "60 minutes" rather than rolling over to "1h 0m".

**Fix:** Add `{"seconds": 150, ...}` and `{"seconds": 3599, ...}` to duration_cases.json, then align the implementations (e.g. `minutes = int((seconds + 30) // 60)` in Python) and decide the 60-minute rollover explicitly.

## [MINOR] User-visible strings not marked for translation (400 fragment, whole 500 page)
`apps/accounts/views.py`

`views.py:38` and `:57` pass literal English `"Invalid theme value."` / `"Invalid language value."` into `partials/preference_error.html`, and `templates/500.html:5,11` hardcodes "Server error — PR Radar" and "Something went wrong.". None appear in django.po (checked the msgid list), so the Ukrainian UI is not at parity and no gate catches it — the canary test only inspects the login page.

**Fix:** Wrap the two view messages in `gettext` (or move the wording into the template with `{% translate %}`), use `{% translate %}` in 500.html — it renders fine without context processors — and run `make messages` plus fill the uk msgstrs.

## [MINOR] The urlpatterns walk is not recursive and skips unnamed patterns and the admin namespace
`tests/test_urls_login.py`

PLAN.md T7 promises a "recursive walk" over urlpatterns; `_named_urls()` (lines 22-34) iterates one level of `resolver.namespace_dict` plus top-level `URLPattern`s only, keeps only patterns that have a `name`, and line 49 drops everything under `admin:`. It covers all of phase 1's URLs, but a nested `include()` or an unnamed route added in a later phase silently escapes the deny-by-default assertion — which is the one check that keeps criterion 3 true as the URL surface grows. Nothing asserts the admin is closed to anonymous users either.

**Fix:** Walk `URLResolver.url_patterns` recursively, accumulating prefixes so unnamed patterns are reachable too, and assert an anonymous GET of `/admin/` redirects to the admin login. Keep `ALLOWED_ANONYMOUS_NAMES` as the documented allowlist (and drop the now-unreachable `admin:login` entry and the duplicate `javascript-catalog` append on line 51).

## [MINOR] `make messages` recompiles Django's bundled catalogues and writes .mo into .venv
`Makefile`

`compilemessages` (Makefile:41) walks every locale directory on the path, so a run emits ~1200 lines of "File .../site-packages/django/contrib/... is already compiled and up to date" and will write `.mo` files inside `.venv` when they are missing (I saw this in the `make e2e-up` output). It makes the target's real output unreadable and couples the build to the venv being writable.

**Fix:** Scope it: `uv run python manage.py compilemessages -l uk --ignore=.venv` (same for the `e2e-up` recipe).

## [MINOR] prod silently accepts the insecure default SECRET_KEY
`config/settings/prod.py`

`base.py:14` defaults `SECRET_KEY` to `insecure-dev-key-change-me` and `prod.py` does not override or require it, so a deployment with an unset `SECRET_KEY` starts with a published signing key and no error (the lint gate runs `manage.py check`, not `check --deploy`). docs/CONFIGURATION.md documents the default rather than refusing it.

**Fix:** In prod.py: `SECRET_KEY = env.str("SECRET_KEY")` so a missing value raises `ImproperlyConfigured` at import, and add a test asserting the prod module refuses the base default.

## [NIT] The author's spec and an ADR were cosmetically reformatted with no decision logged
`docs/SPEC.md`

The diff reformats the `MetricDef(...)` code block in docs/SPEC.md (lines 285-296) and in docs/dev/adr/0007. The changes are purely whitespace — no meaning changed — but docs/SPEC.md is the author's source of truth for this run and ADRs are prior decisions; editing either belongs in DECISIONS.md, and nothing in the phase needed it.

**Fix:** Revert both hunks, or log the reformat as a decision bullet if it was deliberate.

## [NIT] Small dead/duplicated code
`tests/test_translations.py`

`test_language_switch_renders_uk:57` asserts `"Огляд" in content or "Огляд" in content` (the same condition twice); `config/security.py:5` defines `_TOKEN_PREFIXES`, which nothing reads (the regex duplicates the list); `templates/base.html:15` renders the default title as "PR Radar — PR Radar".

**Fix:** Drop the duplicated disjunct, build the regex from `_TOKEN_PREFIXES` (or delete the constant), and make the title block fall back to a bare "PR Radar".
