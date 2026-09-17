# PR Radar — Architecture

Design of record for the autonomous run started 2026-09-17.
Source of truth: `docs/SPEC.md`. Where this document adds a number the spec does not give, it is marked
**(assumption)**.

---

## Problem

PR Radar is a **local, single-tenant Django tool for team leads and engineering managers**. It reads pull-request
activity from GitHub (many repositories, across several organisations and several credentials), stores it in a local
SQLite database, and answers two questions on dashboards:

1. **AI adoption and compliance** — which PRs were AI-assisted, with which tool, whether the author disclosed it,
   and which PRs break the company's AI policy.
2. **Delivery quality and dynamics** — throughput, lead time, review latency, PR size, rework, revert rate, CI
   first-pass rate and 21-day churn, at global / project / repository / person level, with an AI vs non-AI
   comparison beside every cohort-capable metric.

Developers whose PRs are analysed are **not users**. They have no account, see nothing, and receive nothing. They
exist only as `Person` rows. The tool **only reads** GitHub — it never writes a comment, status or label.

The product is used to prepare 1:1s and retros and to police an AI policy. That makes the output *evidence about
named individuals*, which drives the design's emphasis on correct attribution, sample-size honesty, and audit.

---

## Constraints

| | |
|---|---|
| Runtime | Python **3.12** (`requires-python = ">=3.12"`), Django **5.2 LTS** |
| Package manager | `uv` + `pyproject.toml` + committed `uv.lock` |
| Database | SQLite, WAL mode, `busy_timeout`. **No SQLite-specific SQL** — ORM only, so `DATABASE_URL` can point at PostgreSQL later (spec §13) |
| Delivery | Runs on the lead's own machine: `manage.py runserver` + one `run_huey` worker. No container, no deploy, no hosting in v1 |
| Team | One operator, who is also the only admin. No CI environment, no on-call |
| Network | The app calls **api.github.com only**, outbound, authenticated with PATs. It never accepts inbound traffic from anywhere but localhost |
| **Credentials (intake)** | **No GitHub token exists for this run.** No live GitHub call may be made at any point. Every GitHub behaviour is exercised against JSON fixtures under `tests/fixtures/github/`, mocked with `respx`. `bootstrap_connection` is implemented and unit-tested but never run for real |
| CSS (intake) | **Tailwind standalone CLI**, not Pico.css. Compiled CSS is committed; running or testing the app never needs Node |
| Languages | UI: English (default) + Ukrainian, full parity. Code, comments, docs, commits, logs, command output: English only |
| Timezone | Storage in UTC; every day boundary, rollup and report computed in `REPORT_TIMEZONE` (`Europe/Kyiv`, configurable) |
| Scope (intake) | All phases 0–10 of spec §14, in order, gated by `ruff check` + `ruff format --check` + `mypy` + `pytest` |

---

## Shape

One Django project, one process for the web, one process for background work, one SQLite file. Everything else is
a module boundary, not a process boundary. There is no queue broker, no cache server, no second service — the
spec's volumes (§ Non-functional requirements) do not justify any of them.

```
                     ┌──────────────── trust boundary: the internet ────────────────┐
                     │                                                              │
   api.github.com ───┤  github_sync.client (httpx, GraphQL v4 + REST)               │
   (read-only PAT)   │      ▲ per-connection rate budget, retries, token masking    │
                     └──────┼───────────────────────────────────────────────────────┘
                            │
   git (subprocess) ────────┼──── churn.service (bare clones under DATA_DIR/repos)
                            │
        ┌───────────────────┴─────────────────────────────────────────┐
        │                    ingestion pipeline                        │
        │  sync ─▶ upsert ─▶ identity ─▶ derived ─▶ ai ─▶ policy ─▶ dirty-days
        └───────────────────┬─────────────────────────────────────────┘
                            │ writes
                    ┌───────▼────────┐        ┌──────────────┐
                    │  SQLite (WAL)  │        │ huey.sqlite3 │  (separate file)
                    └───────┬────────┘        └──────▲───────┘
                            │ reads                   │ enqueue
        ┌───────────────────▼─────────────────────────┴──────────────┐
        │  metrics.service  ─▶  dashboards.views  ─▶  templates+htmx │
        │         ▲ scope_for_user (single authorization choke point)│
        └───────────────────┬────────────────────────────────────────┘
                            │
   ┌──── trust boundary: authenticated lead/admin session ────┐
   │  browser: Django templates, htmx fragments, Chart.js     │
   └──────────────────────────────────────────────────────────┘
```

