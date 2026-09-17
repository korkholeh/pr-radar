# Phase 1 — Skeleton, auth, i18n and design tokens

Goal: a runnable, logged-in, bilingual, themed empty application where every gate command works from a clean
checkout.

Sources: `docs/SPEC.md` §2, §3, §10.1, §10.5, §10.7, §11, §12, §13, §14 phase 0, §15 ·
`.autodev/ARCHITECTURE.md` (Components, Cross-cutting, Delivery) · `.autodev/PROFILE.md` · `CLAUDE.md`.

## Context

### What exists

The repository contains documentation only: `CLAUDE.md`, `docs/SPEC.md`, `docs/dev/adr/0001..0008`, and the
`.autodev/` run state (`ARCHITECTURE.md`, `RISKS.md`, `ROADMAP.md`, `DECISIONS.md`, `PROGRESS.md`, `guides/`).
There is no `pyproject.toml`, no `manage.py`, no `apps/`, no `config/`, no `static/`, no `locale/`, no `Makefile`,
no test suite. `uv run pytest -q` therefore cannot run yet — this phase is what makes it run.

Verified on this machine (commands and output recorded in `.autodev/DECISIONS.md`): `uv 0.11.21`,
`msgfmt`/`xgettext`/`msgmerge` present under `/opt/homebrew/bin`, Playwright chromium already cached,
`tailwindcss v4.3.3` macOS-arm64 release asset reachable (HTTP 200), `htmx` latest 2.0.10.

### What this phase changes

It creates the whole runnable shell: uv project, four settings modules, logging with token masking, huey on its
own SQLite file, the `accounts` app (preferences, audit, groups, the first `scope_for_user`), auth pages behind
`LoginRequiredMiddleware`, a single placeholder Overview page in `dashboards`, the design-token CSS and the
committed Tailwind build, `base.html` with navigation, theme and language switchers, the i18n plumbing plus the
Ukrainian catalogue, the Makefile, the pytest/ruff/mypy gates, the e2e harness, and the first operator docs.

### Key files created

```
manage.py  pyproject.toml  uv.lock  .python-version  .env.example  .gitignore  Makefile  README.md  conftest.py
config/            settings/{base,local,prod,e2e}.py  urls.py  huey.py  db.py  logging_filters.py  security.py
                   asgi.py  wsgi.py
apps/accounts/     models.py selectors.py services.py views.py urls.py forms.py admin.py apps.py
                   migrations/0001_initial.py 0002_groups.py
                   management/commands/seed_e2e.py  templates/registration/*  tests/*
apps/dashboards/   views.py urls.py apps.py formatting.py templatetags/formatting.py
                   templates/dashboards/overview.html  tests/*
templates/         base.html  partials/{nav,theme_switcher,language_switcher}.html  403.html 404.html 500.html
static/css/        tokens.css  src/input.css  app.css (generated, committed)
static/js/         theme.js  formatting.js
static/vendor/     htmx.min.js (vendored, committed)
locale/uk/LC_MESSAGES/  django.po  djangojs.po
tests/             test_no_hardcoded_colors.py test_translations.py test_css_tokens.py test_urls_login.py
                   fixtures/duration_cases.json
e2e/               conftest.py  surfaces.toml  support/personas.py  plans/auth.plan.yaml  web/*.py  README.md
docs/              SETUP.md CONFIGURATION.md TRANSLATIONS.md PROGRESS.md DECISIONS.md
```

## Design

### Settings and storage

`config/settings/base.py` reads `django-environ` from `.env`: `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`,
`DATABASE_URL`, `DATA_DIR`, `REPORT_TIMEZONE`, `FIELD_ENCRYPTION_KEYS`, `STORE_RAW_PAYLOADS`. `DATA_DIR`
defaults to `BASE_DIR/data` and is created at import if missing; it holds `db.sqlite3`, `huey.sqlite3`, `logs/`,
`cache/`, `exports/`, `repos/`, `staticfiles/`. `TIME_ZONE = "UTC"`, `USE_TZ = True`; `REPORT_TIMEZONE` is a
setting only — the day-boundary helper that consumes it belongs to phase 7.

