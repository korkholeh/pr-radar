# Review — phase 9 round 2

**Verdict:** changes_requested

Phase 9 is materially complete and the r1 blockers/majors are genuinely fixed: `scope_for_user()` is a real grant list enforced at every exit, the background export layer (ExportJob, huey tasks, `process_exports`/`cleanup_exports`, "My exports", author-only 403 download, 7-day cleanup, stuck sweep) exists with tests, every export path writes an AuditEntry, the report's Violations sheet is scope-narrowed with a regression test, uk translations and `docs/user/` pages landed, and all 27 plan tasks are checked with no `[~]`. I verified the full suite (exit 0), ruff, mypy, `makemigrations --check` and `manage.py check` myself. All 13 acceptance criteria map to real, non-vacuous tests. One major remains: `build_report()` never branches on `params.mode`, so the "Download report (XLSX)" button on a Day-mode page produces a workbook named and labelled for the day but filled with the whole period's aggregates — reachable from the UI and invisible to the current tests, which check only sheet names and the filename. Five minors follow (row-set materialised before the cap check, a vacuous own-jobs assertion, background XLSX losing hyperlinks, test files polluting `data/exports/`, and a SQLite-only JSON lookup).

## [MAJOR] Day-mode report aggregates the whole period but is named and labelled for the single day
`apps/dashboards/exports/reports.py`

`build_report()` never branches on `params.mode`. `_summary_rows()` (reports.py:112), `_trends_rows()` (:167), `_pr_rows()`/`TABLE_SPECS[...].row_builder` and `_violation_rows()` (:258) all aggregate over `params.date_from..params.date_to`. Only `_summary_metric_keys()` (:98) is mode-aware — in Day mode it picks `kpis.DAY_ROWS`'s metric keys and then computes them over the *period*, which is not what the Day page shows (`services.build_dashboard()` calls `build_kpi_row(..., params.day, params.day, "day", ...)`). Meanwhile `report_filename()` (:378) uses `params.day`, and the Parameters sheet lists `mode=day, day=<date>` from `params.to_query_dict()`. This is reachable from the UI: `day.html` extends `partials/_page.html`, which includes `filter_bar.html`, which renders the `show_report_link` "Download report (XLSX)" button — so a lead on `/?mode=day&day=2026-08-15` downloads `pr-radar_report_global_2026-08-15.xlsx` containing the default 30-day period's numbers, with a Parameters sheet asserting it is the day's. That directly contradicts the module docstring ("a number in the report can never disagree with the page it came from") and RISKS row 1 — these are the numbers that land in a 1:1. Nothing catches it: `test_report_contains_every_mandated_sheet[day-*]` asserts sheet names only, and `test_export_report_view.py::test_day_mode_report_uses_the_day_in_the_filename` asserts only the filename.

**Fix:** In `build_report()`, derive the window once from the mode — `date_from = date_to = params.day` and `granularity="day"` when `params.mode == "day"` — and thread it through `_summary_rows`, `_trends_rows`, `_pr_rows`, `_violation_rows` and `services.report_row_count()` (which has the same period/day mismatch). Add a test: seed a PR on day A and one on day B inside the same period, build a Day-mode report for day A, assert the PRs sheet holds only day A's PR and the Summary value equals a direct `compute(..., params.day, params.day, granularity="day")` call. If day-mode reports are meant to be out of scope instead, hide the link by setting `show_report_link` only when `params.mode == "period"` and say so in DECISIONS.

## [MINOR] `export_table` materialises every row before deciding to enqueue a job
`apps/dashboards/views.py`

views.py:301-306 calls `full_rows(table_key, scope, params)` and only then compares `len(rows)` against `EXPORT_SYNC_MAX_ROWS`. For the case the job exists to serve (a 20 001-row PR export), the request pays the full queryset evaluation and dict construction, throws the result away, and the background task rebuilds it — strictly worse than streaming. Plan §6 says "`views.export_table` / `views.export_report` count the rows first", and RISKS row 13's mitigation is "the >cap path becomes a job instead of blocking a request". `export_report` does it correctly via `services.report_row_count()`'s `.count()`.

**Fix:** Give `TableSpec` a cheap count path (a `count_builder`, or have `full_rows` accept a `count_only` flag / return a queryset the view can `.count()` on) and branch on the count before building rows, mirroring `report_row_count()`.

