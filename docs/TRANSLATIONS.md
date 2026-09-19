# Translations

The UI ships in English and Ukrainian with full parity (`CLAUDE.md`). `LANGUAGE_CODE = "en"`; `uk` is the only
other entry in `LANGUAGES`. A new English string gets its Ukrainian translation **in the same phase** that adds
the string — never later — because `tests/test_translations.py` fails the build otherwise.

## How language is chosen

`UserPreference.language` (set through the language switcher) wins over everything else. Browser `Accept-Language`
detection is off on purpose: `apps.accounts.middleware.UserLanguageMiddleware` copies the authenticated user's
stored preference into the `django_language` cookie before `LocaleMiddleware` runs, so the profile choice follows
the person to a new device/session rather than being overridden by that device's browser locale. An anonymous
visitor (e.g. on the login page) only gets the cookie, since there is no `UserPreference` yet.

## Add a new string

1. Mark it in the template/Python/JS source:
   - Templates: `{% translate "Text" %}` or `{% blocktranslate %}` for placeholders.
   - Python: `gettext_lazy` for anything evaluated at import/class-definition time (model verbose names, choice
     labels); `gettext`/`ngettext` inside functions.
   - JavaScript: call `gettext`/`ngettext` (and `interpolate` for named placeholders) as provided by Django's
     `/jsi18n/` catalog, loaded in `base.html` for authenticated pages — see `static/js/formatting.js`.
     `makemessages -d djangojs` extracts these calls the same way it extracts Python `gettext` calls.
   - Always a **full sentence** with named placeholders (`%(count)s`, not string concatenation), and `ngettext` for
     anything that varies with a count — see `CLAUDE.md`.
2. Regenerate and translate:
   ```bash
   make messages
   ```
   This runs `makemessages -l uk -a` for both the `django` and `djangojs` domains (ignoring `static/vendor/`), then
   `compilemessages`. New entries land in `locale/uk/LC_MESSAGES/{django,djangojs}.po` with an empty `msgstr`.
3. Fill in every empty `msgstr` in the `.po` file by hand (Ukrainian, matching the placeholders in the `msgid`
   exactly — `%(count)s` must appear in the translation too). Do not leave an entry `fuzzy`; resolve or rewrite it.
4. Run `make messages` again to compile the `.mo` files, then commit both the `.po` and the regenerated `.mo`
   files — they are shipped artifacts, same as `static/css/app.css` (see `CLAUDE.md`). Then `uv run pytest -q` —
   `tests/test_translations.py` checks that the committed `.mo` matches its `.po` source, that no `.po` entry is
   empty or fuzzy, that placeholders match between `msgid` and `msgstr`, and that a canary list of English
   nav/auth strings does not appear when the page is rendered in `uk`.

## Long Ukrainian strings must not clip

Ukrainian text commonly runs 40% or more longer than the English original for the same sentence (compound words,
longer verb forms). A layout that fits the English string exactly — a fixed-height KPI title, a table header with
`whitespace-nowrap` — clips or overflows in `uk` even though it renders fine in `en`. The rule: any element that
holds a translated string wraps rather than clips. In practice this means `break-words` and `hyphens-auto` on KPI
card titles, buttons and table headers (`apps/dashboards/templates/dashboards/partials/kpi_card.html`,
`partials/table.html`), a two-line clamp with the full string kept in a `title` attribute for a card, and no
`whitespace-nowrap` on a metric table's column headers. Preferred over overflow: `overflow-x-auto` on the
containing element is fine (a table scrolling sideways is normal), an individual cell's text silently spilling
out of its box is not.

Check any new template against this before committing: switch the language switcher to Українська and look at
the page at both a desktop and a narrow (mobile) viewport width — `document.documentElement.scrollWidth <=
document.documentElement.clientWidth + 1` (no horizontal overflow) and no individual KPI card or table header
element with `scrollWidth > clientWidth` (nothing clipped) are the two checks worth running by hand if you don't
have the e2e suite running locally.

## Canary strings

`tests/test_translations.py::CANARY_ENGLISH_STRINGS` is a short list of nav/auth/common English strings (`Log
in`, `Overview`, `Small sample`, `Sync`, …) that `test_canary_english_strings_absent_from_uk_render` asserts do
not appear when a representative page renders in `uk` — a cheap, broad smoke test that the language actually
switched, distinct from checking any one string's translation exists. Several apps keep their own smaller
`CANARY_ENGLISH_STRINGS` list scoped to their own pages for the same purpose: `apps/ai_detection/tests/test_views.py`,
`apps/catalog/tests/test_views_people.py`, `apps/connections/tests/test_views.py`,
`apps/github_sync/tests/test_views.py`. Add a new page-defining string to the nearest one of these rather than
only trusting the empty-`msgstr` gate in `make messages` to catch a leak.

## Never store rendered text

System-generated text — policy violations, GitHub-connection check results, sync errors — is stored as a code plus
parameters (e.g. `{"code": "rate_limited", "retry_after": 42}`), never as a pre-rendered English sentence. The
reader's language decides the wording at render time; storing English text would freeze it and make the Ukrainian
UI permanently half-translated for old rows. See `CLAUDE.md` and ADR entries for `policy`/`github_sync` (phases 2
and 3) for where this applies. `apps/policy/messages.py` is the worked example: `RULE_MESSAGES` maps each
`PolicyViolation.RuleCode` to a `gettext_noop`-wrapped sentence (plus a plural form and a `count_key` for the
ones that vary with a count), and `render_violation()` applies `gettext`/`ngettext` at read time — the stored
`PolicyViolation` row itself never carries English text, only `rule_code` and `details_params`. The same pattern
is what `apps/connections/check_codes.py`'s `render_message()`/`render_hint()` use for connection-check results
(see `docs/GITHUB_CONNECTIONS.md`).

## Adding a third language

1. Add the language to `LANGUAGES` in `config/settings/base.py` and create `locale/<code>/LC_MESSAGES/`.
2. `make messages` will not create the new catalogue by itself (its `-l uk` is hardcoded); run
   `uv run python manage.py makemessages -l <code> -a` and the `djangojs` equivalent once to seed the files, then
   translate them the same way as Ukrainian.
3. Add the language to the `language_switcher.html` partial's options and to any canary tests that assume exactly
   two languages.
