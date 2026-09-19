# Progress

What exists in the codebase right now, kept current as phases land. For the full phase-by-phase plan see
`.autodev/ROADMAP.md`; for the reasoning behind a specific choice see `docs/DECISIONS.md` and `docs/dev/adr/`.

## Phase 1 — Skeleton, auth, i18n and design tokens

Delivers a runnable, logged-in, bilingual, themed empty application. Everything below works from a clean
checkout with `uv sync && uv run python manage.py migrate`.

- **Project shell**: `pyproject.toml`/`uv.lock`, `manage.py` (defaults to `config.settings.local`),
  `config/settings/{base,local,prod,e2e}.py`, SQLite in WAL mode with `busy_timeout=5000` and
  `foreign_keys=ON` (`config/db.py`), huey queued on its own `DATA_DIR/huey.sqlite3` file.
- **Secrets**: `config/security.py::mask_secrets` + `config/logging_filters.py::SecretMaskingFilter` on the root
  logger, masking every GitHub token shape before it can reach a log line.
- **`accounts` app**: `UserPreference` (theme, language), `AuditEntry` (actor, code, before/after JSON),
  `ScopeFilter`/`scope_for_user()` (unrestricted in this phase — every future selector already takes one),
  `record_audit`/`set_theme`/`set_language` services, the `admin`/`lead` groups (empty until phase 2).
- **Auth**: Django's login/logout/password-reset views and templates, `LoginRequiredMiddleware` so every URL
  except that allowlist redirects an anonymous visitor to `/accounts/login/`.
- **`dashboards` app**: a single placeholder Overview page at `/`, the post-login landing page and the home
  the navigation points at until later phases add real pages.
- **Theme**: `UserPreference.theme` → cookie → `system` resolution order, a context processor, a pre-paint
  inline script in `<head>` so there is no flash, `static/js/theme.js` wiring the switcher.
- **i18n**: English + Ukrainian with `LocaleMiddleware` and a custom `UserLanguageMiddleware` that makes the
  stored preference win over `Accept-Language`; `JavaScriptCatalog` at `/jsi18n/`; a duration formatter
  implemented once in Python (`apps/dashboards/formatting.py`) and once in JS
  (`static/js/formatting.js`), proven to agree via the shared fixture `tests/fixtures/duration_cases.json`.
- **Design tokens**: `static/css/tokens.css` is the only file allowed a colour literal (enforced by a grep
  test); Tailwind v4 standalone build, committed `static/css/app.css`.
- **Gates**: `uv run pytest -q` (unit/integration), the lint chain in `CLAUDE.md`, and
  `make e2e-up && uv run pytest e2e -q; make e2e-down` (login, theme persistence, language persistence, and
  JS/Python duration-formatter parity, driven with Playwright against `config.settings.e2e`).

### Out of scope for phase 1

Every domain model (`catalog`, `activity`, `ai_detection`, `policy`, `metrics`, `churn`, `connections`,
`github_sync`), any GitHub client code, real navigation targets beyond Overview, charts, tables and exports.
See `.autodev/phases/01-skeleton/PLAN.md`'s "Out of scope" section for the full list and which phase owns each.

## Phase 2 — Domain models, migrations and Django admin

Every table in spec §4 now exists, migrates from zero, and is visible in `/admin/` with the right read-only and
hidden fields. No new URL, template or page — the only human surface added is the admin.

- **Eight new apps**: `connections`, `catalog`, `activity`, `ai_detection`, `policy`, `metrics`, `churn`,
  `github_sync`, each with `models.py`, `admin.py`, `factories.py` and its own `migrations/0001_initial.py`
  (`policy` additionally ships `0002_policyviolation` after `activity.0001`, since the FK graph between the two
  apps is mutual).
- **`connections.GitHubConnection`**: the column set for a PAT or GitHub App connection, including
  `token_encrypted`/`private_key_encrypted` as `BinaryField`s with no plaintext column. Encryption itself and the
  GraphQL client land in the next phase.
- **`catalog`**: `Organization`, `Repository`, `Project` (with `color` as a chart-token name, not a hex literal),
  `Person`, `Identity` (uniqueness enforced after `normalize.py::normalize_identity_value` casefolds and strips
  the value), and `AppSetting` backed by the frozen `setting_defs.py::SETTING_DEFS` registry, seeded by a data
  migration and read through `catalog/services.py`'s typed accessors (`get_setting`/`get_int`/`get_bool`/…).
