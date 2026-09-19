# Handoff — PR Radar

Branch `autodev/spec-20260917-0714`, 13 commits, 662 files. Written 2026-09-19. Nothing in this session was
committed; the working tree holds the documentation changes described at the bottom.

## What was built

The whole product in `docs/SPEC.md`: a local Django app that pulls pull requests from GitHub's GraphQL API into
SQLite, resolves each commit and review to a `Person`, detects AI assistance and evaluates a nine-rule AI policy,
and serves dashboards on AI adoption and delivery quality at global, project, repository and person level. Every
number goes through one metrics registry (39 metrics), every query through one authorization gate, and every
table and report exports to CSV or XLSX. It ships in English and Ukrainian, light and dark, with an empty-state
explanation on every page and a visible "Small sample" label on any metric computed from fewer than five PRs.

Screenshots: [`SCREENS.md`](SCREENS.md). Reviewer-facing description: [`PR_BODY.md`](PR_BODY.md). How it fits
together: [`../docs/dev/architecture.md`](../docs/dev/architecture.md).

## State

**Verified — run in this session, from this working tree:**

| Check | Result |
|---|---|
| `uv sync` | exit 0 — 55 packages, nothing to change |
| `uv run ruff check .` | `All checks passed!` |
| `uv run ruff format --check .` | `475 files already formatted` |
| `uv run mypy` | `Success: no issues found in 35 source files` |
| `uv run python manage.py makemigrations --check --dry-run` | `No changes detected` |
| `uv run python manage.py check` | `System check identified no issues (0 silenced)` |
| `make e2e-up && uv run pytest e2e -q; make e2e-down` | **63 passed**, exit 0, no stray processes left |
| `make css` | Tailwind v4.3.3, "Done in 45ms" — `static/` byte-identical afterwards |
| `make messages` | exit 0; regenerated `locale/uk/LC_MESSAGES/django.po` (see below) |
| `uv run python manage.py runserver 8099 --noreload` | `GET /` → `302`, the anonymous redirect to login. Stopped again. |
| `uv run pytest -q` | **1,841 tests, no failures**, exit 0 (≈13 min) |

`make messages` produced a 344-line diff in `django.po`, all of it comment-level: a new `POT-Creation-Date` and
updated `#:` source references, plus the same translations re-wrapped onto continuation lines. No `msgid` was
added or removed, no entry became fuzzy or obsolete. The committed file had simply gone stale against line
numbers that moved. It is regenerated in the working tree and
`uv run pytest tests/test_translations.py tests/test_model_i18n.py -q` is green.

The first `uv run pytest -q` of this session failed exactly one case —
`tests/test_docs.py::test_every_docs_internal_link_resolves`, on a link this session had just introduced
(`docs/dev/architecture.md: adr/`; the test resolves every markdown link to a *file* and rejects a directory
target). Fixed by making the ADR directory a plain code span rather than a link, and the full suite re-run clean
afterwards. Worth knowing before you add a link to a directory in any `docs/` page.

**Assumed, not verified here:**

- `make vendor` — re-downloads htmx and Chart.js from unpkg. The outputs are committed and
  `tests/test_vendored_assets.py` passed; running it would only prove the network works.
- Everything about talking to GitHub. See *Known gaps*.

**One caveat about the e2e run.** A `runserver`/`run_huey` pair left over from the previous step was still
holding port 8100, so this session's `make e2e-up` reseeded the e2e database but its own server could not bind;
the 63 cases ran against the older process, which serves identical code (nothing but documentation changed since
commit `c577286`). Those orphaned processes were stopped at the end of this session. Re-run the e2e command on a
clean machine if you want the result without that footnote.

## Decisions a human should confirm

These are the calls a developer might reasonably overrule. The full log is `DECISIONS.md` (531 entries, 464 KB —
`grep -n '^- \['` it, do not read it whole).

- **A PR with zero attributable lines gets a settled `ChurnResult` row** (`status=ok`, `lines_at_merge=0`,
  `churn_ratio=None`), where spec §9.6 reads literally as "write no row". With no row the PR stays eligible
  forever and is re-cloned and re-blamed on every nightly run. `churn_ratio=None` still means no fabricated
  `0%`. Deliberate, twice reviewed. *(`p10`)*
