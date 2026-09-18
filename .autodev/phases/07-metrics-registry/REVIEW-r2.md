# Review — phase 7 round 2

**Verdict:** changes_requested

Round 2 of phase 7 fixes every blocker/major from r1 convincingly: restricted callers now bypass access-agnostic rollups (with a corrected cache test), rebuild() is batched via grouped aggregates and guarded by both an oracle test comparing every rollup row to the per-scope calculator and a query-count bound, mark_dirty() records violation days, and the four untested metrics plus the promised registry gate exist. All eight acceptance criteria have a real test; all 20 PLAN tasks are checked with none marked [~]; `uv run pytest -q` and the full lint gate (ruff, ruff format, mypy, makemigrations --check, manage.py check) pass on my own run. One major remains, of the same class as r1's violations finding: mark_dirty() never nominates the *reverted* PR's merge day, so revert_rate silently under-reports after an incremental sync until a full recompute — verified by probe (DirtyDay = [2026-06-20] only; revert_rate__num for 2026-06-10 absent, vs (1.0, 1) after a full rebuild). Also three smaller items: a prose paragraph inserted mid-table breaks docs/CONFIGURATION.md rendering for the policy/churn/ui/export groups; rebuild_dirty() can drop a concurrently written DirtyDay marker; and the restricted read path costs 902 queries where the unrestricted one costs 7 (measured), with no bound test before phase 9 turns narrowing on.

## [MAJOR] mark_dirty() never nominates the reverted PR's merge day, so revert_rate stays stale after an incremental sync
`apps/metrics/services.py`

`mark_dirty()` (services.py:84) records the Kyiv days of the synced PR's own `created_at`/`merged_at`/`closed_at`, its reviews' `submitted_at` and its violations' `created_at`. `revert_rate`'s numerator is `_merged_on_day(...).filter(reverted_by__isnull=False)` (quality.py) — i.e. a property of the *reverted* PR's merge day that only changes when a *different*, later PR is synced with `reverts_pr` pointing at it (`activity/derive.py:366`). Syncing the revert PR marks only its own days, so the old merge day's rollup rows are never rebuilt. Proven with a probe: PR merged 2026-06-10, rollups built; a revert PR merged 2026-06-20 is then synced and `mark_dirty(revert.id)` leaves `DirtyDay = [2026-06-20]` (2026-06-10 absent); `revert_rate__num` for 2026-06-10 stays missing (the ratio reads 0.0) while a full `rebuild(2026-06-10)` writes `(1.0, 1)`. Result: revert_rate silently under-reports — including at PERSON scope — until someone runs a full `manage.py recompute`. This is exactly the class of bug r1's finding #4 fixed for violations; it is the last cross-PR dependency the dirty-day set misses (all other additive metrics are keyed on a timestamp of the PR itself or its own reviews/violations). RISKS row 1 (a wrong number about a named individual) is the risk this hits.

**Fix:** In `mark_dirty()`, also add `day_of(merged_at)` for the PR referenced by `reverts_pr` (`PullRequest.objects.filter(reverted_by__id=pull_request_id)` or read `reverts_pr_id` and its `merged_at`), mirroring the review/violation queries already there. If `derive` can clear or repoint `reverts_pr`, mark the previously referenced PR's merge day too. Pin it with a `test_mark_dirty_records_the_reverted_pr_merge_day` case in `test_data_version.py`.

## [MINOR] The new metrics-settings paragraph is inserted inside the settings table and breaks its rendering
`docs/CONFIGURATION.md`

The explanatory paragraph added at docs/CONFIGURATION.md:68 sits between the `metrics | DEFAULT_PERIOD_DAYS` row and the `policy | NO_TESTS_MIN_LINES` row. A blank line plus prose terminates a GitHub-flavoured Markdown table, so every row after it — the whole `policy`, `churn`, `ui` and `export` groups — renders as literal `| policy | ... |` text instead of table rows. The content itself is right and needed; only its position is wrong.

**Fix:** Move the paragraph to after the last table row (or above the table), leaving the table contiguous.

## [MINOR] rebuild_dirty() computes a day's rows outside the transaction but deletes its DirtyDay marker inside it, so a concurrent mark_dirty for that day is lost
`apps/metrics/rollups.py`

`rebuild_dirty()` (rollups.py:135) calls `_build_rows_for_day(date)` before opening the transaction, then inside the transaction deletes the rollups, writes the freshly computed rows and deletes `DirtyDay` rows for that date. `DirtyDay.date` is unique, so a concurrent `mark_dirty()` for the same day is a no-op `get_or_create` on the same row — which is then deleted. A PR committed in that window (e.g. a huey `process_pull_request` task still running while a sync's post-run rebuild proceeds, or a `recompute` overlapping a sync) is silently omitted from the rebuilt day and no marker survives to retry it; the day stays wrong until a full recompute. Low probability today (one sync at a time, single huey thread), but the failure is silent and permanent.

**Fix:** Capture the `DirtyDay` row's `id`/`marked_at` before computing the day's rows and delete by that id/`marked_at__lte` inside the transaction, so a marker written after the read survives and the day is rebuilt again.

## [MINOR] The restricted-ScopeFilter read path costs O(days x buckets) queries per metric and has no bound test
`apps/metrics/services.py`

`_counter_ratio_value_from_raw()` (services.py:206) calls the calculator once per day, and `_compute_uncached()` calls it again for the previous period and once per series bucket. Measured on a 30-day window, day granularity, 5 counter/ratio keys: 902 queries for a restricted caller vs 7 for an unrestricted one (values are correct). A phase-8 dashboard requesting 10-20 metrics would cost a few thousand queries per cold read once phase 9 turns per-user narrowing on — against RISKS row 10's 1.5 s budget. Correctness is right and the fix location is a single function, so this is not urgent, but nothing in the test suite bounds it and phase 9 will inherit it silently.

**Fix:** Reuse the batch machinery for the restricted path: run each metric's grouped-aggregate query once over the whole span grouped by day (or sum PROJECT-scope rollups over `access.project_ids` with a repo-dedup step), and add a `django_assert_num_queries` bound for a restricted `compute()` alongside `test_compute_query_count_is_bounded_for_a_multi_metric_call`.

## [NIT] test_every_registered_metric_has_a_test greps source text rather than proving an assertion exists
`apps/metrics/tests/test_registry.py`

The acceptance-criterion gate reads the six metric-test modules as text and asserts the key appears as a quoted string. A key mentioned in a comment, a helper list or an unrelated import would satisfy it, and the module list is hardcoded so a test written in a seventh file wouldn't count. All 39 keys currently do have real value assertions (checked), so this is only about future drift.

**Fix:** Keep the grep gate but scope it to `get_metric("<key>")` occurrences, and derive the module list from `tests_dir.glob("test_metrics_*.py")` so a new metric-test module counts automatically.