- **`activity`**: `PullRequest`, `Commit`, `PullRequestCommit`, `PRFile`, `Review`, `ReviewComment`,
  `CheckStatus` — the raw GitHub fields plus the derived-cache columns later phases fill in (`ai_status`,
  `size_bucket`, `is_rubber_stamp`, …), all defaulting to `None`/`unknown` rather than `0`/a rendered value.
- **`ai_detection`**: `DetectionRule`, `AISignal` (no uniqueness constraint — one rule may legitimately match
  two commits of a PR; re-running detection deletes and rewrites a PR's signals).
- **`policy`**: `AIPolicy` (versioned, resolved by `policy/services.py::current_policy()`), `SensitivePathRule`,
  `PolicyViolation` with `(pull_request, rule_code, details_hash)` uniqueness and the canonical
  `policy/services.py::details_hash()` (sha256 over a `sort_keys=True` JSON dump) that makes the constraint
  actually catch a re-evaluated duplicate.
- **`metrics.DailyRollup`**: additive-only rollup rows, with the documented unique constraint plus a second
  **partial** constraint for `scope_id IS NULL` rows (SQL treats two NULLs as distinct, so the global scope needs
  its own index to stay duplicate-free).
- **`churn.ChurnResult`**, **`github_sync.SyncRun`**: columns only, per spec §4; no service reads or writes them
  yet.
- **`accounts.UserProjectAccess`** (`(user, project)` unique) and the `admin` group's new
  `catalog.manage_settings` permission; `scope_for_user()` itself is deliberately left unrestricted — narrowing
  it is the scoped-access phase's job.
- **Admin**: `config/admin.py::ReadOnlyAdminMixin` makes every GitHub-owned model's changelist read-only (200 on
  list, 403 on add); `GitHubConnectionAdmin` excludes both encrypted fields and disables add entirely;
  `Person.notes` is excluded from every list display.
- **Factories**: one `factory.django.DjangoModelFactory` per concrete model (`tests/test_factories.py` enforces
  this by walking `django.apps.apps.get_models()`), each producing a `full_clean()`-valid instance.
- **i18n**: every `verbose_name` and choice label is `gettext_lazy`, enforced by `tests/test_model_i18n.py`;
  Ukrainian translations for all of it are in the same phase.
- **Docs**: `docs/CONFIGURATION.md` gained the full `AppSetting` reference table.

### Out of scope for phase 2

Token encryption, connection verification, the GraphQL client, `sync`/`SyncRun` writing, `bootstrap_connection`,
identity auto-mapping and bot detection, `fixtures/detection_rules.yaml` and the eight AI detectors, the nine
policy evaluators and the violations console, the `MetricDef` registry and `compute()`, the churn service and
`compute_churn`, every dashboard page/chart/table/export, and narrowing `scope_for_user()` by
`UserProjectAccess`. See `.autodev/phases/02-data-model/PLAN.md`'s "Out of scope" section for which later phase
owns each.

## Phase 3 — GitHub connections and incremental sync

Everything between a token in an admin's hands and rows in `activity`. No live GitHub call anywhere — the whole
phase is built and tested against fixtures in `tests/fixtures/github/`, mocked with `respx`; `conftest.py` fails
the suite on any unmocked outbound request.

- **Credentials at rest** (`connections/crypto.py`, ADR 0004): `MultiFernet` encryption keyed by
  `FIELD_ENCRYPTION_KEYS`, `services.py::set_token`/`plaintext_token` as the only two callers of
  `encrypt_token`/`decrypt_token` (enforced by a grep test), `manage.py rotate_encryption_key` (`--dry-run`
  supported) to re-encrypt every row under key #0 after a key rotation.
- **The `GitHubAuth` protocol** (`connections/auth.py`): `PATAuth` for `fine_grained_pat`/`classic_pat`, with a
  `__repr__`/`__str__` override so a token can never reach a traceback's rendered locals.
