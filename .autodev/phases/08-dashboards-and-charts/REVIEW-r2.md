# Review — phase 8 round 2

**Verdict:** changes_requested

Round 2: every blocker and major from REVIEW-r1 is genuinely fixed and each carries a regression test that can fail — delta columns now render in the metric's own unit (CSV, XLSX and HTML all asserted), Overview gained the spec §10.3 projects table, the people table is row-narrowed per scope, the chart build/endpoint tests now seed data and call rollups.rebuild() before comparing element-wise to compute(), and the AppSetting cache is invalidated by model signals so an admin edit takes effect. All 24 PLAN tasks are [x] with no [~] claims; `uv run pytest -q` and the full lint gate both exit 0 on my own runs; uk catalogs are complete and the .mo matches the .po. One new blocker outweighs that: the project and repository multi-selects that spec §10.1 and PLAN.md:122 require are parsed, round-tripped and rendered as selected, but no code reads them — verified at runtime that /?project=<Alpha> and /?repository=<r1> return the identical global prs_merged value (5) as the unfiltered page, so the page silently shows org-wide numbers under an applied filter (RISKS row 1). Secondary: the people export carries org-wide person metrics at project/repository scope with no marker in the file (the caveat exists only in the HTML table), the review-fix round logged no DECISIONS entries at all (one docstring claims an entry that does not exist), spec §10.6's conditional delta formatting in XLSX is still missing and unlogged, and the metric tables ignore the cohort filter the rest of the page honours.

## [BLOCKER] Project and repository filters are inert — the filter bar narrows nothing
`apps/dashboards/params.py`

`DashboardParams.project_ids`/`repository_ids` (params.py:88-89) are parsed (params.py:176-177), serialized into the query string (params.py:113-116) and rendered as selected options in the filter bar (partials/filter_bar.html:84-110), but no consumer exists: `grep -rn 'project_ids|repository_ids' apps/dashboards apps/metrics --include=*.py` outside params.py/tests returns only `apps/metrics/services.py`'s access-side checks. `services.build_dashboard()`, `kpis.build_kpi_row()`, every `charts.py` builder, all four `rows.py` builders and `views.export_table` take only `(scope, params)` and use `params` for dates/granularity/cohort. Proven at runtime on a seeded DB (project Alpha = 1 merged PR, project Beta = 4): GET `/` → prs_merged KPI 5; GET `/?project=<Alpha.pk>` → 5; GET `/?repository=<r1.pk>` → 5. A lead who picks a project on Overview sees org-wide numbers under a UI that says the filter is applied — spec §10.1 mandates the multi-select, PLAN.md:122 marks it 'multi-select narrowing, Overview only', and this is RISKS row 1 (a wrong number lands in a 1:1). No test covers narrowing, which is why the suite is green.

**Fix:** Apply the selection where the data is read: in `dashboard()`/`_index_context()`/`chart_json`/`export_table`, intersect `scope.access.project_ids` with `params.project_ids` (a narrowed `ScopeFilter`, which `compute`/`compute_many` already honour and already key their cache on) before building the `Scope`; for `repository_ids`, narrow the row builders' querysets and add repository narrowing to `ScopeFilter` or, if that is phase-9 work, hide the repository control this phase and log it. Add a test asserting a project-filtered Overview KPI/table differs from the unfiltered one, and a `to_query_dict()` round-trip test that the filtered view is restorable.

## [MAJOR] People export at project/repository scope ships org-wide metrics with no caveat in the file
`apps/dashboards/rows.py`

`people_rows()` (rows.py:139-160) narrows the row set to people active in the scope but deliberately leaves the metric values organization-wide; `partials/table.html:10-14` prints a note saying so. The CSV/XLSX path (`views.export_table` → `full_rows` → the same `people_rows`) carries no equivalent marker: columns are titled 'PRs merged', 'Lead time p50', the filename is `pr-radar_people_repo-3_<from>_<to>.xlsx`, and nothing in the file says the numbers are not that repository's. The export is exactly the artifact that gets pasted into a 1:1 (RISKS row 1). The docstring also states 'Logged in DECISIONS', but `.autodev/DECISIONS.md` has no `p08-review_fix1` section at all — the claim is false.

