# Phase 9 — People, PRs, Reviews, scoped access and reports

**Goal.** The remaining read surface (People, Person, PR list, PR detail, Reviews), the per-project access
restriction enforced at *every* exit, and multi-sheet dashboard reports including background exports.

Spec: §10.3 (People / PR / Reviews pages), §10.6 (XLSX reports, export layer, volumes, audit), §11 (access).
Architecture: `.autodev/ARCHITECTURE.md` (`scope_for_user` choke point, `dashboards` owns the read surface and
`ExportJob`, ADR 0002, ADR 0005, ADR 0007). Risks: rows 1, 3, 10, 13.

## Context

### What exists

- **`accounts`**: `ScopeFilter(unrestricted, project_ids, repository_ids)` and `scope_for_user(user)` —
  still the phase-1 stub that always returns `ScopeFilter(unrestricted=True)`. `UserProjectAccess(user, project)`
  and `AuditEntry` models exist with migrations; `accounts.services.record_audit(actor, action, obj, before, after)`
  is the one writer.
- **Selectors already take a `ScopeFilter`** and compose on it, so the stub is the *only* thing making the
  restriction inert: `catalog.selectors.projects_in_scope/repositories_in_scope/people_in_scope`,
  `activity.selectors._scoped/pull_requests_in_scope/pull_requests_for_metrics/excluded_pull_requests`,
  `policy.selectors.violations_in_scope`, `metrics.selectors.scoped_pull_requests/scoped_reviews/
  scoped_review_comments/scoped_violations`, `ai_detection.selectors`.
- **`metrics`**: registry of 40+ metrics with `levels` including `person`; `compute()`/`compute_many()` are the
  only read entry points; `services.scope_for(user, scope_type, scope_id)` already drops an out-of-scope path/query
  id back to `GLOBAL` (RISKS row 3); `_id_in_scope()` covers `project`, `repo`, `person`.
- **`dashboards`**: `params.py` (`DashboardParams` + `parse()` + `narrow_scope()`, whole filter state in the query
  string), `forms.DashboardFilterForm` (drops out-of-scope project/repository ids silently),
  `kpis.py`, `charts.py` (`CHART_REGISTRY`, six specs, `/api/charts/<key>/`), `rows.py`
  (`project_rows`/`repository_rows`/`people_rows`/`recent_pr_rows`), `exports/columns.py`
  (`ExportColumn`, `TableSpec`, `TABLE_SPECS` for `projects`/`repositories`/`people`/`recent_prs`),
  `exports/csv.py` (`stream_csv`, injection-neutralised), `exports/xlsx.py` (`write_xlsx`, one sheet, bold/frozen
  header, autofilter, real numbers, delta colouring), `tables.py` (`build_table_context`, `full_rows`),
  `services.build_dashboard`, `views.dashboard/chart_json/export_table/projects_index/repositories_index/
  pull_request_detail`, `selectors.py` (Day-mode raw lists), `seed_demo`.
- **PR detail** exists but only as an AI-signals page (title, GitHub link, `ai_status`/disclosure/tools, signals
  table with evidence). No timeline, no metrics, no files, no violations, no churn.
- **People** exists only as the admin-only *settings* page (`catalog:people`, permission
  `catalog.manage_settings`) and as the `people` table embedded in dashboard pages. There is no lead-facing People
  page and no Person page.
- **`policy`**: console with filters and bulk `acknowledge`/`waive` through
  `policy.services.apply_bulk_status_change`; `policy.selectors.violations_for_pull_request(scope, pk)` is ready
  for the PR page.
- **`churn`**: models only (phase 10 computes `ChurnResult`).
- **Exports today** are synchronous only; `EXPORT_SYNC_MAX_ROWS` (20 000) is read and merely *logged* when
  exceeded (`views.export_table`), with an explicit "phase 9's `ExportJob`" comment. No `ExportJob`, no
  `apps/dashboards/models.py`, no `apps/dashboards/tasks.py`, no `exports/reports.py`, no export `AuditEntry`.
- **Infra**: `DATA_DIR/exports/` is already created at settings import; `huey` is configured on its own SQLite
  file and is `immediate=True` in tests (root `conftest.py`); `openpyxl` and `xlsxwriter` are both installed;
  `tests/test_urls_login.py` walks every named URL asserting an anonymous redirect (new URLs join it for free via
  `SAMPLE_ARGS`); `tests/test_pages_smoke.py` renders pages in en/uk × light/dark.

### What this phase changes

1. `scope_for_user()` stops being a stub — one function, every exit narrowed.
2. Four new pages: People (`/people/`), Person (`/people/<pk>/`), PR list (`/prs/`), Reviews (`/reviews/`), plus a
   real PR detail page.
3. `DashboardParams` grows a `PRFilters` member so the PR list's filters live in the same query-string owner and
   therefore reach the export layer unchanged.
4. `exports/xlsx.py` is refactored into a reusable `write_sheet()`; `exports/reports.py` is new and produces the
   seven-sheet workbook with native Excel charts.
5. `ExportJob` + huey background export + "My exports" + author-only download + 7-day cleanup + stuck sweep.
6. Every produced export file writes an `AuditEntry`.

### Key files

| Area | Files |
|---|---|
| Access | `apps/accounts/selectors.py`, `apps/accounts/tests/test_scope_for_user.py`, `tests/test_scope_isolation.py` |
| People/Person | `apps/dashboards/views.py`, `urls.py`, `person.py` (new), `templates/dashboards/people_index.html`, `person.html`, `partials/person_*.html` |
| PR list/detail | `apps/dashboards/pr_filters.py` (new), `forms.py`, `params.py`, `rows.py`, `exports/columns.py`, `pr_detail.py` (new), `templates/dashboards/pull_requests.html`, `pull_request_detail.html` |
| Reviews | `apps/dashboards/reviews.py` (new), `charts.py`, `templates/dashboards/reviews.html`, `static/css/tokens.css` |
| Reports | `apps/dashboards/exports/xlsx.py`, `exports/reports.py` (new) |
| Background export | `apps/dashboards/models.py`, `migrations/0001_initial.py`, `services.py`, `tasks.py`, `admin.py`, `management/commands/process_exports.py`, `cleanup_exports.py`, `templates/dashboards/exports.html` |

