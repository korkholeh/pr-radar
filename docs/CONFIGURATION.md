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
