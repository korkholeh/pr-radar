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

Add that user to the `admin` group from `/admin/` (Django admin, `django.contrib.auth` → Users) so they can reach
the admin-only pages — Connections, Repositories, Sync, People, Detection rules, AI policy and Sensitive paths.
A user in the `lead` group sees the dashboards, the policy console and the people, PR and review pages, but none
of Settings. The Django admin site itself is reachable to any superuser regardless of group.

## Connect GitHub

Set `FIELD_ENCRYPTION_KEYS` in `.env` before creating a connection — see `docs/GITHUB_CONNECTIONS.md` for token
types, scopes, the first-run checklist and `manage.py bootstrap_connection` (an optional one-time `.env`-driven
shortcut for the first connection).

## Run

Two processes, both from the repository root:

```bash
uv run python manage.py runserver 8000     # web
uv run python manage.py run_huey           # background worker (sync, exports, churn)
```

Open <http://127.0.0.1:8000/>. Both processes read the same `.env` and the same `DATA_DIR`; the worker must be
running for anything queued in the background — a sync, churn recomputation, and a table or report export past
`EXPORT_SYNC_MAX_ROWS` rows (see `docs/CONFIGURATION.md`). If the worker is ever stopped for a while, catching up
is one command each rather than restarting it: `manage.py process_exports` runs every export left `pending`,
`manage.py cleanup_exports` deletes export files past their retention window and clears a job stuck `running` for
over an hour.

Author-baseline signals (`docs/user/tune-ai-detection.md`) run as a scheduled huey task,
`compute_baselines`, at 01:00 server time. They compare each author's recent work against their own trailing
history over a rolling window, so they have to be recomputed on a schedule rather than during a sync: a
verdict about an author changes when *other* pull requests arrive. It makes no GitHub call and can be run by
hand with `manage.py compute_baselines`. If no baseline rule is active — the shipped ones all start
deactivated — the run finishes immediately having written nothing.

Churn analysis (`docs/user/churn.md`) runs as a scheduled huey task, `compute_churn`, at 02:00 server time — an
hour before the 03:00 export cleanup — as long as `run_huey` is running. It clones each repository with open
churn work into `DATA_DIR/repos/<owner>/<name>.git` (bare clones, fetched in place on later runs rather than
re-cloned) and can be run manually with `manage.py compute_churn`. Budget disk space for one bare clone per
active repository — the same order of magnitude as that repository's own `.git` directory — and expect the first
nightly run after connecting a large repository to take longer than subsequent ones, which only fetch new
commits. Deleting `DATA_DIR/repos/` is always safe: the next run re-clones whatever it needs.

The same run also analyses pull-request diffs for structural AI signals, from the clone it has just made, for
the repositories named in `DIFF_ANALYSIS_REPOSITORIES` (empty by default — nothing is analysed until you opt a
repository in; `"*"` opts in all of them). It adds no GitHub API call, takes what is left of a repository's
`CHURN_REPO_TIME_BUDGET_SECONDS` after churn itself, and `manage.py compute_churn --no-diffs` runs churn
without it. Opting in a repository with a long history drains its backlog over successive nights rather than
in one run.

## Scheduling

`run_huey` (see "Run" above) already runs `sync` (at the top of every hour), `compute_baselines` (01:00 server
time), `compute_churn` (02:00 server time) and the export-cleanup task (03:00 server time)
on its own internal scheduler as long as the process stays up — for most single-operator setups that is enough,
and nothing below is required. Use an OS scheduler instead when you want the web process and the worker to
survive a reboot unattended, or when you'd rather have `cron`/`launchd` retry a crashed run than rely on the
worker process being restarted by hand.

### macOS: launchd

Two agents: one that keeps `run_huey` running (a "keep alive" daemon, since the worker is a long-lived process,
not a periodic job), one that runs `sync`/`compute_churn`/`cleanup_exports` on a calendar schedule as a backstop
even if the worker's own scheduler is briefly down.