- **The GraphQL/REST client** (`github_sync/client.py`, `errors.py`, `rate_limit.py`, `queries.py`): typed
  errors (`GitHubAuthError`, `GitHubSSOError`, `GitHubSchemaError` naming the full JSON path, …),
  `errors.require`/`optional` (missing optional fields map to `None`, never `0`), per-connection `RateBudget`,
  retries with jittered exponential backoff honouring `Retry-After`, and pagination that also follows **nested**
  connections (reviews, commits, files, review threads) past their first page — a truncated nested page raises
  rather than silently under-counting.
- **Connection verification** (`connections/services.py::verify_connection`, `check_codes.py`): stores codes +
  parameters only, never a rendered message, so `last_check_result` renders in the reader's language forever;
  throttled to once an hour unless `force=True`. The expiry/invalid banner
  (`connections/context_processors.py::connection_alerts`) is admin-only and costs one query.
- **Sync** (`github_sync/services.py::run_sync`, `sync_repository`, `mappers.py`, `upserts.py`,
  `pipeline.py::process_pull_request`): a per-repository watermark with `SYNC_OVERLAP_MINUTES` overlap, one
  `transaction.atomic()` per pull request, a round-robin scheduler across connections so one connection waiting
  on its rate limit doesn't stall the others, a `SyncLock` (unique-row mutex with a stale-lock steal — SQLite
  doesn't honour `select_for_update`) guaranteeing one sync at a time, and per-connection quarantine: a 401
  marks a connection `invalid` and skips only its repositories, a 403+SSO marks it `degraded`, and the run
  continues for every other connection either way. `process_pull_request()` is defined, called exactly once per
  synced PR after its transaction commits, and does nothing yet — phases 4–6 fill it in without reopening the
  orchestrator.
- **Surfaces**: Settings → Connections (list/create/edit/check/deactivate/delete), Settings → Repositories
  (discovery grouped by owner, archived hidden by default, rebind-with-confirm), and the Sync page (manual
  "Sync now" via huey, a polled status fragment, the run history with masked per-connection stats). Every one of
  these returns a fragment for `HX-Request` and a full page otherwise; mutations are POST; a failed check or an
  attempted delete of a connection with repositories renders a visible message, never a blank page.
  `manage.py sync` exposes the same orchestrator with `--repo`/`--project`/`--since`/`--full`.
- **`bootstrap_connection`**: creates a `"Default (.env)"` connection from `GITHUB_TOKEN` on first run, status
  `unverified`, no GitHub call unless `--verify` is passed explicitly.
- **The leak test** (`tests/test_token_leak.py`, spec §12): after a sync that fails partway, walks every table
  via DB introspection, greps the raw on-disk SQLite file, the log file, `SyncRun.error_log` and three rendered
  pages, asserting the token (beyond its last 4 characters) is nowhere.
- **i18n**: every string this phase added has a Ukrainian translation in the same phase (`tests/test_translations.py`
  and a canary-string render test are the gate).
- **Docs**: `docs/GITHUB_CONNECTIONS.md` (token types/scopes, first-run checklist, every verification code and
  what to do about it, rotation, recovery from a lost `FIELD_ENCRYPTION_KEYS`, rebinding), `docs/user/connect-github.md`.

### Out of scope for phase 3

Identity → `Person` mapping, bot detection, the unmapped-identity queue (`activity.derive`) → phase 4. AI
detection, policy evaluation, dirty-day marking and rollups → phases 5–7 (all four are stages of
`process_pull_request()`, which ships empty here by design). `manage.py recompute`/`compute_churn`/`seed_demo`/
`metrics_doc`/`seed_detection_rules`, the PR/repository/person detail pages, tables, filters, CSV/XLSX export,
`GitHubAuth` for GitHub Apps, scoped selectors and `UserProjectAccess` enforcement (`scope_for_user()` stays
unrestricted), `git` credential handling via `GIT_ASKPASS`, webhooks and scheduled daily verification as a cron
entry. See `.autodev/phases/03-connections-and-sync/PLAN.md`'s "Out of scope" section for which later phase
owns each.

## Phase 10 — CI first-pass, follow-up fixes and churn

The three remaining quality metrics from `docs/METRICS.md`'s row 3 (`ci_first_pass_rate`, `followup_fix_rate`,
`churn_21d`) are all real now, not placeholders.

