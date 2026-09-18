# Review — phase 10 round 1

**Verdict:** changes_requested

Phase 10 delivers all seven acceptance criteria with genuine tests — a self-built temporary git repository with hand-counted line numbers, real clone/fetch against a file:// origin, a full token-leak sweep over argv/remote/.git/config/logs/ChurnResult.error, and positive+negative pairs for both ratio metrics plus the on-screen "heuristic" label. All 21 PLAN tasks are checked with matching artifacts, every planned deviation is logged in DECISIONS, and the docs set is complete. Three major defects sit in real-input territory the suite does not reach: a PR that deletes any non-excluded file always ends as error/blame_failed (blame on a path absent at the merge commit) and is then retried every night forever; the per-repository time budget is computed at submit time so every repository past the first pool wave silently does zero work after paying for its clone; and worker threads still call get_int(), which opens a second SQLite connection on a cold settings cache — the exact contention hazard DECISIONS claims was designed out — with the resulting OperationalError escaping run_churn and aborting the run.

## [MAJOR] Any PR that deletes a file always ends as status=error, and is retried forever
`apps/churn/services.py`

`_sum_blame` (services.py:87) blames every non-excluded `PRFile.path` at `merge_commit_sha`. `PRFile` rows come from GraphQL `changeType` lowercased (`apps/github_sync/mappers.py:135`), so a PR that removes a file stores a row with `status="deleted"` and `is_excluded=False`. That path does not exist at `merge_commit_sha`, and `blame_counts` raises `GitOperationError` for a missing path — the project's own `apps/churn/tests/test_blame.py::test_blame_counts_raises_on_a_missing_path` proves exactly this. The whole PR therefore returns `error`/`blame_failed` instead of a ratio. Deleting a file is routine, so a large share of real PRs will never get a churn number, `churn_21d` is biased toward PRs that only add, and because `error` rows are deliberately retried by `eligible_pull_requests`, every such PR is re-cloned and re-blamed on every nightly run, permanently. No test covers a PR with a deleted file — the case taxonomy in PLAN §11 does not list it.

**Fix:** In `_sum_blame`, skip paths absent at the rev (reuse `blame._path_exists_at`) or catch `GitOperationError` per path and contribute 0 — a deleted file genuinely added 0 surviving lines at merge. Add a `git_origin` history where the merge commit deletes a file and assert the PR still computes a ratio.

## [MAJOR] Per-repository time budget starts ticking at submit time, not when the worker starts
`apps/churn/services.py`

In `run_churn` (services.py:264-268) the deadline `time.monotonic() + repo_budget` is evaluated eagerly in the main thread inside the submit comprehension, for every repository at once. With `CHURN_MAX_WORKERS=4` and `CHURN_REPO_TIME_BUDGET_SECONDS=600`, the 5th and later repositories burn their entire budget sitting in the executor queue. A repository that waits >600 s still pays for `ensure_clone` (the expensive part) in `_compute_repository`, then immediately breaks out of the PR loop and returns `[]` — no rows, no error, no log line. Silent zero progress for every repository past the first wave on any install with more than a handful of repos, which is exactly the nightly-run shape this budget exists for.

**Fix:** Pass `repo_budget` (not an absolute deadline) into `_compute_repository` and compute `deadline = time.monotonic() + repo_budget` as its first statement, so the budget measures work time rather than queue time.

## [MAJOR] Worker threads still issue ORM reads, and an unexpected worker exception aborts the whole run
`apps/churn/services.py`

DECISIONS records that worker threads must never open their own ORM connection (they hit `sqlite3.OperationalError: database table is locked`, RISKS row 14), and that the fix was prefetching. Two DB-backed reads remain inside the pool: `get_int("CHURN_MAX_FILES")` at services.py:127 and `get_int("CHURN_GIT_TIMEOUT_SECONDS")` at clones.py:28 and clones.py:37. Both go through `catalog.services._all_setting_rows()`, which falls through to `AppSetting.objects.all()` whenever the FileBasedCache entry is cold — e.g. the first nightly run after a cache wipe or a fresh `DATA_DIR` — so up to four worker threads open their own SQLite connections concurrently. `OperationalError` is not a `GitOperationError`, so it is not converted to an `error` outcome; it propagates out of `future.result()` (services.py:270) and out of `run_churn`, losing every not-yet-collected repository's outcomes and skipping `bump_data_version()`. The same escape hatch exists for `OSError` from `target.parent.mkdir`/`shutil.rmtree` in `ensure_clone` (the disk-exhaustion case PLAN §11 defers). PLAN §6 and the RISKS table both promise "never an exception escaping `run_churn`".

**Fix:** Read `CHURN_MAX_FILES`, `CHURN_GIT_TIMEOUT_SECONDS` and `CHURN_REPO_TIME_BUDGET_SECONDS` once in `run_churn` and pass them into `_compute_repository` / `compute_churn_for_pull_request` / `ensure_clone`. Wrap the `future.result()` call in `try/except Exception`, logging and emitting `error` outcomes for that repository's PRs, so one repository's unexpected failure cannot abort the run.

