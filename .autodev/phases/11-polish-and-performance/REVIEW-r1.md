# Review — phase 11 round 1

**Verdict:** changes_requested

Phase 11's code is strong and the automated gate is genuinely green — I re-ran ruff, ruff format, mypy, makemigrations --check, manage.py check, the full `uv run pytest -q` (exit 0) and `tests/test_performance.py` standalone (pass). The performance work is profile-driven rather than guessed: `include_series` threading, a new batched `period_by_person`/`at_date_by_person` calculator interface, the `DURATION_MODE` per-row settings N+1, and SQL-level `recent_prs` pagination take Overview from 29.4s/5407 queries to under the 1.5s budget, with every assertNumQueries pin moved and justified. Empty states, the small-sample marker chain, the WCAG pair table with two named exemptions, and the uk catalogue (0 untranslated, 0 fuzzy, every new string translated) all land as planned. Blocking the approval: PLAN.md tasks T16–T21 are unchecked and the diff confirms it — not one file under `docs/`, no README/CHANGELOG/docs/PROGRESS change, and no e2e uk-layout spec. That leaves three acceptance criteria unproven (the docs-path test, the e2e half of the full gate, and the phase goal's \"fully documented in English\"), and the long-Ukrainian-string layout deliverable has no verification at any viewport. Separately, the five hand-written per-person calculators that now feed the People table have no value-parity test against the canonical `period()`/`at_date()` path — I checked parity manually on dev data (6 people, 0 mismatches) so nothing is wrong today, but `churn_21d` is exercised by no data at all and no cohort other than `all` is covered, which is RISKS row 1's failure mode left unguarded.

## [MAJOR] Documentation tasks T17–T20 not done — phase goal's "fully documented" is unmet
`docs/SETUP.md`

The diff contains no `docs/` file, no `README.md` and no `CHANGELOG.md` change. Verified: `grep -rn "launchd\|cron\|Scheduling\|profile_dashboard" docs/ README.md CLAUDE.md` returns nothing; `ls docs/user/` has no `index.md` or `troubleshooting.md`; `CHANGELOG.md`'s Unreleased section and `docs/PROGRESS.md` both stop at phase 10 ("Empty-state/MIN_SAMPLE polish, the contrast audit, and seed_demo --scale → phase 11"). So the missing items are: SETUP.md Scheduling (launchd/cron) + backup-restore verification + key-material warning + Performance section, CONFIGURATION.md env-var table, GITHUB_CONNECTIONS.md first-live-sync checklist, TRANSLATIONS.md long-string/canary notes, POLICY.md reading notes, docs/user/index.md + troubleshooting.md linked from README, and the phase-11 CHANGELOG/docs/PROGRESS entries. Phase 11 also ships user-visible behaviour with no user doc: page-level empty states, the `≈` small-sample glyph in metric tables, the retuned palette, and two new operator tools (`seed_demo --scale large`, `scripts/profile_dashboard.py`) that nothing outside PLAN.md/DECISIONS.md mentions.

**Fix:** Complete T17/T18/T20 as written in PLAN.md. At minimum: the SETUP.md Scheduling + Performance sections (they are the only home for `seed_demo --scale large` and `scripts/profile_dashboard.py`), docs/user/index.md + troubleshooting.md, and the CHANGELOG entry covering empty states, small-sample markers in tables, and the contrast retune.

## [MAJOR] Acceptance criterion "every docs path referenced by README/CLAUDE.md exists" has no test (T19)
`tests/test_docs.py`

`grep -n "readme\|docs_path\|internal_link" tests/test_docs.py` returns nothing — neither `test_every_docs_path_referenced_by_readme_and_claude_md_exists` nor `test_every_docs_internal_link_resolves` was written. I checked the criterion by hand (`grep -oE 'docs/[A-Za-z0-9_./-]+' README.md CLAUDE.md` then testing each path) and every referenced file does exist today, so the product is fine — but the criterion is unproven and will silently rot the first time a doc is renamed.

**Fix:** Add the two tests from PLAN.md T19: regex `docs/[A-Za-z0-9_./-]+` over README.md + CLAUDE.md asserting each path exists (directories asserted as directories), plus relative-link resolution inside `docs/**`.

## [MAJOR] e2e deliverable T16 missing and the e2e half of the gate never run (T21)
`e2e/web`

