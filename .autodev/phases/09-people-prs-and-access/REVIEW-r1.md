# Review — phase 9 round 1

**Verdict:** changes_requested

Phase 9's access-control core is genuinely good: `scope_for_user()` is now a real grant list with a per-request memo, every new view and selector composes on it, and `tests/test_scope_isolation.py` proves the four exits (page, chart JSON, CSV, XLSX) plus a fifth for the report, each with a positive control. Criteria 1–5, 7 (report sheets, parametrized over four levels × two modes), 8 (Person.notes absent from every export, read back with openpyxl rather than a byte grep) and 13 (People default name order) are all proven by tests. Lint gates verified clean by me: ruff, mypy, makemigrations --check, manage.py check, and the full pytest run in TEST_OUTPUT.txt.\n\nBut the phase stops mid-scope and PLAN.md says so: T21 `[~]`, T22–T27 `[ ]`. The whole background-export layer (ExportJob service/task/commands/pages), the author-only 403 download, the 7-day cleanup, and the export AuditEntry do not exist — four acceptance criteria with no code and no test, and RISKS row 13's mitigations absent. On top of that, one real data bug: the report's Violations sheet reads `violations_in_scope(scope.access)` instead of the existing `scoped_violations(scope)`, so a project- or person-scoped report lists every visible project's violations. Ukrainian translations (CLAUDE.md's same-phase rule, RISKS row 8) and all `docs/user/`/CHANGELOG updates are also outstanding.

## [BLOCKER] Background export layer (T22–T24) not implemented — 3 acceptance criteria have no code and no test
`apps/dashboards/views.py`

`ExportJob` exists as a model + admin + factory only. `grep ExportJob` finds no service, no huey task, no management command, no view, no URL, no test. `views.export_table` (line ~302) still only logs when `EXPORT_SYNC_MAX_ROWS` is exceeded and streams every row anyway; `views.export_report` has no row check at all. Consequently:
- "An export of 20 001 rows returns an ExportJob instead of a file, and the enqueue is asserted" — no code, no test (plan T22, `test_export_jobs.py` does not exist).
- "Downloading another user's export file returns 403, and a file past its 7-day expiry is deleted by the cleanup task" — no `/exports/` page, no `/exports/<pk>/download/`, no `cleanup_exports()`/`cleanup_exports_task`/`manage.py cleanup_exports` (plan T23/T24).
`EXPORT_RETENTION_DAYS` and `compute_expires_at()` are dead code with no caller and no test. RISKS row 13's mitigations (job instead of a blocking request, author-only file access, 7-day deletion) are therefore all absent. PLAN.md marks T22/T23/T24 `[ ]` and T21 `[~]`, so the phase is knowingly unfinished rather than mis-reported.

**Fix:** Implement plan §6: `services.export_table_or_job()`/`run_export_job()`/`cleanup_exports()`, `tasks.export_job_task` + `cleanup_exports_task`, `manage.py process_exports`/`cleanup_exports`, `/exports/` and `/exports/<pk>/download/` (403 on `job.user_id != request.user.id`), and the threshold branch in both `export_table` and `export_report`. Add `apps/dashboards/tests/test_export_jobs.py` with the three criterion tests, plus tests for `ExportJob` defaults and `compute_expires_at()` (T21's missing model tests).

## [BLOCKER] No AuditEntry on any export path (T25) — acceptance criterion unmet
`apps/dashboards/views.py`

`record_audit` is called exactly once in `apps/dashboards` (views.py:470, the person-notes write). The criterion "Every export writes an AuditEntry recording the user, kind, filters and row count" is not met for sync CSV (`export_table` → `stream_csv`), sync XLSX, or the report (`export_report`); `test_export_audit.py` does not exist. Row-count capture for CSV also needs care: `stream_csv` streams a generator, so the count has to be taken from the materialised row list before streaming, not after.

**Fix:** Add one helper in `apps/dashboards/services.py` called from `export_table` (both formats) and `export_report`: `record_audit(user, "export.table"|"export.report", obj, after={"kind", "format", "table_key", "scope_type", "scope_id", "filters": params.to_query_dict().urlencode(), "rows": len(rows)})`, and a parametrized `test_export_audit.py` asserting exactly one entry per produced file.

## [MAJOR] Report's Violations sheet ignores the report's scope — a project report lists every project's violations
`apps/dashboards/exports/reports.py`

`_violation_rows()` (reports.py:258) starts from `violations_in_scope(scope.access)`, which applies only the *access* filter, not the page's `Scope`. Every other sheet narrows by scope (`_summary_rows` → `compute(scope, …)`, PRs sheet → `scoped_pull_requests(scope)`). So a report generated from `/projects/7/` or `/people/12/` contains the violations of every project/person the reader may see. `apps/metrics/selectors.py:61` already provides `scoped_violations(scope)`, which applies `_narrow_by_scope(..., prefix="pull_request__")` — it just is not used here. The scope-isolation test only proves the *access* filter holds, so nothing catches this; `test_reports.py` asserts sheet names, not sheet scoping.

**Fix:** Use `apps.metrics.selectors.scoped_violations(scope)` in `_violation_rows()` instead of `policy.selectors.violations_in_scope(scope.access)`, and add a test: seed two projects, build a PROJECT-scope report, assert the Violations sheet contains only that project's violation.

## [MAJOR] Ukrainian translations missing for every new phase-9 string (T26)
`locale/uk/LC_MESSAGES/django.po`

`make messages` was not run. Grepping the .po for strings this phase added returns nothing: "Reviewer load", "Timeline", "Waiting for review", "Download report (XLSX)" (and the PR-detail Metrics/Churn/Files blocks, the person comparison and notes cards, PR-filter labels). CLAUDE.md: "New UI strings get their Ukrainian translation **in the same phase**"; RISKS row 8 is exactly this. `tests/test_translations.py` stays green only because the msgids were never extracted, so the gate cannot catch it — the uk UI silently falls back to English on five new pages. `tests/test_pages_smoke.py` was also not extended with `/people/`, `/people/<pk>/`, `/prs/`, `/reviews/` in en/uk × light/dark as the plan's standing gates require.

**Fix:** Run `make messages`, translate every new msgid, commit `.po`/`.mo`, and extend `tests/test_pages_smoke.py` with the four new pages in both languages and themes.

## [MAJOR] No user or changelog documentation for five new user-visible pages (T27)
`docs/user/`

This phase ships People, Person, PR list, PR detail and Reviews pages plus an XLSX report download, and `docs/user/` still contains only the six pre-existing pages — no `people.md`, `pull-requests.md`, `reviews.md`, `exports.md`. `CHANGELOG.md` is untouched despite CLAUDE.md's "User-visible changes go in CHANGELOG.md". Only `docs/CONFIGURATION.md` was updated (correctly: `PR_FILES_DISPLAY_LIMIT`, `REVIEW_HEATMAP_TOP_N`, `EXPORT_RETENTION_DAYS`). `docs/SETUP.md` also still says nothing about running the worker for large exports, and `docs/DECISIONS.md` has no phase-9 section.

**Fix:** Write the four task-shaped `docs/user/` pages, add the phase-9 CHANGELOG entries, and fold the phase's decisions into `docs/DECISIONS.md` (the `.autodev/DECISIONS.md` log already has the material).

## [MINOR] Notes form has no `action`, so a non-htmx submit silently discards the note
`apps/dashboards/templates/dashboards/partials/person_notes.html`

The form declares `hx-post="{% url 'dashboards:person_notes' … %}"` and `method="post"` but no `action`. Without htmx the browser posts to the current URL `/people/<pk>/`, which is `views.dashboard` — an unrestricted-method view that just re-renders the person page, so the edit is lost with no error. `views.person_notes`'s redirect fallback is unreachable from the UI. The sibling form in `partials/pr_violations.html:21` gets this right (it sets both `action` and `hx-post`). `test_person_notes.py::test_saving_notes_round_trips` posts to the URL directly, so it passes regardless.

**Fix:** Add `action="{% url 'dashboards:person_notes' scope_object.pk %}"` to the form tag, mirroring `pr_violations.html`.

## [MINOR] PR list and waiting-for-review list are unbounded row sets
`apps/dashboards/rows.py`

`pull_request_rows()` materialises every PR in the period into dicts before `tables.py` paginates in Python; at the spec's 20 k-PR target each `/prs/` page view builds the whole list. `reviews.prs_waiting_for_review()` likewise returns every open unreviewed PR with no cap and no pagination, and computes `duration_hours` per row in Python. `recent_prs` avoids this with its own cap. Phase 11 owns the latency budget, so this is a heads-up, not a gate — but the two new uncapped builders are the ones that will fail it.

**Fix:** Cap `prs_waiting_for_review()` (e.g. a `REVIEWS_WAITING_LIMIT` AppSetting with a "+N more" line, as the PR files list already does), and consider pushing the PR table's slice into the queryset before dict construction.

## [MINOR] Page-level query bounds not added for /prs/, /reviews/, /people/<pk>/
`apps/dashboards/tests/test_query_counts.py`

The plan's standing gates say `test_query_counts.py` is "extended with the four new list pages"; the diff only bumps the six existing bounds by +1 for the real `scope_for_user()`. Only `/people/` got a bound (in `test_people_page.py`). The row builders are protected (`test_pr_rows.py` and `test_reviews_selectors.py` use `CaptureQueriesContext` to prove constant query counts, `test_pr_detail.py` bounds the detail view), so an N+1 inside a builder would be caught — but a regression in the page assembly around them would not.

**Fix:** Add `django_assert_num_queries` bounds for `dashboards:pull_requests_index`, `dashboards:reviews` and `dashboards:person` to `test_query_counts.py`.