`local.py` = base + `DEBUG=True` + console logging. `prod.py` = base + `DEBUG=False`, whitenoise, HSTS,
`SECURE_SSL_REDIRECT`, secure/httponly cookies, `X_FRAME_OPTIONS=DENY`, `SECURE_CONTENT_TYPE_NOSNIFF`,
`SECURE_REFERRER_POLICY="same-origin"`. `e2e.py` = prod minus TLS (plain http on 127.0.0.1): `DEBUG=False`,
`ALLOWED_HOSTS=["127.0.0.1","localhost"]`, `SECURE_SSL_REDIRECT=False`, cookie `Secure` off,
`DATA_DIR=BASE_DIR/.e2e/data`, so a real 500 is a real error page and the e2e run never touches the developer's
database.

SQLite is configured in `config/db.py`: a `connection_created` receiver issues `PRAGMA journal_mode=WAL`,
`synchronous=NORMAL`, `foreign_keys=ON`, `busy_timeout=5000` one statement at a time (sqlite3 rejects multi-
statement `init_command`), guarded on `connection.vendor == "sqlite"`. `OPTIONS["transaction_mode"] = "IMMEDIATE"`
(Django 5.1+) is merged only when the parsed `DATABASE_URL` is SQLite, so pointing `DATABASE_URL` at PostgreSQL
later changes nothing else. No SQLite-specific SQL anywhere (ADR 0001).

### Logging and secret masking