`~/Library/LaunchAgents/com.pr-radar.worker.plist` — restarts `run_huey` if it exits:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.pr-radar.worker</string>
    <key>WorkingDirectory</key><string>/path/to/pr-radar</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/pr-radar/.venv/bin/python</string>
        <string>manage.py</string>
        <string>run_huey</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>/path/to/pr-radar/data/logs/worker.stdout.log</string>
    <key>StandardErrorPath</key><string>/path/to/pr-radar/data/logs/worker.stderr.log</string>
</dict>
</plist>
```

A second agent, e.g. `~/Library/LaunchAgents/com.pr-radar.schedule.plist`, with a `StartCalendarInterval` array
(one dict per run time) instead of `KeepAlive`, invoking `manage.py sync`, `manage.py compute_baselines`, `manage.py compute_churn` and
`manage.py cleanup_exports` — either as three separate agents or a small wrapper shell script the single agent
calls in sequence. Load either agent with `launchctl load ~/Library/LaunchAgents/com.pr-radar.worker.plist` (add
`bootstrap gui/$(id -u)` on newer macOS if `load` is deprecated on your version); `launchctl list | grep pr-radar`
confirms it registered.

### Linux: crontab

There is no long-running worker equivalent to `launchd`'s `KeepAlive` in plain `crontab`; either run `run_huey`
under `systemd` (a user service with `Restart=on-failure`) or rely on the periodic commands below alone, which
work standalone since every huey task is also a plain management command (see
`docs/dev/adr/0005-huey-sqlite-worker-with-every-task-also-a-command.md`):

```cron
# crontab -e
0 * * * *   cd /path/to/pr-radar && /path/to/pr-radar/.venv/bin/python manage.py sync >> data/logs/cron-sync.log 2>&1
0 1 * * *   cd /path/to/pr-radar && /path/to/pr-radar/.venv/bin/python manage.py compute_baselines >> data/logs/cron-baselines.log 2>&1
0 2 * * *   cd /path/to/pr-radar && /path/to/pr-radar/.venv/bin/python manage.py compute_churn >> data/logs/cron-churn.log 2>&1
0 3 * * *   cd /path/to/pr-radar && /path/to/pr-radar/.venv/bin/python manage.py cleanup_exports >> data/logs/cron-cleanup.log 2>&1
```

### Where logs land

Every management command and the worker log to both the console and `DATA_DIR/logs/pr-radar.log` (rotating, see
`docs/CONFIGURATION.md`'s Logging section) regardless of how they were launched; the redirections above only
capture anything printed straight to stdout/stderr outside that logger (a traceback before logging is configured,
for instance). `SecretMaskingFilter` applies the same way no matter what launched the process, so a token is
never exposed in either place.

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

### Backing up a live database without stopping the app

SQLite's WAL mode means `db.sqlite3` alone can be mid-write relative to its `-wal` file; a plain file copy while
the app is running can capture an inconsistent snapshot. SQLite's own `.backup` command produces a consistent
snapshot without stopping either process:

```bash
sqlite3 data/db.sqlite3 ".backup data/backups/db-$(date +%Y%m%d-%H%M%S).sqlite3"
```

This is safe to run on a schedule (see "Scheduling" above) alongside the stop/copy/restart approach for the rest
of `DATA_DIR`; it only covers `db.sqlite3` (not `huey.sqlite3`, `exports/`, `repos/`), which is fine for the
operator-owned data it exists to protect — a lost queued export or churn clone only costs a re-run, never a
lead's judgement call.

### `.env` and key material are part of the backup

`DATA_DIR` on its own is not enough to restore a working system: `.env`'s `FIELD_ENCRYPTION_KEYS` is what
decrypts every stored GitHub token, and it lives outside `DATA_DIR`. A restore performed with a different (or
missing) `FIELD_ENCRYPTION_KEYS` starts up fine but cannot decrypt any connection's token — syncing fails with an
authentication error that looks like a revoked token, not a missing key. Back up `.env` (or at least
`FIELD_ENCRYPTION_KEYS`) together with, but separately from, `DATA_DIR` — see `docs/GITHUB_CONNECTIONS.md`'s
"Recovery when `FIELD_ENCRYPTION_KEYS` is lost" for what to do if the key truly is gone: there is no recovery, only
replacing the affected connections' tokens with `manage.py bootstrap_connection` or the Settings UI.

### Verify a restore

After restoring, before trusting the instance:

1. `uv run python manage.py check` — catches a settings/migration mismatch between the restored database and the
   code version running against it.
2. Log in and open **Settings → Connections** — every connection should show status `ok` (or `degraded`/`invalid`
   for a reason already known before the backup, not a new one). A connection newly showing `invalid` after a
   restore, with no code change to explain it, means `FIELD_ENCRYPTION_KEYS` did not travel with the backup.

## Future deployment

Everything above assumes one operator running the app on their own machine, as the spec requires for v1. The
settings are already split so a future networked deployment only changes `config/settings/prod.py`'s environment
values and `DATABASE_URL` (the ORM is used exclusively — see ADR 0001 — so pointing `DATABASE_URL` at PostgreSQL
needs no code change). `config/settings/prod.py` already sets `DEBUG=False`, HSTS, secure cookies and
`SECURE_SSL_REDIRECT`; a real deployment still needs a WSGI/ASGI server (e.g. gunicorn/uvicorn) in front of it,
which this version does not ship. Run `manage.py collectstatic --ignore=src` before serving (the manifest
post-processor chokes on `static/css/src/input.css`'s Tailwind-only `@import "tailwindcss"` otherwise — that
directory holds the Tailwind CLI's input, never something the app serves directly).

`prod.py` already configures whitenoise to serve the collected static files directly from the WSGI/ASGI app, so
a reverse proxy (nginx, Caddy) in front of it is optional rather than required — add one for TLS termination, a
custom domain, or serving more than one app on the same host, not because static files need it. Point
`DATABASE_URL` at a `postgres://` URL and run `migrate` again to move off SQLite; no other code change is needed
(ADR 0001). What stays single-node regardless: huey's own SQLite queue file (`huey.sqlite3`) assumes one worker
process — running two `run_huey` processes against the same queue file is not supported — and churn's git clones
under `DATA_DIR/repos/` are local disk state a second web/worker node would each need its own copy of, or share
over a network filesystem. Moving either of those to something multi-node-safe (a different queue backend,
shared clone storage) is out of scope for v1.

## Performance

Two operator tools exist for reproducing and checking dashboard performance at a realistic data volume, without
a live GitHub connection:

- `uv run python manage.py seed_demo --reset --scale large` seeds 50 repositories and 20,000 pull requests
  (plus reviews, commits, files and policy violations) directly through a bulk write path rather than the
  row-by-row `sync` pipeline — the ordinary `--scale demo` seed is realistic but small; `--scale large` exists
  purely to have enough volume to profile against. It takes on the order of four minutes; a second run without
  `--reset` raises rather than mixing scales. See `apps/dashboards/management/commands/seed_demo.py`.
- `uv run python scripts/profile_dashboard.py` renders the Overview, a Project, a Repository, the People index
  and Reviews pages through the Django test client against whatever `--scale large` data is currently in your
  database, wrapping each render in `CaptureQueriesContext`, and prints the wall time, query count and the ten
  slowest SQL statements per page. Run `seed_demo --scale large` first — the script exits with an error naming
  the command if it finds no large-scale data. This is how the Overview page's render time was brought from
  29.4s/5,407 queries down to well under the architecture's 1.5s cold-render budget for 50 repos/20,000 PRs (see
  `.autodev/DECISIONS.md` for the before/after numbers and the root cause).
