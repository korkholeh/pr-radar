# Review — phase 7 round 1

**Verdict:** changes_requested

Substantial, well-structured phase: 39 metrics across four calculator strategies, kind derived from the strategy type (RISKS row 7 handled structurally), Kyiv day boundaries routed through one helper with real 23:59/00:01/DST tests, a generated docs/METRICS.md with a working freshness gate, and a version-stamped cache with an access fingerprint. `uv run pytest -q` is fully green and the complete lint gate (ruff, ruff format, mypy, makemigrations --check, manage.py check) plus `metrics_doc --check` all pass; every PLAN.md task is checked, none marked `[~]`. Acceptance criteria 2, 3, 4, 5, 6, 7 and 8 each have a real test (two names cited in the plan's table don't exist — `test_recompute_rebuilds_identical_rollups`, `test_rebuild_writes_no_row_for_a_distribution_metric` — but equivalents do). Four majors block approval. (1) Counter and ratio metrics read `DailyRollup` without ever consulting `scope.access`, so a restricted ScopeFilter at global scope returns the unrestricted number — verified by probe: `prs_merged` = 2.0 where it should be 1.0, while distributions correctly return 1; `test_compute_cache.py:80` asserts the leaked value as expected behaviour. RISKS row 3, which the plan claims this phase mitigates, is only half mitigated. (2) Acceptance criterion 1 is unmet: `lead_time_p90`, `time_to_first_review_p90`, `pr_size_p50` and `reviewer_response_p50` are referenced by no test, and the plan's own gate `test_every_registered_metric_has_a_test` was never written. (3) `rebuild()` implements the scope × cohort × metric loop the plan explicitly rejected — measured 4,539 queries for one day on a 20-PR / 34-scope dataset, and both `recompute` and `run_sync` run it synchronously. (4) `mark_dirty()` omits the violation's own `created_at` day, so `violations_new` is undercounted after an incremental sync (verified). Plus three minors (a UTC/Kyiv date mix in a recompute test, no cache/rollup invalidation on a settings change, a masked exception on the sync failure path) and two nits.

## [MAJOR] Counter/ratio metrics ignore ScopeFilter — RISKS row 3 only half mitigated, and a test asserts the wrong invariant
`apps/metrics/services.py`

`_fetch_counter_ratio_rows()` (services.py:136) filters `DailyRollup` by `scope_type`/`scope_id` only; `rollups.py:29` writes every row under a hardcoded `_UNRESTRICTED` ScopeFilter. So counter and ratio metrics never see `scope.access`, while distribution/state metrics (which go through `selectors.py` → `pull_requests_for_metrics(scope.access)`) do. Proven with a probe test: two projects, one merged PR each, `Scope(GLOBAL, None, ScopeFilter(unrestricted=False, project_ids={mine}))` → `prs_merged` = 2.0 (should be 1.0) while `lead_time_p50.sample_size` = 1. `scope_for()` deliberately routes an out-of-scope id *to global* (services.py:57), which is exactly the path a restricted lead hits. Nothing raises — the number is silently wrong. Not exploitable today because `accounts.scope_for_user()` is hardcoded unrestricted, but PLAN.md's Risks section claims row 3 is handled by this phase and Out-of-scope claims phase 9 will be "a one-place change"; it will not be, because rollups are precomputed access-agnostically. Worse, `apps/metrics/tests/test_compute_cache.py:80` asserts `a_again.value == b_again.value == 3.0` for two *different* restricted ScopeFilters — the test bakes the leak in as intended behaviour (rubric: asserting current behaviour as if it were intent).

**Fix:** Either (a) have `compute()` bypass rollups and compute counters/ratios from raw rows via the calculators whenever `scope.access.unrestricted` is False, or (b) for a restricted caller sum the PROJECT-scope rollups over `access.project_ids` instead of reading the GLOBAL row (dedup repos in two projects first). Whichever you pick, add a test that a restricted ScopeFilter yields the narrowed counter, and change `test_two_scope_filters_do_not_share_an_entry` to assert the two leads get *different* values, not the same 3.0.

