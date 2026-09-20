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
| `REPORT_TIMEZONE` | `Europe/Kyiv` | The timezone dashboards, rollups and reports use for day boundaries. Storage stays UTC (`TIME_ZONE = "UTC"`, `USE_TZ = True`); consumed by the day-boundary helper `apps/metrics/timeframe.py`. |
| `FIELD_ENCRYPTION_KEYS` | empty list | Comma-separated Fernet keys for encrypting GitHub connection tokens at rest. The first key encrypts; every key in the list can decrypt, which is how `manage.py rotate_encryption_key` rotates without downtime. Required before creating a connection — see `docs/GITHUB_CONNECTIONS.md`. |
| `STORE_RAW_PAYLOADS` | `False` | When true, sync stores the raw GitHub GraphQL response alongside the derived fields (more disk, easier debugging). |
| `GITHUB_API_BASE_URL` | `https://api.github.com` | REST/GraphQL host GitHub sync talks to; `GITHUB_GRAPHQL_URL` is derived by appending `/graphql`. Deployment wiring (not operator policy, see `docs/GITHUB_CONNECTIONS.md`) — the one thing to change for GitHub Enterprise Server. |
| `E2E_ADMIN_PASSWORD` | `admin-password-change-me` | Password `manage.py seed_e2e` sets for the `e2e-admin` persona. Only read by that command, under `config.settings.e2e`. |
| `E2E_LEAD_PASSWORD` | `lead-password-change-me` | Password `manage.py seed_e2e` sets for the `e2e-lead` persona. |

## App settings (operator-editable)

Unlike the `.env` keys above, these live in the database (`catalog.AppSetting`), are seeded from the frozen
registry `apps/catalog/setting_defs.py` by the data migration `catalog/0002_app_setting_defaults.py`, and are
meant to be changed at runtime — through the Django admin (`/admin/` → Catalog → App settings) — rather
than by editing a file. The settings that have a dedicated page of their own (the AI policy, sensitive paths,
detection rules) are listed under Settings in the top bar instead. Code reads them
through `apps/catalog/services.py` (`get_setting`/`get_int`/`get_bool`/`get_str`/`get_list`/`get_dict`), which
raises `UnknownSettingError` for a typo'd key and falls back to the code default when a registered key's row is
missing.

