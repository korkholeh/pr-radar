# Find and inspect a pull request

This page is for a lead. It covers the pull requests list and a single PR's own detail page.

## The pull requests list (`/prs/`)

Every PR you have access to in the selected period, newest first. Beyond the usual period/cohort filters,
this page adds its own filters: author, state, AI status, tool, size bucket, and whether the PR has an open
policy violation. Combine as many as you like — an unknown or out-of-scope value in a shared link is dropped
silently rather than erroring, so a bookmarked or shared filtered link never breaks.

Search, sort, and export (CSV/XLSX) work the same as any other dashboard table (`docs/user/dashboards.md`),
and the export always carries your current filters — exactly the rows on screen, not just the current page.

## The PR detail page (`/prs/<id>/`)

Click any PR to open it. The header names the repository, the PR number, its title and its **author** — the
author's name links to their person dashboard when they are someone you have access to, and shows the GitHub
login as-is when no person has been mapped to it yet (map them in **Settings → People**). The page then has five
parts:

- **Timeline** — first commit, opened, ready for review, each review submitted, merged/closed, in order.
  An event that never happened (e.g. no "ready for review" on a PR opened straight out of draft) simply isn't
  shown.
- **Metrics** — lead time, time to first review, cycle time, effective size and its bucket, review rounds,
  commits after the first review, and whether the review looks like a rubber stamp or a self-merge. These are
  this PR's own facts, computed directly from its timestamps — not an aggregate, so they always have a value
  once the PR exists.
- **Files** — every changed file with test/excluded/sensitive-path badges, capped at a configurable limit with
  a "+N more" line for a very large PR (see `PR_FILES_DISPLAY_LIMIT` in `docs/CONFIGURATION.md`).
- **Violations** — any open policy violation on this PR, with the same acknowledge/waive action as the Policy
  console (a reason is required), scoped to this PR only.
- **Churn** — how much of this PR's own lines survive `CHURN_WINDOW_DAYS` after merge, filled in by the nightly
  `compute_churn` job; until then, or if the PR isn't eligible, it shows an explicit state, never a `0%`. See
  `docs/user/churn.md`.
- **AI signals** — the resolved AI status, disclosure and tools, and every detected signal with its evidence
  (unchanged from earlier phases).

## What a restricted lead sees

A PR outside your granted projects is a 404 if reached by a direct link, and never appears in the list, its
filter choices, or an export.
