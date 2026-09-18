# Review — phase 8 round 1

**Verdict:** changes_requested

Phase 8 delivers the whole surface (params round trip, KPI rows, six charts, four tables, CSV/XLSX, both themes, uk translations, seed_demo, docs) and most acceptance criteria are genuinely proven — XLSX formatting, CSV injection, shared-repository counting and the query-string round trip all have tests that can fail. Three things block: every metric table and both exports render the absolute delta through a `percent` column (a +5 PR delta prints '500.0%', a -1h lead-time delta prints '-360000.0%'); the Overview page is missing the projects table spec §10.3 requires and instead shows an unscoped people table whose person metrics are org-wide on a Project/Repository page; and acceptance criterion #3 has no test that could detect a wrong number, because the chart/table 'matches compute()' tests run without rollups.rebuild() and compare None to None — a trap the plan itself identified in T21 and left in place. Plus an operational regression: the new process-wide AppSetting cache is never invalidated by the only path that currently edits a setting (the Django admin). Suite is green (TEST_OUTPUT.txt exit 0), all 24 tasks [x], no [~] claims to verify.

## [BLOCKER] Delta columns render an absolute delta as a percentage — every metric table and both exports show wrong numbers
`apps/dashboards/exports/columns.py`

`_metric_columns()` (columns.py:58-59) creates `<key>__delta` with `type="percent"`, while `rows._metric_row()` (rows.py:68) fills it from `MetricResult.delta`, which `apps/metrics/services.py::_delta()` defines as the absolute difference in the metric's own unit — not a ratio. Verified against the real renderers: `prs_merged__delta = 5.0` renders '500.0%' in the table (tables.py:47 `percent`), in CSV (csv.py:42) and as an XLSX cell with format 0.0% (xlsx.py:84); `lead_time_p50__delta = -3600.0` renders '-360000.0%'. This hits the projects, repositories and people tables at every scope and both export formats — exactly RISKS row 1 ('a wrong number lands in a 1:1'). No test asserts a delta cell's value, which is why the suite is green.

**Fix:** Populate the percent-typed delta cell from `result.delta_ratio` (None when previous_value is 0/None) in `rows._metric_row()`, or keep `delta` and give the delta column the metric's own column type (`int`/`duration`/`percent` via `_UNIT_TO_COLUMN_TYPE`). Add a test with `rebuild()`-backed data asserting one count delta and one duration delta cell in the table, CSV and XLSX.

## [MAJOR] Overview lacks the projects table spec §10.3 requires; the people table it shows instead is not scoped to the page
`apps/dashboards/services.py`

`_TABLE_KEYS_BY_LEVEL` (services.py:17-21) renders `people`+`recent_prs` at global and repo level and `repositories`+`people`+`recent_prs` at project level. Spec §10.3 mandates the **projects** table (row = project, key metrics with deltas) at the global level — it exists only on the separate /projects/ index page, so Overview shipped without it. Plan §5 also assigned it to Overview; the change is mentioned only in T17's task text, not in DECISIONS. Worse, `rows.people_rows(scope.access, params)` (rows.py:125-142, reached via `columns._people_rows`, columns.py:100) ignores `scope` entirely: it lists every person in the viewer's access (`people_in_scope(access)`) and computes `ScopeType.PERSON` metrics, which are org-wide. So a Repository page's people table shows people who never touched that repository, with their company-wide prs_merged / lead_time_p50 / rework_rate presented as that repository's numbers. That is RISKS row 1 in its most direct form, and it is unlogged.

**Fix:** Add the `projects` table to the GLOBAL level. For the people table, pass `scope` into `people_rows()` and at minimum restrict the row set to people with activity in the scope (the day-mode path already does this in `selectors.person_activity_for_day`); since `compute()`'s `Scope` cannot express person-within-repository, either add that compound scope to `apps/metrics` or label the columns explicitly as org-wide until the person page lands in phase 9 — and log whichever you choose in DECISIONS.

## [MAJOR] Acceptance criterion #3 is unproven: the 'matches compute()' tests compare None to None
`apps/dashboards/tests/test_charts_api.py`

`test_endpoint_matches_compute` (test_charts_api.py:12-27) never calls `compute()` — it asserts 200, the key, that `datasets` is a list and that color tokens start with '--'. It cannot fail on a wrong number, a swapped cohort or a wrong metric key. The build-side tests (test_charts_build.py:74+) do compare to `compute()`, but they create no PRs and never call `apps/metrics/rollups.py::rebuild()`, so both sides are all-None series and any mapping error still passes; the same applies to `test_tables.py::test_repositories_table_metric_columns_match_compute_many` (one PR, no rebuild). The plan diagnosed this exact trap in T21 ('None == None passed silently') and did not close it, while PLAN.md's verification table still credits these tests with criterion #3.

**Fix:** Seed PRs/reviews/violations plus `rebuild(DATE_FROM, DATE_TO)` in these tests, assert at least one non-None, non-zero value per dataset, and make the API test compare `response.json()['datasets'][i]['data']` element-wise to the corresponding `compute()` series for the same filters (including the AI/non-AI split for throughput and pr_size_distribution).

