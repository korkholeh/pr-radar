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

## Never store rendered text

System-generated text — policy violations, GitHub-connection check results, sync errors — is stored as a code plus
parameters (e.g. `{"code": "rate_limited", "retry_after": 42}`), never as a pre-rendered English sentence. The
reader's language decides the wording at render time; storing English text would freeze it and make the Ukrainian
UI permanently half-translated for old rows. See `CLAUDE.md` and ADR entries for `policy`/`github_sync` (phases 2
and 3) for where this applies.

## Adding a third language

1. Add the language to `LANGUAGES` in `config/settings/base.py` and create `locale/<code>/LC_MESSAGES/`.
2. `make messages` will not create the new catalogue by itself (its `-l uk` is hardcoded); run
   `uv run python manage.py makemessages -l <code> -a` and the `djangojs` equivalent once to seed the files, then
   translate them the same way as Ukrainian.
3. Add the language to the `language_switcher.html` partial's options and to any canary tests that assume exactly
   two languages.
