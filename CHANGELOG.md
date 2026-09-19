# Changelog

## Unreleased

### Changed

- **The interface has been redesigned.** Every page now shares one visual system: a sticky application header
  with the primary destinations, a Settings menu holding the admin-only pages, a page container with consistent
  width and spacing, and cards for KPIs, charts, filters, forms and tables. Buttons, inputs, labels, badges,
  tables, links, empty states and alerts are shared component classes (`static/css/src/input.css`) rather than
  ad-hoc utility strings, so a change lands everywhere at once. On a narrow screen the header keeps every
  control — the navigation moves to its own scrollable row instead of a duplicated mobile menu. Colours still
  come only from `static/css/tokens.css`; the new surface, accent-tint and shadow tokens are defined there for
  both themes.

### Added

- **Load historical data** on the Sync page (admins): re-fetch every pull request updated in the last 7, 14, 30
  or 90 days, or since a date you pick, for all active repositories or only the ones you select. It shares the
  lock, rate-limit budget and rollup rebuild with an ordinary sync, and its runs are listed as `Backfill` with
  the start date they used. The command-line equivalent, `manage.py sync --since`, is unchanged.

### Fixed

- A connection whose token cannot read the repositories it tracks is no longer reported as healthy. The
  `PERM_PULL_REQUESTS` check now actually lists pull requests on one of the connection's repositories instead of
  being recorded as passing without a request behind it, and a 404 — GitHub's answer for a private repository a
  token may not see — is read as denied access (`PERM_PULL_REQUESTS_DENIED`, `PERM_CONTENTS_DENIED`) rather than
  an unexpected error. A classic token that lacks the `repo` scope while private repositories are tracked is
  flagged as `CLASSIC_PAT_NO_PRIVATE_SCOPE`. Previously such a connection verified as `ok` and every sync
  reported success having synced nothing.
- Repository discovery now lists every repository a connection's token can read, whoever owns it — a
  client-owned or other-organization repository you have access to is no longer hidden. A connection's owner
  login is a label only; it no longer filters the list. When the list spans several owners, an **Owner**
  dropdown narrows it, defaulting to all owners.
- A connection whose token can see no repository at all is now reported as `degraded` with the new
  `REPOS_VISIBLE_NONE` check and a hint, instead of passing verification with "0 repositories" and leaving an
  empty discovery page as the only symptom.

## 0.1.0 — 2026-09-19

The first release. Everything below is new.

### Breaking changes

None — there is no earlier version to break.

### What you must do to run it

- Set `SECRET_KEY` and `FIELD_ENCRYPTION_KEYS` in `.env` (copy `.env.example`) **before** creating a GitHub
  connection. Without `FIELD_ENCRYPTION_KEYS` a token cannot be stored; losing the key later means re-entering
  every token. See `docs/GITHUB_CONNECTIONS.md`.
- Run `uv run python manage.py migrate`, then `createsuperuser`. `migrate` also creates the `admin` and `lead`
  groups.
- Run the background worker (`uv run python manage.py run_huey`) alongside the web process. Without it, syncs,
  churn and large exports never run — though every one of them is also a management command you can run by hand.
- Back up by stopping both processes and copying `DATA_DIR`. Your projects, people, identity mapping, policy and
  violation decisions cannot be recovered by re-syncing GitHub. See `docs/SETUP.md`.

### Connect GitHub and get data in

- GitHub connections (Settings → Connections): add, edit, verify and deactivate a connection; tokens are
  encrypted at rest and shown only as their last four characters. An admin banner warns about invalid, expired
  or soon-to-expire connections.
- Repository discovery and rebinding (Settings → Repositories): browse the repositories a connection can see,
  add the ones you want synced, and move a repository to a different connection without losing its history.
- GitHub sync (Sync page, `manage.py sync`, and a background task): pulls pull requests, reviews, commits,
  checks and files incrementally, with per-connection rate limiting and retry handling. See
  `docs/GITHUB_CONNECTIONS.md` and `docs/user/connect-github.md`.