### Components

Each is a Django app under `apps/`. Views validate input and call a service; business rules live in `services.py`,
queries in `selectors.py`.

| App | Responsibility | Why it exists as its own app |
|---|---|---|
| `accounts` | Django auth, the `admin`/`lead` groups, `UserProjectAccess`, `UserPreference` (theme, language), `AuditEntry`, and the one function `scope_for_user()` | Authorization and audit are the things this tool must not get wrong; they get one owner so they cannot be re-implemented per view |
| `connections` | `GitHubConnection`: Fernet-encrypted token storage, the `GitHubAuth` protocol, connection verification, status/expiry banner, key rotation | The credential is the only secret in the system and the only thing whose leak is unrecoverable; it gets a hard boundary and never leaves this app in plaintext |
| `catalog` | `Organization`, `Repository`, `Project`, `Person`, `Identity`, `AppSetting` | The stable nouns everything else points at; they change on human action, not on sync |
| `github_sync` | The httpx GraphQL/REST client, pagination, per-connection rate budgeting, retries, `SyncRun`, the sync orchestration and the post-processing pipeline hook | The only place that talks to a remote API; isolating it is what makes the whole system testable against fixtures |
| `activity` | `PullRequest`, `Commit`, `PullRequestCommit`, `PRFile`, `Review`, `ReviewComment`, `CheckStatus` + the derived-field service (size buckets, revert, hotfix, test changes, rubber stamp, self-merge, rounds) | The raw event record. Kept separate from interpretation so a rule change never requires a re-sync |
| `ai_detection` | `DetectionRule`, `AISignal`, the eight detectors, the PR-template disclosure parser, `ai_status` resolution | Detection rules are data the admin edits at runtime; they must be re-runnable over stored PRs without touching GitHub |
| `policy` | `AIPolicy`, `SensitivePathRule`, `PolicyViolation`, the nine rule evaluators, idempotent evaluation and auto-resolve | Policy is the product's compliance claim; it needs its own idempotency and its own test per rule |
| `metrics` | `MetricDef` registry, calculators, `DailyRollup`, `compute()`, versioned cache, `metrics_doc` | One registry so UI, docs and XLSX cannot disagree about what a metric means |
| `churn` | Bare git clones, `GIT_ASKPASS` credential handoff, blame-based survival analysis, `ChurnResult` | The only component that shells out and the only one that touches the filesystem at scale |
| `dashboards` | Views, templates, htmx fragments, chart JSON endpoints, django-tables2 tables, the export layer (`exports/columns.py`, `csv.py`, `xlsx.py`, `reports.py`), `ExportJob` | The read surface. Holds no domain rules of its own |
| `config` | settings `base`/`local`/`prod`, urls, huey config, logging with the token-masking filter | — |

Apps I considered and did **not** create: a separate `api` app (the chart JSON endpoints are three views in
`dashboards` and answer only the tool's own templates); a separate `reports` app (the export layer is a package
inside `dashboards`, because its column definitions must stay next to the tables they mirror); a separate
`identity` app (identity resolution is ~200 lines of service over `catalog` models).

### Contracts between components

These are the seams that later phases must not blur. Each is a plain Python function or protocol — no HTTP, no
queue, no serialization.

