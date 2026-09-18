# Churn: how much of a PR's code survives

Churn measures how much of a pull request's changed lines are still present in the codebase some time after
merge — a proxy for "was this actually done, or did it need reworking." It backs the `churn_21d` KPI (Delivery
quality row) and the churn/rework chart, and it shows up per PR on the PR detail page.

## What it measures

For a merged PR, PR Radar counts every line the PR's own commits added (`lines at merge`) and how many of those
same lines are still present, unchanged, `CHURN_WINDOW_DAYS` (default 21) days later (`lines surviving`, tracked
through renames). The churn ratio is `1 − lines surviving / lines at merge`: `0%` means nothing was touched again,
higher means more of the PR's own work was later rewritten or reverted elsewhere.

This is computed by cloning each repository locally and running `git blame`/`git log` against it — the only part
of PR Radar that touches `git` directly rather than the GitHub API. It runs as a nightly background job
(`compute_churn`, 02:00 server time), not on demand, because it is comparatively expensive: cloning, fetching and
blaming large repositories takes real wall-clock time. A PR's churn number typically appears the day after
`CHURN_WINDOW_DAYS` has elapsed since it merged.

## What you see on the PR detail page

The Churn section on a PR's detail page (`/prs/<id>/`) shows one of six states:

- **Not computed yet** — the window hasn't elapsed, or the nightly job hasn't reached this PR yet.
- **A percentage** — e.g. "40% churn — 6 of 10 lines still present after 21 days."
- **"This PR had no measurable lines at merge; churn was not computed."** — the PR added no lines PR Radar
  could attribute to it (an empty diff, or every changed file excluded — see `docs/CONFIGURATION.md`'s
  excluded-path settings). A result is still recorded, with no ratio, so the PR is not re-cloned and
  re-blamed on every future nightly run; there is no meaningful churn ratio for zero lines, so it is never
  shown as a misleading `0%`.
- **"Churn is not measured for rebase merges."** — PR Radar can't reconstruct a rebased PR's original commit
  set after GitHub garbage-collects the pre-rebase history (see "What isn't measured" below).
- **"This PR changes more than N files; churn was not measured."** — the PR exceeds `CHURN_MAX_FILES` (default
  50); analysing it would be disproportionately slow for one PR.
- **"Churn could not be measured."** plus a reason — a git or connectivity problem while computing this PR's
  churn (e.g. a failed clone, a blame timeout, a missing snapshot). This is retried automatically on the next
  nightly run; it is never shown as `0%`.

## What isn't measured

- **Rebase merges.** A rebase rewrites commit SHAs, and GitHub does not keep the pre-rebase history around long
  enough to reconstruct the original PR's line set. Squash and merge-commit PRs are both fully supported.
- **Which PR fixed which.** The related `followup_fix_rate` metric (Person page comparison table, labelled
  "(heuristic)") flags that a PR was *likely* followed by a fix, using title/branch pattern and file overlap —
  it does not link to the specific fix, only counts that one existed.

## Running it manually

```bash
uv run python manage.py compute_churn                      # everything eligible
uv run python manage.py compute_churn --repo owner/name     # one repository
uv run python manage.py compute_churn --project my-project  # one project
uv run python manage.py compute_churn --window 30 --limit 100
```

The command prints a one-line summary (`computed=… skipped=… too_large=… unsupported=… errors=…`) and writes the
same rows the nightly job would. Running it twice is safe — a PR that already has a settled result (`ok`,
`too_large`, or `unsupported_merge_method`) is not recomputed; a PR left in `error` status is retried.

See `docs/CONFIGURATION.md` for `CHURN_WINDOW_DAYS`, `CHURN_MAX_FILES`, `CHURN_MAX_WORKERS`,
`CHURN_GIT_TIMEOUT_SECONDS`, and `CHURN_REPO_TIME_BUDGET_SECONDS`. See `docs/SETUP.md` for the nightly schedule
and the disk space `DATA_DIR/repos/` needs.
