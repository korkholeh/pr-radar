# Setup

PR Radar is a single-operator tool: one web process, one background worker, one SQLite database. This page covers
installing it, running it day to day, and backing it up. See `docs/CONFIGURATION.md` for every `.env` key.

## Install

Requires Python 3.12 and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env    # edit SECRET_KEY at minimum
uv run python manage.py migrate
```

`migrate` also creates the `admin` and `lead` groups. `admin` holds the `catalog.manage_settings` permission;
`lead` holds no model permission — both roles are otherwise distinguished by view-level checks, not Django
permissions (see `docs/dev/adr/0002-session-auth-two-roles-and-one-scope-function.md`).

## Create the first admin

```bash
uv run python manage.py createsuperuser
```

Add that user to the `admin` group from `/admin/` (Django admin, `django.contrib.auth` → Users) if you want them to
use the future admin-only pages; the Django admin site itself is reachable to any superuser regardless of group.

## Run

Two processes, both from the repository root:

```bash
uv run python manage.py runserver 8000     # web
uv run python manage.py run_huey           # background worker (sync, exports, churn)
```

Open <http://127.0.0.1:8000/>. Both processes read the same `.env` and the same `DATA_DIR`; the worker must be
running for anything queued in the background (nothing is queued yet in this phase).

## Building the frontend assets

The compiled CSS (`static/css/app.css`) and the vendored `static/vendor/htmx.min.js` are committed, so a plain
`uv sync` + `migrate` + `runserver` is enough to see a styled app. Only rebuild them after changing a template's
classes or `static/css/tokens.css`:

```bash
make css        # downloads the Tailwind standalone binary into .tools/ (git-ignored) on first use
make vendor      # re-fetches static/vendor/htmx.min.js
```

Commit `static/css/app.css` after `make css` — it is a generated file tracked in version control, and a test
(`tests/test_css_tokens.py`) fails if it goes stale relative to `static/css/tokens.css`.

## Translations

The compiled `locale/uk/LC_MESSAGES/*.mo` catalogues are committed, same as `static/css/app.css`, so a plain
`uv sync` + `migrate` + `runserver` already serves the Ukrainian UI — no separate compile step needed to install.
Only rebuild them after changing a translatable string or a `.po` file:

```bash
make messages    # updates locale/uk/LC_MESSAGES/django.po and djangojs.po, then compiles .mo files
```

Commit the regenerated `.mo` files together with the `.po` changes — `tests/test_translations.py` fails if a
`.mo` goes stale relative to its `.po`. See `docs/TRANSLATIONS.md` for the full workflow.

## Backup and restore

There is no automated backup job. The whole application state is `DATA_DIR` (`data/` by default):
`db.sqlite3`, `huey.sqlite3`, `logs/`, `cache/`, `exports/`, `repos/`. To back it up:

1. Stop both processes (web and worker).
2. Copy `DATA_DIR` elsewhere.
3. Restart both processes.

To restore, stop both processes, replace `DATA_DIR` with the backup copy, and restart. `db.sqlite3` holds every
operator-owned decision (violation acknowledgements, project/repository configuration, identity mapping) that a
re-sync from GitHub cannot regenerate — back it up before any risky change (a manual migration, an upgrade).

A more portable (schema-version-independent) export of the operator-owned tables is `manage.py dumpdata`, e.g.:

```bash
uv run python manage.py dumpdata accounts > accounts-backup.json
```

## Future deployment

Everything above assumes one operator running the app on their own machine, as the spec requires for v1. The
settings are already split so a future networked deployment only changes `config/settings/prod.py`'s environment
values and `DATABASE_URL` (the ORM is used exclusively — see ADR 0001 — so pointing `DATABASE_URL` at PostgreSQL
needs no code change). `config/settings/prod.py` already sets `DEBUG=False`, HSTS, secure cookies and
`SECURE_SSL_REDIRECT`; a real deployment still needs a WSGI/ASGI server (e.g. gunicorn/uvicorn) in front of it,
which this phase does not add. Run `manage.py collectstatic --ignore=src` before serving (the manifest
post-processor chokes on `static/css/src/input.css`'s Tailwind-only `@import "tailwindcss"` otherwise — that
directory holds the Tailwind CLI's input, never something the app serves directly).