```python
# connections — the only way to get credentials, and the only auth abstraction
class GitHubAuth(Protocol):
    def get_headers(self) -> dict[str, str]: ...
    def get_git_credentials(self) -> tuple[str, str]: ...   # (username, token), for GIT_ASKPASS only
    @property
    def rate_limit_key(self) -> str: ...                    # per-connection budget identity

# github_sync — post-processing, called once per PR inside its transaction's commit hook
def process_pull_request(pr: PullRequest) -> None
    #  identity.resolve  →  activity.derive  →  ai_detection.detect  →  policy.evaluate  →  metrics.mark_dirty

# metrics — the single read entry point for every dashboard, chart, table and export
def compute(metric_keys: Sequence[str], scope: Scope, date_from: date, date_to: date,
            cohort: Cohort, granularity: Granularity) -> MetricResultSet
    # returns: value, previous-period value, delta, sample_size, series[]

# accounts — the single authorization choke point
def scope_for_user(user: User) -> ScopeFilter
    # every selector, chart endpoint, table and export starts from this; nothing queries Project/
    # Repository/Person/PullRequest without it

# dashboards.exports — one column description, three renderers
ExportColumn(key, title, type, width, number_format)
    # django-tables2, CSV and XLSX all read the same list; a column added once appears in all three
```

### Where state lives

| State | Home | Notes |
|---|---|---|
| Everything domain | `db.sqlite3` (`DATA_DIR`) | WAL, `busy_timeout=5000`, `foreign_keys=ON` |
| Background task queue | `huey.sqlite3` (`DATA_DIR`) | A separate file so a queue write never blocks a dashboard read |
| GitHub tokens | `GitHubConnection.token_encrypted` (BinaryField) | Fernet ciphertext. Plaintext exists only in a local variable during one request or task |
| Encryption keys | `.env` → `FIELD_ENCRYPTION_KEYS` | Never in the DB. Losing the key means re-entering tokens, not losing data |
| Git working data | `DATA_DIR/repos/<owner>/<name>.git` | Bare clones, re-creatable. Never in the repository |
| Generated exports | `DATA_DIR/exports/` | Deleted after 7 days by a scheduled task |
| Compiled CSS + vendored JS | `static/` **committed** | So neither running nor testing needs Node or a network fetch |
| `.mo` translation files | committed alongside `.po` sources | covered by `tests/test_translations.py::test_mo_catalog_matches_committed_po_source` |
| Metric cache | Django `FileBasedCache` under `DATA_DIR/cache/` | Keyed by `last_data_version`; safe to delete at any time |
| UI filter state | The query string | Spec §10.1 — every dashboard view must be a shareable link |

### Trust boundaries

1. **api.github.com.** Untrusted input. Every string from GitHub (PR titles, bodies, branch names, logins) is
   stored as-is and rendered **escaped**; markdown is not rendered in v1. Payloads are only parsed by
   `github_sync.client`, which validates shape before any model sees it.
2. **The authenticated session.** `LoginRequiredMiddleware` closes every URL except login/logout/password-reset. A
   parametrized test walks `urlpatterns` and asserts anonymous access redirects for *every* route. Inside the
   session, `admin` vs `lead` splits on the `catalog.manage_settings` permission, and `scope_for_user()` splits
   data horizontally by project.
3. **The `git` subprocess.** Receives the token through `GIT_ASKPASS` and an env var only. The token never reaches
   the remote URL, `.git/config`, a credential helper, or a process argument visible in `ps`.
4. **The export file.** Leaves the system to a spreadsheet: formula injection is neutralised, `Person.notes` is
   excluded by construction, and download is restricted to the job's own author.
5. **Logs and `SyncRun.error_log`.** A `logging.Filter` masks `ghp_…`, `github_pat_…`, `gho_…`, `ghs_…`, `ghu_…`
   and `ghr_…` before any record is emitted or persisted.

---

## Data

### Entities and relationships

```
GitHubConnection ──PROTECT──▶ Repository ──▶ Organization
                                  │  ▲
                                  │  └── M2M ── Project ──M2M── UserProjectAccess ──▶ User
                                  ▼
                            PullRequest ──▶ Identity ──▶ Person
                              │ │ │ │ │
                              │ │ │ │ └─▶ PRFile
                              │ │ │ └───▶ Review / ReviewComment ──▶ Identity
                              │ │ └─────▶ PullRequestCommit ──▶ Commit ──▶ Identity
                              │ ├───────▶ CheckStatus
                              │ ├───────▶ AISignal ──▶ DetectionRule
                              │ ├───────▶ PolicyViolation
                              │ └───────▶ ChurnResult
                            DailyRollup (denormalised, derived, disposable)
```

### Identity and ownership