| Group | Key | Type | Default | Meaning |
|---|---|---|---|---|
| sync | `BACKFILL_DAYS` | int | `180` | How many days of history a first sync pulls in. |
| sync | `DEFAULT_CONNECTION_KIND` | str | `fine_grained_pat` | Connection kind preselected when creating a new GitHub connection. |
| sync | `CONNECTION_CHECK_INTERVAL_HOURS` | int | `24` | How often a connection's health is re-checked. |
| sync | `SYNC_OVERLAP_MINUTES` | int | `60` | Minutes a sync re-reads before the last watermark, to absorb late-arriving updates. |
| sync | `SYNC_PR_PAGE_SIZE` | int | `50` | Pull requests fetched per GraphQL page. |
| sync | `SYNC_NESTED_PAGE_SIZE` | int | `100` | Items fetched per page for a pull request's nested connections (reviews, commits, files, …). |
| sync | `RATE_LIMIT_MIN_REMAINING` | int | `200` | Primary rate-limit remaining below which the client waits for reset. |
| sync | `SYNC_MAX_RETRIES` | int | `5` | Maximum retry attempts for a transient request failure. |
| sync | `SYNC_RETRY_MAX_SECONDS` | int | `60` | Maximum backoff delay between retry attempts. |
| sync | `SYNC_LOCK_STALE_MINUTES` | int | `360` | Minutes after which an unreleased sync lock is considered stale and stolen. |
| sync | `TOKEN_EXPIRY_WARNING_DAYS` | int | `14` | Days before token expiry at which the admin banner starts warning. |
| sync | `CONNECTION_RECHECK_MIN_MINUTES` | int | `60` | Minimum minutes between two non-forced verifications of the same connection. |
| ai | `AI_COHORT_INCLUDE_SUSPECTED` | bool | `False` | Whether PRs with a merely suspected AI status count in the AI cohort. |
| ai | `BOT_LOGIN_SUFFIXES` | list | `["[bot]"]` | Login suffixes that mark an account as a bot. |
| ai | `BOT_LOGINS` | list | `["dependabot", "renovate", "github-actions", "copilot-pull-request-reviewer", "charliecreates", "charliehelps"]` | Exact logins that mark an account as a bot. |
| ai | `AI_SUSPECTED_MIN_STRUCTURAL_KINDS` | int | `2` | How many distinct structural signal kinds a pull request needs before its AI status becomes 'suspected'. Counted over kinds, not rows: five commit bursts are one kind of evidence. No number of structural signals ever reaches 'explicit'. |
| ai | `AI_TOOLING_PATH_GLOBS` | list | `.agents`, `.claude`, `.claude/skills`, `AGENTS.md`, `CLAUDE.md`, `.mcp.json`, `.github/copilot-instructions.md`, `.github/agents`, `.cursor`, `.cursorrules`, `.windsurfrules`, `.codex`, `.gemini`, `GEMINI.md`, `.aider.conf.yml`, `.specstory` | Paths that mark a repository as configured for an AI agent. Matched against the repository's root tree, plus one level inside any directory a pattern names. |
| ai | `DISCLOSURE_SECTION_HEADINGS` | list | `["AI assistance"]` | PR template section headings that hold the AI disclosure. |
| ai | `DISCLOSURE_LABELS_NONE` | list | `["None"]` | Disclosure values meaning no AI assistance. |
| ai | `DISCLOSURE_LABELS_PARTIAL` | list | `["Partial"]` | Disclosure values meaning partial AI assistance. |
| ai | `DISCLOSURE_LABELS_SUBSTANTIAL` | list | `["Substantial"]` | Disclosure values meaning substantial AI assistance. |
| ai | `DISCLOSURE_TOOLS_LABELS` | list | `["AI tools used"]` | PR template section headings that list the AI tools used. |
| ai | `DETECTION_DRY_RUN_PR_COUNT` | int | `50` | How many of the most recent PRs a detection rule dry run scans. |
| ai | `DISCLOSURE_TOOL_ALIASES` | dict | `{"claude_code": ["claude code", "claude"], "copilot": ["copilot", "github copilot"], "cursor": ["cursor"], "codex": ["codex"], "devin": ["devin"], "gemini": ["gemini"], "aider": ["aider"], "windsurf": ["windsurf"], "chatgpt": ["chatgpt", "gpt"]}` | Maps free text in the PR template's "AI tools used" line onto canonical `Tool` values; anything unmatched is kept as lowercased raw text. |
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
| metrics | `METRICS_CACHE_TTL_SECONDS` | int | `3600` | How long a `compute()` result stays cached before it expires on its own (a bumped `last_data_version` invalidates it sooner). |
| metrics | `DEFAULT_PERIOD_DAYS` | int | `30` | Default number of days a dashboard period covers when none is chosen. |
| policy | `NO_TESTS_MIN_LINES` | int | `20` | Minimum changed lines before a missing-tests violation applies. |
| policy | `POLICY_DISABLED_RULES` | list | `[]` | Rule codes switched off entirely; an open violation for a disabled code auto-resolves on the next run. An unrecognised code is ignored with a logged warning. See `docs/POLICY.md`. |
| policy | `POLICY_VIOLATION_PATHS_IN_PARAMS` | int | `20` | Maximum file paths stored in a sensitive-path violation's `details_params`; the real total is kept separately as `path_count` so a large PR can't write an oversized row. |
| policy | `VIOLATIONS_PAGE_SIZE` | int | `50` | Rows per page on the Policy console's violation table. |
| churn | `CHURN_WINDOW_DAYS` | int | `21` | Days after merge over which churn is measured. |
| churn | `CHURN_MAX_FILES` | int | `50` | Maximum files in a PR before churn analysis is skipped. |
| churn | `CHURN_MAX_WORKERS` | int | `4` | Maximum concurrent git workers a churn run uses. |
| churn | `CHURN_GIT_TIMEOUT_SECONDS` | int | `120` | Timeout in seconds for a single git subprocess call during churn analysis. |
| churn | `CHURN_REPO_TIME_BUDGET_SECONDS` | int | `600` | Maximum seconds a churn run spends on a single repository before deferring the rest. |
| churn | `DIFF_ANALYSIS_REPOSITORIES` | list | `[]` | Repositories (`owner/name`) whose diffs are analysed for structural AI signals during the nightly churn run; `"*"` opts in every repository, and the empty default runs no diff analysis at all. |
| ui | `DASHBOARD_TABLE_PAGE_SIZE` | int | `25` | Rows per page on a dashboard table (projects, repositories, people, recent PRs). |
| ui | `PR_FILES_DISPLAY_LIMIT` | int | `300` | Maximum files shown on the PR detail page before a "+N more" line. |
| ui | `REVIEW_HEATMAP_TOP_N` | int | `15` | Authors/reviewers shown per axis on the reviews heat map; the rest fold into "Other". |
| ui | `DEFAULT_UI_LANGUAGE` | str | `en` | Default UI language for a new user. Kept in sync with `UserPreference.language`'s field default by a test. |
| ui | `DETECT_BROWSER_LANGUAGE` | bool | `False` | Whether to derive the initial UI language from the browser. |
| ui | `DEFAULT_THEME` | str | `system` | Default UI theme for a new user. Kept in sync with `UserPreference.theme`'s field default by a test. |
| export | `EXPORT_DURATION_UNIT` | str | `hours` | Unit durations are rendered in for CSV/XLSX exports. |
| export | `EXPORT_SYNC_MAX_ROWS` | int | `20000` | Maximum rows a synchronous export may return. |
| export | `EXPORT_RETENTION_DAYS` | int | `7` | Days a background export file is kept before cleanup deletes it. |

