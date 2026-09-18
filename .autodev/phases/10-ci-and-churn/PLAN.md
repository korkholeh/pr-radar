# Phase 10 — CI first-pass, follow-up fixes and churn

**Goal.** The three remaining quality metrics: `ci_first_pass_rate` proven end to end, the `followup_fix_rate`
heuristic actually computed and labelled, and `churn_21d` filled by the only component that shells out to `git`
and touches the filesystem at scale.

Spec: §4.5 (`ChurnResult`), §8.2 (`followup_fix_rate`, `ci_first_pass_rate`, `churn_21d`), §9 (the churn
algorithm), §10.3/§10.4 (KPI row 3, PR detail page), §15 (defaults).
Architecture: `.autodev/ARCHITECTURE.md` (`churn` app responsibility, exit 3 "the `git` subprocess",
budgets table row *Churn*, "git / churn" error handling), ADR 0004 (credential handoff), ADR 0005 (every huey
task is also a management command), ADR 0007 (rollup vs on-read storage).
Risks: rows 1, 2, 8, 10, 11, 14, 15.

---

## Context

### What exists

- **`apps/churn`**: `ChurnResult` model only — `pull_request`, `window_days`, `lines_at_merge`,
  `lines_surviving`, `churn_ratio`, `snapshot_sha`, `computed_at`, `status`
  (`ok`/`unsupported_merge_method`/`too_large`/`error`), `error`; unique on `(pull_request, window_days)`;
  `ChurnResultFactory`; admin. No service, no git code, no command, no task.
- **`ci_first_pass_rate` is already computed** (`apps/metrics/calculators/quality.py:139-200`): a `RatioCalc` over
  `CheckStatus(is_first_ci_commit=True)` with `rollup_state=SUCCESS` as numerator, batched by
  `pull_request__repository_id` / `pull_request__author__person_id`. One test exists
  (`apps/metrics/tests/test_metrics_quality.py::test_ci_first_pass_rate_ignores_prs_with_no_first_ci_commit_check_status`)
  and it already carries a positive and a negative row plus the excluded no-CheckStatus case.
- **`followup_fix_rate` is a registered placeholder** (`quality.py:394-425`): a `RatioCalc` whose numerator,
  denominator and both batch functions return empty. Its title already reads
  *"Follow-up fix rate (heuristic)"* and its description starts with `(heuristic)`.
- **`churn_21d`** is a `DistributionCalc` reading `median(churn_ratio)` over `ChurnResult(window_days=21,
  status=ok)` rows for PRs merged in the period. Already wired into `QUALITY_ROW`
  (`apps/dashboards/kpis.py:47`), the churn/rework chart (`charts.py:264`), `PEOPLE_METRIC_KEYS`
  (`rows.py:40`) and `PERSON_COMPARISON_METRIC_KEYS` (`person.py:30`). It returns `None` today only because
  no `ChurnResult` row is ever written.
- **PR detail** (`apps/dashboards/views.py:_pull_request_detail_context`) already has a churn slot, but it
  filters `status=ChurnResult.Status.OK` and the template
  (`apps/dashboards/templates/dashboards/pull_request_detail.html:65-72`) renders either a percentage or
  *"Churn not computed yet."* — the four statuses are invisible.
- **Derived fields**: `PullRequest.is_hotfix` is already the hotfix/fix pattern this phase needs
  (`apps/activity/derive.py:20-21`: `^\s*(hotfix|fix)\b` on the title, `^(hotfix|fix)/` on `head_ref`).
  `PRFile(path, is_excluded)` gives the file sets; `PullRequestCommit → Commit.sha` gives the PR commit set;
  `PullRequest.merge_method` is `merge`/`squash`/`rebase`/`unknown` and `merge_commit_sha` is stored.
- **Settings**: `CHURN_WINDOW_DAYS`=21 and `CHURN_MAX_FILES`=50 exist in `apps/catalog/setting_defs.py`
  (group `churn`); `FOLLOWUP_FIX_WINDOW_DAYS`=14 and `FOLLOWUP_FIX_FILE_OVERLAP`=0.5 exist (group `metrics`).
  `apps.catalog.services.get_int/get_float` read them; a data migration re-runs `SETTING_DEFS` on each addition
  and `apps/catalog/tests/test_app_settings.py` walks every def.
- **Infra**: `DATA_DIR/repos/` is already created at settings import (`config/settings/base.py:19`).
  `apps.connections.auth.auth_for_connection(connection).get_git_credentials() -> (username, token)` exists and
  is the only credential source. `SecretMaskingFilter` is on the root logger. `apps/dashboards/tasks.py` is the
  in-repo pattern for `@db_periodic_task(crontab(...))` next to a management command. `metrics.services.mark_dirty`
  + `DirtyDay` + `rollups.rebuild_dirty()` is the incremental-rollup machinery. `metrics.services.bump_data_version`
  invalidates the metrics cache. Root `conftest.py` fails any unmocked outbound HTTP request.
- **Docs/tests that gate**: `tests/test_docs.py` and `apps/metrics/tests/test_metrics_doc.py` fail if
  `docs/METRICS.md` is stale; `tests/test_translations.py` fails on empty/fuzzy `msgstr`;
  `tests/test_pages_smoke.py` renders every page in en/uk × light/dark; `tests/test_urls_login.py` walks every
  named URL.

### What this phase changes

1. `followup_fix_rate` stops returning empty: a new derived cache field `PullRequest.has_followup_fix`, a new
   `apps/activity/followup.py` service that maintains it, and a real `RatioCalc` over it.