- Automatic identity resolution: a synced GitHub login is mapped to a person immediately, a git email only when
  GitHub itself links it to a login; bot accounts are detected from a configurable login list.
- People management (Settings → People): list every person with their identities, team, role and PR count; edit
  a person's details, bot flag and metrics exclusion; merge two people into one. See `docs/user/map-people.md`.
- The unmapped-identity queue (Settings → People → Unmapped identities): assign an email to an existing person,
  create a new person from it, mark it as a bot, or exclude it from metrics.
- Every pull request carries its derived fields (size bucket, effective lines, test-file flag, rubber-stamp,
  self-merge, hotfix, revert, review timing and counts), recomputed idempotently after each sync.

### AI detection and policy

- AI detection: eight regex-based signal detectors, a tolerant PR-template disclosure parser and a resolved
  `ai_status`/`ai_disclosure`/`ai_tools` per PR, recomputed idempotently after each sync. See
  `docs/user/tune-ai-detection.md`.
- Settings → Detection rules: list, create, edit and activate/deactivate a detection rule, with a dry run against
  recently stored PRs before saving.
- AI policy engine: nine configurable rules (missing/mismatched disclosure, a disallowed tool, a forbidden or
  extra-review sensitive path, missing human approval, self-merge, missing tests, an oversized AI PR) evaluated
  idempotently after every sync, with results that auto-resolve when the condition clears without ever
  overwriting a lead's own judgement. See `docs/POLICY.md`.
- Policy console (`/policy/`): compliance KPIs, a by-rule violation distribution, a disclosure-mismatch PR list,
  and a filterable, paginated violation table with bulk acknowledge/waive (a reason is required); every status
  change is recorded in the audit trail. See `docs/user/handle-policy-violations.md`.
- Settings → AI policy: a versioned, append-only AI policy (allowed tools, disclosure/approval/test requirements,
  size limit) — saving creates a new version rather than editing history.
- Settings → Sensitive paths: list, create, edit and activate/deactivate a path glob marked `forbidden` or
  `needs_extra_review`, globally or per project.
- `docs/pull_request_template.md`, a recommended PR template matching the disclosure parser's defaults.

### Dashboards and pages

- Dashboards (Overview, Projects, Repositories): KPI cards with delta arrows and sparklines, six charts
  (throughput, AI adoption, latency, PR size distribution, churn/rework, violations by rule), and sortable,
  searchable, paginated tables for projects, repositories, people and recent PRs. Every filter — period,
  granularity, cohort, and a Day mode showing one calendar day — is carried in the URL, so a filtered view can
  be shared as a link. See `docs/user/dashboards.md`.
- People page (`/people/`) and a Person page (`/people/<id>/`): the person's PRs, violations, review load, a
  comparison against their primary project and the whole organisation, and a private notes card visible only to
  leads. See `docs/user/people.md`.
- Pull requests page (`/prs/`): every PR in scope, filterable by author, state, AI status, tool, size and
  whether it has open violations, exportable to CSV/XLSX. See `docs/user/pull-requests.md`.
- PR detail page (`/prs/<id>/`): a chronological timeline, per-PR duration metrics, the changed-files list with
  test/excluded/sensitive-path badges, the resolved AI status with each matched signal's evidence, open
  violations with an inline acknowledge/waive action, and a status-aware churn section.
- Reviews page (`/reviews/`): reviewer workload, an author×reviewer heat map, and the list of PRs waiting for
  review. See `docs/user/reviews.md`.
- Sign in, sign out and reset a forgotten password. Theme switcher (System / Light / Dark), applied before the
  page paints so there is no flash of the wrong theme, and a language switcher (English / Українська) — both
  available on the login page, both saved to your account once you are logged in.

### Metrics and churn

