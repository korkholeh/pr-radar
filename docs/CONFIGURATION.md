# Configuration

Settings are read from a `.env` file at the repository root (via `django-environ`); copy `.env.example` to `.env`
and edit it. `manage.py` defaults `DJANGO_SETTINGS_MODULE` to `config.settings.local`; deployment sets it to
`config.settings.prod`, and the e2e suite to `config.settings.e2e` (see below).

| Key | Default | Meaning |
|---|---|---|
| `SECRET_KEY` | `insecure-dev-key-change-me` | Django's signing key. Set a real random value before anyone but you can reach the app — the default is only safe for a single-operator localhost run. |
| `DEBUG` | `False` (base); `local.py` forces `True`, `prod.py`/`e2e.py` force `False` | Django debug mode. `local` always runs with it on regardless of this key; `prod`/`e2e` always run with it off. |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated list. `e2e.py` overrides this to `127.0.0.1,localhost` regardless of `.env`. |
| `DATABASE_URL` | `sqlite:///data/db.sqlite3` (derived from `DATA_DIR`) | Any URL `django-environ` understands. Only the ORM is used anywhere in the codebase (ADR 0001), so pointing this at `postgres://…` needs no code change. Left unset, it always tracks `DATA_DIR`. |
| `DATA_DIR` | `data` (repository-relative) | Holds `db.sqlite3`, `huey.sqlite3`, `logs/`, `cache/`, `exports/`, `repos/`, `staticfiles/`. Created on settings import if missing. `e2e.py` ignores this and always uses `.e2e/data`, so an e2e run never touches your own `DATA_DIR`. |
| `REPORT_TIMEZONE` | `Europe/Kyiv` | The timezone dashboards, rollups and reports use for day boundaries. Storage stays UTC (`TIME_ZONE = "UTC"`, `USE_TZ = True`); this setting is only consumed by the day-boundary helper phase 7 adds. |
| `FIELD_ENCRYPTION_KEYS` | empty list | Comma-separated Fernet keys for encrypting GitHub connection tokens at rest (phase 3). The first key encrypts; every key in the list can decrypt, which is how `manage.py rotate_encryption_key` rotates without downtime. Unused until phase 3 creates `connections`. |
| `STORE_RAW_PAYLOADS` | `False` | When true, sync stores the raw GitHub GraphQL response alongside the derived fields (more disk, easier debugging). Unused until phase 3. |
| `E2E_ADMIN_PASSWORD` | `admin-password-change-me` | Password `manage.py seed_e2e` sets for the `e2e-admin` persona. Only read by that command, under `config.settings.e2e`. |
| `E2E_LEAD_PASSWORD` | `lead-password-change-me` | Password `manage.py seed_e2e` sets for the `e2e-lead` persona. |

## App settings (operator-editable)

Unlike the `.env` keys above, these live in the database (`catalog.AppSetting`), are seeded from the frozen
registry `apps/catalog/setting_defs.py` by the data migration `catalog/0002_app_setting_defaults.py`, and are
meant to be changed at runtime through the settings UI (phase 8) rather than by editing a file. Code reads them
through `apps/catalog/services.py` (`get_setting`/`get_int`/`get_bool`/`get_str`/`get_list`/`get_dict`), which
raises `UnknownSettingError` for a typo'd key and falls back to the code default when a registered key's row is
missing.