2. `apps/churn` grows a real implementation: `askpass.py`, `gitcmd.py`, `clones.py`, `blame.py`, `selectors.py`,
   `services.py`, `tasks.py`, `management/commands/compute_churn.py`.
3. The PR detail churn section becomes status-aware (four codes, rendered in the reader's language).
4. `followup_fix_rate` gets a visible home (the Person comparison table) so its *"(heuristic)"* label is on screen.
5. Three new `churn` AppSettings, uk translations, `docs/METRICS.md` regeneration, `docs/user/churn.md`,
   `docs/CONFIGURATION.md`/`SETUP.md` additions, a `CHANGELOG.md` entry and an e2e spec.

### Key files

| File | Change |
|---|---|
| `apps/activity/models.py` | `+ has_followup_fix` BooleanField (derived cache block) |
| `apps/activity/followup.py` | **new** — the heuristic and the cache maintainer |
| `apps/github_sync/pipeline.py` | call the maintainer, dirty the affected merge days |
| `apps/github_sync/management/commands/recompute.py` | run the maintainer over the range |
| `apps/metrics/calculators/quality.py` | real `followup_fix_rate` calculator |
| `apps/metrics/services.py` | `mark_dirty(..., extra_pull_request_ids=...)` |
| `apps/catalog/setting_defs.py` + migration | `CHURN_MAX_WORKERS`, `CHURN_GIT_TIMEOUT_SECONDS`, `CHURN_REPO_TIME_BUDGET_SECONDS` |
| `apps/churn/askpass.py` | **new** — executable `GIT_ASKPASS` helper |
| `apps/churn/gitcmd.py` | **new** — the only `subprocess` call site |
| `apps/churn/clones.py` | **new** — bare-clone lifecycle |
| `apps/churn/blame.py` | **new** — `--line-porcelain` parsing, rename following |
| `apps/churn/selectors.py` | **new** — eligibility |
| `apps/churn/services.py` | **new** — the §9 algorithm and the run orchestration |
| `apps/churn/tasks.py`, `apps/churn/management/commands/compute_churn.py` | **new** |
| `apps/dashboards/views.py`, `.../pull_request_detail.html`, `apps/dashboards/person.py` | churn statuses, heuristic label |
| `locale/uk/LC_MESSAGES/django.po`, `docs/*`, `CHANGELOG.md`, `e2e/` | parity, docs, e2e |

---

## Design

### 1. `followup_fix_rate` — a derived cache, not an on-read join

The metric is registered as a **ratio**, so `rollups.rebuild()` writes `followup_fix_rate__num` /
`followup_fix_rate__den` per day per scope per cohort. A numerator that had to join `PRFile` to `PRFile` on
`path` at rollup time would be the one genuinely quadratic query in the registry (RISKS row 10), and the
attribute it measures belongs to the **original** PR's merge day — exactly the shape `revert_rate` already has.

So the heuristic is materialised once, at sync time, into a new derived field:

```python
# apps/activity/models.py, in the "Derived cache" block next to is_revert / is_hotfix
has_followup_fix = models.BooleanField(_("has follow-up fix"), default=False)
```

**`apps/activity/followup.py`**

```python
FOLLOWUP_FIELD = "has_followup_fix"


def compute_has_followup_fix(pr: PullRequest) -> bool:
    """True when a merged PR in the same repository, `is_hotfix`, merged in
    (pr.merged_at, pr.merged_at + FOLLOWUP_FIX_WINDOW_DAYS], shares at least
    FOLLOWUP_FIX_FILE_OVERLAP of `pr`'s non-excluded file paths."""


def update_followup_fixes(pull_request_id: int) -> set[int]:
    """Idempotently recomputes the flag for `pull_request_id` itself *and* for every PR in the
    same repository merged in [pr.merged_at - window, pr.merged_at) — the PRs this one could
    newly be a follow-up fix for. Returns the ids whose flag actually changed."""


def update_followup_fixes_for(queryset) -> int:
    """`recompute`'s batch entry point; same function, no per-PR pipeline overhead."""
```

Rules, all decided here so the implementation has no choice left:

- **The pattern is `PullRequest.is_hotfix`** — `derive.py` already implements *hotfix/fix* on title and head ref.
  No second regex.
- **Overlap** = `|paths(A) ∩ paths(B)| / |paths(A)|`, over `PRFile` rows with `is_excluded=False`. `≥` the
  setting (0.5). `paths(A)` empty ⇒ `False` (a missing value is never a positive; CLAUDE.md).
- **Window** is half-open `(merged_at, merged_at + window]`, so a PR is never its own follow-up and
  same-instant noise does not count.
- **Both must be merged**, in the same repository, and `B.id != A.id`. `B` may itself have a follow-up fix.
- **Recomputation is from scratch per PR**, so the flag can go back to `False` — no sticky `True` (matches
  phase 4's "re-running derive produces identical values").
- **Excluded PRs**: the flag is a raw property of the PR; bot/`exclude_from_metrics` filtering stays where it
  already is, in `metrics.selectors.scoped_pull_requests`.

**Wiring.** `process_pull_request` calls `update_followup_fixes(pull_request_id)` after `derive_pull_request`
(it needs `is_hotfix` and `merged_at`) and before `mark_dirty`, passing the returned ids to
`mark_dirty(pull_request_id, extra_pull_request_ids=changed)` so the *originals'* merge days are rebuilt.
`mark_dirty` grows that one keyword argument and dirties `day_of(merged_at)` for each. `recompute` calls
`update_followup_fixes_for(queryset)` next to `derive_pull_requests`.

**The calculator** replaces the placeholder with the same shape as `rework_rate`:

```python
_register_ratio_over_merged_prs(
    "followup_fix_rate",
    ...,
    lambda ctx: count_value(_merged_on_day(ctx).filter(has_followup_fix=True).count()),
    batch_count(lambda cohort, date: _merged_on_day_population(cohort, date).filter(has_followup_fix=True)),
    "PRs merged on the day with a follow-up fix within the window / PRs merged on the day",
)
```

The title keeps `(heuristic)` and the description keeps its leading `(heuristic)`; `params` gains
`{"heuristic": True, "window_days": ..., "file_overlap": ...}` so `docs/METRICS.md` states the thresholds.

### 2. `ci_first_pass_rate`

Already correct. This phase owns only its **proof**: the existing test is extended/split into an explicit
positive case (first-CI commit `SUCCESS` ⇒ counted) and negative case (first-CI commit `FAILURE` ⇒ not counted,
still in the denominator), plus a rollup round-trip test that `rebuild()` → `compute()` reproduces the same
ratio through `__num`/`__den` rows. No production change.

### 3. Churn — module layout

```
apps/churn/
  askpass.py    # executable; the ONLY thing that ever sees the token besides gitcmd's env dict
  gitcmd.py     # run_git(): the only subprocess call site in the project
  clones.py     # clone_dir(), remote_url_for(), ensure_clone()
  blame.py      # blame_counts(), resolve_path_at()
  selectors.py  # eligible_pull_requests(scope-free; this is a background job, not a read surface)
  services.py   # compute_churn_for_pull_request(), run_churn()
  tasks.py      # @db_periodic_task nightly
  management/commands/compute_churn.py
```

Spec §9 names the module `churn/service.py`; every other app in this codebase puts rules in `services.py`
(CLAUDE.md). We use `services.py` — logged as a deviation in DECISIONS.

### 4. Credential handoff (ADR 0004, RISKS row 2)

`apps/churn/askpass.py` is a committed, executable, dependency-free script:

```python
#!/usr/bin/env python3
"""GIT_ASKPASS helper. git runs this with the prompt as argv[1] and reads one line of stdout.
The token reaches it only through the process environment — never argv, never a file, never a URL."""

import os, sys

prompt = sys.argv[1] if len(sys.argv) > 1 else ""
key = "PR_RADAR_GIT_USERNAME" if prompt.lower().startswith("username") else "PR_RADAR_GIT_TOKEN"
sys.stdout.write(os.environ.get(key, ""))
```

`gitcmd.git_env(credentials)` builds the subprocess environment:

| var | value | why |
|---|---|---|
| `GIT_ASKPASS` | absolute path to `askpass.py` | the handoff |
| `PR_RADAR_GIT_USERNAME` / `PR_RADAR_GIT_TOKEN` | from `GitHubAuth.get_git_credentials()` | env only |
| `GIT_TERMINAL_PROMPT=0` | | never block on a TTY in a background job |
| `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null` | | the operator's credential helper must not see or store our token |
| `GIT_ADVICE=0`, `LC_ALL=C` | | parseable, stable stderr |

The remote is always `https://github.com/<full_name>.git` — no userinfo. Only `clone` and `fetch` get the
credential env; `blame`, `rev-list` and `log` run with a credential-free env. `run_git()` never interpolates the
token into `args`, and its exception message is built from `stderr` passed through the same masking function the
logger uses (`config.logging.mask_secrets`).

### 5. Clone lifecycle

`clone_dir(repository) = DATA_DIR / "repos" / <owner> / f"{<name>}.git"`, taken from `full_name.split("/")`.

```python
def ensure_clone(repository, credentials) -> Path:
    """Bare clone if absent, otherwise fetch. A failing fetch deletes the directory and re-clones
    exactly once (ARCHITECTURE "git / churn": the clone directory is disposable); a second failure
    raises GitOperationError with a masked reason."""
```

- clone: `git clone --bare --quiet <url> <dir>`
- fetch: `git fetch --prune --quiet origin "+refs/heads/*:refs/heads/*"` (bare clone ⇒ branches land in
  `refs/heads/`, so `refs/heads/<default_branch>` is the snapshot ref)
- every call goes through `run_git(..., timeout=CHURN_GIT_TIMEOUT_SECONDS)`.

`remote_url_for(repository)` is a module-level function so tests can point it at a `file://` temporary repository
without a network or a credential.

### 6. The §9 algorithm

`compute_churn_for_pull_request(pr, clone_dir, window_days) -> ChurnOutcome` (a frozen dataclass, **not** a model
write — see §8 on threads):

1. `merge_method == rebase` ⇒ `unsupported_merge_method`, no git work, no ratio.
2. Non-excluded `PRFile` count `> CHURN_MAX_FILES` ⇒ `too_large`, no git work. (Checked before step 3 so a huge
   PR never pays for a fetch.)
3. **PR-commit set.**
   - `squash` ⇒ `{merge_commit_sha}`
   - `merge` ⇒ `{c.sha for c in pr.pull_request_commits}`
   - `unknown` ⇒ inferred: `git rev-list --parents -n 1 <merge_commit_sha>`; two or more parents ⇒ treat as a
     merge commit, otherwise as a squash. (Spec names only three methods; GitHub's GraphQL leaves the field
     absent often enough that dropping `unknown` would silently erase most of the metric.)
   - Empty set, or an empty `merge_commit_sha` ⇒ `error` with reason code `no_pr_commits`.
4. **`lines_at_merge`** = Σ over non-excluded paths of `blame_counts(clone, merge_commit_sha, path)` restricted
   to shas in the PR-commit set.
5. **`snapshot_sha`** = `git rev-list -n 1 --before=<merged_at + window, ISO-8601 UTC> refs/heads/<default_branch>`.
   Empty output (nothing on the default branch by then) ⇒ `error`, reason `no_snapshot`.
6. **`lines_surviving`** = the same sum at `snapshot_sha`, per path resolved through renames by
   `resolve_path_at(clone, merge_commit_sha, snapshot_sha, path)`: try the original path; on git's
   "no such path" try the rename chain from `git log --name-status -M --find-renames --format=%H
   <merge_sha>..<snapshot_sha>`, following `R<score>\t<old>\t<new>` forward. A path that ends deleted contributes
   `0` — which is the correct answer, not an error. Blame always runs with `--line-porcelain -M -C`, so lines
   moved inside or between files still count as surviving.
7. **`lines_at_merge == 0` ⇒ no `ChurnResult` row is written at all** (spec §9.6; ARCHITECTURE "git / churn":
   never a 0 % or 100 %). The outcome carries `skip=True` and the writer drops it.
8. `churn_ratio = 1 - lines_surviving / lines_at_merge`, clamped into `[0.0, 1.0]` (`-M -C` can attribute more
   surviving lines than the PR added when a block is copied).
9. Any `GitOperationError` at any step ⇒ `error` with the masked reason; one PR's failure never aborts the
   repository, and one repository's failure never aborts the run (spec §5.3's rule, reused).

`ChurnResult.error` stores a **reason code plus a short masked detail** (`no_snapshot`, `fetch_failed`,
`blame_failed`, `no_pr_commits`, `contents_read_denied`, `timeout`). CLAUDE.md forbids storing rendered English
message text for system-generated *user-visible* text; the PR page renders the code, and the masked git detail is
shown only as raw diagnostic text under it.

### 7. Eligibility

```python
def eligible_pull_requests(window_days, *, repo_full_names=None, project_slug=None, limit=None):
    """Merged PRs whose window has elapsed and that have no settled ChurnResult.
    `status='error'` rows are re-tried: an error is a transient statement about git, not a verdict."""
```

`state=MERGED`, `merged_at__isnull=False`, `merged_at <= now - window`, and
`~Exists(ChurnResult(pr, window_days, status__in=[ok, too_large, unsupported_merge_method]))`. Ordered by
`merged_at`, `select_related("repository__connection")`.

### 8. Run orchestration, parallelism and SQLite

`run_churn(window_days=None, repo_full_names=None, project_slug=None, limit=None) -> ChurnRunResult`:

- groups eligible PRs by repository;
- resolves `auth_for_connection(repository.connection)` once per repository (a `ConnectionNotUsableError` or a
  connection marked invalid ⇒ every eligible PR of that repository gets `error`/`connection_unusable`, and the
  run continues with the other repositories);
- runs repositories through a `ThreadPoolExecutor(max_workers=CHURN_MAX_WORKERS)` — **git work only**. Each
  worker ensures the clone, computes outcomes for its repository's PRs and returns a list of `ChurnOutcome`
  dataclasses. It opens no ORM connection. **Every `ChurnResult` write happens on the main thread**, after the
  future resolves, via `update_or_create` — one writer, which is what keeps RISKS row 14 (SQLite write
  contention) out of the most parallel component in the system;
- enforces `CHURN_REPO_TIME_BUDGET_SECONDS` (600) per repository: once exceeded, the repository's remaining PRs
  are simply left eligible for the next run — not recorded as errors;
- calls `bump_data_version()` at the end (the `churn_21d` distribution is read from raw rows, so no `DirtyDay`
  rebuild is needed — only the metrics cache must be invalidated).

New AppSettings, group `churn`: `CHURN_MAX_WORKERS` = 4 (ARCHITECTURE budget "≤ 4 concurrent git workers"),
`CHURN_GIT_TIMEOUT_SECONDS` = 120, `CHURN_REPO_TIME_BUDGET_SECONDS` = 600, added to `SETTING_DEFS` with the usual
`catalog` data migration.

### 9. Command and schedule (ADR 0005)

`manage.py compute_churn [--repo OWNER/NAME ...] [--project SLUG] [--window N] [--limit N]` calls `run_churn` and
prints a one-line English summary (`computed`, `skipped`, `too_large`, `unsupported`, `errors`).
`apps/churn/tasks.py::compute_churn_task` is `@db_periodic_task(crontab(hour="2", minute="0"))` calling the same
`run_churn` — 2 AM, an hour before `cleanup_exports_task` at 3 AM.

### 10. UI

- `_pull_request_detail_context` fetches the **latest result for `CHURN_WINDOW_DAYS` regardless of status**
  (`order_by("-computed_at").first()`).
- The template renders four branches from `churn_result.status`, plus the existing "not computed yet" when the
  row is absent: `ok` → the percentage with *"{surviving} of {at_merge} lines still present after {n} days"* as a
  full sentence with named placeholders (never concatenated fragments), `unsupported_merge_method` → *"Churn is
  not measured for rebase merges."*, `too_large` → *"This PR changes more than {limit} files; churn was not
  measured."*, `error` → *"Churn could not be measured."* plus the reason-code sentence. Status is never conveyed
  by colour alone (RISKS row 12) — each branch is a sentence.
- `followup_fix_rate` joins `PERSON_COMPARISON_METRIC_KEYS` (`apps/dashboards/person.py`), whose table renders
  `MetricDef.title` — so the on-screen label is *"Follow-up fix rate (heuristic)"*. The churn KPI
  (`QUALITY_ROW`'s `churn_21d`) needs no change; it stops being `None` as soon as `compute_churn` has run.
- uk translations for every new string in the same phase (CLAUDE.md, RISKS row 8).

### 11. Testing strategy — no network, no fixtures-on-disk for git

Churn is the one component that cannot be tested against JSON fixtures. A session-scoped pytest fixture
(`apps/churn/tests/conftest.py::git_origin`) **builds a real temporary git repository** with
`tmp_path_factory` and `run_git`, using a committed identity (`-c user.name=... -c user.email=...`) and
`GIT_CONFIG_GLOBAL=/dev/null` so the developer's config cannot change the result. It creates a known history
whose surviving-line counts are hand-countable:

- `squash_pr`: one squash commit adding 10 lines to `a.py`; a later commit on the default branch rewrites 4 of
  them ⇒ `lines_at_merge=10`, `lines_surviving=6`, ratio `0.4`.
- `merge_pr`: two feature commits (6 + 4 lines across `b.py`, `c.py`) joined by a real merge commit; a later
  commit deletes 5 ⇒ hand-counted ratio.
- `rebase_pr`: merged by replaying commits; `merge_method=rebase`.
- `rename_pr`: a file renamed after merge, to prove `-M -C` + `resolve_path_at` keep the lines.
- `zero_pr`: a PR whose only file is `is_excluded` ⇒ `lines_at_merge == 0`.

`remote_url_for` is monkeypatched to `file://<the temporary origin>` so `ensure_clone` exercises the real
`clone`/`fetch` path with no network and no credential. The root `conftest.py` HTTP guard is untouched and still
fails on any outbound request.

Cases kept, by `.autodev/guides/case-taxonomy.md`:

| Taxonomy | Case |
|---|---|
| Happy path | squash ratio, merge-commit ratio, both hand-counted |
| Input & boundaries | exactly `CHURN_MAX_FILES` files (ok) vs one more (`too_large`); `lines_at_merge == 0`; `overlap` exactly at `FOLLOWUP_FIX_FILE_OVERLAP`; a follow-up exactly at the window edge |
| State | no `ChurnResult` yet; an `error` row re-tried; an `ok` row skipped |
| Errors | fetch fails ⇒ delete + re-clone once ⇒ `error` with reason; unusable connection; missing snapshot |
| Idempotency | a second `compute_churn` run writes no second row and changes nothing; `update_followup_fixes` twice is identical |
| Async chain | the huey periodic task and the command produce the same rows |
| Security | the token in no argv, no remote URL, no `.git/config`, no log, no `ChurnResult.error` |
| Platform | rename following (`-M -C`) |

Deferred, `deferred_not_authored`: real GitHub `Contents: read` denial (no credentials exist this run — RISKS
row 9; simulated by a fetch that exits 128 with an auth message); clone disk exhaustion; a repository large
enough to exercise the 10-minute budget.

### 12. Deviations from ARCHITECTURE.md (each appended to DECISIONS.md)

1. `apps/churn/services.py`, not spec §9's parenthetical `churn/service.py` — every other app uses `services.py`.
2. A new derived field `PullRequest.has_followup_fix`, which spec §4.2 does not list — the alternative is a
   `PRFile`×`PRFile` self-join inside a rollup batch function.
3. `merge_method == unknown` is resolved by parent count rather than being dropped.
4. `ChurnResult(status='error')` is retried on the next run; the other three statuses are terminal.
5. Git runs in threads, all DB writes on the main thread.
6. Three new `churn` AppSettings for the parallelism/timeout budgets ARCHITECTURE states as prose.

---

## Tasks

- [x] **T1: Churn AppSettings.** `apps/catalog/setting_defs.py` — add `CHURN_MAX_WORKERS` (int, 4),
      `CHURN_GIT_TIMEOUT_SECONDS` (int, 120), `CHURN_REPO_TIME_BUDGET_SECONDS` (int, 600), group `churn`, with
      lazy descriptions; new `apps/catalog/migrations/00NN_churn_settings.py` re-running `SETTING_DEFS`. Covered
      by the existing parametrized `apps/catalog/tests/test_app_settings.py`; add the migration-applies assertion
      there if the existing one is not parametrized over new keys.

- [x] **T2: `has_followup_fix` field.** `apps/activity/models.py` (derived-cache block) + migration.
      Test: `apps/activity/tests/test_models.py` (or the factory test) asserts the default is `False`.

- [x] **T3: The heuristic.** `apps/activity/followup.py` — `compute_has_followup_fix`, `update_followup_fixes`,
      `update_followup_fixes_for`. New `apps/activity/tests/test_followup.py`: positive (fix PR at 10 days,
      60 % overlap), negative-by-window (15 days), negative-by-overlap (40 %), negative-by-pattern (not
      `is_hotfix`), boundary (exactly 14 days, exactly 50 %), excluded files ignored, zero-file PR ⇒ `False`,
      cross-repository PR ignored, and idempotency (two runs identical, and a `True` that turns `False` when the
      fix PR is deleted).

- [x] **T4: Wire the heuristic in.** `apps/metrics/services.py::mark_dirty` grows
      `extra_pull_request_ids: set[int] | None`; `apps/github_sync/pipeline.py` calls
      `update_followup_fixes` and forwards the changed ids; `recompute.py` calls `update_followup_fixes_for`.
      Tests: `apps/github_sync/tests/test_pipeline.py` — syncing a fix PR flips the original's flag and dirties
      the original's *merge* day; `apps/metrics/tests/test_data_version.py` (or the dirty-day test) covers
      `extra_pull_request_ids`.

- [x] **T5: `followup_fix_rate` calculator.** Replace the placeholder in
      `apps/metrics/calculators/quality.py` with the `_register_ratio_over_merged_prs` form; add `params`
      (`heuristic`, `window_days`, `file_overlap`). Tests in `apps/metrics/tests/test_metrics_quality.py`:
      positive + negative rows give `0.5`; a rollup round-trip (`rebuild()` then `compute()`) reproduces it.
      Regenerate `docs/METRICS.md` (`uv run python manage.py metrics_doc`) and commit.

- [x] **T6: `ci_first_pass_rate` proof.** Split the existing test into an explicit positive case and an explicit
      negative case (first-CI `FAILURE` counted in the denominator only), and add a rollup round-trip test —
      `apps/metrics/tests/test_metrics_quality.py`. No production change.

- [x] **T7: `GIT_ASKPASS` helper.** `apps/churn/askpass.py`, committed with the executable bit and a shebang.
      New `apps/churn/tests/test_askpass.py`: running it with a `Username for ...` prompt prints the username,
      with a `Password for ...` prompt prints the token, with neither env var set prints an empty string and
      exits 0.

- [x] **T8: `run_git`.** `apps/churn/gitcmd.py` — `git_env(credentials=None)`, `run_git(args, cwd=None,
      credentials=None, timeout=None)`, `GitOperationError`. Masks stderr through the logging mask; never puts a
      credential in `args`. New `apps/churn/tests/test_gitcmd.py`: env contains the askpass path and the two
      `PR_RADAR_GIT_*` vars and nothing token-shaped in `args`; a non-zero exit raises `GitOperationError` whose
      message is masked; a timeout raises `GitOperationError` with reason `timeout`; a credential-free call has
      no `PR_RADAR_GIT_TOKEN` in its env.

- [x] **T9: Temporary-repository fixture.** `apps/churn/tests/conftest.py::git_origin` building the five
      histories of §11 with `run_git`, plus factory helpers that build matching `PullRequest`/`PRFile`/
      `PullRequestCommit`/`Repository` rows. One self-test asserting the fixture's own hand-counted line numbers
      (so a broken fixture fails loudly rather than making the churn tests vacuous).

- [x] **T10: Clone lifecycle.** `apps/churn/clones.py` — `clone_dir`, `remote_url_for`, `ensure_clone`.
      New `apps/churn/tests/test_clones.py`: first call clones into `DATA_DIR/repos/<owner>/<name>.git`; second
      call fetches and does not re-clone; a fetch failure (a corrupted `objects/` directory or a monkeypatched
      `run_git` failing the first fetch) deletes the directory and re-clones **exactly once**; a second failure
      raises `GitOperationError`.
      **STATUS (session 1 handoff): `apps/churn/clones.py` is written (`clone_dir`, `remote_url_for`, `_clone`,
      `_fetch`, `ensure_clone`) and imports cleanly (`manage.py check` passes), but `apps/churn/tests/test_clones.py`
      does not exist yet — no test has run against it. `apps/churn/tests/conftest.py` already provides
      `git_origin` (session-scoped fixture, five hand-counted histories) and `origin_remote` (function-scoped,
      monkeypatches `apps.churn.clones.remote_url_for` to `file://<git_origin.path>`) — use `origin_remote` in the
      new test so `ensure_clone()` exercises a real bare clone/fetch against the fixture, with `settings.DATA_DIR`
      monkeypatched to `tmp_path` first (see `apps/dashboards/tests/conftest.py` for the existing
      settings-override pattern in this repo) so cloning never touches the developer's real `DATA_DIR/repos/`.
      Resume here first in session 2.

- [x] **T11: Blame.** `apps/churn/blame.py` — `blame_counts(clone, rev, path) -> Counter[str]` parsing
      `--line-porcelain -M -C`, and `resolve_path_at(clone, from_rev, to_rev, path)` following renames.
      New `apps/churn/tests/test_blame.py` against `git_origin`: counts match hand-counted lines; a path missing
      at the target rev resolves through a rename; a deleted path returns `None` and contributes zero.

- [x] **T12: Eligibility.** `apps/churn/selectors.py::eligible_pull_requests` + `apps/churn/tests/test_selectors.py`:
      an unelapsed window is excluded; an `ok`/`too_large`/`unsupported_merge_method` row is excluded; an
      `error` row is included; `--repo`/`--project`/`--limit` narrowing works.

- [x] **T13: The algorithm.** `apps/churn/services.py::compute_churn_for_pull_request` + `ChurnOutcome`.
      New `apps/churn/tests/test_churn_compute.py`: **squash ratio** and **merge-commit ratio** against
      hand-counted lines; **rebase ⇒ `unsupported_merge_method` and `churn_ratio is None`**; **> `CHURN_MAX_FILES`
      ⇒ `too_large`** (and exactly at the limit ⇒ computed); **`lines_at_merge == 0` ⇒ `skip`**; `unknown`
      merge method inferred both ways; renames survive; missing snapshot ⇒ `error`/`no_snapshot`; ratio clamped
      to `[0, 1]`.

- [x] **T14: The run.** `apps/churn/services.py::run_churn` + `ChurnRunResult` — grouping, the thread pool,
      main-thread writes, the per-repository time budget, `bump_data_version()`. New
      `apps/churn/tests/test_churn_run.py`: writes one row per computed PR and **no row** for the
      `lines_at_merge == 0` PR; a second run is a no-op (idempotency); a repository whose fetch fails records
      `error` for its PRs and the *other* repository still computes; an unusable connection yields
      `error`/`connection_unusable`; `bump_data_version` is called once.

- [x] **T15: Token-leak test.** `apps/churn/tests/test_churn_token_leak.py` (mirroring
      `tests/test_token_leak.py`): after a full `run_churn` against `git_origin` with a real-shaped token stored
      on the connection, assert the token appears in **no recorded `argv`** (a `run_git` spy), in **no remote
      URL** (`git config --get remote.origin.url` and `git remote -v`), in **no `.git/config`** text, in no log
      file, and in no `ChurnResult.error`.

- [x] **T16: Command and schedule.** `apps/churn/management/commands/compute_churn.py` and
      `apps/churn/tasks.py`. New `apps/churn/tests/test_compute_churn_command.py`: `call_command("compute_churn")`
      writes the same rows as `run_churn` and prints the English summary; `--repo` with an unknown name exits
      non-zero; `apps/churn/tests/test_tasks.py` asserts the periodic task is registered on the huey crontab and
      calls `run_churn`.

- [x] **T17: PR detail churn section.** `apps/dashboards/views.py::_pull_request_detail_context` (latest result,
      any status) and `pull_request_detail.html` (five branches, full sentences with named placeholders).
      Tests in `apps/dashboards/tests/test_pull_request_detail.py`: each of the five branches renders its own
      text; `assertNumQueries` for the page does not regress.

- [x] **T18: The heuristic label on screen.** Add `followup_fix_rate` to `PERSON_COMPARISON_METRIC_KEYS`
      (`apps/dashboards/person.py`). Test in `apps/dashboards/tests/` asserting the rendered Person page contains
      the word `heuristic`, and that the comparison-table query count does not regress.

- [x] **T19: Translations.** `make messages`; fill every new uk `msgstr`; commit `locale/uk/LC_MESSAGES/django.po`
      and `.mo`. Covered by `tests/test_translations.py` (no empty/fuzzy, placeholders match) and
      `tests/test_pages_smoke.py`.

- [x] **T20: Docs.** `docs/user/churn.md` (what churn measures, why a rebase merge has no number, what each
      status means, when the nightly job runs); `docs/CONFIGURATION.md` (the three new settings + the two
      existing churn settings + the two follow-up settings); `docs/SETUP.md` (the nightly `compute_churn`
      launchd/cron entry, `DATA_DIR/repos` disk expectations, and that deleting the clones is safe);
      `docs/METRICS.md` regenerated; `CHANGELOG.md` entry; `docs/PROGRESS.md` phase entry. `tests/test_docs.py`
      gates the links.
      **STATUS (session 2 handoff): not started, but partly satisfied already.** `docs/METRICS.md` is
      regenerated and fresh (T5, `manage.py metrics_doc --check` passes) and `docs/CONFIGURATION.md` already
      lists all five churn/follow-up settings (`CHURN_WINDOW_DAYS`, `CHURN_MAX_FILES`, `CHURN_MAX_WORKERS`,
      `CHURN_GIT_TIMEOUT_SECONDS`, `CHURN_REPO_TIME_BUDGET_SECONDS`) plus the two `FOLLOWUP_FIX_*` ones — a
      session-1 addition, verified still accurate. Still missing: `docs/user/churn.md` (new file — what churn
      measures, why a rebase merge has no number, what each of the four statuses means, when the nightly job
      runs), `docs/SETUP.md`'s nightly `compute_churn` schedule/`DATA_DIR/repos` disk-expectations paragraph, a
      `CHANGELOG.md` entry, and a `docs/PROGRESS.md` phase entry. Resume here first in session 3.

- [x] **T21: e2e.** `e2e/plans/churn.plan.yaml` and `e2e/web/test_churn.py` driving the PR detail page for a PR
      with an `ok` result and one with an `unsupported_merge_method` result, seeded by a new
      `_seed_churn()` in `apps/accounts/management/commands/seed_e2e.py` (`ChurnResult` rows written directly —
      the e2e stack runs no git). Register the surface in `e2e/surfaces.toml` if a new one is needed.

---

## Verification

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run mypy \
  && uv run python manage.py makemigrations --check --dry-run \
  && uv run python manage.py check
uv run python manage.py metrics_doc --check
make messages   # then confirm locale/uk/LC_MESSAGES/django.po has no empty or fuzzy msgstr
make e2e-up && uv run pytest e2e -q; make e2e-down
```

The test command is unchanged: `uv run pytest -q`.

| Acceptance criterion | Test that proves it |
|---|---|
| A temporary git repository, churn ratio for a **squash** merge against hand-counted lines | `apps/churn/tests/test_churn_compute.py::test_squash_merge_ratio_matches_hand_counted_lines` (T13, fixture T9) |
| …and for a **merge commit** | `apps/churn/tests/test_churn_compute.py::test_merge_commit_ratio_matches_hand_counted_lines` (T13) |
| A rebase merge ⇒ `status == unsupported_merge_method` and **no ratio** | `apps/churn/tests/test_churn_compute.py::test_rebase_merge_is_unsupported_and_has_no_ratio` (T13) |
| More than `CHURN_MAX_FILES` files ⇒ `status == too_large` | `apps/churn/tests/test_churn_compute.py::test_more_files_than_the_limit_is_too_large` (+ the at-the-limit case) (T13) |
| `lines_at_merge == 0` ⇒ **never a fabricated ratio** (settled as `status=ok, churn_ratio=None` instead of the literal "no row" of spec §9.6, so the PR is not re-cloned/re-blamed forever — deviation logged `p10-review_fix1`, re-verified `p10-e2e`) | `apps/churn/tests/test_churn_compute.py::test_zero_lines_at_merge_is_skipped` + `apps/churn/tests/test_churn_run.py::test_writes_one_row_per_computed_pr_and_a_ratio_less_row_for_zero_lines_at_merge` (T14) |
| A failing `git fetch` deletes and re-clones **once**, then records `status == error` with a reason | `apps/churn/tests/test_clones.py::test_failing_fetch_deletes_and_reclones_once` (T10) + `apps/churn/tests/test_churn_run.py::test_repeated_fetch_failure_records_error_with_reason` (T14) |
| The token appears in no git argv, no remote URL and no `.git/config` after a churn run | `apps/churn/tests/test_churn_token_leak.py` (T15) |
| `ci_first_pass_rate` has a positive and a negative test | `apps/metrics/tests/test_metrics_quality.py::test_ci_first_pass_rate_counts_a_successful_first_ci_commit` / `…::test_ci_first_pass_rate_excludes_a_failed_first_ci_commit_from_the_numerator_only` (T6) |
| `followup_fix_rate` has a positive and a negative test | `apps/activity/tests/test_followup.py` (window/overlap/pattern negatives) + `apps/metrics/tests/test_metrics_quality.py::test_followup_fix_rate_positive_and_negative_row` (T3, T5) |
| The follow-up metric's **UI label reads "heuristic"** | `apps/dashboards/tests/test_person.py::test_person_comparison_table_labels_followup_fix_rate_a_heuristic` (T18); also `docs/METRICS.md` freshness (`tests/test_docs.py`) |
| Bare clones live under `DATA_DIR/repos/<owner>/<name>.git`, fetched before each computation | `apps/churn/tests/test_clones.py::test_clone_path_and_second_call_fetches_instead_of_recloning` (T10) |
| `compute_churn` and the nightly schedule | `apps/churn/tests/test_compute_churn_command.py`, `apps/churn/tests/test_tasks.py` (T16) |
| Churn on the PR detail page | `apps/dashboards/tests/test_pull_request_detail.py` (five status branches) (T17) |
| Churn in the churn KPI | `apps/dashboards/tests/test_charts_build.py` / KPI test updated so `churn_21d` is non-`None` once `ChurnResult` rows exist (T14/T17) |

---

## Risks

| Row | What this plan does |
|---|---|
| **11 — churn is the most fragile feature** | Failure is data: four `ChurnResult` statuses with a reason code, never an exception escaping `run_churn`. One PR's failure does not stop its repository; one repository's failure does not stop the run. `CHURN_MAX_FILES`, `CHURN_MAX_WORKERS`=4 and a per-repository time budget bound the cost. Clones are disposable and re-created on a failed fetch. Every branch of the algorithm is tested against a git repository the test builds itself. |
| **2 — token leak** | The token reaches `git` only through `GIT_ASKPASS` + two env vars read by a 6-line helper; the remote URL is credential-free; `GIT_CONFIG_NOSYSTEM`/`GIT_CONFIG_GLOBAL=/dev/null` keep the operator's credential helper out; `run_git` never interpolates a credential into `args` and masks stderr before it reaches an exception, a log or `ChurnResult.error`. T15 asserts all of it after a real run. |
| **1 — a wrong number about a named person** | `followup_fix_rate` keeps `(heuristic)` in its registry title *and* its description, and gets a visible home where that label renders (T18). `lines_at_merge == 0` produces no row rather than a fake 0 %. `MIN_SAMPLE` greying already applies to both ratios through `compute()`. |
| **14 — SQLite write contention** | Git work is threaded; **every** DB write happens on the main thread after a future resolves. The nightly job runs at 02:00, between the hourly syncs and the 03:00 export cleanup. |
| **10 — dashboard latency** | `followup_fix_rate` is materialised into a boolean at sync time, so its rollup batch stays a grouped `COUNT`, not a `PRFile`×`PRFile` self-join. `churn_21d` continues to read `ChurnResult` rows directly (a distribution, never rolled up — RISKS row 7 unaffected). T17/T18 keep their `assertNumQueries` guards. |
| **8 — translation lag** | Every new string (five churn status sentences, the command summary is English-only by convention, the new setting descriptions, the docs links) is translated in T19, inside this phase. |
| **15 — stale generated artefact** | T5 and T20 regenerate `docs/METRICS.md`; `metrics_doc --check` and `tests/test_docs.py` are in the gate. |
| **9 — no real GitHub credentials** | The `Contents: read` denial path is exercised by a fetch that exits 128 with an auth-shaped stderr, and recorded as `deferred_not_authored` with that reason; `docs/user/churn.md` states the required PAT permission for the operator's first real run. |

---

## Out of scope

- **Phase 11** owns: empty-state text and `MIN_SAMPLE` greying passes across every page, the contrast audit, the
  Ukrainian proofread and long-string layout pass, `seed_demo --scale` (50 repos / 20 000 PRs), the profiling-driven
  index and `select_related` pass, the 1.5 s timed dashboard test, and completing the rest of `docs/`.
- A **UI trigger** for churn (a "compute churn now" button) — the spec puts churn on the CLI and the nightly
  schedule only; the Sync page's button stays sync-only.
- **Rebase-merge churn.** Explicitly unsupported in v1 (spec §9.3); reconstructing the pre-rebase commit set
  would need the PR's head-ref history, which GitHub garbage-collects.
- **A `FollowupFix` link model** exposing *which* PR fixed which. The metric needs a boolean; the link table is a
  later feature if a lead ever asks to click through.
- **Shallow or partial clones**, clone garbage collection, and a disk-usage cap for `DATA_DIR/repos` — documented
  as an operator concern in `docs/SETUP.md` instead.
- **Live GitHub access of any kind**, here as everywhere else in this run.