- **GitHub owns** `Organization`, `Repository`, `PullRequest`, `Commit`, `Review`, `ReviewComment`, `PRFile`,
  `CheckStatus`. Identity is `github_id` (or `(repository, sha)` for commits); a re-sync overwrites them safely.
  These are read-only in Django admin.
- **The operator owns** `Project`, `Person` (`display_name`, `team`, `role_hint`, `notes`, `is_bot`,
  `exclude_from_metrics`), `Identity → Person` mapping, `GitHubConnection`, `AIPolicy`, `SensitivePathRule`,
  `DetectionRule`, `AppSetting`, `UserProjectAccess`. **This data cannot be recovered by re-syncing GitHub.**
- **The system owns** derived fields on `PullRequest`, `AISignal`, `PolicyViolation` (except the human `status`,
  `resolved_by`, `resolution_comment`), `ChurnResult`, `DailyRollup`. All recomputable by `manage.py recompute`.

`Identity` is the hinge: a GitHub login or a git email, unique per `(kind, value)`, with a nullable `person`. An
unmapped identity is a queue item, never a silent drop. Person-level metrics are therefore only as correct as the
mapping — the "unmapped identities" count is surfaced on the dashboard as a data-quality signal, not hidden.

### What must never be lost

In descending order of pain:

1. **`PolicyViolation.status` / `resolved_by` / `resolution_comment`** and `AuditEntry` — a lead's judgement calls
   about named people. Never truncated, never recomputed. Auto-resolve only ever moves `open → resolved` and
   records that it was automatic; it never touches `acknowledged` or `waived`.
2. **`Person.notes`** — private 1:1 notes. Never exported, never in a report sheet, never in a chart payload.
3. **`Identity → Person` mapping and `Person` merges** — hours of manual work; a merge writes an `AuditEntry` and
   is not a cascade delete.
4. **`GitHubConnection` rows** — a `PROTECT` FK from `Repository` means deleting a connection requires rebinding
   or deactivating its repositories first. Historical data is never deleted with a connection.
5. **`Project` definitions and `AppSetting` values** — the shape of every report.

Everything else (`PullRequest` and below, `AISignal`, `DailyRollup`, `ChurnResult`, the cache, the clones) is
reconstructible from GitHub plus a `recompute`, and is treated as such.

### Two data-model rules that later phases must not break

- **`DailyRollup` stores only additive metrics** (counters and sums). Medians and percentiles are computed from
  raw rows on read, because a median of medians is wrong and the spec explicitly forbids it. A rollup row is
  therefore always safe to delete and rebuild.
- **`PolicyViolation` stores `rule_code` + `details_params`, never a rendered message.** Same for
  `GitHubConnection.last_check_result`. The text is rendered in the reader's language at read time. This is what
  makes the Ukrainian translation total rather than partial.

---

## Cross-cutting

### Authentication and authorization
- Django session auth. `LoginRequiredMiddleware` (Django 5.1+) denies by default; login, logout and password reset
  are the only `@login_not_required` views. No self-registration; the first admin comes from `createsuperuser`.
- Two groups: `admin` (holds `catalog.manage_settings`: connections, repositories, projects, people, policy,
  detection rules, settings, users, sync) and `lead` (all dashboards, people pages, PRs, violation actions,
  triggering a sync).
- **Horizontal isolation** is `scope_for_user(user) -> ScopeFilter`, in `accounts`. No `UserProjectAccess` rows for
  a user means "sees everything"; any rows mean "sees exactly those projects, their repositories, and the people
  who were active in them; their global level is the union of those projects". Every selector, chart endpoint,
  table, CSV and XLSX export begins there. Tests assert isolation on pages, chart JSON *and* export files, since a
  view permission is not a template permission and neither is an export permission.

### Input validation
- Django forms for every write; nothing is written from `request.GET`/`POST` without a form or a typed parser.
- Filter query strings are parsed by one `DashboardFilterForm` that clamps dates, rejects unknown metric keys and
  ignores project/repo ids outside `scope_for_user`. An out-of-scope id is silently dropped, not 403 — it must not
  confirm that the id exists.