## Design

### 1. `scope_for_user()` — the one authorization change

```python
def scope_for_user(user) -> ScopeFilter:
    if user is None or not getattr(user, "is_authenticated", False):
        return ScopeFilter(unrestricted=False, project_ids=frozenset())
    cached = getattr(user, "_pr_radar_scope", None)  # per-request memo
    if cached is not None:
        return cached
    project_ids = frozenset(UserProjectAccess.objects.filter(user=user).values_list("project_id", flat=True))
    scope = (
        ScopeFilter(unrestricted=True)
        if not project_ids
        else ScopeFilter(unrestricted=False, project_ids=project_ids)
    )
    user._pr_radar_scope = scope
    return scope
```

- **No rows ⇒ sees everything** (spec §11), including a superuser or an `admin` — the restriction is a grant
  list, not a role. A superuser with rows *is* restricted; that is the spec's rule and is what makes the test
  fixture honest.
- **Anonymous ⇒ empty grant**, not `unrestricted`. Every view is `login_required`, so this is defence in depth:
  a selector reached from a management command or a mis-wired view yields nothing rather than everything.
- `repository_ids` stays `None` here — it is the filter bar's axis (`params.narrow_scope()`), never an access
  grant. `UserProjectAccess` grants projects only.
- The memo is attached to the `User` instance (request-lifetime) so a page that calls `scope_for_user()` a dozen
  times pays one query (RISKS row 10). It is not a cross-request cache: a revoked grant takes effect on the next
  request.

**Threading it through the remaining exits.** Every domain selector already composes on `ScopeFilter`, so the work
is an audit plus four gaps:

- `catalog.selectors.people_for_settings/unmapped_identities/bot_person_count` ignore their `scope` argument.
  They back `catalog:people`, an admin-only settings page. They get the same narrowing as `people_in_scope`
  (people with authored PRs in scope) so the argument stops lying; admins without grants are unaffected.
- `catalog.views.person_edit/person_merge` fetch `Person.objects` directly → `people_for_settings(scope)`.
- `metrics.services._id_in_scope()` checks `project_ids` only. It is reached only with an access filter from
  `scope_for_user()` (projects axis), so it is correct today; a comment records why rather than adding dead code.
- The new views (People, Person, PR list, Reviews, PR detail, exports, reports) each start from
  `scope_for_user(request.user)` and `metrics.services.scope_for(...)`, never from `Model.objects`.

**Path id vs query-string id** (already the project's rule, extended to the new pages): an out-of-scope or unknown
id **in the path** is a 404 via `get_object_or_404(<scoped queryset>, pk=…)`; the same id **in the query string**
is dropped silently — `DashboardFilterForm`/`PullRequestFilterForm` build their choice querysets from the scoped
selectors, and `scope_for()` drops an out-of-scope `scope_id` back to global (RISKS row 3, ARCHITECTURE line 251).

### 2. People page and Person page

- `GET /people/` → `views.people_index`: the existing `people` `TableSpec` through
  `tables.build_table_context("people", scope, params)`, exactly like `projects_index`/`repositories_index`
  (filter bar, search, sort, pagination, CSV/XLSX buttons for free). Default order is
  `rows.people_rows()`'s own order, which is `people_in_scope()`'s `order_by("display_name")` — **name order, never
  a ranking** (RISKS row 1); a test asserts it and that no `sort` is applied by default.
- `GET /people/<pk>/` → the existing `views.dashboard` with `scope_type=ScopeType.PERSON`, scoped lookup
  `get_object_or_404(people_in_scope(access), pk=pk)`. It reuses the KPI rows, the chart cards and the filter bar,
  and adds three person-only blocks built in a new `apps/dashboards/person.py`:
  - `build_comparison(scope, params, person)` → `list[ComparisonRow(metric, person_value, person_sample,
    project_value, org_value, previous_value, below_min_sample)]` over
    `PERSON_COMPARISON_METRIC_KEYS = (ai_pr_share, disclosure_rate, prs_merged, lead_time_p50,
    time_to_first_review_p50, pr_size_p50, rework_rate, reviews_given, reviewer_response_p50, churn_21d)`.
    Baselines come from `metrics.compute_many()` at `PROJECT` scope for the person's **primary project** (the
    project with most of their PRs in the period, `None` when they have none) and at `GLOBAL` scope for the
    reader's whole visible organization. Both are real medians over raw rows at that level — never a median of
    per-person medians (ADR 0007, RISKS row 1). `Person.team` is shown on the page; a per-team baseline would need
    a `team` dimension in `Scope`/`DailyRollup` and is deferred (DECISIONS).
  - PR list and violations for the person: `rows.pull_request_rows()` and `policy.selectors.violations_in_scope()`
    narrowed to the person, both already scoped.
  - Review load: `reviews.reviewer_load(scope, params)` filtered to this person (§4).
  - **Private notes**: `Person.notes`, rendered in a card with an inline edit form.
    `POST /people/<pk>/notes/` (htmx, returns the notes fragment; a full request re-renders the page) writes
    `record_audit(user, "person.notes", person, before=…, after=…)`. `AuditEntry.changes` stores the *lengths*
    and a truncated diff marker, not the note text, so the audit log never becomes a second copy of private text.
    Permission: any authenticated lead with the person in scope (spec §11 gives leads the people pages).
  - `Person.notes` is not in `PEOPLE_COLUMNS` and is not in any report sheet — asserted by test (RISKS row 13).

### 3. PR list and PR detail

**Filters as part of the query-string owner.** `apps/dashboards/pr_filters.py`:

```python
@dataclass(frozen=True)
class PRFilters:  # all-empty default == "no PR filter applied"
    author_ids: tuple[int, ...] = ()  # Person ids, scoped
    states: tuple[str, ...] = ()  # PullRequest.State
    ai_statuses: tuple[str, ...] = ()  # AIStatus
    tools: tuple[str, ...] = ()  # ai_detection.Tool
    size_buckets: tuple[str, ...] = ()  # SizeBucket
    has_violations: str = ""  # "" | "yes" | "no"

    def is_empty(self) -> bool: ...
    def apply(self, queryset): ...  # the only place the filters touch the ORM
```

`forms.PullRequestFilterForm` validates them, taking `people=people_in_scope(access)` the same way
`DashboardFilterForm` takes `projects=`/`repositories=` — an out-of-scope author id or an unknown enum value is
**dropped**, never a validation error or a 403. `params.parse()` builds `DashboardParams.pr_filters`;
`to_query_dict()` emits them (canonical, sorted, defaults omitted). Dates reuse the existing period filter.
Consequence: the export URLs built by `tables.build_table_context()` carry the PR filters with no extra code, and
`views.export_table` exports exactly the filtered set (spec §10.6 "поточний вигляд з усіма рядками").

**The table.** `rows.pull_request_rows(scope, params)` — `metrics.selectors.scoped_pull_requests(scope)` narrowed
to the period by `created_at`, then `PRFilters.apply()`, `select_related("repository", "author__person")` and
`annotate(violations_count=Count("violations", filter=Q(status="open")))` (one query, no N+1), ordered
`-created_at`. `TABLE_SPECS["pull_requests"]` reuses `RECENT_PRS_COLUMNS` plus `disclosure`, `review_rounds` and
`churn_ratio` (empty until phase 10). `recent_prs` keeps its own capped builder for the dashboard pages.
`SEARCH_KEYS["pull_requests"] = ("title", "author", "repository")`.

`GET /prs/` → `views.pull_requests_index`, htmx fragment / full page by `HX-Request`, filter bar + PR filter form.

**PR detail** (`views.pull_request_detail`, extended) builds its context in a new `apps/dashboards/pr_detail.py`:

- `timeline(pr)` → ordered `TimelineEvent(kind, at, label_code, actor)` from `first_commit_at`, `created_at`,
  `ready_for_review_at`, each `Review.submitted_at` (state as a code), `merged_at`/`closed_at`. Codes render in the
  reader's language; nothing pre-rendered is stored.
- `pr_metrics(pr)` → per-PR durations and counters computed directly from the row (lead time, time to first
  review, cycle time, effective size, size bucket, review rounds, commits after first review, rubber-stamp,
  self-merge). These are single-PR facts, not aggregates — `metrics.compute()` is the entry point for
  *dashboard aggregates*, and a per-row derived value is not one (same rule already applied by `rows.py` for the
  `recent_prs` columns).
- Files: `PRFile` rows with `is_test` / `is_excluded` / `matched_sensitive_rule` badges, capped at
  `get_int("PR_FILES_DISPLAY_LIMIT")` (new `AppSetting`, default 300) with a "+N more" line.
- Violations: `policy.selectors.violations_for_pull_request(scope, pk)` with per-violation acknowledge/waive
  posting to the existing `policy:violation_bulk_action` (it already takes a list of ids and a comment) and
  swapping the violations fragment back.
- Churn slot: `ChurnResult` for the PR when one exists, otherwise an explicit "not computed yet" empty state
  (phase 10 fills it) — never a `0%`.
- AI signals with evidence and disclosure stay as they are.

### 4. Reviews page

`apps/dashboards/reviews.py`, everything from `metrics.selectors.scoped_reviews(scope)` /
`scoped_pull_requests(scope)`:

- `reviewer_load(scope, params)` → `[(Person, reviews_given)]`, one `values(...).annotate(Count)` query,
  descending by count. Reviewer load *is* a workload chart, so descending order is correct here — it is not a
  people ranking (RISKS row 1); the page says so in its subtitle.
- `author_reviewer_matrix(scope, params)` → `HeatMap(authors, reviewers, cells[author_id][reviewer_id], max_value)`
  from one `values("pull_request__author__person", "reviewer__person").annotate(Count)` query, capped at the top
  `REVIEW_HEATMAP_TOP_N` (new `AppSetting`, default 15) people per axis with the rest folded into an "Other" row
  and column so the table stays readable. Rendered as a server-side HTML table (Chart.js has no matrix type),
  one of five intensity classes per cell; the five steps are **new tokens** `--heat-0…--heat-4` in
  `static/css/tokens.css` with a separate light and dark scale (spec §10.5), so the no-hardcoded-colour grep test
  keeps passing. Each cell carries a text value as well as a colour (§10.5 "status is never colour-only").
- `prs_waiting_for_review(scope, params)` → open, non-draft PRs with no `first_review_at`, ordered by wait length,
  with the hours waited computed in the same query.
- `review_load_share` KPI via `metrics.compute()` (already registered, `global/project/repo`).

`GET /reviews/` → `views.reviews`, scope from the query string (`scope_type`/`scope_id`, same rule as
`chart_json`), filter bar, htmx fragment / full page. A new `reviewer_load` `ChartSpec` joins `CHART_REGISTRY`
(so `/api/charts/reviewer_load/` and `charts.js` are reused as-is), and `charts.py` gains
`DASHBOARD_CHART_KEYS` / `REVIEWS_CHART_KEYS`; `services.build_dashboard()` iterates `DASHBOARD_CHART_KEYS`
instead of the whole registry so the new chart does not appear on every dashboard page.
The reviewer-load table and the heat map both get CSV/XLSX buttons via `TABLE_SPECS["reviewer_load"]`.

### 5. `exports/reports.py` — the seven-sheet dashboard report

`write_xlsx()` is first split so the report and the table export share every formatting rule:

```python
# exports/xlsx.py
def open_workbook(*, constant_memory: bool) -> tuple[Workbook, BytesIO, Formats]
def write_sheet(workbook, formats, sheet_name, columns, data_rows) -> Worksheet   # header, widths, freeze,
                                                                                 # autofilter, typed cells
def write_xlsx(columns, data_rows, sheet_name) -> bytes        # unchanged signature, now a two-line wrapper
```