**Fix:** Either suffix the people table's metric column titles with an org-wide marker (e.g. `_('%(title)s (org-wide)')`) so HTML, CSV and XLSX all carry it, or write the note into the XLSX as a first row / sheet comment and as a CSV preamble line. Add a test asserting the marker appears in a project-scope people export, and log the accepted limitation in DECISIONS so the docstring's claim becomes true.

## [MINOR] The review-fix round changed ~25 files and logged zero decisions
`.autodev/DECISIONS.md`

`.autodev/PROGRESS.md` records `2026-09-18 09:17:45 p08-review_fix1 — partial`, and 25 source/test files are newer than `REVIEW-r1.md`, but `grep -n '^## ' .autodev/DECISIONS.md` shows the file jumps from `p08-test_fix1` straight to this step's empty `p08-review2`. Non-trivial calls made in that round — rendering scope-unnarrowed person metrics behind a label, moving the settings-cache invalidation to model signals, keeping `EXPORT_SYNC_MAX_ROWS` as a warning rather than a cap, adding `include_inactive` to the canonical selectors — are recorded only in code comments, one of which (`rows.py:144`) points at a DECISIONS entry that does not exist.

**Fix:** Append a `## p08-review_fix1` section with one bullet per decision above, in the file's `- [p08/review_fix1] <decision> — why: … — alternatives: …` format.

## [MINOR] XLSX delta cells still have no conditional green/red formatting (spec §10.6, mandatory list)
`apps/dashboards/exports/xlsx.py`

Spec §10.6 line 474 lists 'Умовне форматування дельт (зелений/червоний за direction)' under 'Форматування XLSX (обовʼязково)'. `write_xlsx()` applies only number formats (xlsx.py:124-138); no `conditional_format` / per-cell colour is applied to `<key>__delta` columns. Raised as part of round-1 MINOR #6, still not implemented and not logged as deferred. The metric `direction` needed for it is already available via `get_metric(key)` in `exports/columns.py`.

**Fix:** Carry the metric's `direction` on the delta `ExportColumn` and add two workbook formats (good/bad, light palette regardless of UI theme) applied in `_write_value` for delta columns; assert the font/fill colour on a positive and a negative delta cell with openpyxl. Otherwise log the deferral in DECISIONS with a phase pointer.

## [MINOR] Metric tables ignore the cohort filter while KPIs, charts and recent_prs honour it
`apps/dashboards/rows.py`

`project_rows`/`repository_rows`/`people_rows` hardcode `cohort=Cohort.ALL` (rows.py:81, 110, 157) with no comment, while `kpis.build_kpi_row()` resolves `params.cohort` and `recent_pr_rows()` filters by it (rows.py:163). Switching the filter bar to 'AI' changes the KPI row, the charts and the recent-PRs table but silently leaves the projects/repositories/people tables on all PRs, so two halves of one page describe different populations. Neither PLAN.md nor DECISIONS states this is intended.

**Fix:** Either pass `params.cohort` through to `compute_many()` for the three metric tables (falling back to ALL for `cohort=compare`), or state the choice in a docstring plus a DECISIONS bullet and label the tables as all-PR.

## [NIT] Dead branch for a non-existent column type
`apps/dashboards/tables.py`

`_cell_html` tests `column.type in ("int", "lines")` (tables.py:51), but `"lines"` is not a member of `COLUMN_TYPES` (exports/columns.py:19-21) — the `lines` metric *unit* maps to the `float` column type via `_UNIT_TO_COLUMN_TYPE`. `ExportColumn.__post_init__` would raise before such a column existed.

**Fix:** Drop `"lines"` from the tuple.