## [MAJOR] Acceptance criterion 1 unmet: 4 of 39 metrics have no test, and the gate that would catch it was never written
`apps/metrics/tests/test_registry.py`

PLAN.md's acceptance table cites `test_registry.py::test_every_registered_metric_has_a_test` (parametrized over REGISTRY) as the proof for "Every metric has a test over a hand-computed factory_boy dataset". That test does not exist anywhere in the tree. Cross-referencing the 39 keys in `docs/METRICS.md` against every test module under `apps/metrics/tests/`, `apps/github_sync/tests/` and `tests/`, four metrics are referenced by no test at all: `lead_time_p90`, `time_to_first_review_p90`, `pr_size_p50`, `reviewer_response_p50`. The p90 path through `percentile()` (the interpolation branch at base.py:45-52) is therefore never exercised against a hand-computed p90 on a real dataset, and `reviewer_response_p50` — the one metric with a documented approximation and a PERSON-only `levels` set — has neither a value test nor a `MetricNotAvailableAtLevel` test.

**Fix:** Write the four missing cases in `test_metrics_flow_distributions.py` (a hand-computed p90 for an even and an odd sample; `pr_size_p50` over known effective_additions/deletions; `reviewer_response_p50` at PERSON scope including the `ready_for_review_at is None` → `created_at` fallback), then add the promised `test_every_registered_metric_has_a_test` gate so the next metric added without a test fails the build.

## [MAJOR] rebuild() uses the scope × cohort × metric loop the plan explicitly rejected — 4,539 queries for one day on a 20-PR dataset
`apps/metrics/rollups.py`

PLAN.md's Design says rebuild "for each day makes **one pass over that day's rows**, bucketing into `(scope, cohort, metric)` accumulators, rather than looping over every scope × cohort × metric combination." `_build_rows_for_day()` (rollups.py:126) does precisely the rejected thing: for every one of the 20 counter/ratio MetricDefs, for every scope, for every cohort, it calls the calculator, and each calculator issues its own COUNT/aggregate query. Measured with `CaptureQueriesContext` on a deliberately small dataset (3 projects, 10 repos, 20 people, 20 PRs, one day): **4,539 queries and 782 rows for a single day**. This is not phase-11 profiling: `manage.py recompute` with no `--from` defaults to earliest-PR-day → today and runs this per day, and `run_sync()` calls `rebuild_dirty()` synchronously at the end of every sync. At the spec's 50-repo / 20k-PR target the per-day scope count is ~5x larger, putting a full recompute in the millions of queries. It also breaks the plan's claim that the table "stays proportional to activity, not to scopes × days" — 782 rows for 20 PRs is proportional to touched-scopes × cohorts × metrics.

**Fix:** Restore the planned single-pass design: fetch the day's PRs/reviews/violations once with the fields the counters need, then accumulate per `(scope, cohort, metric_key)` in Python. If that is too large for this round, at minimum batch per metric with a `values(...).annotate(Count())` grouped by repository/author instead of one query per scope, and add an `assertNumQueries` bound on `rebuild()` for a fixed dataset so the cost cannot regress unnoticed.

## [MAJOR] mark_dirty() never marks the day a violation was created, so violations_new is undercounted after an incremental sync
`apps/metrics/services.py`

`mark_dirty()` (services.py:83) records the Kyiv days of the PR's `created_at`/`merged_at`/`closed_at` plus each review's `submitted_at`. `PolicyViolation.created_at` is `auto_now_add` (apps/policy/models.py:119) — the moment `evaluate_pull_request()` writes the row, which for a re-evaluated or newly-matched PR has nothing to do with the PR's own dates. The `violations_new` counter is keyed on that `created_at` (docs/METRICS.md: "count of violations with created_at on the day"), so its day is never added to `DirtyDay` and `rebuild_dirty()` never rebuilds it. Proven: a PR created 10 days ago that gains a violation today leaves `DirtyDay` = `[2026-09-08]` only; `day_of(violation.created_at)` = 2026-09-18 is absent. Result: after an incremental sync, `violations_new` for today reads None/0 until someone runs a full `recompute`. `_scopes_for_day()` already knows violations matter for a day (rollups.py:86) — the dirty set just never nominates that day.