- **`merge_method == "unknown"` is resolved from the clone** — `git rev-list --parents -n 1`, ≥2 parents ⇒ merge
  commit, else squash — rather than treated as an unsupported merge. GitHub omits the field often enough that
  the strict reading would erase most of the churn metric. Overrule this if you would rather under-report than
  risk a wrong classification. *(`p10/plan`)*
- **`followup_fix_rate`'s definition.** Overlap is `|paths(A) ∩ paths(B)| / |paths(A)|` over non-excluded files,
  window half-open `(merged_at, merged_at + FOLLOWUP_FIX_WINDOW_DAYS]`, both PRs merged in the same repository,
  and it reuses `is_hotfix`'s title/branch regex rather than a second pattern. The spec fixes ≥50% and 14 days
  but not the denominator or the boundary. It is labelled "(heuristic)" everywhere it is shown. *(`p10/plan`)*
- **An out-of-scope project or repository id in a query string is dropped silently, not answered `403`.** A 403
  confirms to a restricted lead that the id exists. *(`architect/design`)*
- **A restricted `ScopeFilter` bypasses `DailyRollup` entirely** in `compute()` and `compute_many()`, falling
  back to the calculator. Rollups are written under unrestricted access, so reading one back for a narrowed
  caller returns the global number. Correct, and slower for restricted leads. *(`p07/review_fix1`)*
- **`TOOL_NOT_ALLOWED` fires nothing while `AIPolicy.allowed_tools` is empty.** An empty list is read as "not
  configured yet", not as "no tool is allowed" — otherwise saving the first policy version flags every AI PR in
  the system at once. *(`p06/plan`)*
- **Saving the AI policy creates a new version**; the first-ever version is stamped at the earliest synced PR's
  `created_at` so it governs existing history rather than only future PRs. *(`p06/review_fix2`)*
- **`is_rubber_stamp` uses a strict `<` against 10 minutes**, so an approval at exactly 10 minutes is not a
  rubber stamp — the tie falls on the side that does not accuse anyone. *(`p04/plan`)*
- **`is_hotfix` patterns are module constants in `activity/derive.py`, not an `AppSetting`.** One fewer tunable;
  change it in code if your team's conventions differ. *(`p04/plan`)*
- **Identity resolution never re-points an identity that already has a person.** A re-sync cannot overwrite the
  operator's mapping. *(`p04/plan`)*

## Known gaps

**Spec §10.3 items with no page of their own** — reachable only through the Django admin at `/admin/`:

- Project CRUD and repository assignment (`catalog.Project` is admin-registered; there is no Settings page).
- The `AppSetting` thresholds: `STALE_DAYS`, `MIN_SAMPLE`, size buckets, churn window, excluded and test path
  globs. `docs/CONFIGURATION.md` now says so; it previously promised a settings UI that was never built.

**Left unfixed from the final review** (`phases/11-polish-and-performance/REVIEW-r2.md`, both MINOR, both
confirmed still present in the tree):

- `apps/dashboards/templates/dashboards/partials/table.html:44` — the `overflow-x-auto` wrapper makes that div
  the nearest scrollport, so the `sticky top-0` on the header row at line 47 no longer sticks. Nothing tests it.
  Either drop the now-inert `sticky`, or give the wrapper a max height plus `overflow-y: auto`.
- `conftest.py:82` — `large_scale_seed` is module-scoped behind a session refcount that cannot work (module
  fixture lifetimes never overlap), so the 20,000-PR dataset is seeded twice. This is why `uv run pytest -q`
  takes roughly 13 minutes for 1,841 tests, about 8 of them in the two seeds. Make it session-scoped with a `pytest_sessionfinish` teardown.
  Note that `-m 'not slow'` is only half a workaround: the `slow` marker is on `tests/test_performance.py`
  alone, and the five tests in `apps/dashboards/tests/test_seed_demo_scale.py` consume the same fixture
  unmarked, so one full seed still runs (1,840 of 1,841 tests are selected).