Table exports keep `constant_memory=True` (streaming, spec §10.6). The **report** opens the workbook with
`constant_memory=False, in_memory=True`: native charts and several sheets need the workbook to keep its sheets
addressable, and a report is bounded by `EXPORT_SYNC_MAX_ROWS` or runs in the background anyway (DECISIONS).

```python
def build_report(scope, params, *, user, language) -> bytes
def report_filename(scope, params) -> str    # pr-radar_report_<scope-slug>_<from>_<to>.xlsx, ASCII
```

Sheets, in order, names translated into `language` (≤31 chars, invalid characters stripped by the existing
`_clean_sheet_name`):

| Sheet | Content |
|---|---|
| Summary | scope, period, previous period; one row per KPI of the page's rows with value, previous value, delta, and the AI / non-AI cohort values |
| Trends | one row per interval with every series of the page's charts as columns, plus **native Excel charts** (`workbook.add_chart`) for throughput, AI share and lead time, anchored on the sheet |
| Projects / Repositories / People | the page's own tables (`TABLE_SPECS` + `tables.full_rows()`), exactly the sheets the level has |
| PRs | every PR in scope in the period: repo, number, URL (hyperlink), author, state, dates, size, `ai_status`, tools, disclosure, review rounds, churn, violations count |
| Violations | violations created in the period, from `policy.selectors.violations_in_scope()` |
| Metrics | the registry reference: key, title, description, formula, unit, direction, levels, cohorts |
| Parameters | filters (the canonical query string, expanded one row per filter), user, generation time, last successful sync time, tool version, AI-cohort setting, `REPORT_TIMEZONE`, report language |

`build_report` is pure (`scope`, `params`, `user`, `language` → bytes) so the huey task can call it with
`translation.override(job.language)` and no request. `Person.notes` is in no sheet's column list — structurally,
not by filtering — and a test greps the produced bytes for a sentinel note.

`GET /report.xlsx?scope_type=&scope_id=&<filters>` → `views.export_report`, available on Overview, Project,
Repository, Person and Policy, in both Period and Day mode. The Policy level renders the same sheet set with the
policy page's tables. Button: "Download report (XLSX)" beside the existing per-table export menu.

### 6. `ExportJob`, background export, "My exports"

`apps/dashboards/models.py` (new app models module + `migrations/0001_initial.py`):

```python
EXPORT_STORAGE = FileSystemStorage(location=settings.DATA_DIR / "exports")   # never MEDIA_ROOT: no URL serves it

class ExportJob(models.Model):
    class Kind(TextChoices):   TABLE_CSV, TABLE_XLSX, REPORT_XLSX
    class Status(TextChoices): PENDING, RUNNING, DONE, FAILED
    user, kind, params (JSON), language, status, file (FileField(storage=EXPORT_STORAGE)),
    rows (null), error_code (CharField, a code + params in `error_params`, never a rendered message),
    created_at, started_at, finished_at, expires_at
```

- `params` stores the canonical query string plus `scope_type`/`scope_id`/`table_key`/`fmt` — enough for the task
  to rebuild `Scope` **and re-resolve `scope_for_user(job.user)` at run time**, so a grant revoked between enqueue
  and run is honoured.
- `expires_at = created_at + EXPORT_RETENTION_DAYS` (new `AppSetting`, default 7).

Flow (`services.py`):

```python
def export_table_or_job(request, table_key, fmt) -> HttpResponse | ExportJob
def run_export_job(job_id) -> ExportJob        # PENDING -> RUNNING -> DONE/FAILED, writes the file + AuditEntry
def cleanup_exports(now=None) -> CleanupResult # deletes expired files+rows, sweeps stuck RUNNING jobs
```

- `views.export_table` / `views.export_report` count the rows first; `rows > EXPORT_SYNC_MAX_ROWS` ⇒ create the
  job, enqueue `tasks.export_job_task(job.id)`, and return the "queued" fragment (htmx) or a redirect to
  `/exports/` (normal request). At or below the cap the file is streamed in the request as today.
- ADR 0005 ("every huey task is also a management command"): `manage.py process_exports` runs every `PENDING`
  job inline, `manage.py cleanup_exports` runs the cleanup — nothing is lost when no worker runs.
- `tasks.py`: `@db_task() export_job_task(job_id)` and
  `@db_periodic_task(crontab(hour="3", minute="0")) cleanup_exports_task()`, both thin wrappers over `services`.
- Stuck sweep: `status=RUNNING` and `started_at` older than one hour ⇒ `FAILED` with `error_code="stuck"`, so the
  htmx poller never spins forever (ARCHITECTURE "Background worker").
- `GET /exports/` → "My exports": **the caller's own jobs only** (`ExportJob.objects.filter(user=request.user)`),
  newest first. htmx `hx-trigger="every 3s"` on the list fragment while any job is `PENDING`/`RUNNING`, polling
  stops (the fragment stops emitting the trigger) once all are terminal.
- `GET /exports/<pk>/download/` → `FileResponse`; `job.user_id != request.user.id` ⇒ **403** (not 404 — this is an
  identity refusal on an object the caller reached by id, unlike an out-of-scope filter value); missing/expired
  file ⇒ a visible "expired" fragment/page, never an empty body.

### 7. `AuditEntry` for every export

One helper, called on every path that *produces a file* (sync CSV, sync XLSX, sync report, background job
completion):

```python
record_audit(user, action, obj, after={"kind": …, "format": …, "table_key": …,
                                       "scope_type": …, "scope_id": …, "filters": <canonical query string>,
                                       "rows": <row count>})
```

with `action` in `{"export.table", "export.report"}`. For a background job the entry is written by the task when
the file is complete (that is when the row count is known) with `actor = job.user` and `object = job`. The entry is
English + codes (ARCHITECTURE: `AuditEntry` is not translated) and contains no `Person.notes` and no token.

