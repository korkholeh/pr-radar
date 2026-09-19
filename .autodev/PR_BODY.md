# PR Radar — the whole product, in one branch

A lead can now connect a GitHub organisation, sync its repositories into a local SQLite database, and read
dashboards on AI adoption, AI-policy compliance and delivery quality at the global, project, repository and
person level — in English or Ukrainian, light or dark, with every table exportable and every number that rests
on too few pull requests visibly marked as such.

![Overview KPI grid with small-sample labels](.autodev/phases/11-polish-and-performance/screenshots/02-small-sample.png)

*Overview at the default 30-day period. Most cards are dimmed and labelled "Small sample" — fewer than five PRs
behind the number. "Open violations" and "Open PRs" clear the threshold and render at full strength.*

![PR detail page showing a churn percentage sentence](.autodev/phases/10-ci-and-churn/screenshots/02-pr-detail-churn-ok.png)

*A merged PR's churn: "40.0% churn — 6 of 10 lines still present after 21 days." A full sentence with named
placeholders, so it translates; never a bare percentage, and never a fabricated `0%`.*

![People table in Ukrainian with wrapped column headers](.autodev/phases/11-polish-and-performance/screenshots/04-ukrainian-layout.png)

*The People table in Ukrainian. Long headers wrap rather than clip. The `(Δ)` delta columns are still English —
a known, unclosed gap.*

The rest of the gallery is [`.autodev/SCREENS.md`](.autodev/SCREENS.md); the state of the repository is
[`.autodev/HANDOFF.md`](.autodev/HANDOFF.md).

## Why

The tool exists to prepare 1:1s and retros and to police an AI-usage policy. That makes its output *evidence
about named individuals*, which is why so much of the design is about being honest rather than being clever: a
metric below `MIN_SAMPLE` is greyed and labelled instead of shown plainly, a missing value is an em dash and
never a zero, an unmapped identity is a visible queue item instead of a silent drop, and auto-resolve never
overwrites a lead's own judgement about a violation.

## Worth a close look

**The credential path.** Tokens are `MultiFernet` ciphertext in `GitHubConnection.token_encrypted`; plaintext
exists only in a local variable. `config/security.py::mask_secrets()` is installed on the root logger and runs
before anything reaches `SyncRun.error_log`. `git` gets the token through `GIT_ASKPASS` plus an environment
variable — never a remote URL, `.git/config`, a credential helper or `argv`. `tests/test_token_leak.py` greps
the on-disk database, the log file, `error_log`, rendered HTML and export files after a *failing* sync. Worth
reading because the failure here is not undoable by us.

**The authorization path.** `accounts.selectors.scope_for_user()` is the only gate, and every selector takes the
`ScopeFilter` it returns rather than starting from `objects.all()`. Isolation is asserted separately on pages,
chart JSON, CSV and XLSX, because a view permission is not an export permission. Two consequences to agree with
or overrule: an out-of-scope id in a query string is dropped **silently** rather than answered `403` (a 403
confirms the id exists), and a restricted `ScopeFilter` **bypasses `DailyRollup` entirely** in both `compute()`
and `compute_many()` — rollups are written under unrestricted access, so reading one back for a narrowed caller
would hand them the global number. The second choice is correct and slow; phase 10's profiling note says so.

**Two deliberate deviations from the spec.** Spec §9.6 reads literally as "write no `ChurnResult` row for a PR
with zero attributable lines"; the shipped behaviour writes a settled row (`status=ok`, `lines_at_merge=0`,
`churn_ratio=None`) because with no row the PR stays eligible forever and is re-cloned and re-blamed nightly —
`churn_ratio=None` still means no fabricated percentage. And `merge_method == "unknown"` is resolved from the
clone (`git rev-list --parents -n 1`, ≥2 parents ⇒ merge commit, else squash) rather than treated as
unsupported, because GitHub omits the field often enough that the strict reading erases most of the metric.

**Migrations.** 28 migrations across ten apps, all forward-only, applied from zero in the gate
(`makemigrations --check --dry-run` is part of the lint command). Nothing drops an operator-owned column. The
data a re-sync cannot recover — projects, people, the identity mapping, policy versions, violation decisions,
notes — only ever gets added to.

**Exports leave the system.** CSV values starting with `= + - @` get an apostrophe prefix and XlsxWriter runs
with `strings_to_formulas=False`; `Person.notes` is excluded by construction and asserted absent; a generated
file is downloadable only by its own author; every export writes an `AuditEntry`.

**What is not finished.** Three things a reviewer should know before merging:

- **No live GitHub call has ever been made by this code.** Every GitHub behaviour is exercised against JSON
  fixtures under `tests/fixtures/github/`, and the suite fails on any unmocked outbound request. The first real
  sync is also the first real schema check. `docs/GITHUB_CONNECTIONS.md` has the first-run checklist.
- **Two admin surfaces from spec §10.3 exist only in the Django admin**: project CRUD with repository
  assignment, and the `AppSetting` thresholds (`STALE_DAYS`, `MIN_SAMPLE`, size buckets, churn window, excluded
  and test path globs). Every other Settings item has its own page.
- **Two live findings from the final review were left unfixed**: the `overflow-x-auto` table wrapper silently
  disables the sticky header (`apps/dashboards/templates/dashboards/partials/table.html:44`), and
  `conftest.py::large_scale_seed` is module-scoped behind a session refcount that cannot work, so the 20,000-PR
  dataset is seeded twice and the suite costs about eight extra minutes.