**Translation:** the `(Δ)` delta column headers on metric tables are still English ("PRs merged (Δ)", "AI PR
share (Δ)"). Visible in [`SCREENS.md`](SCREENS.md), phase 11 frame 04. Everything else is translated; the
`.po` gate only catches missing `msgstr`s, not a string that was never marked for translation.

**Bookkeeping:** `phases/11-polish-and-performance/PLAN.md` leaves T16–T21 unticked and its `TEST_OUTPUT.txt`
records no e2e run, though all six deliverables exist in the tree and pass. `docs/PROGRESS.md` documents phases
1–3, 10 and 11 only — phases 4–9 were never written up there.

**Uncommitted and deliberately held back:** `phases/11-polish-and-performance/REVIEW-r1.md` is the one file in
the working tree that predates this session. The orchestrator excluded it from commit `c577286` because it
*quotes* a token-shaped literal while describing the finding that got rid of it. That finding was fixed: the
fake tokens are now built at runtime (`"ghp_" + _fake_token_body(36)` in `apps/connections/tests/test_crypto.py`
and `tests/test_logging.py`), and both test modules — held back by the same guard through phases 1–10 — were
committed in `c577286`. Nothing here is a real credential. Commit the review file or gitignore it; leaving it
untracked is the only state that loses it.

**`deferred_not_authored` e2e cases** — every case a phase chose not to drive in a browser, with the test that
covers it instead:

| Plan | Case | Covered instead by |
|---|---|---|
| `churn` | `too_large` and `error` status sentences | `apps/dashboards/tests/test_pull_request_detail.py` |
| `churn` | "Churn not computed yet" empty state | same file's no-result case |
| `churn` | A real `compute_churn` run populating the page | `apps/churn/tests/test_churn_compute.py`, `test_churn_run.py` (real temporary git repo) |
| `connections` | A live sync run | `apps/github_sync/tests/test_sync.py` — intake forbids any live GitHub call |
| `people` | Derived PR fields on a dashboard | `apps/activity/tests/test_derive.py` (one test per field) |
| `people` | Identity resolution from a sync | `apps/catalog/tests/test_identity.py`, `apps/github_sync/tests/test_pipeline.py` |
| `people` | Double-submitted assign | Not reachable — the row is swapped out on first success |
| `ai_detection` | Re-run detection creates no duplicate signal | `apps/ai_detection/tests/test_services.py` |
| `ai_detection` | Deactivating a rule removes its signals on the next run | same file |
| `ai_detection` | All eight detectors, matching and non-matching | `apps/ai_detection/tests/test_detectors.py` (16 cases) |
| `ai_detection` | Disclosure parser edge cases | `apps/ai_detection/tests/test_disclosure.py` |
| `ai_detection` | All five `ai_status` outcomes | `apps/ai_detection/tests/test_ai_status.py` |
| `ai_detection` | `recompute` / `seed_detection_rules` commands | their own command tests |
| `policy_console` | Nine rules × positive/negative/idempotency/auto-resolve | `test_rules_*.py`, `test_evaluate.py` |
| `policy_console` | PR predating `effective_from`; resolved violation re-judged; project/repo, date and pagination filters | `test_evaluate.py`, `test_status_change.py`, `test_selectors.py` — the e2e seed has one repo, one project, six violations |
| `ai_policy_settings` | Duplicate `effective_from` rejected | `apps/policy/tests/test_forms.py` — the form has no `effective_from` field |
| `sensitive_paths_settings` | Audit entries for create/edit/toggle | `apps/policy/tests/test_views_sensitive_paths.py` |
| `auth` | Password-reset page not admin-branded | Not re-verified in a browser: the running surface had the pre-fix template cached by `cached.Loader`. **Add this case on the next surface restart.** |
| several | a11y and dark-mode sweeps, per page | Swept centrally by `tests/test_token_contrast.py`, `test_status_not_color_only.py`, `test_pages_smoke.py` |

**Needs credentials or a product answer:**

- A GitHub personal access token. Nothing in this repository has ever called GitHub.
- Whether the two admin surfaces above need their own pages, or the Django admin is enough.
- Whether the `followup_fix_rate` and churn definitions above match what the team means by those words.

## Open risks

From [`RISKS.md`](RISKS.md), the rows still live:

- **Row 9 — no GitHub credentials exist (the big one).** Sync, connection verification, rate limiting and
  discovery are validated against JSON fixtures only. A real schema or header mismatch is undiscovered. The
  client raises a named `GitHubSchemaError` with the JSON path rather than failing obscurely, and
  `docs/GITHUB_CONNECTIONS.md` has a first-run checklist so the first live sync is deliberate and observable.
- **Row 1 — the tool measures people.** All the mitigations shipped (`MIN_SAMPLE` greying, the unmapped-identity
  queue, bot exclusion, the "(heuristic)" label, name-ordered people table), but the risk is the product's, not
  the code's. The first real dataset is the first real test of attribution.
- **Row 5 — AI detection is wrong in both directions.** Regex heuristics over text that tool vendors change.
  Rules are editable `DetectionRule` rows with a dry-run button, and `AISignal.evidence` makes any claim
  inspectable — but nothing has been calibrated against real PRs yet.
- **Row 11 — churn is the most fragile feature.** It shells out to `git`, needs bare clones and disk, depends on
  `Contents: read`, and is meaningless for rebase merges. Tested against a temporary git repository built inside
  the test; never against a real remote.
- **Row 8 — Ukrainian parity**, reduced to the `(Δ)` column headers above.
- **Row 14 — SQLite write contention.** WAL, `busy_timeout=5000`, a separate queue file and a single-threaded
  worker; never exercised under real concurrent load.
- **Row 2 (token leak) and row 3 (cross-project leak)** are mitigated and tested on every exit, but they are the
  two failures that cannot be undone — re-read those tests before changing either path.

Closed: rows 4 (time ceiling — all eleven phases landed), 10 (dashboard latency — measured at 1.3–1.5 s on
50 repos / 20,000 PRs), 12 (theme contrast — `tests/test_token_contrast.py`), 15 (stale generated artefacts —
freshness tests), 6, 7, 13.

## Next steps

1. **Read the two unfixed review findings above and fix the fixture one first.** Making `large_scale_seed`
   session-scoped cuts roughly eight minutes off every full test run, which changes how often you run it.
2. **Get a token and do the first real sync**, following `docs/GITHUB_CONNECTIONS.md`. Start with one small
   repository and `BACKFILL_DAYS` low. This is the single largest unknown in the project; everything else is
   tested, and this is not.
3. **Work the unmapped-identity queue** on that first dataset before looking at any person-level number.
4. **Calibrate AI detection** against twenty PRs whose AI use you can verify by hand, and adjust the
   `DetectionRule` rows. Then, and only then, show anyone an adoption number.
5. **Run `compute_churn` against one real repository** and check the `ChurnResult.status` distribution. This is
   the feature most likely to behave differently outside a test.
6. Fix the sticky-header regression and the untranslated `(Δ)` headers.
7. Decide whether project CRUD and the `AppSetting` thresholds deserve their own pages.

## What this session changed (uncommitted)

- **New:** `docs/dev/architecture.md`, `docs/user/README.md`, `.autodev/SCREENS.md`, `.autodev/HANDOFF.md`,
  `.autodev/PR_BODY.md`.
- **Rewritten:** `README.md` (runnable quickstart plus a where-to-read-next table), `CHANGELOG.md`
  (`Unreleased` → `0.1.0 — 2026-09-19`, grouped by impact, with a "what you must do to run it" section).
- **Corrected:** `CLAUDE.md` (dropped Alpine from the stack — it is not used anywhere; pointed the Docs section
  at `docs/dev/architecture.md`), `docs/CONFIGURATION.md` (app settings are edited in the Django admin, not in a
  settings UI that was never built), `docs/SETUP.md` (the admin-only pages exist; "this phase" → "this
  version"), `docs/user/getting-started.md` (a garbled opening sentence), `docs/user/index.md` (points first
  readers at the new guide index).
- **Regenerated:** `locale/uk/LC_MESSAGES/django.po` by running `make messages` — comment-level only, see *State*.
- **Appended:** `.autodev/DECISIONS.md`, one `finalize` section.
- No application code was changed.
