# PR Radar

A local Django tool for team leads. It pulls pull-request data from GitHub via the GraphQL API into SQLite and
shows dashboards on **AI adoption / AI-policy compliance** and on **delivery quality and dynamics** at the global,
project, repository and person level. Only leads and managers are users; the developers whose PRs are measured
have no accounts and are represented as `Person` records.

Spec: `docs/SPEC.md` (Ukrainian, author's source of truth). Everything else — code, comments, docstrings, commits,
logs, command output, documentation — is **English**. The UI ships in English and Ukrainian with full parity.

## Stack

Python 3.12 · Django 5.2 LTS · SQLite (WAL, ORM only so `DATABASE_URL` can point at PostgreSQL later) ·
httpx + GraphQL · huey on its own SQLite file · Django templates + htmx + Alpine · Tailwind standalone CLI with the
compiled CSS committed · vendored Chart.js · django-tables2 + django-filter · XlsxWriter ·
pytest / pytest-django / factory_boy / freezegun / respx · ruff + mypy · uv · Playwright for e2e.

## Commands

All run from the repository root, all non-interactive.

| | |
|---|---|
| install | `uv sync` |
| test | `uv run pytest -q` |
| lint | `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python manage.py makemigrations --check --dry-run && uv run python manage.py check` |
| format | `uv run ruff format .` |
| run | `uv run python manage.py runserver 8000` |
| worker | `uv run python manage.py run_huey` |
| e2e | `make e2e-up && uv run pytest e2e -q; make e2e-down` |
| css | `make css` — then commit `static/css/app.css` |
| translations | `make messages` — `makemessages -l uk -a` + `compilemessages` |

Management commands: `sync`, `recompute`, `compute_churn`, `seed_demo`, `seed_e2e`, `metrics_doc`,
`bootstrap_connection`, `rotate_encryption_key`, `seed_detection_rules`.

## Conventions

- One app per bounded piece of the domain: `accounts`, `connections`, `catalog`, `github_sync`, `activity`,
  `ai_detection`, `policy`, `metrics`, `churn`, `dashboards`, plus `config`.
- Views are thin: validate input, call a service, render a template. Rules live in `services.py`, queries in
  `selectors.py`.
- **Every** selector starts from `scope_for_user(user)`, never from `Model.objects.all()`. One authorization
  choke point, used by pages, chart JSON endpoints, tables, CSV and XLSX alike.
- `metrics.compute()` is the only read entry point for dashboards, charts, tables and exports.
- `DailyRollup` stores only additive metrics. Medians and percentiles are computed from raw rows on read.
- Day boundaries, rollups and reports use `REPORT_TIMEZONE` (`Europe/Kyiv`); storage is UTC. One helper owns it.
- System-generated text (violations, connection checks, sync errors) is stored as a code plus parameters and
  rendered in the reader's language. Never store a rendered English message.
- New UI strings get their Ukrainian translation **in the same phase**. Never concatenate translated fragments;
  full sentences with named placeholders, plurals via `ngettext`.
- Colour literals are allowed only in `static/css/tokens.css`; a test greps for violations. Dark mode is bound to
  `[data-theme="dark"]`, written on `<html>` by the server. `charts.js` reads colours at runtime and redraws on
  `themechange`.
- Every htmx endpoint returns a fragment; the same URL returns a full page for a normal request, chosen by
  `HX-Request`. Errors return a visible fragment, never an empty 400 body. Anything that mutates is POST/PUT/DELETE.
- **No GitHub token ever reaches** a log, an exception, `SyncRun.error_log`, a template, the Django admin, an
  export, a git remote URL or a subprocess argument. `git` receives it through `GIT_ASKPASS` + env only.
- **No live GitHub call at any point**, in tests or otherwise. Everything is exercised against JSON fixtures in
  `tests/fixtures/github/` mocked with `respx`; `conftest.py` fails the suite on any unmocked outbound request.
- Metrics with a sample below `MIN_SAMPLE` (5) are greyed and labelled. A missing value is `None`, never `0`.
- List views carry `assertNumQueries` tests so an N+1 fails the build.
- Generated files under version control (`static/css/app.css`, `docs/METRICS.md`, `locale/**/*.po`) each have a
  freshness test — regenerate and commit them when their source changes.

## Docs

`docs/SETUP.md`, `CONFIGURATION.md`, `METRICS.md`, `POLICY.md`, `GITHUB_CONNECTIONS.md`, `TRANSLATIONS.md`,
`DECISIONS.md`, `PROGRESS.md`; architecture decision records in `docs/dev/adr/`.

Autodev docs: .autodev/ (ARCHITECTURE.md, RISKS.md, ROADMAP.md, PROGRESS.md, DECISIONS.md, phases/NN-*/PLAN.md)