`e2e/web/` contains no `test_layout_uk.py` and `e2e/plans/` no `layout_uk.plan.yaml`. The plan's own deliverable "long Ukrainian strings do not clip or overflow" (RISKS row 8, the reason T14 changed `kpi_card.html` and `table.html`) therefore has no proof at any viewport — the T14 edits are unverified beyond "the tree still renders". T21 is unchecked and `.autodev/phases/11-polish-and-performance/TEST_OUTPUT.txt` records only `uv run pytest -q`; there is no `make e2e-up && uv run pytest e2e -q` output anywhere in the phase artefacts, so the acceptance criterion "the full gate — … and the e2e suite — passes" is unproven. Note this is the fourth consecutive phase with e2e unrun (phases 8 and 9 recorded "e2e skipped: the surfaces could not be started"), and phase 11 is the last phase.

**Fix:** Write `e2e/plans/layout_uk.plan.yaml` + `e2e/web/test_layout_uk.py` per PLAN.md §6 (uk at 1280×800 and 390×844, `scrollWidth <= clientWidth + 1`, no clipped KPI card or table header), then run `make e2e-up && uv run pytest e2e -q; make e2e-down` and record the output. If the surfaces genuinely cannot start, paste the failing command and its exact output into DECISIONS.md rather than leaving the task blank.

## [MAJOR] New batched per-person calculators have no value-parity test against the canonical path
`apps/metrics/services.py`

`compute_many()` now routes PERSON scope through `_other_results_by_person()` (apps/metrics/services.py:597), which calls five newly hand-written duplicates of existing logic: `_period_by_person` / `_pr_size_p50_period_by_person` / `_reviewer_response_p50_period_by_person` (flow.py), `_churn_21d_period_by_person` (quality.py), `_violations_open_at_date_by_person` (adoption.py). Each re-derives the population, the cohort narrowing and the grouping that `period()`/`at_date()` already express — e.g. `_reviewer_response_p50_period_by_person` re-implements `_reviews_given_in_scope`'s reviewer-vs-author distinction from scratch. The only coverage added is query counts (`test_query_counts.py`, `test_people_page.py`) and `test_compute_include_series.py` (series shape only). The existing equivalence test `apps/metrics/tests/test_compute_many.py::test_compute_many_matches_compute_field_by_field_for_every_scope` runs at REPO scope with `include_series=True`, so it never enters the new branch. I spot-checked parity by hand against the dev database (6 people × `PEOPLE_METRIC_KEYS`, cohort=all, 0 mismatches), so no live divergence — but `churn_21d` returned `None`/sample 0 for every person (no `ChurnResult` rows in the large seed), so that calculator's grouping is exercised by nothing at all, and no cohort other than `all` is covered. A drift here puts a different lead time in the People table than on the same person's detail page: RISKS row 1's exact failure mode.

**Fix:** Extend `apps/metrics/tests/test_compute_many.py` with a PERSON-scope, `include_series=False` case that compares `compute_many(...)[person_id]` field-by-field against `compute(keys, Scope(PERSON, person_id, ...), include_series=False)` for every key in `PEOPLE_METRIC_KEYS`, parametrized over `Cohort.ALL`/`AI`/`NON_AI`, with a fixture that gives at least one person a settled `ChurnResult` and one person zero rows.

## [MINOR] period_has_pull_requests() uses a different window predicate than the page it labels
`apps/dashboards/selectors.py`

`apps/dashboards/selectors.py::period_has_pull_requests()` tests `created_at` inside the period and ignores both cohort and `params.pr_filters`, while the surfaces below the banner use other predicates: KPIs use `merged_at` (`_merged_population`), the Recent PRs table uses `last_activity_at` (`rows.recent_pr_rows`). Two inconsistencies follow. (1) False positive: with the `7d` preset over PRs created earlier but merged/active this week, the page renders populated KPI cards and a populated Recent PRs table under the sentence "No pull requests match this period and filter. Widen the period or clear the filters." (2) False negative: a cohort or PR filter that matches nothing leaves every table and KPI empty with no explanation, even though the explanation text explicitly offers "clear the filters" as the remedy. `test_empty_period.py` seeds PRs whose `created_at` and `merged_at` are both inside the window, so neither case is covered.

**Fix:** Make the existence check match what the page shows — e.g. `Q(created_at__range=...) | Q(last_activity_at__range=...)` on the scoped, cohort-narrowed queryset with `params.pr_filters` applied — and add a test with a PR created before the period and merged inside it asserting no banner.

## [MINOR] The min-sample regression guard cannot fail for the case its docstring claims
`tests/test_min_sample_surfaces.py`

`tests/test_min_sample_surfaces.py` states it "Fails on a new surface added without a marker" and PLAN.md T5 repeats the claim. `_files_matching_markers()` greps for `below_min_sample`/`small_sample_note`/`cell-small-sample`/`__low` and compares the hit set to `_DECLARED_MARKER_FILES`. A new template that renders a `MetricResult` *without* any marker contains none of those strings, so it never enters the set and the assertion still passes. The test only catches the reverse (a declared surface losing its marker, or a new surface that does carry one).