## [MINOR] "My exports shows only own jobs" test asserts factory state, not the response
`apps/dashboards/tests/test_export_jobs.py`

`test_exports_list_shows_only_the_callers_own_jobs` (test_export_jobs.py:155) creates a job for each of two users, then asserts `ExportJob.objects.filter(user=lead_user).count() == 1` and the same for the other user — both restate the factory calls and hold regardless of what `views.exports_index` does. If the view were changed to `ExportJob.objects.all()`, this test would still pass. The view is correct today (`ExportJob.objects.filter(user=request.user)`), so this is a test-quality gap, not a live leak; the 403 download case is covered separately.

**Fix:** Assert on the rendered response: `body.count('data-testid="export-job-row"') == 1`, and that the other user's `export_download` URL / job pk does not appear in the body.

## [MINOR] Background XLSX table exports silently lose hyperlinks that the synchronous export has
`apps/dashboards/services.py`

`views.export_table` absolutizes `url` columns with `_absolute_urls(request, spec, rows)` (views.py:249) before `write_xlsx`. `services.run_export_job()` (services.py:~250) calls `write_xlsx(list(columns), rows, ...)` with the raw relative rows, so `exports/xlsx.py::_write_value` takes the `elif display:` branch and writes plain text. The same table exported above the cap therefore comes back without clickable links — an unannounced behaviour difference triggered purely by row count. `build_report`'s `base_url=""` has the same effect on background reports.

**Fix:** Store `base_url = request.build_absolute_uri("/")` in `ExportJob.params` at enqueue time (both call sites already have the request) and, in `run_export_job()`, apply it — pass it to `build_report(...)` and run the table rows through an equivalent of `_absolutize()` before `write_xlsx`.

## [MINOR] Export-job tests write real files into the developer's `data/exports/` and never clean them
`apps/dashboards/tests/test_export_jobs.py`

`pyproject.toml` runs pytest under `config.settings.local`, where `DATA_DIR = BASE_DIR/"data"`, and `ExportJob.file`'s storage is `DATA_DIR/exports`. Tests that actually run a job (`test_run_export_job_writes_file_rows_and_audit`, `test_run_export_job_reresolves_scope_for_user_at_run_time`, `test_completed_background_job_writes_one_audit_entry`) write real files there; the DB row rolls back but the file does not. Verified on this checkout: `ls data/exports | wc -l` → 21 leftover CSVs from prior runs (`pr-radar_projects_global_..._4R3x7tw.csv` etc.). Gitignored, so nothing is committed, but the suite mutates the dev data directory and grows it without bound.

**Fix:** Add an autouse fixture in `apps/dashboards/tests/conftest.py` (or root `conftest.py`) that points the export storage at `tmp_path` for the duration of a test — e.g. `monkeypatch.setattr(settings, "DATA_DIR", tmp_path)` plus re-binding `ExportJob._meta.get_field("file").storage` to a `FileSystemStorage(location=tmp_path/"exports")`.

## [MINOR] `PRFilters.tools` uses `ai_tools__icontains`, which is SQLite-only in practice
`apps/dashboards/pr_filters.py`

pr_filters.py:47-53 filters a JSONField with `Q(ai_tools__icontains=f'"{tool}"')`. The substring reasoning is sound and the switch away from `contains` was necessary (SQLite reports `supports_json_field_contains = False`) and is logged in DECISIONS. But CLAUDE.md's stated reason for ORM-only code is that `DATABASE_URL` can point at PostgreSQL later, and a `LIKE`-family lookup against a `jsonb` column does not work there — the PR list's tool filter would break on the first Postgres deployment, silently invalidating every export built from it.

**Fix:** Branch on the backend inside `apply()`: `if connection.features.supports_json_field_contains: q |= Q(ai_tools__contains=[tool]) else: q |= Q(ai_tools__icontains=f'"{tool}"')`, with a comment naming both paths. Keeps the single ORM choke point and the existing regression test, and makes the Postgres path correct by construction.

## [NIT] Test name claims 20 000 rows but exercises 3
`apps/dashboards/tests/test_export_jobs.py`

`test_20000_rows_still_streams_synchronously` sets `EXPORT_SYNC_MAX_ROWS` to 3 and creates 3 projects. The at-cap boundary it checks is the right thing to test; the name just misreports it.

**Fix:** Rename to `test_an_export_at_the_sync_cap_still_streams_synchronously`.