### Error handling

- Unknown `table_key`/`fmt`/`chart_key`/`scope_type` ⇒ 404 (existing rule).
- Out-of-scope or unknown id in a **path** ⇒ 404 on a scoped queryset; in the **query string** ⇒ dropped silently.
- Another user's export ⇒ 403; expired file ⇒ a visible message.
- A failing background export ⇒ `status=FAILED` + `error_code`, rendered in the reader's language on
  "My exports"; the exception is logged (the `SecretMaskingFilter` is already on the root logger).
- Every htmx endpoint returns a visible fragment on error, never an empty 400 (CLAUDE.md).

### Architecture compliance

- `scope_for_user()` remains the single choke point; no view grows a per-view permission check (ARCHITECTURE
  "Rejected: a per-view permission decorator").
- `metrics.compute()`/`compute_many()` stay the only aggregate read entry point. `pr_detail.pr_metrics()` and
  `reviews.py` read raw rows through scoped selectors — per-row facts and page-specific lists, the same exception
  `dashboards/selectors.py` (Day mode) already documents.
- `DailyRollup` gains nothing; the person comparison uses medians computed from raw rows (ADR 0007).
- Deviations, all logged in DECISIONS.md: the `team` baseline on the person page, the report workbook's
  `constant_memory=False`, and `charts.DASHBOARD_CHART_KEYS` narrowing what `build_dashboard()` renders.

## Tasks

- [x] **T1** — Real `scope_for_user()` in `apps/accounts/selectors.py` (grant list, anonymous ⇒ empty, per-request
  memo). Tests `apps/accounts/tests/test_scope_for_user.py`: no rows ⇒ unrestricted; rows ⇒ exactly those project
  ids; anonymous ⇒ restricted-empty; superuser with rows is restricted; one query per request (memo).
- [x] **T2** — Close the selector gaps the real filter exposes: `catalog.selectors.people_for_settings/
  unmapped_identities/bot_person_count` honour their `scope`; `catalog.views.person_edit/person_merge` fetch from a
  scoped queryset; comment on `metrics.services._id_in_scope()`. Tests in `apps/catalog/tests/test_selectors.py`.
- [x] **T3** — `tests/test_scope_isolation.py`: **four separate tests** that a restricted lead sees another
  project's data in neither a page (`/`, `/projects/<other>/`), nor `/api/charts/<key>/`, nor the CSV export, nor
  the XLSX export — each paired with a positive assertion that their *own* project's data is present.
- [x] **T4** — `tests/test_scope_isolation.py` (continued): the global level for a restricted lead equals exactly
  the sum of their accessible projects (`prs_merged` over three projects, two granted); and an out-of-scope
  `project=`/`repository=`/`author=`/`scope_id=` in the query string is dropped silently (200, data unchanged),
  never 403, while the same id in a path is 404.
- [x] **T5** — People page: `views.people_index`, `/people/`, `dashboards/people_index.html` +
  `partials/index_content.html` reuse, nav entry, uk strings. Tests: 200 for a lead; **default ordering is by
  name** (asserted on the rendered row order and on `rows.people_rows()`); `assertNumQueries` bound.
- [x] **T6** — Person page shell: `views.dashboard` at `ScopeType.PERSON` via `/people/<pk>/`, scoped
  `get_object_or_404`, `person.html`. Tests: own-scope 200, out-of-scope person id in the path ⇒ 404, KPI row and
  charts render at person level.
- [x] **T7** — `apps/dashboards/person.py::build_comparison()` (person vs primary project vs organization vs
  previous period, `MIN_SAMPLE` greying). Tests `apps/dashboards/tests/test_person_comparison.py`: hand-computed
  values for a two-person project; a person with no PRs ⇒ `None`, never `0`; below-`MIN_SAMPLE` flagged.
- [x] **T8** — Private notes card + `POST /people/<pk>/notes/` (htmx fragment, full-page fallback,
  `AuditEntry` without the note text). Tests: save round trip, fragment returned for htmx, audit written,
  out-of-scope person ⇒ 404, anonymous ⇒ redirect.
- [x] **T9** — `apps/dashboards/pr_filters.py` + `forms.PullRequestFilterForm` + `DashboardParams.pr_filters` in
  `params.parse()`/`to_query_dict()`. Tests `apps/dashboards/tests/test_pr_filters.py`: query-string round trip;
  unknown enum dropped; out-of-scope author id dropped; empty filters serialise to nothing.
- [x] **T10** — `rows.pull_request_rows()` + `TABLE_SPECS["pull_requests"]` + `SEARCH_KEYS`. Tests:
  each filter narrows correctly (state, AI status, tool, size, has-violations, author, dates), violations count
  annotation, no N+1 (`assertNumQueries`). Found and fixed a real bug while writing these: `PRFilters.apply()`'s
  `tools` branch used `ai_tools__contains=[tool]`, which needs JSON1 containment SQLite's
  `supports_json_field_contains` reports as unavailable — switched to `ai_tools__icontains=f'"{tool}"'`
  (exact since `Tool` values never nest as a JSON-quoted substring of one another), with a regression test in
  `test_pr_filters.py`.
- [x] **T11** — PR list page `/prs/` (`views.pull_requests_index`, template, filter form in the filter bar, export
  buttons), nav entry, uk strings. Tests: 200 + fragment for htmx; the CSV export of a filtered list contains
  every matching row and only those.
- [x] **T12** — `apps/dashboards/pr_detail.py`: `timeline()` + `pr_metrics()`. Tests: event order and omission of
  absent events; durations against hand-computed values; a draft PR with no `ready_for_review_at` ⇒ `None`.