- GitHub payloads are shape-validated in `github_sync.client` before hitting a model. A missing field means the
  derived metric is `None` (spec §15), never `0`.
- Every data-changing action is POST/PUT/DELETE even when htmx makes GET convenient; CSRF is set once in
  `base.html` via `hx-headers`.

### Error handling
- Three levels, deliberately different:
  - **Sync errors are data**, not exceptions that reach a user: one failed PR does not stop a repository, one
    failed repository does not stop the run, and everything lands in `SyncRun.error_log` (masked) and
    `stats_by_connection`.
  - **User-facing errors are always a visible fragment.** An htmx endpoint never answers a failure with an empty
    400 body — it returns the form or an error partial with the right status, so the button is never "frozen".
  - **Unexpected exceptions** produce themed 403/404/500 templates. `DEBUG=False` in the prod settings and in e2e,
    so a real 500 is never hidden behind the debug page.

### Logging and observability
- `structlog`-style key/value through the stdlib `logging` config in `config/settings/base.py`; English only.
- A `SecretMaskingFilter` on the root logger strips GitHub token shapes from every record — this filter is
  installed in phase 0, before any token exists, so no phase can forget it.
- **What the operator looks at when it "doesn't work":** the Sync page (last `SyncRun`, per-connection stats,
  rate-limit state, error log), the Connections page (status, last check result, expiry), and the "data is current
  as of …" banner on every dashboard. Plus `manage.py check` and the log file at `DATA_DIR/logs/pr-radar.log`.
- `MIN_SAMPLE` (5) greys out any metric computed from too few PRs, and unmapped-identity and bot-PR counters are
  shown rather than hidden. Observability here includes *observability of the numbers themselves*, because the
  numbers get shown to a manager talking to a person.

### Configuration and secrets
- `django-environ`, `.env` (git-ignored), `.env.example` committed and complete.
- From `.env`: `SECRET_KEY`, `FIELD_ENCRYPTION_KEYS` (comma-separated; first encrypts, all decrypt →
  `MultiFernet` rotation), `DATABASE_URL`, `DATA_DIR`, `REPORT_TIMEZONE`, `DEBUG`, `ALLOWED_HOSTS`,
  `STORE_RAW_PAYLOADS`.
- From `AppSetting` (typed rows, editable in the UI, with defaults in code): `STALE_DAYS`, `MIN_SAMPLE`, size
  buckets, churn window, `EXCLUDED_PATH_GLOBS`, test path globs, bot logins, AI cohort composition, disclosure
  synonyms, `detect_browser_language`, `EXPORT_SYNC_MAX_ROWS`. Operational knobs belong to the operator, not to a
  redeploy.
- GitHub tokens are **never** in `.env` after first boot: `GITHUB_TOKEN`, if present and no connection exists,
  creates a "Default (.env)" connection once; afterwards the DB is the source of truth and a warning is logged if
  the variable is still set.

### Internationalization
- `LANGUAGE_CODE="en"`, `LANGUAGES=[en, uk]`, `LocaleMiddleware`, no `i18n_patterns` (URLs stay language-free so
  links are shareable across a bilingual team). Browser detection off by default.
- Preference order: `UserPreference.language` → `django_language` cookie → `en`.
- Translatable: templates, Python (`gettext_lazy` in models, forms, choices, `verbose_name`, the metric registry,
  policy rule texts, connection-check texts, `messages`), and JS via `JavaScriptCatalog`.
- **Not** translatable: GitHub data, user-entered names, metric keys, rule codes, logs, `AuditEntry`.
- Concatenating translated fragments is banned; only full sentences with named placeholders. Plurals always via
  `ngettext` (Ukrainian has three forms).
- Duration and number formatting has **one** implementation in Python and **one** in JS, both locale-aware, and
  they are tested against each other.
- CI-equivalent gates: `.po` files up to date, no empty/fuzzy `msgstr` in `uk`, placeholder sets identical between
  `msgid` and `msgstr` (via `polib`), and a smoke test that renders every page in both languages with a canary
  list of English strings that must not survive into the `uk` render.

