# Roadmap — PR Radar

Spec: `docs/SPEC.md` · generated 2026-09-17 07:32:09

PR Radar is a local Django 5.2 tool that pulls pull-request data from GitHub's GraphQL API into SQLite and shows team leads dashboards on AI adoption, AI-policy compliance, and delivery quality at the global, project, repository and person level. The roadmap keeps spec §14's own eleven phases (0–10) in their original order: skeleton and auth, domain models, encrypted connections plus fixture-driven sync, identity resolution and derived PR fields, AI detection, the policy engine, the metrics registry, dashboards and charts, the remaining pages with scoped access and exports, CI/churn, and finally polish and performance. The order is dependency-correct and front-loads the data layer, which is also the mitigation for the run's time-ceiling risk. Every phase ends with the same gate — ruff check, ruff format --check, mypy, makemigrations --check, manage.py check, pytest — and every UI string introduced in a phase gets its Ukrainian translation in that same phase. No live GitHub call is made at any point; all GitHub behaviour is exercised against JSON fixtures mocked with respx, and conftest.py fails the suite on any unmocked outbound request.

**Test command:** `uv run pytest -q`

## Assumptions

- The roadmap keeps spec §14's eleven phases (0-10) rather than compressing to 3-10, because intake settled scope as 'all phases 0 through 10, in order' and merging any pair would exceed one focused session.
- `uv run pytest -q` cannot be executed yet: the repository contains only `.autodev/`, `.git/` and `docs/` — no `manage.py`, no `pyproject.toml`. `uv --version` returns `uv 0.11.21`, so the runner is present; phase 0 makes the command work on a clean checkout.
- Phases 1 (data-model) and 6 (metrics-registry) are not user-facing: they ship migrations plus Django admin, and a registry plus a management command. Every other phase ships a page or flow a lead operates.
- Phase 3 owns identity resolution and derived PR fields together, as spec §14 groups them — the derived fields (rubber stamp, self-merge, review rounds) all need 'not the author, not a bot'.
- `manage.py seed_e2e` is a separate phase-0 command from `seed_demo` (phase 7), because `make e2e-up` in PROFILE.md calls it from phase 0.
- `seed_demo --scale` for the 50-repo / 20 k-PR performance target is added in phase 10, not phase 7 — phase 7 needs readable data, phase 10 needs volume.
- Spec §10.6's two export types split across phases: table CSV/XLSX export in phase 7 with the tables it exports; the multi-sheet dashboard report and `ExportJob` background exports in phase 8.
- `ci_first_pass_rate` and `followup_fix_rate` are registered as MetricDefs in phase 6 returning `None`, and computed in phase 9 — otherwise the registry has holes and the `metrics_doc` freshness test breaks.
- Leak and isolation guarantees are asserted in the phase that introduces each exit: token leak in phase 2 (DB, log, error_log, page) and phase 9 (git argv, remote URL, .git/config); project isolation in phase 8 across pages, chart JSON, CSV and XLSX separately.
- `docs/METRICS.md` is generated in phase 6; `SETUP.md`/`CONFIGURATION.md`/`TRANSLATIONS.md` in phase 0, `GITHUB_CONNECTIONS.md` in phase 2, the rest of `docs/` completed in phase 10.
- The Ukrainian spec text is the author's source of truth; all English UI source strings are formulated by the implementing phase and recorded as pairs in the `.po` files.

## Phases

### 1. Skeleton, auth, i18n and design tokens

**Goal:** A runnable, logged-in, bilingual, themed empty application where every gate command works from a clean checkout.

**User-facing:** yes — gets end-to-end cases and user docs