- [x] **T13** — PR detail page rebuild: timeline, metrics, files with test/excluded/sensitive badges (+ display
  cap), violations with acknowledge/waive, churn slot empty state, AI signals kept. Tests in
  `apps/dashboards/tests/test_pr_detail.py`: each block renders; out-of-scope PR ⇒ 404 (pre-existing test); the
  action swaps only the `#pr-violations` fragment on htmx and re-renders the whole page on a normal POST;
  `assertNumQueries` bound raised from `<10` to `<20` for the new blocks. **Deviation from the plan text, logged in
  DECISIONS**: the action posts to a new `dashboards:pull_request_violation_action`
  (`POST /prs/<pk>/violations/`), not to `policy:violation_bulk_action` — that view's htmx branch always re-renders
  the Policy console's own filtered/paginated table, which has no notion of "this one PR", so reusing it would have
  swapped the PR page's violations block with console-wide content. The new view calls the same
  `policy.services.apply_bulk_status_change()` and the same `BulkViolationActionForm`/translated notice wording, so
  behaviour and audit trail are identical; `violation_ids` is bound to
  `policy.selectors.violations_for_pull_request(scope, pk)`, so a violation from another PR is a validation error
  (test `test_violation_action_rejects_a_violation_from_another_pr`), not silently applied.
- [x] **T14** — `apps/dashboards/reviews.py`: `reviewer_load()`, `author_reviewer_matrix()` (with the "Other"
  fold), `prs_waiting_for_review()`. Tests `apps/dashboards/tests/test_reviews_selectors.py`: hand-computed
  counts; self-review excluded; bots excluded; empty scope ⇒ empty structures; query counts constant. All 9
  tests pass (`uv run pytest apps/dashboards/tests/test_reviews_selectors.py -q`).