**Fix:** In `mark_dirty()`, also add `day_of(created_at)` for the PR's `PolicyViolation` rows (mirroring the Review query already there). A `test_mark_dirty_records_the_violation_creation_day` case in `test_data_version.py` pins it.

## [MINOR] test_from_to_rebuilds_only_that_range mixes UTC and Kyiv dates and will flake at night
`apps/github_sync/tests/test_recompute_command.py`

The test (line 158) builds its range and its assertions from `timezone.now().date()` — a UTC calendar date — while rollups are keyed by Kyiv days. Between 21:00 and 24:00 UTC (22:00–24:00 in winter) Kyiv is already on the next calendar day, so `DailyRollup.objects.filter(date=in_range.merged_at.date())` looks up the wrong day and `--to now.date()` can exclude the day the row was actually written to. In a project whose central invariant is the Kyiv day boundary, the test that guards `--from/--to` should not be the one using UTC dates.

**Fix:** Use `apps.metrics.timeframe.day_of(...)` / `today()` for both the `--from/--to` arguments and the assertions, as `test_sync.py::test_successful_sync_leaves_rollup_rows_...` already does.

## [MINOR] Changing a metrics setting invalidates neither the rollups nor the compute() cache
`apps/catalog/setting_defs.py`

`data_version()` is bumped only by `run_sync()` and `manage.py recompute`. Several `metrics`-group settings are baked into stored rollup rows or into a cached result: `AI_COHORT_INCLUDE_SUSPECTED` decides which PRs land in the `ai`/`non_ai` rollup rows, `PR_SIZE_BUCKETS`/`REVIEW_LOAD_TOP_N`/`STALE_DAYS`/`WAITING_REVIEW_HOURS` feed calculators, and `MIN_SAMPLE` is frozen into the cached `below_min_sample` flag. Flipping `AI_COHORT_INCLUDE_SUSPECTED` in the settings UI leaves every cohort rollup wrong indefinitely (nothing rebuilds them), and a `MIN_SAMPLE` change is invisible for up to `METRICS_CACHE_TTL_SECONDS`. Nothing in the diff or in docs/CONFIGURATION.md tells the operator a recompute is required.

**Fix:** Call `metrics.services.bump_data_version()` from `catalog.services.set_setting()` when the changed key is in the `metrics` group (clears the read cache), and note in `docs/CONFIGURATION.md` that the cohort- and bucket-affecting settings need `manage.py recompute` to rewrite existing rollups.

## [MINOR] rebuild_dirty() on the sync failure path can mask the original exception
`apps/github_sync/services.py`

In `run_sync`'s `except Exception as exc:` block the new `rebuild_dirty()` / `bump_data_version()` calls (lines 416-417) run before the bare `raise`. `rebuild_dirty()` is the most expensive operation in the function and touches every registered calculator; if it raises (an IntegrityError on a concurrent DirtyDay, a calculator bug), the bare `raise` is never reached and the caller sees the rebuild's exception instead of the sync failure that actually happened. The `SyncRun` row is already saved with its terminal status at that point, so the rebuild is best-effort by nature.

**Fix:** Wrap the two calls in a `try/except Exception: logger.exception(...)` so a failed post-run rebuild is logged and the original sync exception still propagates.

## [NIT] CHANGELOG says "~40 metrics"; the registry holds exactly 39
`CHANGELOG.md`

The registry is a fixed, generated-and-documented set (`docs/METRICS.md` lists 39). A user-facing changelog can state the number exactly.

**Fix:** Say "39 metrics".

## [NIT] apps/metrics/docs.py is the only new metrics module outside the mypy file list
`pyproject.toml`

T1 added `types.py`, `registry.py`, `rollups.py`, `timeframe.py` and `calculators/` to `[tool.mypy].files`; `services.py`/`selectors.py` are covered by the `apps/*/` globs. `apps/metrics/docs.py` — which generates a version-controlled artefact and does `getattr`-based duck typing on calculator strategies (`_calculator_callable`) — is checked by nothing.

**Fix:** Add `apps/metrics/docs.py` to the mypy file list.