## [MINOR] No timeout on blame, rev-list or log; only clone/fetch are bounded
`apps/churn/gitcmd.py`

`CHURN_GIT_TIMEOUT_SECONDS` is passed only from `clones._clone`/`_fetch`. `blame_counts`, `resolve_path_at`, `_path_exists_at`, `_pr_commit_set`'s `rev-list` and `_snapshot_sha` all call `run_git` with `timeout=None`. A `git blame -M -C` on a pathological file blocks its worker thread indefinitely; `ThreadPoolExecutor.__exit__` then waits forever, so the nightly huey task never finishes. The per-repository budget cannot help — it is only checked between PRs. PLAN §5 states every call goes through `run_git(..., timeout=CHURN_GIT_TIMEOUT_SECONDS)`.

**Fix:** Default `run_git`'s `timeout` to the configured value (resolved once on the main thread, per the finding above), or pass it explicitly from `blame.py` and the `services.py` git call sites.

## [MINOR] run_git masks stderr by token-shape pattern only, though it holds the exact credential
`apps/churn/gitcmd.py`

`run_git` (gitcmd.py:69) masks stderr with `config.security.mask_secrets`, which only matches the `ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_`/`github_pat_` prefixes. Any credential that does not carry one of those prefixes would pass straight through into `GitOperationError`, then into `ChurnResult.error`, then onto the PR detail page as `churn_error_detail`. `run_git` already has `credentials[1]` in hand; masking the literal value is a free, shape-independent guarantee, which matters because CLAUDE.md states the no-token rule absolutely and this is the newest path from a token to a DB column and a template.

**Fix:** In `run_git`, after `mask_secrets`, also `detail.replace(token, f"***{token[-4:]}")` when `credentials` was supplied.

## [MINOR] Zero-line PRs never settle, and a stale error row survives a later skip
`apps/churn/services.py`

A PR whose `lines_at_merge == 0` correctly writes no `ChurnResult` (spec §9.6), but that also means it never becomes settled for `eligible_pull_requests`, so every nightly run re-selects it, fetches its repository and re-blames it — forever, for every such PR. Combined with the deleted-file bug above, the nightly run's work grows monotonically rather than draining. Separately, `_write_outcome` returns early on `skip`, so if a PR had an `error` row from an earlier run and now evaluates to skip, the stale error row stays on the PR detail page indefinitely.

**Fix:** Either record the skip in a way that settles the PR without a bogus ratio (e.g. a `ChurnResult` with `status=ok`, `lines_at_merge=0` and `churn_ratio=None`, which `churn_21d` already filters out via `churn_ratio__isnull`), or track skipped PR ids and exclude them from eligibility. Delete any existing row when an outcome turns out to be a skip.

## [MINOR] update_followup_fixes re-derives every PR merged in the previous 14 days on every single sync
`apps/activity/followup.py`

`process_pull_request` calls `update_followup_fixes(pull_request_id)` for every synced PR. That loads every PR in the repository merged in `[merged_at - 14d, merged_at)` (not filtered to `state=MERGED`, so open/closed rows are fetched and discarded) and runs `compute_has_followup_fix` on each, which in turn issues one `_non_excluded_paths` query per candidate. For a repository merging ~100 PRs per fortnight that is thousands of queries per synced PR, and an hourly sync processes many PRs. The design moved the quadratic cost out of the rollup (RISKS row 10) but landed it in the sync path, where nothing measures it.

**Fix:** Filter `originals` to `state=MERGED`; load the candidate/original path sets in one `PRFile` query grouped by `pull_request_id` instead of one query per PR; and skip the cascade entirely when the freshly synced PR is not `is_hotfix` (it cannot be anyone's follow-up fix).

## [NIT] The too_large sentence renders today's CHURN_MAX_FILES, not the limit in force when the row was written
`apps/dashboards/views.py`

`churn_max_files` is read live in `_pull_request_detail_context` (views.py:486). A row recorded as `too_large` under a limit of 50 will read "more than 200 files" after an operator raises the setting, which is simply false for that PR. `too_large` is terminal, so the row is never recomputed to match.

**Fix:** Either store the limit that triggered the status (e.g. in `ChurnResult.error` as a parameter) and render that, or drop the number from the sentence.

## [NIT] New query-count guard is weaker than the exact assertion it duplicates
`apps/dashboards/tests/test_person_page.py`

`test_person_comparison_table_query_count_does_not_regress` uses `django_assert_max_num_queries(330)` while `apps/dashboards/tests/test_query_counts.py::test_person_page_query_count` already pins the same page at exactly 321 (and was not touched by this phase, so adding `followup_fix_rate` to `PERSON_COMPARISON_METRIC_KEYS` cost nothing). A 9-query slack ceiling next to an exact assertion adds no signal and will mask a small regression if the exact test is ever relaxed.

**Fix:** Drop the new test, or make it an exact `django_assert_num_queries` for the comparison-table fragment specifically.
