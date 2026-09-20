# Architecture

What PR Radar is made of, how the pieces talk to each other, and which shapes are expensive to change. The design
of record the build started from is `.autodev/ARCHITECTURE.md`; this page describes what actually shipped, and
marks the places where the two differ under [Divergences from the design of record](#divergences-from-the-design-of-record).

The decisions that are costly to reverse each have an ADR under `docs/dev/adr/`, linked from the section they
belong to. Narrower, phase-level calls are in [`../DECISIONS.md`](../DECISIONS.md).

## Shape

One Django project. One web process (`manage.py runserver`), one background worker (`manage.py run_huey`), one
SQLite database for the domain and a second one for the queue. No broker, no cache server, no second service.

```
                    ┌───────────── trust boundary: the internet ─────────────┐
  api.github.com ───┤  apps/github_sync/client.py (httpx, GraphQL v4 + REST) │
  (read-only PAT)   │     per-connection rate budget, retries, masking       │
                    └──────┬────────────────────────────────────────────────-┘
                           │
  git (subprocess) ────────┼──── apps/churn (bare clones under DATA_DIR/repos)
                           │
       ┌───────────────────┴─────────────────────────────────────────────┐
       │ ingestion: sync → upsert → identity → derive → follow-up →      │
       │            AI detect → policy evaluate → mark dirty days        │
       └───────────────────┬─────────────────────────────────────────────┘
                           │ writes
                   ┌───────▼────────┐        ┌──────────────┐
                   │  db.sqlite3    │        │ huey.sqlite3 │
                   │  (WAL)         │        │ (own file)   │
                   └───────┬────────┘        └──────▲───────┘
                           │ reads                   │ enqueue
       ┌───────────────────▼─────────────────────────┴───────────────────┐
       │ apps/metrics.compute() → apps/dashboards views → templates+htmx │
       │        ▲ accounts.scope_for_user() — one authorization gate     │
       └───────────────────┬─────────────────────────────────────────────┘
                           │
  ┌──── trust boundary: authenticated lead/admin session ────┐
  │ browser: Django templates, htmx fragments, Chart.js      │
  └──────────────────────────────────────────────────────────┘
```

## Components

Ten apps under `apps/`, plus `config/`. Views validate input and call a service; rules live in `services.py`,
queries in `selectors.py`.

| App | Responsibility | Key modules |
|---|---|---|
| `accounts` | Session auth, the `admin`/`lead` groups, `UserProjectAccess`, `UserPreference` (theme, language), `AuditEntry`, and `scope_for_user()` | `selectors.py` (`scope_for_user`), `middleware.py` (`UserLanguageMiddleware`), `services.py` (`record_audit`) |
| `connections` | `GitHubConnection`: Fernet token storage, the `GitHubAuth` protocol, verification, status/expiry banner, key rotation | `crypto.py`, `auth.py`, `check_codes.py`, `services.py` |
| `catalog` | `Organization`, `Repository`, `Project`, `Person`, `Identity`, `AppSetting`; identity resolution and merges; path globs | `identity.py`, `globs.py`, `setting_defs.py`, `normalize.py` |
| `github_sync` | The httpx GraphQL/REST client, pagination, per-connection rate budgeting, retries, `SyncRun`, `SyncLock`, the orchestration, the repository AI-tooling probe and the post-processing hook | `client.py`, `queries.py`, `rate_limit.py`, `mappers.py`, `upserts.py`, `tooling.py`, `pipeline.py`, `services.py` |
| `activity` | `PullRequest`, `Commit`, `PullRequestCommit`, `PRFile`, `Review`, `ReviewComment`, `CheckStatus`, the derived-field service and the follow-up-fix heuristic | `derive.py`, `followup.py` |
| `ai_detection` | `DetectionRule`, `SignalRule`, `AISignal`, `DiffAnalysis`, twelve detectors, and three structural families — five per-PR kinds, four author baselines, four diff kinds — plus the PR-template disclosure parser and `ai_status` resolution | `detectors.py`, `structural.py`, `baselines.py`, `diffsignals.py`, `evidence.py`, `disclosure.py`, `rules.py`, `services.py`, `tasks.py` |
| `policy` | `AIPolicy`, `SensitivePathRule`, `PolicyViolation`, twenty-four rule evaluators (nine from the spec, fifteen from the PLANEKS standards), idempotent evaluation and auto-resolve, message rendering | `rules.py`, `services.py`, `messages.py` |
| `metrics` | The `MetricDef` registry (44 metrics), four calculator strategies, `DailyRollup`, `compute()`/`compute_many()`, the versioned cache, `metrics_doc` | `registry.py`, `calculators/`, `rollups.py`, `services.py`, `timeframe.py`, `docs.py` |
| `churn` | Bare git clones, `GIT_ASKPASS` credential handoff, blame-based survival analysis, `ChurnResult`, and the diff reads that feed `ai_detection`'s diff kinds from the same clone | `clones.py`, `gitcmd.py`, `askpass.py`, `blame.py`, `diffs.py`, `services.py` |
| `dashboards` | Views, templates, htmx fragments, chart JSON, django-tables2 tables, the export layer, `ExportJob` | `views.py`, `charts.py`, `kpis.py`, `tables.py`, `forms.py`, `exports/` |
| `config` | Settings (`base`/`local`/`prod`/`e2e`), urls, huey, the secret-masking filter, SQLite pragmas | `settings/`, `security.py`, `logging_filters.py`, `db.py`, `htmx.py` |

There is no `api` app (the chart JSON endpoints are views in `dashboards` and serve only this tool's own
templates) and no `reports` app (the export layer is a package next to the tables it mirrors).

## Contracts

Five seams. Each is a plain Python call — no HTTP, no queue, no serialization between them.

```python
# connections — the only way to get credentials, and the only auth abstraction
class GitHubAuth(Protocol):
    def get_headers(self) -> dict[str, str]: ...
    def get_git_credentials(self) -> tuple[str, str]: ...   # for GIT_ASKPASS only
    @property
    def rate_limit_key(self) -> str: ...

# github_sync — post-processing, run once per PR on its transaction's commit hook
def process_pull_request(pull_request_id: int) -> None
    # identity → derive → follow-up fixes → AI detect → policy evaluate → metrics.mark_dirty

# metrics — the single read entry point for every dashboard, chart, table and export
def compute(metric_keys, scope, date_from, date_to,
            cohort="all", granularity="day", include_series=True) -> MetricResultSet
def compute_many(metric_keys, scope_type, scope_ids, access, date_from, date_to, ...) -> dict[int, MetricResultSet]

# accounts — the single authorization choke point
def scope_for_user(user) -> ScopeFilter
    # every selector, chart endpoint, table and export starts here

# dashboards.exports — one column description, three renderers
ExportColumn(key, title, type, width=15, link_key=None, direction=None)
```

`scope_for_user()` is a grant list: no `UserProjectAccess` rows means unrestricted; any rows mean exactly those
projects, their repositories and the people active in them. The result is memoised on the user object for the
lifetime of one request, so a page that asks a dozen times pays one query, and a revoked grant takes effect on
the next request. An out-of-scope id in a query string is dropped silently rather than answered `403`, so the
response never confirms that the id exists; the same id in a URL path is a `404`.

## Where state lives

| State | Home | Notes |
|---|---|---|
| Everything domain | `db.sqlite3` under `DATA_DIR` | WAL, `synchronous=NORMAL`, `foreign_keys=ON`, `busy_timeout=5000`, applied per connection in `config/db.py` |
| Background queue | `huey.sqlite3` under `DATA_DIR` | A separate file so a queue write never blocks a dashboard read |
| GitHub tokens | `GitHubConnection.token_encrypted` | Fernet/`MultiFernet` ciphertext; plaintext exists only in a local variable |
| Encryption keys | `.env` → `FIELD_ENCRYPTION_KEYS` | Comma-separated; the first encrypts, all decrypt, so rotation is additive |
| Git working data | `DATA_DIR/repos/<owner>/<name>.git` | Bare clones, disposable; deleting the directory is always safe |
| Generated exports | `DATA_DIR/exports/` | Deleted after their retention window by `cleanup_exports` |
| Metric cache | `FileBasedCache` under `DATA_DIR/cache/` | Key includes `data_version()`; safe to delete at any time |
| Compiled CSS, vendored JS | `static/` — committed | So neither running nor testing needs Node or a network fetch |
| Translations | `locale/uk/LC_MESSAGES/` — `.po` **and** `.mo` both committed | `tests/test_translations.py::test_mo_catalog_matches_committed_po_source` keeps the compiled catalogue in step with its source |
| UI filter state | The query string | Every dashboard view must be shareable as a link |

## Data model

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

**GitHub owns** `Organization`, `Repository`, `PullRequest`, `Commit`, `Review`, `ReviewComment`, `PRFile`,
`CheckStatus` — keyed on `github_id` (or `(repository, sha)` for commits), read-only in the Django admin, safe to
overwrite on a re-sync.

**The operator owns** `Project`, `Person`, the `Identity → Person` mapping, `GitHubConnection`, `AIPolicy`,
`SensitivePathRule`, `DetectionRule`, `AppSetting`, `UserProjectAccess`. **None of this can be recovered by
re-syncing GitHub** — it is the reason `docs/SETUP.md` insists on backing up `DATA_DIR`.

**The system owns** the derived fields on `PullRequest`, `AISignal`, `PolicyViolation` (except the human `status`,
`resolved_by`, `resolution_comment`), `ChurnResult` and `DailyRollup`. All of it is rebuilt by `manage.py
recompute`.

Two rules the rest of the code depends on:

- **`DailyRollup` stores only additive values** — counters, and the numerator and denominator of a ratio. Medians,
  percentiles, size buckets and state metrics are computed from raw rows on read, because a median of daily
  medians is not the period median. A rollup row is therefore always safe to delete and rebuild. See
  [ADR 0007](adr/0007-hybrid-metrics-additive-rollups-plus-on-read-distributions.md).
- **`PolicyViolation` stores `rule_code` plus `details_params`, never a rendered message.** Same for
  `GitHubConnection.last_check_result` and sync errors. The text is rendered in the reader's language at read
  time, which is what makes the Ukrainian UI total rather than partial.

## Ingestion

`manage.py sync` (or the huey task, or the Sync page button) takes a DB lock row, walks the active connections
round-robin so one rate-limited connection does not stall the others, and for each repository pages GraphQL from
its `last_synced_at` watermark minus `SYNC_OVERLAP`. Each PR's upserts run in one transaction;
`process_pull_request` runs on commit.

- Every write is `update_or_create` keyed on `github_id` or `(repository, sha)`. Re-running a sync produces zero
  new rows — there is a test for it.
- `last_synced_at` advances only after a repository finishes, so a crash re-reads that repository rather than
  skipping a window.
- A nested connection (reviews, comments, commits, files) that cannot be fully paginated fails that PR loudly
  instead of storing a truncated list; a truncated list would quietly corrupt a metric.
- Errors are data, not exceptions: one failed PR does not stop a repository, one failed repository does not stop
  the run, and everything lands masked in `SyncRun.error_log` and `stats_by_connection`. `run_sync` guarantees a
  terminal `SyncRun` status on every escape path, including a killed process whose stale lock is later stolen.
- Post-processing is idempotent by construction: identity resolution is lookup-or-create, derived fields are pure
  functions of stored rows, `AISignal` is unique per `(pull_request, rule, evidence hash)`, and `PolicyViolation`
  is unique per `(pull_request, rule_code, details_hash)`. Auto-resolve only ever moves `open → resolved` and
  marks itself automatic — it never overwrites a lead's `acknowledged` or `waived`.
- `mark_dirty()` records the affected `REPORT_TIMEZONE` dates (including the day of a reverted PR, and of any PR
  whose follow-up-fix flag changed); `rollups.rebuild_dirty()` deletes and rewrites exactly those
  `(date, scope, scope_id, cohort, metric_key)` rows, then `data_version()` is bumped so every cache key changes.

## AI detection: two families, three execution sites

Detection is two rule families that meet only at `resolve_ai_status()`, and never write into each other.

| | **Text rules** (`DetectionRule`) | **Structural signals** (`SignalRule`) |
|---|---|---|
| What it matches | a string a tool wrote — a commit trailer, a session link, a chat-history file in the diff | a shape — commit burst, new-file ratio, an author's own baseline, a wholesale reformat |
| Evidence | `AISignal.evidence`, the matched substring, needing no translation | `evidence_code` + `evidence_params`, rendered as a sentence in the reader's language by `evidence.py` |
| Confidence | up to `high` | never `high` — a `CheckConstraint` on `SignalRule` refuses it |
| Effect on `ai_status` | one `high` signal ⇒ `ai_explicit` | `AI_SUSPECTED_MIN_STRUCTURAL_KINDS` (2) distinct *kinds* ⇒ `ai_suspected`, never further |
| Seeded by | `seed_detection_rules` (`fixtures/detection_rules.yaml`) | `seed_signal_rules` (`fixtures/signal_rules.yaml`), at the repository root |

Why the asymmetry is a decision rather than a tuning choice:
[ADR 0009](adr/0009-structural-signals-never-reach-high-confidence.md).

The two families run at **three different times**, which is why a pull request's status can change without a
re-sync:

1. **On sync**, inside `process_pull_request`: every text detector, plus the per-PR structural kinds
   (`structural.py`), which need only the PR's own commits, files and timestamps.
2. **Nightly**, in `compute_baselines_task` (also `manage.py compute_baselines`, and `recompute --baselines`):
   the author-history kinds in `baselines.py`. They compare an author against their own twelve-week median, so
   they cannot be computed from one pull request in isolation.
3. **In the churn job**, `apps/churn/diffs.py`: the diff-level kinds in `diffsignals.py`, reusing the bare clone
   `blame.py` already made. This is also where `DiffFacts` is produced for the three policy checks that read the
   diff. A repository not in `DIFF_ANALYSIS_REPOSITORIES` simply has no `DiffAnalysis`, and every consumer is
   silent rather than accusing.

Outcome data — churn, reverts, follow-up fixes — is never an input to any of it. It is measured *for* the AI
cohort; feeding it back into deciding who is in the cohort would make the AI-quality dashboards self-proving.
`apps/ai_detection/tests/test_structural.py` asserts it.

## Reads

`metrics.compute()` is the only read entry point. Counters and ratios come from `DailyRollup`; distributions and
state metrics are computed from raw rows for the requested window. Two consequences worth knowing before
changing it:

- **A restricted `ScopeFilter` never reads rollups.** Rollup rows are written once under unrestricted access, so
  reading one back for a narrowed caller would hand them the unrestricted number. `compute()` and `compute_many()`
  both fall back to the calculator for restricted callers. This is slower and correct.
- **`include_series=False`** skips the per-bucket series everywhere. Table rows only read `.value`, `.delta` and
  `.below_min_sample`, and the per-bucket raw-row query was the dominant cost profiling found on the projects,
  repositories and people tables.

A metric computed from fewer than `MIN_SAMPLE` (5) rows is returned flagged, and every surface that shows it —
KPI card, table cell, export — renders it dimmed and labelled "Small sample". A missing value is `None`, never
`0`.

## Cross-cutting

**Authorization.** `LoginRequiredMiddleware` denies by default; login, logout and password reset are the only
`@login_not_required` views, and a parametrized test walks `urlpatterns` asserting the anonymous redirect for
every route. Inside a session, `admin` versus `lead` splits on `catalog.manage_settings`; data splits
horizontally through `scope_for_user()`. Isolation is asserted separately on pages, chart JSON, CSV and XLSX,
because a view permission is not a template permission and neither is an export permission. See
[ADR 0002](adr/0002-session-auth-two-roles-and-one-scope-function.md).

**Secrets.** `config/security.py::mask_secrets()` strips `ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_` and `github_pat_`
shapes, keeping the last four characters; it is installed as a filter on the root logger and used before anything
is written to `SyncRun.error_log`. `git` receives the token through `GIT_ASKPASS` and an environment variable
only — never in a remote URL, `.git/config`, a credential helper, or a process argument. `tests/test_token_leak.py`
searches the on-disk database, the log file, `error_log`, rendered HTML and export files after a *failing* sync.
See [ADR 0004](adr/0004-encrypted-multi-connection-github-credentials.md).

**Exports.** One `ExportColumn` list feeds django-tables2, CSV and XLSX. CSV values starting with `= + - @` are
prefixed with an apostrophe and XlsxWriter runs with `strings_to_formulas=False`; `Person.notes` is excluded by
construction and asserted absent by test; an export past `EXPORT_SYNC_MAX_ROWS` becomes a huey job; a generated
file is downloadable only by its own author; every export writes an `AuditEntry`.

**Front end.** Server-rendered Django templates plus htmx; every htmx endpoint returns a fragment and the same URL
returns a full page for a normal request, chosen by `config/htmx.py::is_htmx()`. Charts are a vendored Chart.js
reading CSS custom properties at runtime and redrawing on `themechange`. Colour literals live only in
`static/css/tokens.css`; dark mode is bound to `[data-theme="dark"]` written on `<html>` by the server. See
[ADR 0006](adr/0006-server-rendered-templates-htmx-and-vendored-chartjs.md) and
[ADR 0008](adr/0008-css-custom-property-tokens-with-committed-tailwind-build.md).

**Internationalization.** `LANGUAGES = [en, uk]`, `LocaleMiddleware`, no `i18n_patterns` — URLs stay
language-free so links are shareable. `UserLanguageMiddleware` copies `UserPreference.language` into the cookie
`LocaleMiddleware` reads. System-generated text is a code plus parameters, rendered at read time. Gates check
`.po` freshness, no empty or fuzzy `msgstr`, matching placeholder sets, and a canary list of English strings that
must not survive into a `uk` render. See [`../TRANSLATIONS.md`](../TRANSLATIONS.md).

**Time.** Storage is UTC. Every day boundary, rollup and report uses `REPORT_TIMEZONE` (`Europe/Kyiv` by default)
through one helper, `apps/metrics/timeframe.py`, tested against 23:59 / 00:01 and a DST transition.

**Background work.** Every huey task is a thin wrapper over a service function that a management command also
calls, so no capability is lost when the worker is not running. One worker thread, because SQLite has one writer.
Tests run with `HUEY.immediate = True`; the two paths that must not be inline assert the enqueue instead. See
[ADR 0005](adr/0005-huey-sqlite-worker-with-every-task-also-a-command.md).

## Testing

`uv run pytest -q` runs `apps/` and `tests/`; `e2e/` is excluded from `testpaths` because it needs a live server.
Beyond per-app tests, `tests/` holds the cross-cutting guards:

| File | What it prevents |
|---|---|
| `test_http_guard.py` | Any unmocked outbound request — the suite fails rather than reaching the network |
| `test_token_leak.py` | A token in the database, a log, `error_log`, HTML or an export |
| `test_scope_isolation.py` | A restricted lead seeing another project through a page, chart, CSV or XLSX |
| `test_urls_login.py` | A route that is reachable anonymously |
| `test_no_hardcoded_colors.py`, `test_css_tokens.py`, `test_token_contrast.py` | A colour outside `tokens.css`, a stale `app.css`, a contrast pair below WCAG AA in either theme |
| `test_translations.py`, `test_model_i18n.py` | An untranslated or fuzzy string, a mismatched placeholder, English surviving into a `uk` render |
| `test_docs.py` | A check code, derived field, rule code, detector, setting or metric key missing from the docs; a broken internal doc link |
| `test_empty_states.py`, `test_min_sample_surfaces.py`, `test_status_not_color_only.py` | A blank page with no explanation, an unlabelled small sample, a status conveyed by colour alone |
| `test_performance.py` | The Overview page exceeding its 1.5 s budget on 50 repositories / 20,000 PRs |

The e2e suite drives a real browser against `config.settings.e2e` (`DEBUG=False`, port 8100, its own database)
with `make e2e-up` / `make e2e-down`. Each plan under `e2e/plans/*.plan.yaml` lists the cases it authored and, under
`deferred_not_authored`, the ones it deliberately did not, each with the non-browser test that covers it instead.

## Divergences from the design of record

`.autodev/ARCHITECTURE.md` was written before the code. Where the two disagree, the code is the truth. The
differences worth knowing:

| Design of record | What shipped | Why |
|---|---|---|
| `process_pull_request(pr: PullRequest)` | `process_pull_request(pull_request_id: int)`, with a `followup` step between derive and detect | The hook runs on commit, so it re-reads the row by id; the follow-up-fix heuristic arrived in phase 10 and is a derived field like the rest |
| `compute(...)` returns rollup-backed counters for everyone | A restricted `ScopeFilter` bypasses rollups entirely, in both `compute()` and `compute_many()` | Rollups are written under unrestricted access; reading one back for a narrowed caller leaked the global number (RISKS row 3) |
| `compute()` only | `compute()` plus `compute_many()` and an `include_series` flag | Phase 11 profiling: one rollup query per table instead of one per row, and no series query for cells that never render a series |
| Settings `base`/`local`/`prod` | Plus `config/settings/e2e.py` | The e2e suite must run with `DEBUG=False` so a real 500 is a real error page, and must not share the developer's database |
| Alpine.js in the front-end stack | Not used — htmx only, plus six hand-written scripts (`theme.js`, `charts.js`, `formatting.js`, `filters.js`, `datepicker.js`, `tagselect.js`) | Nothing in the built UI needed it; adding an unused dependency to the page weight would have been cost without benefit |
| Session-based language storage | `UserLanguageMiddleware` writes `request.COOKIES[LANGUAGE_COOKIE_NAME]` | Django 5.2 removed session-based i18n storage; `LocaleMiddleware` resolves from the cookie |
| `STATICFILES_STORAGE` for manifest static files | A `STORAGES` dict in `config/settings/prod.py` | `STATICFILES_STORAGE` is a no-op on this Django version — it was verified to have zero effect |
| Spec §9's `churn/service.py` | `apps/churn/services.py` | Every other app spells it `services.py` |
| Spec §9.6: a PR with zero attributable lines gets no `ChurnResult` row | A settled row: `status=ok`, `lines_at_merge=0`, `churn_ratio=None` | With no row the PR stays eligible forever and is re-cloned and re-blamed on every nightly run; `churn_ratio=None` still means no fabricated `0%` |
| `merge_method == "unknown"` is unsupported | Resolved from the clone: `git rev-list --parents -n 1` on the merge commit, ≥2 parents ⇒ merge commit, else squash | GitHub omits the field often enough that treating it as unsupported would erase most of the churn metric |
| `followup_fix_rate` computed inside the metric | Materialised into `PullRequest.has_followup_fix` at sync/recompute time | The metric is a ratio evaluated inside the rollup rebuild; a `PRFile`×`PRFile` self-join per day per cohort there would be the slowest thing in the run |

The full, terse log of every such call is `.autodev/DECISIONS.md`; the curated human-facing subset is
[`../DECISIONS.md`](../DECISIONS.md).

## Known rough edges

- `apps/dashboards/templates/dashboards/partials/table.html` wraps tables in `overflow-x-auto`, which makes that
  div the nearest scrollport and silently disables the `sticky top-0` on the header row. Nothing tests it. Either
  drop the now-inert `sticky`, or give the wrapper an explicit max height plus `overflow-y: auto`.
- `conftest.py::large_scale_seed` is module-scoped behind a session refcount, intending to seed the 20,000-PR
  dataset once. Module fixture lifetimes never overlap, so it seeds twice — about 8 of the roughly 13 minutes
  a full `uv run pytest -q` takes over 1,841 tests. Making it session-scoped with a
  `pytest_sessionfinish` teardown fixes it. The `slow` marker only covers `tests/test_performance.py`, so
  `-m 'not slow'` still pays one seed through `apps/dashboards/tests/test_seed_demo_scale.py`.
- No live GitHub call has ever been made by this codebase, in tests or otherwise. Every GitHub behaviour is
  exercised against JSON fixtures under `tests/fixtures/github/`. The first real sync is therefore also the first
  real schema check — `docs/GITHUB_CONNECTIONS.md` has the first-run checklist.

## ADR format

One decision per file, `adr/NNNN-slug.md`, with `Status` and `Date`, then Context / Decision / Consequences /
Alternatives considered. Write one when a choice is expensive to reverse: a storage shape, an authorization model, a
dependency the whole UI rests on. Narrower calls go in [`../DECISIONS.md`](../DECISIONS.md) instead.