**Deliverables:**
- uv project with pyproject.toml, .python-version and committed uv.lock
- config/settings/{base,local,prod,e2e}.py with DATA_DIR, DATABASE_URL, SQLite WAL + busy_timeout, security headers
- Logging with SecretMaskingFilter installed on the root logger
- huey configured against its own DATA_DIR/huey.sqlite3
- accounts app: admin/lead groups, UserPreference, AuditEntry, a first scope_for_user() returning everything
- LoginRequiredMiddleware plus login/logout/password-reset templates
- i18n: LocaleMiddleware, LOCALE_PATHS, JavaScriptCatalog, language switcher, duration formatter in Python and JS, locale/uk/LC_MESSAGES/*.po
- static/css/tokens.css, Tailwind standalone build, committed static/css/app.css
- base.html with navigation, theme switcher, pre-paint inline theme script and hx-headers CSRF
- Makefile with e2e-up, e2e-down, css, vendor, messages
- pytest/ruff/mypy configuration per .autodev/PROFILE.md; conftest.py with the respx assert-all-mocked default
- .env.example, README.md, docs/SETUP.md, docs/CONFIGURATION.md, docs/TRANSLATIONS.md
- One trivial unit test and one Playwright spec that logs in

**Acceptance criteria:**
- `uv sync`, `uv run pytest -q` and the full lint chain succeed from the repository root on a clean checkout
- `make css`, `make messages`, and `make e2e-up && uv run pytest e2e -q; make e2e-down` all succeed
- A parametrized test over urlpatterns asserts every URL except login/logout/password-reset redirects an anonymous user to login
- Switching language flips a page to `uk` and the choice survives a reload and a new session
- Switching theme persists server-side in UserPreference and applies with no flash on first paint
- A grep test finds no `#hex` or `rgb(` colour literal outside static/css/tokens.css
- A polib test asserts the uk .po files have no empty or fuzzy msgstr and placeholders match their msgid
- A test asserts static/css/app.css contains every token name defined in tokens.css

### 2. Domain models, migrations and Django admin

**Goal:** Every table in spec §4 exists, migrates from zero, and is visible in Django admin with the right read-only and hidden fields.

**User-facing:** no

**Deliverables:**
- Models for catalog, activity, ai_detection, policy, metrics, churn, connections and github_sync
- All spec uniqueness constraints, including DailyRollup(date, scope_type, scope_id, cohort, metric_key) and PolicyViolation(pull_request, rule_code, details_hash)
- AppSetting with typed values and the §15 defaults
- Indexes for the obvious lookup paths
- gettext_lazy on every verbose_name and choice label
- Django admin for all models, read-only for synced GitHub data, no token field on GitHubConnection
- factory_boy factories for every model, reused by later phases

**Acceptance criteria:**
- `migrate` on an empty database succeeds and `makemigrations --check --dry-run` reports no pending changes
- A test opens every registered admin changelist and add form as a superuser and gets 200, or 403 where the model is read-only
- A test asserts the GitHubConnection admin form exposes no token field
- A test asserts DailyRollup, PolicyViolation and Identity reject duplicate rows on their documented uniqueness constraints
- A test asserts every AppSetting default from spec §15 is present after migration

### 3. GitHub connections and incremental sync

**Goal:** Encrypted multi-connection credentials, a fixture-tested GraphQL client, repository discovery, and an idempotent incremental sync driven from CLI, UI button and huey.

**User-facing:** yes — gets end-to-end cases and user docs

**Deliverables:**
- Fernet/MultiFernet token_encrypted with a single decrypt accessor, plus manage.py rotate_encryption_key
- Connection form and verification flow storing last_check_result as codes plus parameters, with the invalid/expired/expiring banner
- bootstrap_connection from .env — implemented and unit-tested, never executed against the real API
- The GitHubAuth protocol (get_headers, get_git_credentials, rate_limit_key)
- httpx GraphQL/REST client with pagination, per-connection rate budgeting, retries and a named GitHubSchemaError
- tests/fixtures/github/ JSON fixtures mirroring real GraphQL and REST response shapes
- Repository discovery UI per connection with sync_since and re-binding to another connection
- The §5.3 sync algorithm with per-PR transactions, update_or_create upserts and the process_pull_request hook
- SyncRun with stats, stats_by_connection and a token-masked error_log
- manage.py sync, the huey task, and the Sync page with htmx polling
- docs/GITHUB_CONNECTIONS.md with the operator's first-run checklist

**Acceptance criteria:**
- Syncing the fixtures creates the expected PullRequest, Commit, Review, PRFile and CheckStatus rows with correct fields
- A second sync over the same fixtures changes no row count and no github_id — upserts are idempotent
- Tests cover nested-connection pagination beyond 100 items and secondary-rate-limit backoff honouring Retry-After
- A 401 on one connection marks it invalid and skips only its repositories, while another connection's repositories keep syncing
- After a sync that fails partway, a leak test finds neither the token nor any fragment beyond last4 in the database, the log file, SyncRun.error_log or any rendered page
- Any unmocked outbound HTTP request fails the test suite
- A fixture missing a field the client depends on raises GitHubSchemaError naming the JSON path

### 4. Identity resolution and derived PR fields

**Goal:** GitHub logins and git emails collapse into people, bots are excluded and counted separately, and every cached PR field the metrics depend on is computed and tested.

**User-facing:** yes — gets end-to-end cases and user docs

**Deliverables:**
- Identity auto-mapping from ID+login@users.noreply.github.com and from GitHub-linked commit author emails
- Bot detection from the configurable login list ([bot] suffix, dependabot, renovate, github-actions)
- The unmapped-identity queue page with assign, create person, merge two people, mark bot and exclude actions
- activity.derive computing size_bucket, effective_additions/deletions under EXCLUDED_PATH_GLOBS, has_test_changes, is_revert + reverts_pr, is_hotfix, is_rubber_stamp, is_self_merged, review_rounds, commits_after_first_review and ready_for_review_at from the timeline
- derive wired into process_pull_request
- Settings → People UI

**Acceptance criteria:**
- Each derived field has a test over hand-built fixtures, including a never-drafted PR where ready_for_review_at equals created_at, a revert chain, an excluded-path-only PR with effective_lines == 0, and a rubber stamp at exactly the 10-minute boundary
- Merging two Person records re-points every Identity and leaves no orphan identity or PR
- A bot-authored PR is flagged is_bot and appears in the separate bot counter rather than in metrics
- The unmapped-identity queue lists a seeded unmatched email and its page count matches the model count
- Re-running derive over a PR produces identical values — no drift on a second pass

### 5. AI detection

**Goal:** Eight detectors over stored data, a tolerant disclosure parser, a resolved ai_status per PR, and detection rules the admin edits without a release.

**User-facing:** yes — gets end-to-end cases and user docs

**Deliverables:**
- fixtures/detection_rules.yaml covering Claude Code, Copilot, Cursor, Codex, Devin, Gemini, Aider and Windsurf, each rule carrying a source comment and disputed ones seeded confidence=low
- manage.py seed_detection_rules
- The eight detectors: commit_trailer, commit_author, pr_author, pr_body_footer, html_comment, label, branch_pattern, commit_message
- The PR-template disclosure parser with configurable synonyms, returning none/partial/substantial/missing/ambiguous plus disclosed_tools
- ai_status resolution per §6.3 and the configurable AI cohort
- docs/pull_request_template.md
- The detection-rules settings page with a dry run against the last N stored PRs
- Re-running detection over stored PRs without touching GitHub

**Acceptance criteria:**
- Every detector has a matching and a non-matching test
- The disclosure parser is tested on [x] and [X], mixed case, a malformed template, two ticked boxes yielding ambiguous, and a missing section yielding missing
- Each of the five ai_status outcomes has a test
- Re-running detection twice creates no duplicate AISignal rows
- Deactivating a rule removes its signals on the next detection run
- Every AISignal carries an evidence fragment of at most 200 characters, shown on the PR page
- The rule dry-run page reports matches against the last N PRs without writing AISignal rows

### 6. AI policy engine and violations console

**Goal:** Nine policy rules evaluated idempotently, violations a lead can acknowledge or waive, and an audit trail of those judgements.

**User-facing:** yes — gets end-to-end cases and user docs

**Deliverables:**
- AIPolicy singleton with versioning and effective_from
- SensitivePathRule with glob matching, global or per project
- The nine rule evaluators: DISCLOSURE_MISSING, DISCLOSURE_MISMATCH, TOOL_NOT_ALLOWED, SENSITIVE_PATH_FORBIDDEN, SENSITIVE_PATH_REVIEW, NO_HUMAN_APPROVAL, SELF_MERGE, NO_TESTS, AI_PR_TOO_LARGE
- details_params stored as data only, with the message rendered from rule_code at read time in the reader's language
- Idempotent policy.evaluate wired into process_pull_request, plus auto-resolve with an automatic marker
- The Policy page: compliance KPIs, violation chart, filterable table, bulk acknowledge/waive with a mandatory comment
- AuditEntry on every violation status change and settings change
- Policy and sensitive-path settings UI

**Acceptance criteria:**
- Each of the nine rules has a positive test, a negative test, an idempotency test where a second run creates no second row, and an auto-resolve test
- Auto-resolve moves only open → resolved and never changes an acknowledged or waived violation, asserted by test
- A PR created before AIPolicy.effective_from produces no violation
- A merge-dependent rule produces no violation on an open PR
- Acknowledging or waiving without a comment is rejected by the form
- Every status change writes an AuditEntry recording who, when, and the before/after state
- No PolicyViolation row stores rendered message text — asserted by a test over details_params

### 7. Metrics registry, rollups and recompute

**Goal:** One registry that UI, docs and exports all read, with storage decided by metric kind and every day boundary computed in Europe/Kyiv.

**User-facing:** no

**Deliverables:**
- MetricDef and the registry with lazy-translated title and description
- Every metric of spec §8.2 implemented as a calculator
- The Europe/Kyiv day-boundary helper used by every event attribution
- DailyRollup writes restricted to additive kinds; distributions computed from raw rows on read
- compute() returning value, previous-period value, delta, sample_size and series
- State-metric reconstruction on a date (open, stale, waiting_review_24h)
- Cohort handling for all / ai / non_ai with the §15 default cohort
- FileBasedCache keyed by last_data_version, incremented after every sync and recompute
- manage.py recompute and manage.py metrics_doc generating docs/METRICS.md
- Bot and exclude_from_metrics exclusion with a separate counter

**Acceptance criteria:**
- Every metric has a test over a hand-computed factory_boy dataset
- Day-boundary tests cover a PR merged at 23:59 and at 00:01 Kyiv time and a DST transition day
- A test asserts no distribution-kind metric ever reaches DailyRollup
- Deleting all rollups for a range and running recompute reproduces identical rollup rows
- compute() reports sample_size so a sample below MIN_SAMPLE can be greyed by the UI
- A metric with no underlying data returns None, never 0
- A stale docs/METRICS.md fails its freshness test
- A second compute() with the same arguments is served from cache, and a bumped last_data_version misses it

### 8. Dashboards, charts, themes and table export

**Goal:** Overview, Project and Repository pages in Period and Day modes, with KPI rows, charts, tables, both themes, and CSV/XLSX export of any table.

**User-facing:** yes — gets end-to-end cases and user docs

**Deliverables:**
- manage.py seed_demo
- The global filter bar with all state in the query string
- Overview / Project / Repository from one template across three scopes, plus Day mode
- Three KPI rows with delta, arrow, direction colouring and a sparkline
- The six charts behind /api/charts/<chart_key> JSON endpoints
- Vendored Chart.js and charts.js reading colours via getComputedStyle and redrawing on themechange
- django-tables2 tables (projects, repositories, people, recent PRs) with sort, sticky header, pagination and search
- apps/dashboards/exports/columns.py, csv.py and xlsx.py for table export with the mandated XLSX formatting
- The 'data as of <last successful sync>' banner
- Ukrainian translations for every string introduced

**Acceptance criteria:**
- A smoke test renders every page on seed_demo data in en and uk and in both themes, and asserts no canary English string appears in the uk render
- Every list view has an assertNumQueries test that fails on an N+1
- A chart JSON endpoint returns the same numbers as compute() for the same filters
- Changing a filter changes the query string, and the query string alone restores the whole view
- A CSV export of a filtered, sorted, searched table contains every matching row, not just the current page
- A CSV value starting with =, +, - or @ is prefixed with an apostrophe
- An exported XLSX opens with openpyxl and has bold headers, frozen panes, autofilter, real numeric cells, 0.0% percentages, durations as hours and clickable PR hyperlinks
- A repository belonging to two projects is counted once at the global level

### 9. People, PRs, Reviews, scoped access and reports

**Goal:** The remaining pages, the per-project access restriction enforced at every exit, and multi-sheet dashboard reports including background exports.

**User-facing:** yes — gets end-to-end cases and user docs

**Deliverables:**
- The People table, sorted by name by default, and the person page with project/team median comparison and private notes
- The PR list with all filters and the PR detail page: timeline, metrics, AI signals with evidence, disclosure, files marked test/excluded/sensitive, violations with actions and a churn slot
- The Reviews page: reviewer load, author→reviewer heat map, review load share and PRs waiting for review
- UserProjectAccess and the real scope_for_user() threaded through every selector, with out-of-scope ids in the query string dropped silently
- exports/reports.py producing the seven-sheet dashboard report for Overview, Project, Repository, Person and Policy in both modes, with native Excel charts
- ExportJob plus the huey background export above EXPORT_SYNC_MAX_ROWS, htmx status polling, the 'My exports' page, author-only download, 7-day cleanup and the stuck-job sweep
- AuditEntry written for every export

**Acceptance criteria:**
- Four separate tests assert a restricted lead sees another project's data in neither a page, nor a chart JSON endpoint, nor a CSV export, nor an XLSX export
- The global level for a restricted lead equals exactly the sum of their accessible projects
- An out-of-scope project id passed in the query string is dropped silently rather than returning 403
- A report XLSX opens with openpyxl and contains every mandated sheet (Summary, Trends, tables, PRs, Violations, Metrics, Parameters)
- A test asserts Person.notes appears in no exported file
- An export of 20 001 rows returns an ExportJob instead of a file, and the enqueue is asserted
- Downloading another user's export file returns 403, and a file past its 7-day expiry is deleted by the cleanup task
- Every export writes an AuditEntry recording the user, kind, filters and row count
- The People table's default ordering is by name, asserted by test

### 10. CI first-pass, follow-up fixes and churn

**Goal:** The three remaining quality metrics, including the only component that shells out to git and touches the filesystem at scale.

**User-facing:** yes — gets end-to-end cases and user docs

**Deliverables:**
- ci_first_pass_rate computed from CheckStatus.is_first_ci_commit
- The followup_fix_rate heuristic (14 days, hotfix/fix pattern, ≥50% file overlap), labelled a heuristic everywhere it is shown
- Bare clone management under DATA_DIR/repos/<owner>/<name>.git with fetch before each computation
- The GIT_ASKPASS helper taking the token from the process environment only
- PR-commit set selection per merge method, with rebase yielding unsupported_merge_method
- git blame --line-porcelain -M -C for lines_at_merge and lines_surviving, plus snapshot_sha selection
- churn_ratio with the lines_at_merge == 0 skip, CHURN_MAX_FILES limit and bounded parallelism
- manage.py compute_churn and the nightly huey schedule
- Churn surfaced on the PR detail page and in the churn KPI

**Acceptance criteria:**
- A test builds a temporary git repository and asserts the churn ratio for a squash merge and for a merge commit against hand-counted lines
- A rebase merge yields ChurnResult.status == unsupported_merge_method and no ratio
- A PR touching more than CHURN_MAX_FILES files yields status == too_large
- A PR whose lines_at_merge is 0 records no ChurnResult rather than a 0% ratio
- A failing git fetch deletes and re-clones once, then records status == error with a reason
- A test asserts the token appears in no git argv, no remote URL and no .git/config after a churn run
- ci_first_pass_rate and followup_fix_rate each have a positive and a negative test, and the follow-up metric's UI label reads 'heuristic'

### 11. Polish, performance and documentation

**Goal:** The product reads correctly at its edges, meets the latency budget on realistic data volume, and is fully documented in English with a proofread Ukrainian UI.

**User-facing:** yes — gets end-to-end cases and user docs

**Deliverables:**
- Empty states with an explanation on every page, chart and table
- MIN_SAMPLE greying with the 'small sample' label everywhere a metric is shown
- A contrast audit of every token pair in both themes, with status never conveyed by colour alone
- Ukrainian proofread plus a long-string layout pass on KPI cards, buttons and column headers
- seed_demo --scale producing 50 repositories and 20 000 PRs
- An index and select_related/prefetch_related pass driven by profiling against that seed
- docs/ completed: SETUP.md with the launchd/cron schedule, backup-restore and the Future deployment section, CONFIGURATION.md, METRICS.md, POLICY.md, GITHUB_CONNECTIONS.md, TRANSLATIONS.md and docs/user/
- The final docs/PROGRESS.md entry

**Acceptance criteria:**
- A timed test asserts a 90-day Overview on the 50-repo / 20 000-PR seed renders in under 1.5 s locally
- A test asserts every metric with a sample below MIN_SAMPLE renders with the small-sample marker
- Rendering every page over an empty period shows the explanation text and no chart error
- A test over tokens.css asserts every token pair meets WCAG 2.1 AA — text ≥ 4.5:1, chart and KPI elements ≥ 3:1
- The uk render of every page has no empty or fuzzy string and no canary English string
- Every file referenced by README.md and CLAUDE.md under docs/ exists
- The full gate — ruff check, ruff format --check, mypy, makemigrations --check, manage.py check, pytest, and the e2e suite — passes