### Accessibility
- WCAG 2.1 AA contrast in **both** themes: text ≥ 4.5:1, KPI numbers and chart elements ≥ 3:1.
- Status is never colour-only: ▲▼ arrows, icons and text badges accompany every good/bad signal; the categorical
  palette is colour-blind-safe and AI/non-AI keep the same meaning in both themes.
- Tables get real `<th scope>`, sortable headers are buttons with `aria-sort`, charts carry a text alternative
  (the same series are reachable as a table via the export button).
- Keyboard: every htmx action is reachable and focus is managed on swap; the theme and language switchers are
  ordinary menus, not hover-only.
- Long Ukrainian strings must not break KPI cards, buttons or column headers — checked in phase 10.

---

## Non-functional requirements

Values from the spec are unmarked; the rest are chosen here and marked **(assumption)**.

| Dimension | Target |
|---|---|
| Repositories | 50 active (spec §14 phase 10); **up to 150 without redesign (assumption)** |
| Pull requests | 20 000 in the seeded profiling set; **~60 000 rows before SQLite needs revisiting (assumption)** |
| Row volume | PRs × ~12 (commits, reviews, comments, files, checks) ⇒ **~250 k–800 k rows, DB under 2 GB (assumption)** |
| Named users | **1–5 leads, ≤ 3 concurrent sessions (assumption)** |
| Concurrency | 1 web process + 1 huey worker, both on SQLite WAL. **One writer at a time; `busy_timeout=5000 ms` (assumption)** |
| Sync | One run at a time, enforced by a DB lock. **A 50-repo incremental run completes in under 30 min, inside one hourly schedule window (assumption)** |
| Backfill | 180 days by default |
| GraphQL budget | 5 000 points/hour per connection; pause when `remaining < 200`; **incremental sync of one repo costs < 100 points (assumption)** |
| Dashboard latency | 90-day dashboard renders **< 1.5 s** locally on seed data (spec §14 phase 10). **Warm cache < 300 ms (assumption)** |
| Table/list latency | **< 800 ms for a 50-row page over 20 k PRs (assumption)**; list views are covered by `assertNumQueries` so an N+1 fails the build |
| Synchronous export | ≤ 20 000 rows in-request via XlsxWriter `constant_memory`; larger goes to huey |
| Background export | **≤ 5 min for 200 000 rows; files expire after 7 days (assumption)** |
| Churn | ≤ 50 files per PR, else `too_large`; **≤ 4 concurrent git workers, ≤ 10 min per repository per nightly run (assumption)** |
| Clone disk | **`DATA_DIR/repos` may reach 1× the sum of repository sizes; documented in SETUP.md (assumption)** |
| Startup | **`runserver` ready in < 5 s; no network call at import time (assumption)** |
| Browsers | **Current Chrome, Firefox, Safari (last 2 versions); laptop and tablet widths first-class, phone acceptable as stacked columns (assumption)** |
| Python / Django | 3.12+ / 5.2 LTS |
| Backup / restore | **Copying `DATA_DIR` while the app is stopped is a complete backup; `manage.py dumpdata` for the operator-owned models is the portable form. Documented in `docs/SETUP.md` (assumption)** |

---

## Failure modes

### GitHub API (`github_sync.client`)

| Mode | Handling |
|---|---|
| Timeout | httpx timeout **(connect 10 s, read 30 s — assumption)**. Counts as a retryable failure |
| 502/503/secondary rate limit | Exponential backoff with jitter, honouring `Retry-After`; **5 attempts, capped at 60 s (assumption)**. Then the repository is marked failed for this run and the run continues |
| Primary rate limit | `rateLimit { remaining resetAt cost }` requested in every GraphQL query. `remaining < 200` ⇒ sleep until `resetAt`, logged into `SyncRun`. **Budget is per connection**: one connection waiting does not stall the others |
| 401 | The connection becomes `invalid`; all its repositories are skipped for the rest of the run; a banner appears for admins. Other connections keep going |
| 403 with SSO marker (`X-GitHub-SSO`) | Connection becomes `degraded`, with the authorization link surfaced in the UI |
| Token expired | `expires_at` from the `github-authentication-token-expiration` header; `expired` status; banner at < 14 days |
| Repeated failures | Re-verification is rate-limited to **once per hour per connection** so a broken token cannot become a request storm |
| Partial page / nested pagination | Nested connections (reviews, comments, commits, files) are always fully paginated past 100; a truncated nested page is a hard error for that PR, not a silent short list |
| Duplicate delivery / re-run | Every write is `update_or_create` keyed on `github_id` or `(repository, sha)`. Re-running a sync produces zero new rows — asserted by a test |
| Restart mid-flight | `last_synced_at` is advanced **only after a repository finishes**. A crash re-reads that repository from its previous watermark; the `SYNC_OVERLAP` (1 h) window guarantees nothing between the two runs is missed |
| Unknown outcome | Irrelevant by construction: the tool only issues GET/read queries. There is no write whose landing could be in doubt |
| Schema drift (GitHub changes a field) | The client validates the shape it needs and raises a named `GitHubSchemaError` with the path; the PR fails, the run continues, the error is in `error_log`. A missing optional field yields `None`, never `0` |
| Two syncs at once | A DB-level lock row; the second invocation exits with a clear message. The UI button enqueues rather than runs |