- **`followup_fix_rate`**: `PullRequest.has_followup_fix` (`apps/activity/followup.py`), a derived boolean
  materialised at sync/`recompute` time — true when a later merged PR in the same repository matches the
  `is_hotfix` title/branch pattern within `FOLLOWUP_FIX_WINDOW_DAYS` and shares at least
  `FOLLOWUP_FIX_FILE_OVERLAP` of this PR's non-excluded files. The `RatioCalc` reads the flag directly, so its
  rollup batch stays a grouped `COUNT`, never a `PRFile`×`PRFile` join. Surfaced, labelled "(heuristic)", on the
  Person page's comparison table.
- **`ci_first_pass_rate`**: unchanged production code from phase 7; this phase added an explicit positive/negative
  test pair and a rollup round-trip proof.
- **Churn** (`apps/churn`, spec §9, see `docs/user/churn.md`): the only component that shells out to `git`
  directly. `askpass.py` hands a GitHub token to git through `GIT_ASKPASS` + two env vars only — never argv,
  never the remote URL, never `.git/config` (`GIT_CONFIG_NOSYSTEM`/`GIT_CONFIG_GLOBAL=/dev/null` keep the
  operator's own credential helper out); `gitcmd.run_git()` is the one `subprocess` call site, masking stderr
  before it reaches an exception, a log, or `ChurnResult.error`. `clones.py` keeps one disposable bare clone per
  repository under `DATA_DIR/repos/<owner>/<name>.git`, re-cloned once on a failed fetch. `blame.py` sums
  `git blame --line-porcelain -M -C` for a PR's own commits at merge and again at a snapshot
  `CHURN_WINDOW_DAYS` later, following renames; `services.py::compute_churn_for_pull_request` turns that into a
  churn ratio with four terminal-or-retried statuses (`ok`, `unsupported_merge_method` for rebase merges,
  `too_large` past `CHURN_MAX_FILES`, `error` retried on the next run); a PR with zero attributable lines still
  gets a settled `ok` row with `churn_ratio=None` rather than no row at all, so it is never re-cloned and
  re-blamed forever, and never shown as a fabricated `0%`. `run_churn` threads git work across repositories
  (`CHURN_MAX_WORKERS`) while keeping every
  `ChurnResult` write on the main thread — the one place in the system this parallel against SQLite's
  single-writer model. Runs nightly at 02:00 (`compute_churn_task`) and via `manage.py compute_churn`.
- **PR detail page**: the Churn section is now status-aware — a percentage, or one of four sentences explaining
  why there isn't one — never a colour-only signal and never a fake `0%`.
- Every new string has its Ukrainian translation in this phase.

### Out of scope for phase 10

A UI trigger for churn (CLI/nightly only, per spec), rebase-merge churn (the pre-rebase history GitHub
garbage-collects), a `FollowupFix` link model naming *which* PR fixed which, clone garbage collection/disk caps
for `DATA_DIR/repos` (documented as an operator concern instead). Empty-state/`MIN_SAMPLE` polish, the contrast
audit, and `seed_demo --scale` → phase 11.

## Phase 11 — Polish, performance and documentation

The last phase. No new domain behaviour; it closes out empty states, the small-sample marker, colour contrast,
dashboard performance at realistic scale, the Ukrainian long-string layout, and the documentation set.

- **Empty states**: one shared partial (`templates/partials/empty_state.html`) and a `{% small_sample_note %}`
  tag back every list-shaped page and chart card. Dashboard, Overview, Reviews and Person pages distinguish
  "nothing has ever been synced" from "nothing matches this period/filter" via
  `apps/dashboards/selectors.py::period_has_pull_requests()`, never by inspecting a KPI's value — a real zero
  and "no data" render differently. Ten previously-bare list views (catalog people/identities, detection rules,
  connections list/discovery, sync runs, policy versions/sensitive paths/violations, exports) gained an
  explanation and, where there's an obvious next step, an action link.
- **Small-sample marker reaches tables, not just KPI cards**: `rows.py::_metric_row()` now carries a
  `f"{key}__low"` flag per cell, and `tables.py` renders a compact `≈` glyph (`data-testid="cell-small-sample"`,
  with a `title`/`aria-label`, so the signal is never colour-only) — distinct from the fuller "Small sample"
  sentence `{% small_sample_note %}` renders on a KPI card. A regression sweep (`tests/test_min_sample_surfaces.py`)
  pins the exact set of templates/modules allowed to render a `MetricResult`'s below-sample state.
- **WCAG-AA contrast retune** (`tests/test_token_contrast.py`, ADR 0008): two new tokens, `--border-strong`
  (a control's own boundary, as opposed to a decorative divider, which keeps `--border`) and `--on-heat` (text
  colour against a heat-map cell), plus retuned light-theme `accent`/`neutral`/`good`/`warning`/`heat-3` hex
  values — every foreground/background pair in the token set now clears 4.5:1 (text) / 3:1 (UI boundaries) in
  both themes, with two named, tested exemptions (`--border`/`--grid` decorative lines, `--heat-1..4` fills
  below the marker text's own contrast requirement).
- **`seed_demo --scale {demo,large}`** (`apps/dashboards/management/commands/seed_demo.py`): `large` seeds 50
  repositories / 20,000 pull requests via a bulk write path in a separate, never-colliding namespace from the
  existing demo scale, purely to have realistic volume to profile dashboards against; a second run without
  `--reset` raises rather than mixing scales.
- **Dashboard performance**: `scripts/profile_dashboard.py` (Django test client + `CaptureQueriesContext`,
  wall time and the ten slowest SQL statements per page) measured the Overview page at 29.4s / 5,407 queries
  cold on `--scale large` data — 20x over the architecture's 1.5s budget. Three independent root causes, found
  by re-profiling after each fix rather than guessing: (1) every distribution/state metric built a per-bucket
  `SeriesPoint` even where nothing read `.series` — a new `include_series=False` path through
  `apps/metrics/services.py` and every dashboard call site that doesn't render a sparkline; (2) the People
  table's `compute_many()` fallback cost one `compute()` call *per person* for 5 metrics — a new batched
  `period_by_person`/`at_date_by_person` calculator interface (`PeriodContextMany`/`DayContextMany` in
  `apps/metrics/calculators/base.py`) cut that to 2 queries per metric regardless of person count; (3)
  `duration_hours()` re-read the `DURATION_MODE` setting from a pickled cache on every one of ~129,000 row
  iterations for a value it always discards. Cold Overview now renders in roughly 1.3-1.5s at the same 50
  repos/20,000 PRs, with every `assertNumQueries` pin moved and its new count justified in-place.
- **Ukrainian long strings don't clip**: KPI card titles, buttons and table headers gained `break-words`/
  `hyphens-auto`; metric table column headers lost `whitespace-nowrap`, which is what clipped a long Ukrainian
  header. `django.po` had a terminology/casing proofread pass. `tests/test_pages_smoke.py` now sweeps every
  named page (not just the 12 dashboard ones) for both roles, and `CANARY_ENGLISH_STRINGS` grew to match.
- **Docs**: this phase's own documentation debt — `docs/SETUP.md` (Scheduling via launchd/cron, backup
  verification and the `FIELD_ENCRYPTION_KEYS` warning, Future deployment, Performance), `docs/CONFIGURATION.md`
  (`MIN_SAMPLE`/`STALE_DAYS`/theme-language reading notes), `docs/GITHUB_CONNECTIONS.md` (rate-limit budget),
  `docs/TRANSLATIONS.md` (long-string rule, canary lists), `docs/POLICY.md` (reading a greyed number,
  auto-resolve), `docs/user/index.md` and `docs/user/troubleshooting.md` (both linked from `README.md`), and
  `tests/test_docs.py`'s path/link-resolution regression tests — closed out in a review round-1 follow-up
  rather than the original implementation session; see `.autodev/DECISIONS.md`'s `p11/apply-review` entries for
  what was inferred versus found explicit in the code.

### Out of scope for phase 11

A `DistributionCalc`/`StateCalc` execution-strategy redesign that would let a metric column's sort work without
computing every row first (documented as a follow-up in `.autodev/DECISIONS.md` rather than attempted — the
current fix set already meets the 1.5s budget without it). Fact-table denormalisation for state metrics (ADR
0007's escape hatch, not needed). A fourth language. Multi-node deployment (documented in `docs/SETUP.md`'s
Future deployment section as explicitly out of scope for v1, not implemented).