**Fix:** Either restate the docstring to what the test actually guards, or invert the check: enumerate the templates that render a metric value (e.g. grep for `metric_value`/`extract_value` call sites) and assert each one is in the declared marker set.

## [MINOR] test_reviewer_load_rows_carry_no_metric_result asserts nothing
`tests/test_min_sample_surfaces.py`

`tests/test_min_sample_surfaces.py::test_reviewer_load_rows_carry_no_metric_result` creates no fixture data, so `reviewer_load(scope, params)` always returns `[]` and the assertion `result == [] or all(...)` is satisfied by the left disjunct on every run. It cannot fail, including if `reviewer_load` later starts returning `MetricResult`s — the exact drift the test is placed there to catch.

**Fix:** Seed at least one reviewer with reviews and assert on the populated result (`all(isinstance(count, int) …)` with a non-empty list), dropping the `result == []` escape.

## [MINOR] Policy console KPI now shows two overlapping small-sample messages
`apps/policy/templates/policy/partials/kpis.html`

`apps/policy/templates/policy/partials/kpis.html` gained `{% small_sample_note %}` ("Small sample") directly above the pre-existing, more informative note "Sample too small (2 below 5) to be reliable." Both render whenever `kpi_low_sample` is true, so the same KPI card carries the same warning twice in two different wordings.

**Fix:** Keep one. Either drop the `{% small_sample_note %}` include here (the blocktranslate sentence already carries the sample numbers and satisfies the not-colour-only rule), or replace the sentence with the shared tag if uniformity across surfaces matters more than the numbers.

## [MINOR] Default pytest run now pays two independent ~4-minute large-scale seeds
`tests/test_performance.py`

`apps/dashboards/tests/test_seed_demo_scale.py` and `tests/test_performance.py` each own a module-scoped `django_db_setup` that runs `seed_demo --scale large` (50 repos / 20,000 PRs, measured at 222.8s in DECISIONS) and each tears it down with `reset_large_scale_data()`, so the same volume is built twice per run. PLAN.md's own risk note bounds the added gate wall-clock at "~3 minutes"; the actual cost is roughly triple that. Both are in the default run by design (`slow` is registered but not deselected).

**Fix:** Share one seed — e.g. a session-scoped fixture in a common conftest that seeds once and tears down after the last consumer — or mark one of the two modules to reuse the other's data explicitly.

## [MINOR] The 1.5s budget assertion sits inside the measured spread
`tests/test_performance.py`

PLAN.md T11 session 7 records "cold render ~1.3-1.8s across repeated runs (thin but consistent margin)" against `assert cold_elapsed < 1.5`. A run at the top of that recorded range fails. My own run of `tests/test_performance.py` passed, and the full `uv run pytest -q` passed, so this is a flake risk rather than a live failure — but the phase's own measurements straddle the threshold it asserts.

**Fix:** Do not move the threshold (it is the architecture's stated budget). Either close more of the gap, or make the measurement less noisy — e.g. assert the median of three renders rather than a single sample — and record the residual in RISKS.

## [NIT] Wrong type annotation on seed_demo's batch_meta
`apps/dashboards/management/commands/seed_demo.py`

`apps/dashboards/management/commands/seed_demo.py::_seed_large_pull_requests` declares `batch_meta: list[tuple[random.Random, Repository, Identity, int]]` but appends `(files, repository, author_identity, index)` where `files` is `list[dict[str, Any]]` — which is what the consumer `_seed_large_children` correctly annotates. Not caught because `[tool.mypy] files=` does not cover management commands.

**Fix:** Change the first tuple element to `list[dict[str, Any]]` to match `_seed_large_children`'s signature.

## [NIT] The token-leak guard tests are still held back from every commit
`tests/test_logging.py`

`tests/test_logging.py` and `apps/connections/tests/test_crypto.py` have never been committed (`git log -- <paths>` is empty; the orchestrator holds them back each phase with "it contains what looks like a GitHub token"). The literals are obvious fakes (`ghp_1234567890abcdefghijklmnopqrstuvwxyz`), so nothing is leaking — but the effect is that CLAUDE.md's hardest rule ("No GitHub token ever reaches a log…") has no test in the repository, and neither does token encryption. Phase 11 is the last phase, so this is the last chance to land them.

**Fix:** Build the fake tokens at runtime instead of as literals (e.g. `"ghp_" + "".join(...)` or a factory helper) so the secret scanner passes and both modules can be committed.