### Multi-step ingestion (sync → post-processing → rollups)

- Each PR's upserts run in **one transaction**; post-processing runs on commit. A crash between two PRs leaves a
  consistent DB with a stale watermark — the safe direction.
- Post-processing is **idempotent by design**: identity resolution is a lookup-or-create, derived fields are pure
  functions of stored rows, `AISignal` is unique per `(pull_request, rule, evidence-hash)`, and `PolicyViolation`
  is unique per `(pull_request, rule_code, details_hash)`. Re-running `recompute` over the same data changes
  nothing.
- **Auto-resolve** closes an `open` violation when the condition disappears, marked as automatic. It never
  reopens or overwrites a human `acknowledged`/`waived`.
- Rollups are rebuilt from a **dirty-days set**: post-processing records affected `REPORT_TIMEZONE` dates, and the
  rollup task deletes and rewrites exactly those `(date, scope, cohort, metric)` rows. Half-written rollups are
  therefore self-healing on the next run, and `manage.py recompute --from --to` is the manual repair tool.
- **Cache staleness** cannot outlive a write: `last_data_version` increments at the end of every sync and
  recompute and is part of every cache key.

### Background worker (huey)

- Worker down ⇒ tasks queue in `huey.sqlite3` and run when it starts. **Every task is also a management command**,
  so no capability is lost if the operator never runs the worker.
- Task killed mid-flight ⇒ tasks are written to be re-runnable (the same idempotency as above). An export job that
  dies leaves `ExportJob.status='running'`; a startup sweep **(assumption)** marks jobs older than 1 hour as
  `failed` so the UI never polls forever.
- Duplicate enqueue (double-clicked button) ⇒ the sync lock makes the second a no-op; export jobs are cheap
  enough to allow duplicates.

### git / churn

- Missing `Contents: read` on the PAT ⇒ `ChurnResult.status='error'` with an explanation, not a crash.
- Rebase merges ⇒ `unsupported_merge_method`, by design in v1.
- `> CHURN_MAX_FILES` ⇒ `too_large`.
- `lines_at_merge == 0` ⇒ result skipped entirely rather than recorded as 0 % or 100 %.
- Interrupted `git fetch` / corrupt clone ⇒ the clone directory is disposable; a failed fetch deletes and re-clones
  once, then reports `error` **(assumption)**.
- Clone disk exhaustion ⇒ the job fails with a clear error; clones are outside the repository and can be deleted.

### Local storage

- `database is locked` ⇒ WAL + `busy_timeout`; the worker and the web process are separated onto different files
  where possible (queue) so contention is limited to real domain writes.
- Interrupted migration ⇒ Django migrations are transactional on SQLite; `DATA_DIR` copy before upgrade is the
  documented rollback.
- Lost `FIELD_ENCRYPTION_KEYS` ⇒ tokens are unrecoverable, all other data survives; the recovery procedure is
  "re-enter the tokens", documented in `docs/GITHUB_CONNECTIONS.md`.
- Old schema, new code ⇒ `manage.py migrate` is a documented startup step; `manage.py check` and a
  `makemigrations --check --dry-run` in the lint gate mean a model change without a migration fails the build.