- [x] **T15** — Reviews page `/reviews/`: `reviewer_load` `ChartSpec` + `DASHBOARD_CHART_KEYS`/`REVIEWS_CHART_KEYS`
  split (`services.build_chart_cards(scope, params, chart_keys=...)` now takes the key list explicitly),
  `reviewer_heat_map.html` partial with `--heat-0`..`--heat-4` tokens in `tokens.css` (light + dark, inline
  `style="background-color: var(--heat-N)"` per cell so Tailwind's static-class scanner is not in the loop),
  waiting-for-review list, `review_load_share` KPI row, `TABLE_SPECS["reviewer_load"]` (CSV/XLSX export buttons
  come for free via `partials/table.html`), nav entry. Tests in `apps/dashboards/tests/test_reviews_page.py`: 200
  full page + htmx fragment; `/api/charts/reviewer_load/` payload; heat-map cells carry both `data-heat-level`
  text and the colour; reviewer-load table's default order is workload (descending), not name.
  `tests/test_no_hardcoded_colors.py` still green (checked directly). **Not done as part of T15**: Ukrainian
  translations for the new strings (`make messages` not run this session) — still correctly gated behind T26 below,
  which was already scoped to do the phase's full i18n sweep at the end; until then `uk` falls back to the English
  msgid for every string this session added (Timeline, Metrics, Churn, Files, Reviewer load, Author × reviewer,
  Waiting for review, Violations-on-a-PR strings, etc.). `make css` also not run yet — needed once before T26 closes
  (new Tailwind classes were only ones already used elsewhere on other pages, but verify before commit).
- [x] **T16** — `exports/xlsx.py` refactor into `open_workbook()`/`write_sheet()`/`write_xlsx()` with no behaviour
  change; existing `test_export_xlsx.py` must pass untouched, plus a test that `write_sheet` can write two sheets
  into one workbook.
- [x] **T17/T18 — done this session.** `apps/dashboards/tests/test_reports.py` added: sheet names/order
  per level (four `ScopeType` levels × period/day mode — Policy isn't a `Scope` level, deferred to T19),
  native Excel charts present on Trends, a Summary value matching a direct `compute()` call, Parameters
  sheet fields, `report_filename()`. Found and fixed a real bug while writing it: `_write_value()` in
  `exports/xlsx.py` crashed on any `url` column when `base_url=""` (the T22 background-job call shape)
  because `xlsxwriter.write_url()` rejects an app-relative href outright — now falls back to a plain-text
  cell (see DECISIONS). All 31 tests across `test_reports.py`/`test_export_xlsx.py`/`test_export_view.py`
  green. Superseded text below (kept for the design-choice record, all now verified by the tests above):
  `apps/dashboards/exports/reports.py` (`build_report()`,
  `report_filename()`) is **written but has no tests yet** — code for both T17 (Summary, Trends + three native
  Excel charts, the level's table sheets) and T18 (PRs, Violations, Metrics, Parameters sheets) landed in one pass
  since it's one cohesive function; a throwaway smoke test (`build_report()` on a GLOBAL scope produces a valid
  `.xlsx`, deleted before commit — not part of the suite) confirmed it runs without crashing, but **no assertions
  exist yet** on sheet names/order, KPI values matching `compute()`, native charts being present
  (`ws._charts` non-empty on Trends), the five-level × two-mode sheet-completeness matrix, or the Parameters sheet's
  fields. `apps/dashboards/services.TABLE_KEYS_BY_LEVEL` was made public (was `_TABLE_KEYS_BY_LEVEL`) so
  `reports.py` can reuse it without duplicating the level→table-sheet mapping; `apps/dashboards/exports/xlsx.py`
  gained no further changes beyond T16's `open_workbook()`/`write_sheet()`. Next session: write
  `apps/dashboards/tests/test_reports.py` per T17/T18's own criteria above, fix whatever it finds (the design
  choices below are This Session's untested assumptions, most likely first place to look for bugs), then mark T17
  and T18 done separately once each's own tests pass. **Logged design choices needing verification against real
  test assertions**, not yet in DECISIONS.md because they're unconfirmed: Summary sheet uses `type="float"` for
  every numeric KPI column (value/previous/delta/AI/non-AI) rather than the metric's own unit-specific type, so a
  cell holds `compute()`'s raw value directly (ratio 0..1, duration in seconds) — chosen so one column shape works
  across metrics of different units in one table; Trends sheet is pivoted from the *existing* `throughput`/
  `ai_adoption`/`latency` `ChartSpec.build()` payloads (not a fresh `compute()` call) so it can never disagree with
  the dashboard's own charts, restricted to those three (not all six `DASHBOARD_CHART_KEYS`) since only these three
  are literally "one row per interval" — `pr_size_distribution`/`churn_rework` bucket by size/metric-name, not by
  date; the Policy console (not a `Scope`/`DashboardParams` page) is **not yet wired to `build_report()`** — T19
  needs to decide what `scope`/`params` a Policy-triggered report gets, since Policy's own filters
  (`ViolationFilterForm`) never went through `DashboardParams`; `base_url: str = ""` (not `request`) is how
  `build_report()` stays request-free per plan §5 while still emitting absolute hyperlinks when a view supplies one
  (`request.build_absolute_uri("/")`) — unverified end-to-end since no view calls it yet (that's T19).
- [x] **T18** — see T17 above; both done together, tests exist and pass.
- [x] **T19** — `views.export_report` + `/report.xlsx` (`dashboards:export_report`) + the "Download report (XLSX)"
  button, done for the four `ScopeType`-based pages (Overview/Project/Repository/Person — the `dashboard()` view,
  `show_report_link=True` in its context, rendered by `filter_bar.html`'s new `{% report_url %}` tag). **Policy is
  deliberately not wired** (logged in DECISIONS and in the view's own docstring): Policy filters through
  `ViolationFilterForm`, not `DashboardParams`/`Scope`, so it needs its own scope/params mapping — a follow-up task,
  not guessed at here. uk strings for "Download report (XLSX)" **not yet added** (still gated behind T26's i18n
  sweep, same as T15). Tests: `apps/dashboards/tests/test_export_report_view.py` (200 + XLSX content type + ASCII
  filename, project/person scope 200, unknown scope_type 404, day-mode filename, button renders on Overview) and
  `tests/test_scope_isolation.py::test_restricted_lead_report_excludes_other_project` (the report-level case of T3's
  criterion). All green.
- [x] **T20** — `apps/dashboards/tests/test_export_no_notes.py`: parametrized over every `TABLE_SPECS` key's CSV and
  XLSX, and over the report at all four `ScopeType` levels, with a sentinel note on a seeded `Person`. **Note**: the
  xlsx assertions read cells back with `openpyxl` rather than a raw byte search — `.xlsx` is a DEFLATE-compressed
  zip, so `sentinel.encode() in xlsx_bytes` would silently pass even if the sentinel *were* written (verified this
  empirically before writing the real assertion). All green.
- [x] **T21 — finished in review_fix1.** `apps/dashboards/models.py` (`ExportJob`, `Kind`/`Status` choices,
  `compute_expires_at()`), migration `apps/dashboards/migrations/0001_initial.py`, `EXPORT_RETENTION_DAYS`
  `AppSetting` (`apps/catalog/setting_defs.py` + seed migration `0008_export_retention_days_setting.py`),
  `apps/dashboards/admin.py` (`ExportJobAdmin`, read-only), and model tests in `test_export_jobs.py`
  (`test_export_job_defaults`, `test_compute_expires_at_uses_the_retention_setting`).
  **Deviation from the plan's pseudocode, logged in DECISIONS**: `EXPORT_STORAGE` is a **callable** (a zero-arg
  function returning `FileSystemStorage(location=DATA_DIR/"exports")`), not a bare instance — Django's migration
  serializer bakes an instantiated `FileSystemStorage`'s `location` into the migration file as a literal absolute
  path (this machine's `DATA_DIR`), which would break `makemigrations --check` and the file's real location on any
  other machine/environment; a callable is recorded as an import reference instead and re-evaluated at runtime.
- [x] **T22 — finished in review_fix1.** Background export: the threshold branch in `views.export_table`/
  `views.export_report`, `services.create_export_job()`/`run_export_job()`, `apps/dashboards/tasks.py::
  export_job_task`, `manage.py process_exports`. Tests in `test_export_jobs.py`: an export above
  `EXPORT_SYNC_MAX_ROWS` returns an `ExportJob` and the enqueue is asserted (task patched); at-cap still streams;
  the task re-resolves `scope_for_user` at run time (a grant revoked after enqueue is honoured); a failing job
  ends `FAILED` with an `error_code`, not an exception.
- [x] **T23 — finished in review_fix1.** "My exports" page `/exports/` (`partials/exports_list.html`, htmx
  polling every 3s while any job is pending/running) + `/exports/<pk>/download/` (403 on another user's job, a
  visible "unavailable" page for a not-yet-done or expired one). Tests: own-jobs-only, 403, polling stops once
  terminal, a `FAILED` job's translated reason, `assertNumQueries` bound, anonymous redirect.
- [x] **T24 — finished in review_fix1.** `services.cleanup_exports()` + `apps/dashboards/tasks.py::
  cleanup_exports_task` (periodic, 03:00) + `manage.py cleanup_exports`. Tests: a file past its retention window
  is deleted (file gone and row gone); a fresh job survives; a `RUNNING` job stuck over an hour is swept to
  `FAILED` with `error_code="stuck"`; a recently-started `RUNNING` job is left alone.
- [x] **T25 — finished in review_fix1.** `services.record_export_audit()`, called from `views.export_table`
  (both formats), `views.export_report` and `services.run_export_job()` on success. Tests in
  `test_export_audit.py`, parametrized over sync CSV, sync XLSX, sync report and a completed background job —
  each writes exactly one entry recording user, kind, filters and row count; a failed job writes none.
- [x] **T26 — finished in review_fix1.** `make messages` (38 previously-empty + 46 fuzzy msgids translated by
  hand), `make css` (no-op — the compiled CSS already covered every class this phase's templates use),
  `tests/test_pages_smoke.py` extended with `people_index`, `person`, `pull_requests_index`,
  `pull_request_detail`, `reviews`, `exports_index` in en/uk × light/dark; `tests/test_translations.py` green.
- [x] **T27 — finished in review_fix1.** `docs/user/people.md`, `docs/user/pull-requests.md`,
  `docs/user/reviews.md`, `docs/user/exports.md`; `docs/SETUP.md`'s "Run" section now covers the worker's export
  role and the `process_exports`/`cleanup_exports` fallback commands; `docs/user/dashboards.md`'s stale
  "background exports are planned" line now points at `exports.md`; `CHANGELOG.md` phase-9 entries; a new
  `## Phase 9` section in `docs/DECISIONS.md`. `docs/CONFIGURATION.md` already covered every new setting from
  the T21 session and needed no further change.

## Verification

```
uv run ruff format .
uv run ruff check . && uv run ruff format --check . && uv run mypy \
  && uv run python manage.py makemigrations --check --dry-run && uv run python manage.py check
uv run pytest -q
make css        # after template/JS changes; commit static/css/app.css
make messages   # after any new UI string; commit locale/uk/LC_MESSAGES/*.po|.mo
```

Test command is unchanged: `uv run pytest -q`.

| # | Acceptance criterion | Test that proves it |
|---|---|---|
| 1 | Restricted lead sees no other project's data in a **page** | `tests/test_scope_isolation.py::test_restricted_lead_page_excludes_other_project` (T3) |
| 2 | …nor in a **chart JSON endpoint** | `…::test_restricted_lead_chart_json_excludes_other_project` (T3) |
| 3 | …nor in a **CSV export** | `…::test_restricted_lead_csv_export_excludes_other_project` (T3) |
| 4 | …nor in an **XLSX export** | `…::test_restricted_lead_xlsx_export_excludes_other_project` (T3), plus the report-level case in T19 |
| 5 | Global level = exactly the sum of accessible projects | `…::test_global_equals_sum_of_accessible_projects` (T4) |
| 6 | Out-of-scope id in the query string is dropped silently | `…::test_out_of_scope_query_ids_are_dropped_silently` + `…::test_out_of_scope_path_id_is_404` (T4) |
| 7 | Report XLSX opens with openpyxl and has every mandated sheet | `apps/dashboards/tests/test_reports.py::test_report_contains_every_mandated_sheet` (parametrized over the five levels × two modes, T18) |
| 8 | `Person.notes` appears in no exported file | `apps/dashboards/tests/test_export_no_notes.py` (T20) |
| 9 | 20 001 rows ⇒ `ExportJob`, enqueue asserted | `apps/dashboards/tests/test_export_jobs.py::test_export_above_sync_cap_returns_job_and_enqueues` (T22) |
| 10 | Another user's export download ⇒ 403 | `…test_export_jobs.py::test_download_of_another_users_export_is_forbidden` (T23) |
| 11 | A file past its 7-day expiry is deleted by the cleanup task | `…test_export_jobs.py::test_cleanup_deletes_expired_file_and_row` (T24) |
| 12 | Every export writes an `AuditEntry` (user, kind, filters, rows) | `apps/dashboards/tests/test_export_audit.py` (parametrized, T25) |
| 13 | People table default ordering is by name | `apps/dashboards/tests/test_people_page.py::test_people_table_defaults_to_name_order` (T5) |

Standing gates this phase must not break: `tests/test_urls_login.py` (every new named URL redirects anonymous —
add `pk`/`table_key` samples), `tests/test_no_hardcoded_colors.py` (the heat-map scale lives in `tokens.css`),
`tests/test_css_tokens.py` (run `make css`), `tests/test_translations.py` (no empty/fuzzy `uk` msgstr, placeholders
match), `tests/test_pages_smoke.py`, `tests/test_token_leak.py` (export files included), and the
`assertNumQueries` suite in `apps/dashboards/tests/test_query_counts.py`, extended with the four new list pages.

## Risks

| Row | Risk | What this plan does |
|---|---|---|
| 3 | A restricted lead sees another project's data through a non-page exit | The stub dies in T1; T3/T4 assert the four exits separately, each with a positive control so a broken page cannot pass as "isolated"; the report path gets its own case in T19; every new selector composes on `ScopeFilter`; background jobs re-resolve `scope_for_user()` at run time, not at enqueue time |
| 13 | An export leaks or misbehaves | `Person.notes` is excluded by construction (never in a column list) and asserted absent across every CSV, XLSX and report (T20); the >cap path becomes a job instead of blocking a request (T22); files live under `DATA_DIR/exports/` behind an author-only view (T23) and are deleted after 7 days (T24); every export writes an `AuditEntry` (T25); CSV injection neutralisation and `strings_to_formulas=False` are unchanged and still covered |
| 1 | A wrong number lands in a 1:1 | The People table defaults to name order and is asserted (T5); the person comparison uses medians over raw rows at project/organization level, never a median of per-person medians (T7); `MIN_SAMPLE` greying carries over from the shared KPI/table code; the Reviews page labels reviewer load as workload, not ranking; private notes are audited without copying their text |
| 10 | Dashboards miss the latency budget / N+1 | `assertNumQueries` on People, PR list, PR detail, Reviews and My exports (T5, T10, T13, T14, T23); the heat map and reviewer load are one aggregate query each; the PR list annotates the violations count instead of per-row counting; `scope_for_user()` is memoised per request |
| 8 | Ukrainian lags the UI | T26 is a task in this phase, not a later one; the smoke test renders every new page in both languages and both themes |

## Out of scope

- **Churn values** on the PR page and in the churn KPI — phase 10 computes `ChurnResult`; this phase renders the
  slot and its "not computed yet" empty state only.
- `ci_first_pass_rate` and `followup_fix_rate` — phase 10.
- A `team` dimension in `Scope`/`DailyRollup` for a per-team median baseline — deferred (DECISIONS); the person
  page compares against the primary project and the organization.
- Performance profiling against the 50-repo / 20 k-PR target, index tuning and the 1.5 s budget — phase 11.
- Playwright e2e specs for the new pages, and the final documentation/proofreading pass — phase 11.
- User and role management UI (Django admin covers it, spec §10.3), and a UI for granting `UserProjectAccess`
  (Django admin covers it; the model and its enforcement are this phase's deliverable).