`config/security.py` owns one function, `mask_secrets(text: str) -> str`, matching the GitHub token shapes
(`ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`, `github_pat_`) and replacing everything but the last four characters.
`config/logging_filters.py::SecretMaskingFilter` applies it to `record.msg`, every element of `record.args` and
`record.exc_text`, and is installed on the **root** logger in `base.py` (console + rotating file at
`DATA_DIR/logs/pr-radar.log`). Phase 3 reuses `mask_secrets` for `SyncRun.error_log`; the seam exists from day one
so no later phase has to remember it (risk #2).

### accounts

```python
class UserPreference(models.Model):          # OneToOne(User), created on demand
    theme:    "system" | "light" | "dark"    # default "system" (spec §15)
    language: "en" | "uk"                    # default "en"

class AuditEntry(models.Model):              # English, codes + JSON, never translated
    actor  = FK(User, null=True, on_delete=SET_NULL)
    created_at, action (code), object_type, object_id, changes = JSONField()  # {"before": …, "after": …}
```

`apps/accounts/selectors.py`:

```python
@dataclass(frozen=True)
class ScopeFilter:
    unrestricted: bool = True
    project_ids: frozenset[int] | None = None

def scope_for_user(user) -> ScopeFilter:     # phase 1: always unrestricted
```

The dataclass is the contract phase 8 narrows; it ships now so every selector written in phases 2–7 already takes
one (ADR 0002, risk #3). `UserProjectAccess` is **not** created here — it needs `catalog.Project`, which lands in
phase 2.

`apps/accounts/services.py`: `record_audit(actor, action, obj, before, after)`, `set_theme(user, value)` and
`set_language(user, value)` (validate against the choices, upsert `UserPreference`). These two files also give
`mypy`'s `files` globs something to match from the first phase.

A data migration creates the `admin` and `lead` groups, empty. `catalog.manage_settings` does not exist until
phase 2, so phase 2 attaches permissions to the existing groups instead of creating them.

### Auth, URL surface and the deny-by-default check

`django.contrib.auth.middleware.LoginRequiredMiddleware` is installed. URLs in phase 1:

| URL | View | Anonymous |
|---|---|---|
| `/` | `dashboards.views.overview` (placeholder page) | redirect to login |
| `/accounts/login/`, `/logout/`, `/password_reset/…` (4 views) | `django.contrib.auth.views` | allowed |
| `/preferences/language/` (POST) | `accounts.views.set_language` | allowed (switcher on the login page) |
| `/preferences/theme/` (POST) | `accounts.views.set_theme` | allowed (switcher on the login page) |
| `/jsi18n/` | `JavaScriptCatalog` | redirect to login |
| `/admin/…` | Django admin | admin's own login |

The two preference views are `@login_not_required` + `@require_POST` + CSRF-protected; they persist to
`UserPreference` when the user is authenticated and always write the cookie, then redirect to a `next` validated
with `url_has_allowed_host_and_scheme`. An invalid value returns 400 **with a rendered message**, never an empty
body. Whether Django already exempts the contrib auth views is verified by the parametrized test, not assumed; if
it does not, the project wraps them with `login_not_required`.

### Theme (spec §10.5, ADR 0008)

Resolution order: `UserPreference.theme` → `pr_radar_theme` cookie → `"system"`. A context processor exposes
`theme_preference` (`system|light|dark`) and `theme_resolved` (`light|dark`, with `system` resolving to `light`
server-side). `base.html` renders `<html data-theme="{{ theme_resolved }}" data-theme-preference="{{ theme_preference }}">`
and, **as the first child of `<head>`, before the stylesheet link**, a ~10-line inline script that overwrites
`data-theme` from `matchMedia('(prefers-color-scheme: dark)')` when the preference is `system`. Because the
attribute is set before the CSS is parsed, there is no flash. `static/js/theme.js` (deferred) wires the switcher,
POSTs the choice, keeps the `matchMedia` listener alive for `system`, and dispatches the `themechange` event that
`charts.js` will listen to in phase 8.

### Tokens and the Tailwind build

`static/css/tokens.css` is the only file allowed a colour literal: `:root, [data-theme="light"]` and
`[data-theme="dark"]` define `--bg --surface --surface-2 --border --text --text-muted --accent --good --bad
--neutral --warning --series-ai --series-non-ai --series-1..8 --grid --tooltip-bg` (dark background in the
`#0f1115`–`#161a20` band per spec §10.5, AA contrast in both themes). `static/css/src/input.css` does
`@import "tailwindcss"; @import "../tokens.css";`, declares
`@custom-variant dark (&:where([data-theme="dark"], [data-theme="dark"] *));`, maps the tokens into Tailwind with
`@theme inline { --color-bg: var(--bg); … }`, and pins scanning with explicit `@source "../../../templates";`
`@source "../../../apps";` `@source "../../../static/js";`. Tailwind **v4.3.3** standalone is downloaded on
demand by `make css` into a git-ignored `.tools/`; `static/css/app.css` is committed.

The colour-literal grep test scans templates, Python, `static/js/`, and CSS **except** `static/css/tokens.css`
(the token file) and `static/css/app.css` + `static/vendor/` (generated/vendored). The staleness guard for the
generated file is the separate test that every `--token` name in `tokens.css` appears in `app.css` (risk #15).

### i18n (spec §10.7)

`LANGUAGE_CODE="en"`, `LANGUAGES=[("en","English"),("uk","Українська")]`, `USE_I18N=True`, `LocaleMiddleware`
after `SessionMiddleware`, `LOCALE_PATHS=[BASE_DIR/"locale"]`, no `i18n_patterns`. Browser detection is off: a
custom middleware step (`accounts.middleware.UserLanguageMiddleware`, placed before `LocaleMiddleware`) copies
`UserPreference.language` into the `django_language` cookie/session on an authenticated request, so the profile
wins over `Accept-Language` and the choice survives a new session on another device. `JavaScriptCatalog` at
`/jsi18n/`, packages `apps.accounts`, `apps.dashboards`.

Duration formatting has one implementation per runtime: `apps/dashboards/formatting.py::format_duration(seconds,
*, locale)` (plus a `humanize_duration` template filter) and `static/js/formatting.js` exposing
`window.prRadar.formatDuration`, both driven by `ngettext`. Their parity is proved by a shared case table,
`tests/fixtures/duration_cases.json`: a pytest test asserts the Python side against it, and a Playwright spec
evaluates the JS side against the same file in the browser. Using the browser as the JS runtime keeps the promise
that nothing in the test path needs Node.

### Error handling

`403.html`, `404.html`, `500.html` extend `base.html` (500 without context processors, so it renders with the
cookie theme only). `DEBUG=False` in prod and e2e means a real 500 is visible. Every data-changing endpoint is
POST; CSRF for htmx comes from `hx-headers='{"X-CSRFToken": "…"}'` on `<body>` in `base.html`.

### Tests and the respx guard

`conftest.py` at the repository root installs an autouse fixture starting `respx.mock(assert_all_mocked=True,
assert_all_called=False)` for every test, so any unmocked outbound HTTP request raises immediately — the
guarantee, not the habit, that no live GitHub call happens (intake, risk #9). It also sets
`HUEY = {"immediate": True}` through `settings` and provides `lead_user` / `admin_user` fixtures.

### Deviations from `.autodev/ARCHITECTURE.md` / `PROFILE.md`

1. **`mypy.files` is `["apps/*/services.py", "apps/*/selectors.py"]` in this phase**, not PROFILE's list that also
   names `apps/metrics` and `apps/*/services` — those paths do not exist yet and mypy errors on a glob that
   matches nothing. Phase 7 adds `apps/metrics` when it creates it. Same intent, same command (`uv run mypy`).
2. **`apps/dashboards` is created in phase 1** with a single placeholder Overview view. `base.html`, the nav, the
   login redirect target and the e2e login spec all need one real page; putting it anywhere else would move it
   again in phase 8.
3. **The navigation renders only the items whose pages exist.** Spec §10.1's full item list arrives as the phases
   that own those pages land; a nav full of dead links would fail the anonymous-redirect test in the noisiest
   possible way (404 vs redirect) and mislead the operator.
4. **`make vendor` vendors htmx 2.0.10 only.** `static/vendor/chart.umd.js` is added by phase 8, which is the
   first phase that draws a chart; committing an unused 200 KB artefact now would add a file no test can keep
   honest.
5. **Duration parity is proved in the browser**, not by a Node-based unit test, for the reason given above.

All five are appended to `.autodev/DECISIONS.md`.

## Tasks

- [x] T1: uv project bootstrap — `pyproject.toml` (deps: django 5.2.x, django-environ, httpx, huey, whitenoise,
      django-tables2, django-filter, XlsxWriter, cryptography, PyYAML; dev: pytest, pytest-django, factory_boy,
      freezegun, respx, polib, openpyxl, ruff, mypy, django-stubs, pytest-playwright), `.python-version` (3.12), `manage.py` defaulting to `config.settings.local`, `.gitignore`
      (`.env`, `data/`, `.e2e/`, `.tools/`, `__pycache__`, `e2e/artifacts/`, `e2e/RESULTS.md`),
      committed `uv.lock` via `uv sync`. Test: none yet (T14 adds the gate config).
- [x] T2: `config/settings/{base,local,prod,e2e}.py`, `config/urls.py`, `wsgi.py`, `asgi.py`, `config/db.py`
      (pragma receiver + `transaction_mode`), `.env.example` complete. Tests `apps/…/tests/test_settings.py`:
      pragmas applied on a temp-file SQLite connection (`journal_mode=wal`, `foreign_keys=1`, `busy_timeout=5000`),
      `DATA_DIR` subdirectories created, e2e settings have `DEBUG=False` and their own `DATA_DIR`.
- [x] T3: `config/security.py::mask_secrets` + `config/logging_filters.py::SecretMaskingFilter`, wired into the
      root logger in `base.py` with console + rotating file handlers. Test `tests/test_logging.py`: each of the six
      token shapes is masked in `record.msg`, in `%s` args and in an exception traceback; last4 survives; a normal
      message is untouched.
- [x] T4: `config/huey.py` (`SqliteHuey` at `DATA_DIR/huey.sqlite3`, single worker), `huey.contrib.djhuey` in
      `INSTALLED_APPS`. Test: the huey filename is under `DATA_DIR`, differs from the default database path, and
      `HUEY["immediate"]` is true under the test settings.
- [x] T5: `apps/accounts` app with `UserPreference` and `AuditEntry`, `gettext_lazy` on every verbose name and
      choice label, migrations `0001_initial` and `0002_groups` (data migration creating `admin` and `lead`), and
      `admin.py`. Tests: groups exist after `migrate`; `UserPreference` defaults are `system`/`en`; the admin
      changelist for both models answers 200 to a superuser.
- [x] T6: `apps/accounts/selectors.py` (`ScopeFilter`, `scope_for_user`) and `services.py` (`record_audit`,
      `set_theme`, `set_language`). Tests: `scope_for_user` returns an unrestricted filter for a lead and for an
      admin; `record_audit` stores actor, action and `{"before","after"}`; `set_theme`/`set_language` reject an
      unknown value and are idempotent.
- [x] T7: auth surface — `apps/accounts/urls.py` with Django's auth views, templates
      `registration/{login,logged_out,password_reset_form,password_reset_done,password_reset_confirm,password_reset_complete}.html`,
      `LoginRequiredMiddleware` installed. Tests `tests/test_urls_login.py`: parametrized over every entry in
      `urlpatterns` (recursive walk, sample args for `uidb64/token`), anonymous GET redirects to login except the
      documented allowlist; paired positive case — an authenticated lead gets 200 on the same URLs; login with a
      wrong password renders a visible field error; login honours `?next=`.
- [x] T8: `apps/dashboards` app with the placeholder `overview` view at `/`, `templates/dashboards/overview.html`.
      Test: a logged-in lead gets 200 and the page title; an anonymous user is redirected (covered by T7).
- [x] T9: `static/css/tokens.css`, `static/css/src/input.css`, `Makefile` `css` target downloading Tailwind
      v4.3.3 into `.tools/`, committed `static/css/app.css`. Tests: `tests/test_no_hardcoded_colors.py` (no `#hex`
      / `rgb(` / `hsl(` outside `tokens.css`, excluding `app.css` and `static/vendor/`);
      `tests/test_css_tokens.py` (every `--token` name in `tokens.css` appears in `app.css`).
- [x] T10: `templates/base.html` — `<html data-theme>`, the pre-paint inline script as the first head child,
      nav partial, `hx-headers` CSRF on `<body>`, vendored `static/vendor/htmx.min.js` + `make vendor`, plus
      `403/404/500.html`. Tests: the inline theme script precedes the stylesheet link in the rendered HTML;
      `hx-headers` carries a CSRF token; a 404 renders the themed template under `DEBUG=False`.
- [x] T11: theme switcher — `accounts.views.set_theme`, `templates/partials/theme_switcher.html`,
      `static/js/theme.js`, the context processor. Tests: POST `dark` sets `UserPreference.theme` and the cookie,
      and the next page renders `data-theme="dark"`; an anonymous POST sets only the cookie; an invalid value
      returns 400 with a visible message; `system` renders `data-theme-preference="system"`; the preference
      survives a logout and a fresh login.
- [x] T12: i18n — settings (`LANGUAGES`, `LOCALE_PATHS`, `LocaleMiddleware`), `UserLanguageMiddleware`,
      `accounts.views.set_language`, `templates/partials/language_switcher.html` (also on the login page),
      `JavaScriptCatalog` at `/jsi18n/`, all phase-1 strings marked, `locale/uk/LC_MESSAGES/django.po` and
      `djangojs.po` written and complete. Tests `tests/test_translations.py`: POST `uk` flips `/` to Ukrainian;
      the choice survives a reload and a new session for the same user; polib finds no empty and no fuzzy
      `msgstr` and identical placeholder sets between `msgid` and `msgstr` in both `.po` files; a canary list of
      English nav/auth strings does not appear in the `uk` render.
- [x] T13: duration formatter — `apps/dashboards/formatting.py`, the `humanize_duration` template filter,
      `static/js/formatting.js`, `tests/fixtures/duration_cases.json` (0 s, seconds, exact hour, `3h 20m`,
      `2d 4h`, a value that exercises the Ukrainian plural triple, `None`). Test: the Python side matches every
      case in `en` and `uk`; `None` renders as the em dash, never `0`.
- [x] T14: gate configuration — `[tool.pytest.ini_options]` (`DJANGO_SETTINGS_MODULE=config.settings.local`,
      `testpaths=["apps","tests"]`, `addopts="-q --strict-markers"`), `[tool.ruff]`, `[tool.mypy]` with
      django-stubs, root `conftest.py` with the respx assert-all-mocked autouse fixture, `HUEY immediate`, and the
      `lead_user`/`admin_user` fixtures. Tests: one trivial unit test; a test asserting that an outbound
      `httpx.get` inside a test raises instead of hitting the network.
- [x] T15: `apps/accounts/management/commands/seed_e2e.py` creating the `USER_ADMIN` and `USER_LEAD` personas
      idempotently with passwords from the environment (documented defaults). Tests: running it twice creates no
      second user and resets the password; both personas land in the right group.
- [x] T16: `Makefile` with `css`, `vendor`, `messages`, `e2e-up`, `e2e-down` exactly as `.autodev/PROFILE.md`
      specifies, including the readiness poll on port 8100 and pidfile teardown under `.e2e/`. Verified by
      running them (see Verification), not by a unit test.
- [x] T17: e2e harness — `e2e/surfaces.toml`, `e2e/conftest.py` (base URL from `E2E_BASE_URL`, fails with the
      exact `make e2e-up` command when the surface is down), `e2e/support/personas.py`, `e2e/plans/auth.plan.yaml`,
      `e2e/README.md`, and the specs: `web/test_login.py` (persona logs in and reaches Overview),
      `web/test_theme.py` (switch to dark, reload, new browser context → still dark, and `data-theme` is already
      correct at `domcontentloaded`), `web/test_language.py` (switch to `uk`, reload → still `uk`),
      `web/test_duration_format.py` (JS formatter matches `tests/fixtures/duration_cases.json` in both locales).
      Verified with three consecutive `make e2e-up && uv run pytest e2e -q; make e2e-down` round trips, all green.
- [x] T18: docs — `README.md`, `docs/SETUP.md` (install, first admin, run web + worker, `make css`/`make
      messages`, backup = stop both processes and copy `DATA_DIR`, a "Future deployment" note),
      `docs/CONFIGURATION.md` (every `.env` key and its default), `docs/TRANSLATIONS.md` (add a string, update and
      compile, add a language), and the living `docs/PROGRESS.md` and `docs/DECISIONS.md` started for phase 1.
- [x] T19: green-gate pass — regenerate `static/css/app.css` and the `.po`/`.mo` files, run the full lint chain,
      `uv run pytest -q`, and the e2e round trip; fix what they find. No check is weakened to pass. Fixed one
      `ruff` E501 in `e2e/web/test_theme.py`. All commands green on the final run.

## Verification

```bash
uv sync
uv run python manage.py migrate
uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run mypy \
  && uv run python manage.py makemigrations --check --dry-run && uv run python manage.py check
make css && git diff --stat static/css/app.css
make messages
make e2e-up && uv run pytest e2e -q; make e2e-down
```

| Acceptance criterion | Proof |
|---|---|
| `uv sync`, `uv run pytest -q` and the lint chain succeed from a clean checkout | T1/T14/T19; the commands above, run in order in a clean clone |
| `make css`, `make messages`, `make e2e-up && uv run pytest e2e -q; make e2e-down` succeed | T9/T16/T17/T19; the commands above |
| Every URL except login/logout/password-reset redirects an anonymous user to login | `tests/test_urls_login.py::test_anonymous_is_redirected` (parametrized over `urlpatterns`) + `::test_authenticated_lead_gets_200` |
| Language switch flips the page to `uk` and survives a reload and a new session | `tests/test_translations.py::test_language_switch_renders_uk`, `::test_language_survives_new_session`; e2e `web/test_language.py` |
| Theme persists server-side in `UserPreference` and applies with no flash on first paint | `apps/accounts/tests/test_theme.py::test_theme_persisted_for_user`, `::test_inline_script_precedes_stylesheet`; e2e `web/test_theme.py` (attribute correct at `domcontentloaded`, survives reload and a new context) |
| No `#hex` / `rgb(` colour literal outside `static/css/tokens.css` | `tests/test_no_hardcoded_colors.py` |
| uk `.po` files have no empty or fuzzy `msgstr` and placeholders match | `tests/test_translations.py::test_po_complete`, `::test_po_placeholders_match` |
| `static/css/app.css` contains every token defined in `tokens.css` | `tests/test_css_tokens.py::test_app_css_contains_every_token` |
| No live GitHub call is possible from the suite | `tests/test_http_guard.py::test_unmocked_request_fails` (respx assert-all-mocked) |
| A GitHub token never reaches a log record | `tests/test_logging.py` (msg, args, traceback) |

Test command stays `uv run pytest -q`.

## Risks

| Row in `RISKS.md` | What this phase does |
|---|---|
| #2 token leak | `mask_secrets` + `SecretMaskingFilter` on the root logger exist **before** any token does, with the masking function factored out for phase 3's `SyncRun.error_log`; tested against all six token shapes |
| #3 cross-project leak | `ScopeFilter` + `scope_for_user()` ship now as the only authorization entry point, so every selector written in phases 2–7 already takes one instead of being retrofitted in phase 8 |
| #8 Ukrainian lags | the polib completeness/placeholder tests and the canary render test are part of the gate from the first phase, so no later phase can merge an untranslated string |
| #12 both themes | tokens for both themes in one file, the colour-literal grep test, and the `data-theme` attribute contract are established before any component exists |
| #14 SQLite contention | WAL, `busy_timeout=5000`, `foreign_keys=ON`, `transaction_mode=IMMEDIATE`, and huey on a **separate** database file, asserted by a test |
| #15 stale generated artefacts | the `app.css`-contains-every-token test (with `source(none)` so Tailwind's automatic content scan can't drift the build outside the manifest) and the `.po`/`.mo` freshness tests; `.mo` files are committed |
| #9 no GitHub credentials | the respx assert-all-mocked default in `conftest.py`, verified by a test that an unmocked request raises |
| #4 time ceiling | the phase stays at one placeholder page and one app beyond `accounts`; nav items and vendored Chart.js arrive with the phases that need them |

## Out of scope

- All domain models (`catalog`, `activity`, `ai_detection`, `policy`, `metrics`, `churn`, `connections`,
  `github_sync`) and their admin — phase 2.
- `UserProjectAccess`, a restricting `scope_for_user`, and the `catalog.manage_settings` permission on the two
  groups — phase 2 creates the permission, phase 8 enforces the restriction.
- Any GitHub client code, `respx` fixtures under `tests/fixtures/github/`, token encryption — phase 3.
- The `REPORT_TIMEZONE` day-boundary helper, `DailyRollup`, `metrics.compute()`, `docs/METRICS.md` — phase 7.
- Real navigation targets (Projects, Repositories, People, PRs, Policy, Reviews, Settings), KPI cards, charts,
  `static/vendor/chart.umd.js`, `charts.js`, django-tables2 tables and the export layer — phases 7–9.
- `seed_demo`, performance work, contrast and long-Ukrainian-string audit, the remaining `docs/*` — phases 7 and 11.