| Group | Key | Type | Default | Meaning |
|---|---|---|---|---|
| sync | `BACKFILL_DAYS` | int | `180` | How many days of history a first sync pulls in. |
| sync | `DEFAULT_CONNECTION_KIND` | str | `fine_grained_pat` | Connection kind preselected when creating a new GitHub connection. |
| sync | `CONNECTION_CHECK_INTERVAL_HOURS` | int | `24` | How often a connection's health is re-checked. |
| ai | `AI_COHORT_INCLUDE_SUSPECTED` | bool | `False` | Whether PRs with a merely suspected AI status count in the AI cohort. |
| ai | `BOT_LOGIN_SUFFIXES` | list | `["[bot]"]` | Login suffixes that mark an account as a bot. |
| ai | `BOT_LOGINS` | list | `["dependabot", "renovate", "github-actions"]` | Exact logins that mark an account as a bot. |
| ai | `DISCLOSURE_SECTION_HEADINGS` | list | `["AI assistance"]` | PR template section headings that hold the AI disclosure. |
| ai | `DISCLOSURE_LABELS_NONE` | list | `["None"]` | Disclosure values meaning no AI assistance. |
| ai | `DISCLOSURE_LABELS_PARTIAL` | list | `["Partial"]` | Disclosure values meaning partial AI assistance. |
| ai | `DISCLOSURE_LABELS_SUBSTANTIAL` | list | `["Substantial"]` | Disclosure values meaning substantial AI assistance. |
| ai | `DISCLOSURE_TOOLS_LABELS` | list | `["AI tools used"]` | PR template section headings that list the AI tools used. |
| metrics | `MIN_SAMPLE` | int | `5` | Minimum sample size below which a metric is greyed out. |
| metrics | `STALE_DAYS` | int | `5` | Days of inactivity after which an open PR is considered stale. |
| metrics | `WAITING_REVIEW_HOURS` | int | `24` | Hours after which a PR waiting for review is flagged. |
| metrics | `DURATION_MODE` | str | `calendar_hours` | How PR durations are computed. |
| metrics | `PR_SIZE_BUCKETS` | dict | `{"XS": 10, "S": 100, "M": 400, "L": 1000}` | Upper line-count boundary for each PR size bucket. |
| metrics | `EXCLUDED_PATH_GLOBS` | list | lock files, `*.min.js`, `*.min.css`, `**/migrations/**`, `**/generated/**`, `**/vendor/**` | File path globs excluded from size and churn calculations. |
| metrics | `TEST_PATH_GLOBS` | list | `tests/**`, `test_*.py`, `*_test.py`, `*.test.ts(x)`, `*.spec.ts(x)`, `*Tests.swift`, `**/__tests__/**` | File path globs recognised as test files. |
| metrics | `RUBBER_STAMP_MAX_MINUTES` | int | `10` | Maximum minutes between review request and approval to call a review a rubber stamp. |
| metrics | `REVIEW_LOAD_TOP_N` | int | `2` | How many top reviewers count toward review load. |
| metrics | `FOLLOWUP_FIX_WINDOW_DAYS` | int | `14` | Days after merge in which a follow-up fix is attributed. |
| metrics | `FOLLOWUP_FIX_FILE_OVERLAP` | float | `0.5` | Minimum file overlap ratio to attribute a follow-up fix to a PR. |
| policy | `NO_TESTS_MIN_LINES` | int | `20` | Minimum changed lines before a missing-tests violation applies. |
| churn | `CHURN_WINDOW_DAYS` | int | `21` | Days after merge over which churn is measured. |
| churn | `CHURN_MAX_FILES` | int | `50` | Maximum files in a PR before churn analysis is skipped. |
| ui | `DEFAULT_UI_LANGUAGE` | str | `en` | Default UI language for a new user. Kept in sync with `UserPreference.language`'s field default by a test. |
| ui | `DETECT_BROWSER_LANGUAGE` | bool | `False` | Whether to derive the initial UI language from the browser. |
| ui | `DEFAULT_THEME` | str | `system` | Default UI theme for a new user. Kept in sync with `UserPreference.theme`'s field default by a test. |
| export | `EXPORT_DURATION_UNIT` | str | `hours` | Unit durations are rendered in for CSV/XLSX exports. |
| export | `EXPORT_SYNC_MAX_ROWS` | int | `20000` | Maximum rows a synchronous export may return. |

## Settings modules

- `config/settings/base.py` — everything shared: apps, middleware, templates, i18n, static files, logging with
  `SecretMaskingFilter` on the root logger, the SQLite pragmas from `config/db.py`.
- `config/settings/local.py` — base + `DEBUG=True`, for `runserver` on your machine.
- `config/settings/prod.py` — base + `DEBUG=False`, whitenoise, HSTS, secure/httponly cookies,
  `SECURE_SSL_REDIRECT`, `X_FRAME_OPTIONS=DENY`. This is the module a future real deployment sets
  `DJANGO_SETTINGS_MODULE` to.
- `config/settings/e2e.py` — `prod` minus TLS (plain HTTP on `127.0.0.1`), its own `DATA_DIR`
  (`.e2e/data`, git-ignored) so `make e2e-up` never touches your development database. Used only by the
  Playwright suite under `e2e/`.

## SQLite pragmas

`config/db.py` applies `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON` and `busy_timeout=5000` on every
new connection, and sets `OPTIONS["transaction_mode"] = "IMMEDIATE"` — but only when `DATABASE_URL` resolves to
SQLite. None of this runs against a non-SQLite `DATABASE_URL`.

## Logging

Every log record (console + `DATA_DIR/logs/pr-radar.log`, rotating at 10 MB × 5 backups) passes through
`SecretMaskingFilter`, which masks any GitHub token shape (`ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`, `github_pat_`)
down to its last four characters — in the message, in `%s`-style args, and in exception tracebacks.
