# Phase 8 — Dashboards, charts, themes and table export

**Goal:** Overview, Project and Repository pages in Period and Day modes, with KPI rows, charts, tables, both
themes, and CSV/XLSX export of any table.

**User-facing:** yes — this is the first phase that renders a number to a lead. It gets e2e cases and user docs.

---

## Context

### What exists

- **`metrics.compute()` is complete and is the only read entry point** (phase 7).
  `compute(metric_keys, scope, date_from, date_to, cohort="all", granularity="day") -> MetricResultSet`.
  Each `MetricResult` already carries `value`, `previous_value`, `delta`, `delta_ratio`, `sample_size`,
  `previous_sample_size`, `below_min_sample`, `series[SeriesPoint(date_from, date_to, value, sample_size)]` and
  `breakdown[BreakdownItem(label, value, sample_size)]`. It is read-through cached in `FileBasedCache` under a key
  containing `data_version()` and an access fingerprint. **Everything this phase renders comes from it.**
- **`metrics.services.scope_for(user, scope_type, scope_id) -> Scope`** already resolves `scope_for_user()` and
  **silently drops an out-of-scope id back to global** (RISKS row 3). Views never have to decide what a bad id means.
- **39 metrics are registered** (`docs/METRICS.md` is generated from the registry). The ones this phase renders:
  adoption — `ai_pr_share`, `disclosure_rate`, `violations_open`, `violations_new`, `ai_active_people`,
  `ai_pr_count`, `disclosure_mismatch_count`, `ai_status_breakdown`, `ai_tool_breakdown`, `violations_by_rule`;
  flow — `prs_opened`, `prs_merged`, `prs_closed_unmerged`, `reviews_given`, `lead_time_p50/p90`,
  `time_to_first_review_p50/p90`, `cycle_time_p50`, `pr_size_p50`, `pr_size_buckets`, `open_prs`, `stale_prs`,
  `waiting_review_24h`, `wip_per_person`, `review_load_share` (**not** available at person level),
  `reviewer_response_p50` (**person level only**); quality — `rework_rate`, `ci_first_pass_rate`, `revert_rate`,
  `churn_21d` (always `None` until phase 10's `compute_churn`), `review_rounds_avg`, `self_merge_rate`,
  `rubber_stamp_rate`, `test_change_ratio`, `test_lines_ratio`, `review_comments_per_100_lines`,
  `followup_fix_rate` (labelled heuristic, empty until phase 10).
- **`MetricDef` carries everything the UI needs to render a metric without a second registry**: `title`,
  `description` (both lazy-translated), `unit` (`count|ratio|duration|lines|breakdown`),
  `direction` (`higher_is_better|lower_is_better|neutral`), `levels`, `supports_cohorts`, `formula`.
- **Rollup batching already answers the "repository in two projects" question correctly.**
  `calculators/base.py::_grouped_value()` computes the GLOBAL value from the whole queryset and derives PROJECT
  values from REPO values in Python, so a repository in two projects contributes its full value to each project
  but is counted **once** globally. This phase only has to prove it end to end at the page level.
- **Authorization.** `accounts.selectors.scope_for_user(user)` returns an unrestricted `ScopeFilter` until phase 9;
  every selector already takes one. `apps/metrics/selectors.py` narrows by `Scope`/cohort.
- **`apps/dashboards/` is nearly empty**: `views.overview` (a placeholder template), `views.pull_request_detail`
  (phase 5), `formatting.py::format_duration` (+ its JS twin `static/js/formatting.js` and the shared case table
  `tests/fixtures/duration_cases.json`), `templatetags/formatting.py::humanize_duration`, `urls.py` with two routes.
- **UI plumbing exists**: `templates/base.html` (writes `data-theme` on `<html>`, htmx CSRF header,
  `javascript-catalog` with `packages=["apps.accounts", "apps.dashboards"]`), `partials/nav.html`,
  `partials/theme_switcher.html` + `static/js/theme.js` (**already dispatches a `themechange` CustomEvent on
  `document` and listens to `matchMedia`**), `partials/language_switcher.html`, `static/css/tokens.css` (the token
  set the spec names, including `--series-ai`, `--series-non-ai`, `--series-1..8`, `--grid`, `--tooltip-bg`),
  `config/htmx.py::is_htmx` (ADR 0006), `static/vendor/htmx.min.js`, `make css` / `make vendor` / `make messages`.
- **Precedents to copy, not reinvent**: `apps/policy/views.py` (lenient `request.GET` form → selector → paginator
  → `is_htmx` fragment-or-page), `apps/policy/forms.py::ViolationFilterForm` with its `_Lenient*Field` classes
  (an unknown or out-of-scope id is dropped, never a validation error),
  `apps/policy/templates/policy/partials/rule_chart.html` (a chart with a `sr-only` data-table twin),
  `apps/policy/selectors.py::projects_in_scope/repositories_in_scope`.
- **`django_tables2` and `django_filters` are already in `INSTALLED_APPS`; `xlsxwriter` is a runtime dependency
  and `openpyxl` a dev one.** None of them is used by any code yet. **Chart.js is not vendored yet** —
  `static/vendor/` holds only htmx.
- **Settings already defined** (`catalog/setting_defs.py`): `DEFAULT_PERIOD_DAYS`, `METRICS_CACHE_TTL_SECONDS`,
  `MIN_SAMPLE=5`, `STALE_DAYS`, `WAITING_REVIEW_HOURS`, `PR_SIZE_BUCKETS`, `EXPORT_SYNC_MAX_ROWS=20000`,
  `DEFAULT_THEME`, `DEFAULT_UI_LANGUAGE`. Readers: `catalog/services.py::get_int/get_bool/get_str/get_list/get_dict`.
- **Gates that will bite**: `tests/test_no_hardcoded_colors.py` (a `#hex`/`rgb(` outside `tokens.css` fails the
  build — so chart colours must be **token names** resolved in JS), `tests/test_css_tokens.py::test_app_css_is_not_stale`
  (any new template or `.py` under Tailwind's scan globs requires `make css`), `tests/test_translations.py`
  (no empty/fuzzy `msgstr`, matching placeholders, and a `CANARY_ENGLISH_STRINGS` list currently holding
  `Log in, Log out, Overview, Password, Username`), `tests/test_urls_login.py` (walks `urlpatterns`, asserts every
  new route redirects an anonymous user).

### What this phase changes

`apps/dashboards/` becomes the read surface: one parameter object, one view rendering three scopes plus a Day
mode, six chart JSON endpoints, four tables, and a three-renderer export layer. `apps/metrics/` gains exactly one
new public function (`compute_many()`); `apps/catalog/selectors.py` gains the scoped population selectors the
tables need. No model is added in this phase.

### Key files

```
apps/dashboards/params.py            NEW  DashboardParams + parsing/round-tripping the query string
apps/dashboards/forms.py             NEW  DashboardFilterForm (lenient, mirrors policy/forms.py)
apps/dashboards/kpis.py              NEW  the three KPI rows + the Day-mode rows, declaratively
apps/dashboards/charts.py            NEW  CHART_REGISTRY: chart_key -> ChartSpec (+ JSON builders)
apps/dashboards/tables.py            NEW  django-tables2 classes generated from ExportColumn lists
apps/dashboards/rows.py              NEW  row builders: entities -> dict rows via metrics.compute_many()
apps/dashboards/selectors.py         NEW  dashboard-specific scoped reads (recent PRs, day lists)
apps/dashboards/views.py             EDIT dashboard(), chart_json(), export_table(), index pages
apps/dashboards/urls.py              EDIT
apps/dashboards/exports/{__init__,columns,csv,xlsx}.py   NEW
apps/dashboards/templatetags/dashboards.py               NEW  data-as-of banner, kpi/percent/number filters
apps/dashboards/templates/dashboards/{dashboard,day,projects,repositories}.html  NEW
apps/dashboards/templates/dashboards/partials/*.html                            NEW
apps/dashboards/management/commands/seed_demo.py         NEW
apps/metrics/services.py             EDIT compute_many()
apps/catalog/selectors.py            EDIT projects/repositories/people in scope
apps/policy/selectors.py             EDIT delegate its two scope selectors to catalog's
static/js/charts.js                  NEW
static/vendor/chart.umd.js           NEW (vendored, committed)
Makefile                             EDIT  vendor: also fetch Chart.js
templates/partials/nav.html          EDIT  Overview · Projects · Repositories nav entries
locale/uk/LC_MESSAGES/django{,js}.po EDIT
```

---

## Design

### 1. Query-string state: `DashboardParams`

All filter state lives in the query string (spec §10.1, ARCHITECTURE "UI filter state → the query string").
`apps/dashboards/params.py` owns it:

```python
@dataclass(frozen=True)
class DashboardParams:
    mode: str            # "period" | "day"
    preset: str          # "7d" | "30d" | "90d" | "this_month" | "last_month" | "quarter" | "custom"
    date_from: date      # resolved, inclusive
    date_to: date        # resolved, inclusive
    day: date            # only meaningful in day mode (date_from == date_to == day)
    granularity: str     # "day" | "week" | "month"  (resolved; "auto" never survives parsing)
    granularity_is_auto: bool
    cohort: str          # "all" | "ai" | "non_ai" | "compare"
    project_ids: tuple[int, ...]   # multi-select narrowing, Overview only
    repository_ids: tuple[int, ...]
    q: str; sort: str; page: int; table: str    # table state, also in the query string

    def to_query_dict(self) -> QueryDict      # canonical, sorted, omits defaults
    def replace(self, **kwargs) -> DashboardParams
```

- Parsed by `DashboardFilterForm` (`forms.py`), built like `ViolationFilterForm`: **lenient** — an unknown preset,
  a malformed date, an out-of-scope project id or a bad granularity falls back to the default instead of raising.
  The scoped `project`/`repository` querysets are injected by the view, so an id outside `scope_for_user()` is
  dropped silently (RISKS row 3, and the behaviour phase 9's restricted lead depends on).
- `preset="custom"` reads `from`/`to`; every other preset derives the range from `timeframe.today()` — so a shared
  link with `preset=30d` means "the last 30 days *now*", which is what a shared dashboard link should mean.
- **Auto granularity** (spec §10.1 "автовибір за довжиною періоду"): ≤ 14 days → `day`, ≤ 92 days → `week`,
  otherwise `month`. An explicit `granularity=` in the query string wins and sets `granularity_is_auto=False`.
- `to_query_dict()` is what the filter bar's links and the export/chart URLs are built from, which is what makes
  "changing a filter changes the query string, and the query string alone restores the view" testable as a
  round trip: `parse(params.to_query_dict()) == params`.

### 2. One view, three scopes, two modes

```python
# apps/dashboards/urls.py
""                          -> dashboard   (ScopeType.GLOBAL, None)   name="overview"
"projects/"                 -> projects_index
"projects/<int:pk>/"        -> dashboard   (ScopeType.PROJECT, pk)    name="project"
"repos/"                    -> repositories_index
"repos/<int:pk>/"           -> dashboard   (ScopeType.REPO, pk)       name="repository"
"api/charts/<slug:chart_key>/" -> chart_json
"export/<slug:table_key>.<slug:fmt>"  -> export_table    # fmt in {csv, xlsx}
"prs/<int:pk>/"             -> pull_request_detail       (unchanged)
```

`dashboard()` is thin: build `DashboardParams` from the form → `metrics.services.scope_for(request.user, …)` →
call `services.build_dashboard(scope, params)` → render. `apps/dashboards/services.py` assembles the page context
(KPI rows, chart descriptors, table instances, banner) and holds no query of its own beyond `compute*()` and
`selectors.py`. Period mode renders `dashboard.html`; Day mode renders `day.html`; both extend a shared
`partials/_page.html` carrying the filter bar and the banner. `is_htmx(request)` returns the
`partials/dashboard_content.html` fragment from the **same URL**, so filter changes swap only the content region
while the browser URL is updated by htmx `hx-push-url` (ADR 0006; the Back-button case is phase 8's e2e).

Person scope exists in the registry but its page is phase 9; this phase renders `global|project|repo` only.

### 3. KPI rows

`apps/dashboards/kpis.py` declares rows as data, not markup:

```python
@dataclass(frozen=True)
class KpiSpec:
    metric_key: str
    secondary_key: str | None = None  # rendered as a sub-value in the same card
    compare_cohorts: bool = False  # render AI vs non-AI under the value (spec §10.3 row 3)
```

- Row 1 (adoption): `ai_pr_share`, `disclosure_rate`, `violations_open`, `ai_active_people`.
- Row 2 (flow): `prs_merged`, `lead_time_p50`, `time_to_first_review_p50`, `open_prs` (+ secondary `stale_prs`).
- Row 3 (quality, `compare_cohorts=True`): `rework_rate`, `ci_first_pass_rate`, `revert_rate`, `churn_21d`.
- Day mode row: `prs_opened`, `prs_merged`, `prs_closed_unmerged`, `reviews_given`, `ai_pr_count`,
  `violations_new`; end-of-day state row: `open_prs`, `waiting_review_24h`, `stale_prs`.

Rendering rules live in `partials/kpi_card.html` + `templatetags/dashboards.py` and follow spec §10.2:
big value + unit, delta vs previous period with an **arrow glyph (▲/▼/–) as well as** colour (`--good`/`--bad`/
`--neutral` chosen from `MetricDef.direction`; `neutral` and `|delta_ratio| < 5%` are grey), a sparkline drawn
from `MetricResult.series` as **inline SVG built server-side** (no Chart.js, no extra request, and it survives an
htmx swap), the metric title as a tooltip carrying `MetricDef.description`, `below_min_sample` greying the card
and adding a visible "small sample" badge, and `value is None` rendering an em dash with an explanatory empty
state — never `0`. `compare_cohorts` cards call `compute()` a second and third time (cohort `ai`, `non_ai`) and
show both numbers; `cohort=compare` in the filter bar turns this on for *every* row.

Formatting: durations through the existing `format_duration` (the JS twin already exists), ratios as `0.0%`,
counts through Django's locale-aware `intcomma`-style formatting (`USE_THOUSAND_SEPARATOR` per locale).

### 4. Charts

`apps/dashboards/charts.py` holds `CHART_REGISTRY: Mapping[str, ChartSpec]`:

```python
@dataclass(frozen=True)
class ChartSpec:
    key: str
    title: Promise
    chart_type: str  # "bar" | "line"
    stacked: bool
    metric_keys: tuple[str, ...]
    build: Callable[[Scope, DashboardParams], ChartPayload]
    levels: frozenset[str] = _ALL_LEVELS
```

The six charts (spec §10.3):

| key | shape | source |
|---|---|---|
| `throughput` | stacked bars per bucket | `prs_merged` series, once for cohort `ai` and once for `non_ai` |
| `ai_adoption` | two lines | `ai_pr_share` and `disclosure_rate` series |
| `latency` | four lines | `lead_time_p50/p90`, `time_to_first_review_p50/p90` series |
| `pr_size_distribution` | stacked bars, one column per cohort | `pr_size_buckets` **breakdown** for the period, computed for `ai` and `non_ai` |
| `churn_rework` | grouped bars | `churn_21d` and `rework_rate` period values for `ai` vs `non_ai` |
| `violations_by_rule` | stacked bars per bucket | `violations_by_rule` breakdown, one `compute()` per time bucket |

`GET /api/charts/<chart_key>/?<the same query string>` returns

```json
{"key": "throughput", "type": "bar", "stacked": true, "unit": "count",
 "labels": ["2026-09-01", …], "x_title": "…", "y_title": "…",
 "datasets": [{"label": "AI", "color_token": "--series-ai", "data": [3, null, 5]}],
 "empty": false, "empty_message": "…"}
```

- **`color_token`, never a colour literal** — `charts.js` resolves it with `getComputedStyle(document.documentElement)`
  and re-resolves on `themechange`. This is what keeps `tests/test_no_hardcoded_colors.py` green and satisfies
  ADR 0008 (RISKS row 12).
- Labels and axis titles are translated server-side, so the JSON is already in the reader's language; `charts.js`
  only needs `gettext` for the few strings it owns (tooltip suffixes), via the existing `JavaScriptCatalog`.
- The endpoint calls the **same** `metrics.compute()` with the **same** `DashboardParams`, so a chart and a KPI
  card can never disagree; the acceptance test asserts the endpoint's numbers equal `compute()`'s directly.
- `violations_by_rule` is the only chart that needs a breakdown *per bucket*. It loops `compute()` over
  `timeframe.bucket_ranges(...)`, clamped to `CHART_MAX_BUCKETS = 31` by coarsening the granularity for that chart
  alone — a user forcing `granularity=day` over 365 days must not cost 365 `compute()` calls (RISKS row 10).
- A chart not available at the current level (`review_load_share` at person level, later) is omitted from the
  page rather than rendered empty; an unknown `chart_key`, or one not available at the scope, returns 404.
- Accessibility (spec §10.2 / ARCHITECTURE "Accessibility"): every `<canvas>` ships with a visually hidden data
  table of the same numbers, server-rendered — the same pattern `policy/partials/rule_chart.html` already uses.
  It is also what makes the page smoke test able to assert numbers without running JS.

`static/js/charts.js`: one module, `initCharts(root)` scanning `[data-chart-key]`, fetching the JSON, building the
Chart.js config from shared defaults (fonts, grid `--grid`, tooltip `--tooltip-bg`, percent/duration formatters
reusing `static/js/formatting.js`), keeping instances in a registry, and re-creating them on `themechange` and on
`htmx:afterSwap`. Chart.js 4 UMD is vendored to `static/vendor/chart.umd.js` by `make vendor` and **committed**, so
neither tests nor a run need the network.

### 5. Tables

`apps/dashboards/tables.py` defines four tables. Their columns are **generated from an `ExportColumn` list**, so
django-tables2, CSV and XLSX cannot drift apart (ARCHITECTURE: "one column description, three renderers"):

| table_key | rows | columns (beyond the name/link) |
|---|---|---|
| `projects` | projects in scope (Overview) | `prs_merged`, `ai_pr_share`, `disclosure_rate`, `violations_open`, `lead_time_p50`, `rework_rate` — each with its delta |
| `repositories` | repositories of the project | same metric set |
| `people` | people with activity in scope | `prs_merged`, `ai_pr_share`, `disclosure_rate`, `violations_open`, `lead_time_p50`, `pr_size_p50`, `rework_rate`, `reviews_given`, `reviewer_response_p50`, `churn_21d` — **default ordering by name** (RISKS row 1) |
| `recent_prs` | PRs in scope touched in the period | repo, number+link, title, author, state, created/merged, size, AI status, tools, violations count |

- `rows.py` builds metric-backed rows: one `metrics.compute_many()` call per table (see §6), then a plain list of
  dicts. Sorting, searching and pagination happen **in Python over that list** for metric tables (they are at most
  a few hundred rows and the numbers are not in the database in row form), and **in the ORM** for `recent_prs`
  (a real queryset with `select_related("repository", "author__person")` and `annotate(Count("violations"))`).
- Sticky header, right-aligned numbers, zebra/hover from tokens, badges for AI status and severity: a custom
  django-tables2 template `dashboards/partials/table.html` shared by all four.
- Every table renders an "Export ▾ → CSV / XLSX" control pointing at
  `/export/<table_key>.csv?<current query string>` — the current query string is what makes the export carry the
  same filters, sort and search.
- `assertNumQueries` on every list view (RISKS row 10 and this phase's acceptance criterion #2): each test pins an
  exact count, and a companion test adds rows and asserts the count grows only by the known per-row compute cost
  (zero for `recent_prs`), which is what makes a template-level N+1 fail the build.

### 6. `metrics.compute_many()` — the one addition to the metrics contract

A table of 50 repositories × 6 metrics must not be 50 `compute()` calls each doing its own `DailyRollup` query.
`apps/metrics/services.py` gains:

```python
def compute_many(metric_keys, scope_type, scope_ids, access, date_from, date_to,
                 cohort=Cohort.ALL, granularity="day") -> dict[int, MetricResultSet]
```

- Counter/ratio metrics for an unrestricted caller: **one** `DailyRollup` query with `scope_id__in=scope_ids`
  covering the current period, the previous period and every series bucket — reusing `_fetch_counter_ratio_rows`
  widened by `scope_id__in` and the existing `_period_value` arithmetic.
- Distribution/state metrics, and everything for a restricted caller, fall back to the existing per-scope path.
  This is honest rather than clever: ADR 0007 already documents the fact-table escape hatch if percentiles per
  row become the bottleneck, and phase 10 owns profiling.
- **Contract test:** for every scope id, `compute_many(...)[id]` equals `compute(metric_keys, Scope(...), ...)`
  field by field. This is the test that makes the batching safe to trust, and it is cheap.

Deviation note: `ARCHITECTURE.md` lists `compute()` as *the* read entry point. `compute_many()` is an additive
batching wrapper **inside `apps/metrics`**, returning the same `MetricResultSet` values — dashboards still never
touch `DailyRollup` or a domain model directly. Logged in DECISIONS.

### 7. Export layer

```
exports/columns.py  ExportColumn(key, title, type, width, number_format) — types:
                    text | int | float | percent | duration | datetime | date | url | badge_list
                    + TABLE_SPECS: table_key -> (columns, row builder, filename slug)
exports/csv.py      stream_csv(columns, rows, filename) -> StreamingHttpResponse (UTF-8 BOM for Excel)
exports/xlsx.py     write_xlsx(columns, rows, sheet_name, meta) -> bytes
```

- **CSV injection** (RISKS row 13): a value whose first character is `=`, `+`, `-` or `@` is prefixed with an
  apostrophe. Applied to every text-ish cell, in one function, with its own test.
- **XLSX formatting** (spec §10.6, all mandated): `Workbook(..., {"constant_memory": True,
  "strings_to_formulas": False, "strings_to_urls": False})`; bold header row; `freeze_panes(1, 0)`; `autofilter`
  over the used range; widths from `ExportColumn.width` capped at 60; numbers written with `write_number` (never
  as strings); `percent` → `0.0%` with the raw ratio as the value; `duration` → **hours** as a number with format
  `0.0` and the column title suffixed ", h"; `datetime` → a real Excel datetime converted to `REPORT_TIMEZONE` and
  made naive, format `yyyy-mm-dd hh:mm`; `url` → `write_url` (clickable); `None` → an empty cell, never `0`; sheet
  names truncated to 31 chars with `[]:*?/\` stripped.
- `export_table(request, table_key, fmt)` rebuilds the **full** row set from the same params (no pagination) and
  streams it. Filename `pr-radar_<scope-slug>_<from>_<to>.<ext>`, ASCII-only regardless of UI language; Day mode
  uses `_<date>`. `EXPORT_SYNC_MAX_ROWS` is read and the row count recorded now; turning an overflow into an
  `ExportJob` is phase 9.
- Person notes are excluded by construction: `Person.notes` is not in any `ExportColumn` list, and phase 9 adds
  the asserting test alongside the report exporter.

### 8. `seed_demo`

`apps/dashboards/management/commands/seed_demo.py` — deterministic (`random.Random(20260918)`), idempotent
(`get_or_create` on stable `github_id`s), `--reset` to wipe what it created, `--days` (default 120). It builds:
2 organizations, 6 repositories, 3 projects **with one repository deliberately in two projects** (the acceptance
criterion), ~12 people (incl. one bot and one `exclude_from_metrics`), identities, ~250 PRs spread across the
window with realistic AI statuses/disclosures/tools, reviews, review comments, PR files, check statuses; then runs
the real `derive → detect → evaluate` pipeline and `rollups.rebuild(from, to)` + `bump_data_version()`, and writes
a successful `SyncRun` so the "data as of" banner has something to show. It uses plain ORM creates, **not** the
apps' `factories.py`: `factory_boy` is a dev-only dependency and a management command must run in a production
install.

### 9. Banner, nav, themes, i18n

- "Data as of <last successful sync>" — an inclusion tag in `templatetags/dashboards.py` reading the newest
  `SyncRun` with `status=success`, rendered in local `REPORT_TIMEZONE`, with a distinct "never synced" state.
  A tag rather than a context processor so it costs one query on dashboard pages only.
- Nav gains Overview · Projects · Repositories (People/PRs/Reviews arrive in phase 9).
- Both themes come free from the token discipline; the smoke test renders every page with `data-theme` light and
  dark and asserts the pages render and contain no colour literal.
- Every new string is `{% translate %}`/`gettext_lazy`, full sentences with named placeholders, plurals via
  `ngettext`; `make messages` is run and the `uk` translations are written **in this phase** (RISKS row 8), with
  the new nav/KPI strings added to `CANARY_ENGLISH_STRINGS`.

### Error handling

- Unknown `chart_key` / `table_key` / `fmt` → 404. An out-of-scope or unknown project/repository **id in a path**
  → 404 via `get_object_or_404` on a scoped queryset; the same id **in the query string** → silently dropped
  (spec §11, RISKS row 3, and `scope_for()` already does this for the scope itself).
- An htmx request that fails validation returns a **visible fragment** with the message, never an empty 400
  (PROFILE's htmx rule). Since the filter form is lenient, the realistic failure is an empty result set, which is
  an explained empty state, not an error.
- A chart endpoint with no data returns `200` with `"empty": true` and a translated message; `charts.js` renders
  the message instead of an axis-less empty canvas.

---

## Tasks

- [x] **T1: `seed_demo`.** `apps/dashboards/management/commands/seed_demo.py` per §8, plus
  `apps/dashboards/tests/test_seed_demo.py`: running it twice creates no duplicates, one repository belongs to two
  projects, rollups and a successful `SyncRun` exist afterwards, and a bot author's PRs are excluded from
  `pull_requests_for_metrics`.
- [x] **T2: `DashboardParams` + `DashboardFilterForm`.** `apps/dashboards/params.py`, `apps/dashboards/forms.py`.
  Tests `test_params.py`: every preset resolves to the right inclusive range at a frozen `today()`; auto
  granularity at 14/15/92/93 days; an explicit granularity wins; a malformed date/unknown preset/bad cohort falls
  back to defaults instead of raising; an out-of-scope project id is dropped; `parse(params.to_query_dict()) ==
  params` for a fully populated params object (the round trip acceptance criterion #4 rests on).
- [x] **T3: `metrics.compute_many()`.** `apps/metrics/services.py`. Tests
  `apps/metrics/tests/test_compute_many.py`: equality with per-scope `compute()` for every scope over a mixed
  fixture (counter, ratio, distribution, state metrics); one `DailyRollup` query for a counter-only request over
  many scopes (`assertNumQueries`); an empty `scope_ids` returns `{}`; a restricted `ScopeFilter` returns the same
  numbers as the per-scope path.
- [x] **T4: scoped population selectors.** Add `projects_in_scope`, `repositories_in_scope`, `people_in_scope` to
  `apps/catalog/selectors.py`; make `apps/policy/selectors.py`'s two delegate to them (no behaviour change).
  Tests in `apps/catalog/tests/test_selectors.py`: each starts from `ScopeFilter`, excludes inactive rows,
  de-duplicates a repository shared by two projects, and `people_in_scope` excludes bots and
  `exclude_from_metrics` people.
- [x] **T5: formatting helpers + KPI card.** `apps/dashboards/templatetags/dashboards.py` (percent, count,
  metric-value-by-unit, delta arrow, direction class, sparkline SVG) and
  `templates/dashboards/partials/kpi_card.html`. Tests `test_kpi_rendering.py`: `None` renders an em dash not `0`;
  `below_min_sample` adds the small-sample badge and the grey class; a `lower_is_better` metric with a negative
  delta is green while the same delta on `higher_is_better` is red; `|delta_ratio| < 5%` is neutral; the arrow
  glyph is present (status never colour-only); the sparkline SVG has one point per `SeriesPoint`.
- [x] **T6: `kpis.py` + `services.build_dashboard()`.** The three rows per §3, cohort compare, and the page
  context object. Tests `test_kpis.py`: each row's metric keys are all registered and available at every level the
  page renders; `compare` produces AI and non-AI values that match direct `compute()` calls.
- [x] **T7: the dashboard view, template and URLs.** `views.dashboard`, `dashboard.html`, `partials/_page.html`,
  `partials/filter_bar.html`, `partials/dashboard_content.html`, `urls.py`, nav entries. Tests
  `test_dashboard_view.py`: 200 at global/project/repo for a lead; an unknown project pk → 404; an `HX-Request`
  returns the fragment while a normal request returns the full page from the same URL; the filter bar's links
  carry the current query string; anonymous → redirect (covered globally by `tests/test_urls_login.py`).
- [x] **T8: Day mode.** `day.html` + its partials, the two Day KPI rows, the opened/merged PR lists and the
  per-person activity table. Tests `test_day_mode.py`: `mode=day&day=<date>` renders the day rows; a PR merged at
  23:59 Kyiv appears on that Kyiv day and not the UTC one; an empty day shows the explained empty state.
- [x] **T9: "data as of" banner.** Inclusion tag + partial. Tests: shows the last **successful** `SyncRun`'s time
  in `REPORT_TIMEZONE`, ignores a failed newer run, and renders a distinct "never synced" message.
- [x] **T10: chart registry and builders.** `apps/dashboards/charts.py` with the six specs per §4. Tests
  `test_charts_build.py`: each builder's dataset values equal the corresponding `compute()` values; a dataset
  carries `color_token`, never a literal; an empty period yields `empty: true` with a message;
  `violations_by_rule` clamps its bucket count to `CHART_MAX_BUCKETS`.
- [x] **T11: `/api/charts/<chart_key>/` endpoint.** `views.chart_json` + URL. Tests `test_charts_api.py`: the
  endpoint returns exactly the numbers `compute()` returns for the same filters (**acceptance criterion #3**); an
  unknown key → 404; a chart not available at the scope's level → 404; anonymous → redirect; the response is JSON
  with `Content-Type: application/json`.
- [x] **T12: vendor Chart.js.** `make vendor` also fetches Chart.js 4 UMD to `static/vendor/chart.umd.js`;
  commit it. Test `tests/test_vendored_assets.py`: the file exists, is non-empty, and `base.html` references it.
- [x] **T13: `static/js/charts.js`.** Per §4, plus its `themechange`/`htmx:afterSwap` redraw. Tests: the existing
  `tests/test_no_hardcoded_colors.py` covers literals; add a grep test asserting `charts.js` resolves colours via
  `getComputedStyle` and registers a `themechange` listener (the behavioural proof is the phase's e2e case).
  Wired into `services.build_dashboard()` (period mode builds one `ChartPayload` per available chart via
  `CHART_REGISTRY`, alongside the endpoint URL `charts.js` fetches), `partials/chart_card.html` (canvas + sr-only
  data-table twin per plan §4's accessibility rule), `dashboard_content.html`, and `templates/base.html` (script
  tags for the vendored Chart.js, `formatting.js` and `charts.js`, in that order, all deferred).
- [x] **T14: `exports/columns.py`.** `ExportColumn`, the type vocabulary, and `TABLE_SPECS`. Tests
  `test_export_columns.py`: every column type is renderable by both renderers; every table spec's metric keys are
  registered; no spec references `Person.notes`. (Session 4: found and fixed a latent bug where the `date` column
  type crashed both renderers — see DECISIONS.)
- [x] **T15: `exports/csv.py`.** Tests `test_export_csv.py`: a value starting with `=`, `+`, `-` or `@` is
  apostrophe-prefixed (**acceptance criterion #6**); `None` is an empty field; the response is a streaming
  attachment with the ASCII filename.
- [x] **T16: `exports/xlsx.py`.** Tests `test_export_xlsx.py`, reading the bytes back with **openpyxl**
  (**acceptance criterion #7**): bold header, `freeze_panes == "A2"`, an autofilter over the used range, a
  numeric cell that is a real number, a percent cell with number format `0.0%`, a duration cell in hours with
  format `0.0` and a ", h" column title, a clickable PR hyperlink, an empty cell for `None`, and a formula-looking
  title stored as a string rather than a formula.
- [x] **T17: tables.** `apps/dashboards/tables.py`, `apps/dashboards/rows.py` (done, see T14),
  `apps/dashboards/selectors.py` (done, see T8 — day-mode lists only, not the four `TABLE_SPECS` tables),
  `partials/table.html`. Tests `test_tables.py`: sorting by any column, search
  narrows rows, pagination splits them, the people table defaults to name ordering (RISKS row 1), and metric
  columns match `compute_many()`. Wired into `services.build_dashboard()` (period mode: `people`+`recent_prs` at
  global/repo level, `+repositories` at project level) and two new index views/templates (T20, done alongside).
  Only the `TableSpec` named by `params.table` reads `sort`/`q`/`page` from the query string; every other table
  on the same page stays in its default state — logged in DECISIONS.
- [x] **T18: `assertNumQueries` for every list view** (**acceptance criterion #2**). `test_query_counts.py`:
  Overview, Project, Repository, Day mode, the projects index and the repositories index each pin an exact count,
  and a companion case doubles the row count and asserts the growth is exactly the known per-row cost — zero for
  the queryset-backed `recent_prs` table. Session 5: profiling this task found and fixed a real cross-cutting N+1
  — `apps/catalog/services.py::get_setting()` ran one uncached `AppSetting` query per call, and `MIN_SAMPLE`,
  `STALE_DAYS`, `WAITING_REVIEW_HOURS`, `DURATION_MODE` etc. are read once per metric per series bucket, so a
  single Overview render cost 364 queries (108 of them `catalog_appsetting`) for a 2-person fixture. Fixed by
  caching all `AppSetting` rows in the shared `FileBasedCache` (process-wide, invalidated by `set_setting()`),
  dropping Overview to 247 (see DECISIONS). The remaining cost is the documented `compute_many()` fallback for
  distribution/state metrics (plan §6) — measured, not eliminated: `people_rows()` costs 40 queries/person,
  `project_rows()`/`repository_rows()` cost 16/entity (both have 2-5 non-batchable metrics in their column set),
  `recent_pr_rows()` costs 0 extra regardless of row count (a single annotated queryset). Page-level pins use a
  1-person/1-repo/1-project fixture; growth is asserted directly on the `rows.py` builders instead of through a
  full page render, since the per-page numbers are dominated by fixed KPI/chart cost unrelated to table row count.
- [x] **T19: the export view.** `views.export_table` + URL + the Export control in `partials/table.html`. Tests
  `test_export_view.py`: a filtered + sorted + searched table exported to CSV contains **every** matching row, not
  just the current page (**acceptance criterion #5**); the XLSX variant of the same request has the same row
  count; an unknown `table_key`/`fmt` → 404; the filename is ASCII in a `uk` session. Found and fixed a real bug:
  XLSX hyperlinks need an absolute URL, `rows.py`'s relative `reverse()` paths crashed `write_url()` — logged in
  DECISIONS.
- [x] **T20: the projects and repositories index pages.** Thin views over the same tables (nav parity with spec
  §10.1). Tests: 200, the table renders, the export link carries the query string.
- [x] **T21: global double-counting test** (**acceptance criterion #8**). `test_scope_aggregation.py`: with a
  repository belonging to two projects and PRs in it, the Overview's `prs_merged` equals the repository's own
  count once, while each project's page counts it in full — so the global value is **less than** the sum of the
  project rows. Root cause (session 5): counter/ratio metrics like `prs_merged` read from `DailyRollup`
  (`apps/metrics/services.py::compute()`), not live from `PullRequest`; the test created PRs but never called
  `apps/metrics/rollups.py::rebuild()`, so every scope had zero rollup rows and `value` was `None`
  (`sample_size == 0`). Fixed by calling `rebuild(DATE_FROM, DATE_TO)` after creating the PRs, matching the
  pattern in `apps/metrics/tests/test_compute.py`. `test_charts_build.py`/`test_tables.py` didn't hit this because
  they compare a builder's output to `compute()`'s output rather than asserting a literal number, so `None == None`
  passed silently.
- [x] **T22: `uk` translations.** `make messages`, fill every new `msgstr` in `django.po` and `djangojs.po`,
  `compilemessages`, commit `.po` and `.mo`. Extend `CANARY_ENGLISH_STRINGS` with the new nav and KPI strings.
  Test: the existing `tests/test_translations.py` suite (no empty/fuzzy, placeholder parity) now covers them.
  `djangojs.po` had no new strings this phase. Filled 31 previously-empty `django.po` entries and 24 stale-fuzzy
  ones (chart/table/filter-bar strings whose msgid changed enough that gettext could only guess a fuzzy match to
  an unrelated older string, e.g. "Duration" fuzzy-matched to "action"). Added `Projects`, `Repositories`,
  `Export`, `Throughput`, `Small sample` to `CANARY_ENGLISH_STRINGS` for T23's page smoke test.
- [x] **T23: page smoke test** (**acceptance criterion #1**). `tests/test_pages_smoke.py`: parametrized over every
  dashboard URL × `{en, uk}` × `{light, dark}` on `seed_demo` data — asserts 200, a non-empty body, and that no
  canary English string appears in the `uk` render.
  **Not started (session 5 handoff).** Notes from investigation so far, not yet used in any test: seed the DB with
  `django.core.management.call_command("seed_demo")` (see `apps/dashboards/tests/test_seed_demo.py` for the
  pattern — no live GitHub call, safe in a test). Language: `client.post(reverse("accounts:set_language"),
  {"language": "uk", "next": "/"})` then a fresh `Client()` forced-logged-in as the same user (see
  `tests/test_translations.py::test_language_choice_survives_reload_and_new_session`). Theme: stored per-user as
  `UserPreference.theme` (`apps/accounts/models.py`), set via `apps/accounts/services.py::set_theme(user, value)`
  or the `POST accounts:set_theme` view (`apps/accounts/views.py::set_theme_view`, values are
  `UserPreference.Theme.values` — check exact choices, likely `light|dark|system`); `templates/base.html` renders
  `data-theme="{{ theme_resolved }}"` server-side from that preference (`apps/accounts/context_processors.py` —
  not yet read this session, check how `theme_resolved` is computed from the stored preference before writing the
  test). Dashboard URLs to parametrize: `dashboards:overview`, `dashboards:project`/`dashboards:repository` (need
  a real pk from the seeded data — `seed_demo`'s fixed repo full name is `"pr-radar-demo-alpha/atlas"`, see
  `SHARED_REPO_FULL_NAME` in `test_seed_demo.py`, or just grab any `Project`/`Repository` id after seeding),
  `dashboards:projects_index`, `dashboards:repositories_index`, and the Day mode variant of `overview`
  (`?mode=day&day=<a date seed_demo actually has activity on>`). Assert no canary from `CANARY_ENGLISH_STRINGS`
  (`tests/test_translations.py`, already extended this session with `Projects`, `Repositories`, `Export`,
  `Throughput`, `Small sample`) appears in the `uk` render of each page — reuse that list via import, don't
  duplicate it.
- [x] **T24: `make css` + docs.** Regenerate and commit `static/css/app.css` (+ the build manifest), add
  `docs/user/dashboards.md` (a task-shaped page for leads: reading a KPI card, sharing a filtered link,
  exporting a table), a `docs/DECISIONS.md` phase 8 section, and `CHANGELOG.md` entries. Update `CLAUDE.md`'s
  command list with `seed_demo`.
  **Not started.** A full `uv run pytest -q` run at end of session 5 (before this task) already shows two gaps
  T24 should close, both pre-existing (not introduced this session — neither touches a file this session edited):
  `tests/test_docs.py::test_every_setting_is_documented` fails because `DASHBOARD_TABLE_PAGE_SIZE`
  (`apps/catalog/setting_defs.py`, added in an earlier session) is missing from `docs/CONFIGURATION.md`; and
  `tests/test_css_tokens.py::test_app_css_is_not_stale` fails simply because `make css` hasn't been run since the
  new templates landed — this task's own job. Run `uv run pytest -q` again after T24 to confirm both clear.

## Session 5 handoff (context ran out before T23/T24)

Done this session: T21 (fixed a missing `rebuild()` call in the test), T18 (assertNumQueries suite — found and
fixed a real N+1: `apps/catalog/services.py::get_setting()` queried `AppSetting` uncached, now cached in the
shared `FileBasedCache`, invalidated by `set_setting()`; see DECISIONS for the three existing query-count tests
this required updating), T22 (uk translations, `make messages` + `compilemessages`, `CANARY_ENGLISH_STRINGS`
extended). T23 and T24 are untouched — no new files, no code written for either.

A full `uv run pytest -q` run at the end of this session (before starting T23) showed 11 pre-existing failures,
none in files this session touched:
- `tests/test_admin.py::test_fk_heavy_changelist_query_count_does_not_grow_with_rows` — 8 parametrized failures
  (`PolicyViolationFactory`, `ChurnResultFactory`, `AISignalFactory`, `PullRequestCommitFactory`, `PRFileFactory`,
  `ReviewFactory`, `ReviewCommentFactory`, `CheckStatusFactory`). Unrelated app (`apps/*/admin.py`, none of which
  reads a setting), not touched this session — needs triage next session: confirm pre-existing (e.g. `git stash`
  this session's diff and rerun) before assuming it is not this phase's problem.
- `tests/test_css_tokens.py::test_app_css_is_not_stale` — expected, T24's job.
- `tests/test_docs.py::test_every_setting_is_documented` — expected, T24's job (see T24 above).
- `tests/test_urls_login.py::test_every_named_url_is_reversible_with_the_sample_args` — `dashboards:export`
  cannot be reversed with the test's `SAMPLE_ARGS` (needs both `table_key` and `fmt` sample values, e.g.
  `{"table_key": "people", "fmt": "csv"}`, added to `SAMPLE_ARGS` in `tests/test_urls_login.py`). Pre-existing gap
  from T19 (export view), not from this session — fix alongside T24 or as a standalone follow-up.

Next session: pick up at T23 (page smoke test). Investigation already done (seed_demo via `call_command`,
language switch via POST, theme mechanism — `UserPreference.theme` / `set_theme()` / `theme_resolved` context
processor) is written into T23's own bullet above; `apps/accounts/context_processors.py` was not yet read — check
how `theme_resolved` derives from the stored preference before writing the test. Then T24, then re-run the full
suite and linter per PLAN's Verification block before calling the phase done.

---

## Verification

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run python manage.py makemigrations --check --dry-run && uv run python manage.py check
uv run python manage.py metrics_doc --check            # registry doc still fresh
make css   && git diff --exit-code static/css/         # compiled CSS is committed and fresh
make messages && git diff --exit-code locale/          # .po/.mo are committed and fresh
uv run python manage.py seed_demo --reset              # the demo dataset the smoke test runs on
```

| # | Acceptance criterion | Test that proves it |
|---|---|---|
| 1 | A smoke test renders every page on `seed_demo` data in `en` and `uk` and in both themes, with no canary English string in the `uk` render | `tests/test_pages_smoke.py::test_every_dashboard_page_renders[url-lang-theme]`, `::test_no_canary_english_string_in_uk_render` (T23) |
| 2 | Every list view has an `assertNumQueries` test that fails on an N+1 | `apps/dashboards/tests/test_query_counts.py::test_<overview\|project\|repository\|day\|projects_index\|repositories_index>_query_count`, `::test_query_count_does_not_grow_per_row` (T18) |
| 3 | A chart JSON endpoint returns the same numbers as `compute()` for the same filters | `apps/dashboards/tests/test_charts_api.py::test_endpoint_matches_compute[chart_key]` (T11), backed by `test_charts_build.py` (T10) |
| 4 | Changing a filter changes the query string, and the query string alone restores the whole view | `apps/dashboards/tests/test_params.py::test_query_string_round_trip`, `::test_preset_change_changes_the_query_string`; `test_dashboard_view.py::test_view_is_restored_from_the_query_string_alone` (T2, T7) |
| 5 | A CSV export of a filtered, sorted, searched table contains every matching row, not just the current page | `apps/dashboards/tests/test_export_view.py::test_csv_export_contains_every_matching_row_not_just_the_page` (T19) |
| 6 | A CSV value starting with `=`, `+`, `-` or `@` is prefixed with an apostrophe | `apps/dashboards/tests/test_export_csv.py::test_formula_like_values_are_neutralised` (T15) |
| 7 | An exported XLSX opens with openpyxl and has bold headers, frozen panes, autofilter, real numeric cells, `0.0%` percentages, durations as hours and clickable PR hyperlinks | `apps/dashboards/tests/test_export_xlsx.py::test_workbook_formatting`, `::test_numeric_percent_and_duration_cells`, `::test_pr_url_is_a_hyperlink`, `::test_none_is_an_empty_cell` (T16) |
| 8 | A repository belonging to two projects is counted once at the global level | `apps/dashboards/tests/test_scope_aggregation.py::test_shared_repository_is_counted_once_globally` (T21) |

Case-taxonomy coverage for this phase (per `guides/case-taxonomy.md`): happy path (T7, T8, T17); input and
boundaries (T2 — malformed dates, unknown presets, the 14/15/92/93-day granularity edges, the 23:59 Kyiv day
boundary in T8); state (empty period, single row, many rows/pagination in T8/T17); errors (404s and the visible
htmx error fragment in T7/T11/T19); permissions (anonymous redirect via `tests/test_urls_login.py`; the restricted
lead's four exits are phase 9's criterion); navigation (query-string restore, T4's round trip, the htmx Back
button in e2e); platform sanity (both themes and both languages, T23). Deferred as `deferred_not_authored`:
concurrency (no two actors write the same dashboard row), idempotency beyond `seed_demo` (every dashboard route
is a GET), and accessibility beyond the data-table twin and the non-colour-only status glyphs (a full keyboard
pass belongs with phase 11's polish).

---

## Risks

| RISKS row | How this phase touches it | What the plan does |
|---|---|---|
| 1 — a wrong number lands in a 1:1 | Every KPI card, table cell and chart point is this risk made visible | `below_min_sample` greys the card and adds a visible badge; `None` renders an em dash, never `0`; the people table defaults to name order with no ranking column; `followup_fix_rate` and `churn_21d` are labelled (heuristic / not yet computed) rather than shown as `0`; every number comes from `compute()`, already tested in phase 7 |
| 3 — a restricted lead sees another project's data | Three new exits appear here: chart JSON, CSV, XLSX | All three go through `scope_for()`/`scope_for_user()` and the same `selectors.py`; out-of-scope ids in the query string are dropped by the lenient form; `compute_many()` keeps the per-scope path for a restricted `ScopeFilter` rather than reading access-agnostic rollups. The four isolation assertions are phase 9's criterion, but the code paths they will test are built correctly here |
| 8 — Ukrainian translation lags | This phase introduces more UI strings than any other | T22 is a task, not a cleanup: `make messages`, every `msgstr` filled, `.po`+`.mo` committed, new canaries added, and T23 fails the build if an English string leaks into the `uk` render |
| 10 — dashboards miss the 1.5 s budget / N+1 | Metric tables are the natural home of an N+1 | `compute_many()` batches counter/ratio reads into one query; chart bucket loops are clamped by `CHART_MAX_BUCKETS`; T18 pins an exact query count on every list view and asserts it does not grow per row; `compute()`'s cache absorbs repeat renders. Profiling at 50 repos / 20 k PRs stays phase 11's task |
| 12 — both themes must be correct | Charts are the hard part of theming | Colours reach JS as **token names**, resolved with `getComputedStyle` and re-resolved on `themechange`; `tests/test_no_hardcoded_colors.py` keeps literals out; T23 renders every page in both themes; status is arrow + text badge, never colour alone |
| 13 — an export leaks or misbehaves | The whole export layer lands here | Apostrophe prefix for `= + - @`, `strings_to_formulas=False`, `Person.notes` in no column list, `EXPORT_SYNC_MAX_ROWS` read and the row count recorded (the job path is phase 9) |
| 15 — a generated artefact goes stale | New templates change `app.css`'s inputs; new strings change `.po` | T24 runs `make css` and T22 runs `make messages`; both are verified by `git diff --exit-code` in the Verification block and by the existing freshness tests |

---

## Out of scope

- **Phase 9**: the People table page and the person page, the PR list page, the Reviews page, the real
  per-project `scope_for_user()` narrowing and its four isolation tests, `exports/reports.py` (the seven-sheet
  dashboard report with native Excel charts), `ExportJob` + the huey background export above
  `EXPORT_SYNC_MAX_ROWS` + "My exports" + the 7-day cleanup, and the `AuditEntry` written per export.
- **Phase 10**: real `churn_21d` numbers (`compute_churn`) and the `followup_fix_rate` heuristic — both render as
  labelled empty values here.
- **Phase 11**: performance profiling at 50 repos / 20 k PRs, index tuning, the full keyboard/screen-reader pass,
  PNG chart export, and print styling.
- Not in v1 at all (spec §1): a person-level ranking, alerting, and any write path from a dashboard.