---

## Delivery and operations

**Build.** `uv sync` installs from a committed `uv.lock`. Two artefacts are built outside the Python toolchain and
**committed** so that nothing in the test or run path needs Node or the network:
- `static/css/app.css` — built by the Tailwind standalone CLI (`make css`), which the Makefile downloads into a
  git-ignored `.tools/` on demand.
- `static/vendor/chart.umd.js` — Chart.js, vendored once (`make vendor`), never a CDN.

**Run.** Two processes, both from the repository root:
`uv run python manage.py runserver` and `uv run python manage.py run_huey`. `docs/SETUP.md` gives the launchd/cron
entries for hourly `sync` and nightly `compute_churn` — the same tasks the worker schedules, so the operator can
choose either.

**Package.** There is no package in v1. The deliverable is the repository plus `docs/SETUP.md`. Hosting readiness
(spec §13) is preserved by three rules and nothing more: settings split `base`/`local`/`prod`, `DATABASE_URL` and
`DATA_DIR` from the environment, no SQLite-specific SQL, and `whitenoise` for static. `docs/SETUP.md` carries a
"Future deployment" section sketching web + worker + postgres compose, unimplemented.

**Release and upgrade.** `git pull && uv sync && uv run python manage.py migrate`.
`docs/SETUP.md` states plainly: **stop both processes, copy `DATA_DIR`, then upgrade.** User data survives because
every schema change ships as a Django migration and because everything the operator typed by hand lives in tables
that migrations only ever add to. Data migrations are forward-only; a migration that would drop an operator-owned
column requires an ADR.

**Operating.** Daily connection verification (scheduled task), an expiry banner at 14 days, a weekly export sweep,
and the Sync page as the one place that answers "is my data fresh?".

---

## Rejected alternatives

- **Webhooks / real-time ingestion.** Explicitly out of scope, and a local tool has no public URL to receive them.
  Polling with a `SYNC_OVERLAP` window is correct and testable. Rejected.
- **A GitHub App instead of PATs in v1.** Out of scope, and no credentials exist for this run. Kept reachable: the
  `GitHubAuth` protocol and the nullable `app_id`/`installation_id`/`private_key_encrypted` fields mean adding it
  later touches `connections` only. Rejected for v1.
- **PostgreSQL from the start.** Buys concurrency the spec does not need and costs the operator a server on their
  laptop. SQLite + ORM-only + `DATABASE_URL` makes the switch a config change. Rejected.
- **Celery + Redis.** A broker process for an hourly sync and the occasional large export on one machine. `huey`
  with `SqliteHuey` has zero extra processes to install. Rejected.
- **A SPA (React/Vue) front end.** The product is dense tables and charts behind a login for at most five people;
  a build toolchain, a second language and an API surface would cost more than the interactivity is worth. Django
  templates + htmx + a vendored Chart.js deliver the same screens with no build step. Rejected.
- **Pre-computing every metric into rollups.** Percentiles and medians do not compose over daily buckets — the
  spec says so. Rolling up only additive metrics and computing distributions from raw rows is both correct and
  fast enough at the stated volume. Rejected.
- **Materialising an AI/non-AI cohort flag into rollups as a third dimension only.** Kept `cohort` in
  `DailyRollup` because the spec's dashboards show all/AI/non-AI side by side on every row, but the cohort
  *definition* stays a setting, so changing it is a `recompute`, not a migration. (Accepted with that caveat.)
- **Storing rendered violation and connection-check messages.** Would make the Ukrainian UI half-English forever
  and would freeze wording at write time. Storing `rule_code` + params costs one render function. Rejected.
- **Storing PR and comment bodies in full for AI detection.** `PullRequest.body` is needed for disclosure parsing,
  but review comment text is not — only its length is. Less data about people, less to leak, less to explain.
  Kept minimal by design.
- **A per-view permission decorator instead of `scope_for_user`.** Thirty chances to forget one, and exports and
  chart endpoints are exactly where it gets forgotten. Rejected.
- **Pico.css.** Settled at intake in favour of Tailwind for the dense tables, KPI cards and the `data-theme`
  token system. Rejected.