## [MAJOR] New AppSetting cache is never invalidated by the Django admin, the only current way to edit a setting
`apps/catalog/services.py`

`_all_setting_rows()` (services.py:47-59) caches all rows in the shared FileBasedCache with `timeout=None` and is invalidated only by `set_setting()` (services.py:113). No production code calls `set_setting()` — grep over apps/ and config/ finds only its definition — while `AppSettingAdmin` (apps/catalog/admin.py:51-56) exposes `value` as editable and does not override `save_model`. An operator changing MIN_SAMPLE, STALE_DAYS or DASHBOARD_TABLE_PAGE_SIZE in the admin therefore has no effect, permanently, until the cache directory is wiped. docs/CONFIGURATION.md:20 calls these settings 'operator-editable'. The T18 decision entry documents the cache but not this gap.

**Fix:** Invalidate on the model instead of the service: a `post_save`/`post_delete` receiver on `AppSetting` deleting `catalog:app_settings:v1` (covers admin edits, loaddata and migrations), keeping the `set_setting()` delete as-is. A short TTL is a weaker fallback.

## [MINOR] Quality-row KPI cards drop the value, delta and sparkline that spec §10.2 lists as mandatory
`apps/dashboards/kpis.py`

For `compare_cohorts` specs, `build_kpi_row()` sets `main = ai_set[spec.metric_key]` (kpis.py:120) and `kpi_card.html:17-39` renders only the AI and non-AI numbers — no headline value, no delta arrow, no sparkline — for all four quality cards (and for every card when `cohort=compare`). `below_min_sample` and the small-sample badge are also taken from the AI cohort alone. Spec §10.2 lists big value + unit + delta with arrow + sparkline as mandatory card anatomy; §10.3 asks only that the quality row additionally compare AI vs non-AI.

**Fix:** Compute the cohort-ALL result for compare cards too, render it as the card's value/delta/sparkline, and show the AI vs non-AI pair as a sub-line; take `below_min_sample` from the ALL result.

## [MINOR] EXPORT_SYNC_MAX_ROWS is never read, but the user docs promise a cap
`apps/dashboards/views.py`

Plan §7 states 'EXPORT_SYNC_MAX_ROWS is read and the row count recorded now' and T19 is marked [x]; grep shows the setting appears only in `setting_defs.py` and docs. `export_table()` (views.py:151-184) streams whatever `full_rows()` returns, unbounded. docs/user/dashboards.md's last paragraph already tells leads 'A dashboard export is capped at a fixed number of rows for now' — documented behaviour that does not exist. Spec §10.6 also mandates conditional green/red delta formatting in XLSX, which `write_xlsx()` does not apply; neither omission is logged in DECISIONS.

**Fix:** Read `get_int('EXPORT_SYNC_MAX_ROWS')` in `export_table`, log/record the row count and return a visible message when exceeded (the ExportJob path stays phase 9), or correct the user doc; add the delta conditional format (or log the deferral) alongside the delta-column fix.

## [MINOR] Policy console dropdowns silently lost inactive projects and repositories
`apps/catalog/selectors.py`

T4 claims 'no behaviour change', but the new canonical `projects_in_scope`/`repositories_in_scope` (selectors.py:11-27) add `is_active=True`, which the `apps/policy/selectors.py` versions they replaced did not have. The Policy console's filter dropdowns now omit archived projects/repositories, so existing violations on an archived repository can no longer be filtered to. No test covers the inactive case for the policy filters.

**Fix:** Either keep the active filter and log it as an intentional change in DECISIONS (plus a test asserting the policy dropdown behaviour), or give the selectors an `include_inactive=False` flag and pass `True` from the policy filter form.

## [MINOR] Page-level query pins cannot separate fixed cost from per-row cost
`apps/dashboards/tests/test_query_counts.py`

The six page pins use a one-person/one-repo/one-project fixture (247/264/246/24/24/24), and the growth tests call `rows.py` builders directly, so no test renders a page with a growing row count. A template-level N+1 in a table cell would still trip the exact pins, so criterion #2 is met in the narrow sense, but the accepted 40 queries/person and 16 queries/entity mean the seeded Overview (12 people, 6 repos) costs roughly 700 queries against RISKS row 10's 1.5 s budget — measured, documented, and left for phase 10's profiling.

**Fix:** Add one page-level growth case (2 vs 6 people) asserting the delta equals the documented per-row cost, so a future template N+1 is attributable; keep the phase-10 profiling task pointed at the `compute_many()` distribution/state fallback.

## [NIT] Throughput treats an all-zero period as empty, unlike the other builders
`apps/dashboards/charts.py`

`_build_throughput` uses `empty = not any(point.value for point in ...)` (charts.py:118), so a period where every bucket genuinely merged 0 PRs renders 'No data in this period.' instead of a zero line. `_build_ai_adoption` and `_build_latency` correctly test `is not None`. Also `charts.js::initOne` (charts.js:88-96) has no `.catch`, so a failing chart request leaves a blank canvas and an unhandled rejection with no visible message.

**Fix:** Use `point.value is not None` in `_build_throughput`; add a `.catch` in `initOne` that writes the translated error text into the card.