### Reading `MIN_SAMPLE`, `STALE_DAYS` and the UI defaults

`MIN_SAMPLE` (default 5) is not a cosmetic threshold — it decides when a rate or median is greyed with the `≈`
small-sample marker across every KPI card, table cell and comparison row (see `docs/POLICY.md`'s reading notes
and CLAUDE.md's `MIN_SAMPLE` rule). Lowering it makes more small-population numbers appear "confident" than the
statistics actually support; raising it greys out more of the dashboard, especially at the repository/person
level where sample sizes are naturally small. Change it only with that trade-off in mind, and expect the change
to be visible immediately (it bumps `last_data_version`) without needing `manage.py recompute`.

`STALE_DAYS` (default 5) feeds the "stale" flag on an open PR with no recent activity, used by the flow metrics
and the PRs list filter — it is a judgement call about your team's own cadence, not a GitHub-derived constant;
a team that reviews slowly by design will see everything flagged stale at the default. Unlike `MIN_SAMPLE`,
`STALE_DAYS` changes rollup-derived counters, so run `manage.py recompute` after changing it to make historical
rows agree with the new definition (see the note above the settings-modules section).

`DEFAULT_UI_LANGUAGE` (`en`) and `DEFAULT_THEME` (`system`) only apply to a **new** `UserPreference` row — an
existing user's own saved choice always wins, so changing either default has no effect on anyone who has already
logged in and picked a theme/language once.

Changing any `metrics`-group setting bumps `last_data_version`, so `compute()`'s cache stops serving pre-change
results immediately (this is what keeps a `MIN_SAMPLE` change visible right away). It does **not** rewrite
`DailyRollup` rows already on disk: `AI_COHORT_INCLUDE_SUSPECTED` decides which PRs land in the `ai`/`non_ai`
cohort rollups, and `PR_SIZE_BUCKETS`/`REVIEW_LOAD_TOP_N`/`STALE_DAYS`/`WAITING_REVIEW_HOURS` feed calculators
whose stored counters/ratios were computed under the old value — after changing one of these, run
`manage.py recompute` to rebuild history under the new setting.

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