- Metrics registry: 39 metrics across adoption, delivery flow and quality, each with a documented formula, unit
  and direction. See `docs/METRICS.md`.
- Churn analysis: a nightly job (`manage.py compute_churn`, 02:00) clones each repository and measures how much
  of a merged PR's own lines survive `CHURN_WINDOW_DAYS` after merge, feeding the `churn_21d` KPI, the
  churn/rework chart, and a status-aware Churn section on the PR detail page (a percentage, "not measured for
  rebase merges", "too large to measure", or a retried error — never a fake `0%`). See `docs/user/churn.md`.
- `followup_fix_rate` (heuristic): flags a merged PR as likely followed by a fix, by title/branch pattern and
  file overlap within a configurable window; shown labelled "(heuristic)" on the Person page's comparison table.

### Exports

- Table export to CSV and XLSX from any dashboard table, carrying the table's current filters, search and sort
  and covering every matching row, not just the current page. The XLSX file has a bold frozen header, an
  autofilter, real numeric cells, and clickable PR links.
- A seven-sheet dashboard report (Summary, Trends with native Excel charts, the page's own tables, PRs,
  Violations, Metrics reference, Parameters), downloadable as XLSX from Overview, Project, Repository and
  Person pages. See `docs/user/exports.md`.
- Background exports: a table or report export past `EXPORT_SYNC_MAX_ROWS` rows is queued as a job instead of
  blocking the request; "My exports" (`/exports/`) lists your own jobs with live status and a download link,
  `manage.py process_exports`/`cleanup_exports` are the worker-free fallbacks, and every export — synchronous or
  background — writes an audit entry.

### Access and honesty about numbers

- Per-project access restriction: a lead granted access to specific projects (Django admin → Accounts → User
  project access) sees only those projects' data everywhere — every page, chart, CSV/XLSX export and report;
  a lead with no grants continues to see everything.
- Page-level empty states: every dashboard, list and chart card explains *why* it's empty — nothing has ever
  been synced, or nothing matches the current period and filters — instead of showing a blank area, with an
  action link ("Go to Sync") where there's an obvious next step.
- Small-sample marking: any metric computed from fewer than five pull requests is greyed and labelled, on KPI
  cards and on metric table cells alike, with a title/aria-label carrying the full explanation — never colour
  alone. A metric with no data shows an em dash, never a zero.
- A colour-contrast retune for WCAG AA: two new design tokens (`--border-strong` for a control's own boundary,
  `--on-heat` for text on a heat-map cell) and a retuned light-theme palette, so every foreground/background
  pairing used in the app clears the 4.5:1 (text) / 3:1 (UI) contrast ratio in both themes.
- Long Ukrainian strings no longer clip or overflow on KPI cards or table headers.

### Performance

- The Overview page renders well under its 1.5-second budget at 50 repositories / 20,000 pull requests, down
  from roughly 29 seconds before this release's profiling and query-shape fixes.
- `manage.py seed_demo --scale large` seeds 50 repositories and 20,000 pull requests for trying the dashboards,
  and the app itself, at a realistic scale.
- `scripts/profile_dashboard.py` renders the Overview, Project, Repository, People and Reviews pages and reports
  wall time, query count and the slowest SQL statements per page.

### Commands and documentation

- Management commands: `sync`, `recompute` (`--rollups-only` / `--skip-rollups`), `compute_churn`, `seed_demo`,
  `seed_e2e`, `metrics_doc` (`--check`), `bootstrap_connection`, `rotate_encryption_key`,
  `seed_detection_rules`, `process_exports`, `cleanup_exports`.
- User documentation under `docs/user/` — a [guide index](docs/user/README.md), a task → page map, one page per
  feature, and troubleshooting for empty dashboards, small-sample greying, a stale "Data as of" banner, a failed
  sync and a missing churn number.
- Developer documentation: [`docs/dev/architecture.md`](docs/dev/architecture.md) and eight architecture
  decision records under `docs/dev/adr/`.
