# Troubleshooting

This page is for a lead. It covers the handful of things that look like a bug the first time you see them but
are PR Radar telling you something specific — read the sentence on screen first, it is usually the answer.

## A dashboard page looks empty

Two different sentences mean two different things, and PR Radar always shows the specific one rather than a
generic "no data":

- **"No data yet." / "Nothing has been synced from GitHub yet. Run a sync to see data here."** — no pull request
  has ever been synced into this scope at all. Follow the **Go to Sync** link, or see
  [Connect GitHub and sync your repositories](connect-github.md) if you haven't connected a repository yet.
- **"No pull requests in this period." / "No pull requests match this period and filter. Widen the period or
  clear the filters."** — data exists, just not inside the period and filters you currently have set. Widen the
  period (the filter bar's preset) or clear a cohort/PR filter before assuming something is broken.

If you have access to only some projects (Django admin → Accounts → User project access), a project you don't
have access to always looks empty to you, not forbidden — that is by design, not a bug.

## A number is greyed with `≈` ("small sample")

`≈` next to a KPI, table cell, or comparison row means the value is real but computed from fewer than `MIN_SAMPLE`
(default 5) pull requests — too few for a rate or median to be reliable. It is not zero and not missing; hover or
check the "Small sample" wording for the exact count where the page shows one. See `docs/POLICY.md`'s "Reading a
greyed compliance number" for how to read one on the Policy console specifically. Widening the period, or looking
at a parent scope (project instead of repository, or Overview instead of a project), is usually how a number
stops being greyed — the population growing large enough, not a setting an admin needs to change.

## The "Data as of" banner looks stale

Every dashboard page shows **"Data as of *(timestamp)*"** (or **"Never synced yet."** if nothing has run) in
Kyiv time — the moment the last sync finished, not the current time. If it looks old:

1. Check **Sync** (`/sync/`) for the most recent run's status and whether the background worker (`run_huey`) is
   actually running — a queued "Sync now" does nothing without it. See `docs/SETUP.md`'s "Run" section.
2. A `partial` or `failed` run still updates "Data as of" for whatever repositories it did finish; check the run's
   own detail for which repository stopped it, and re-run **Sync now** once that's addressed. A `partial` status
   from a single connection's rate limit or a transient GitHub error usually clears on the next scheduled run
   without any action.
3. If sync is scheduled (launchd/cron, see `docs/SETUP.md`'s "Scheduling") but hasn't run recently, check the
   scheduler is actually installed and check its own log location, not just PR Radar's.

## A sync run failed or is stuck

Look at the run's status on the Sync page: `success` (every repository synced cleanly), `partial` (some
repositories synced, others didn't — see which one and why in that run's own detail), or `failed` (nothing this
run touched went through). The run's error detail never contains a token — see `docs/GITHUB_CONNECTIONS.md`'s
"Verification codes" table for what a specific check code means and what to do about it (an expired token, a
missing scope, an SSO authorization requirement are the most common causes of a sync stalling on one connection).
A sync that looks stuck rather than failed is most often waiting out a rate limit — see "Rate-limit budget" in
`docs/GITHUB_CONNECTIONS.md`; a large first sync pausing for part of an hour is expected, not stuck.

## A PR's churn number is missing

The Churn section on a PR's detail page explains itself — it is never a blank space and never a fake `0%`. The
most common reason it's still showing "Not computed yet" is simply that `CHURN_WINDOW_DAYS` (default 21) hasn't
elapsed since the PR merged, or the nightly job (02:00 server time) hasn't reached it yet. See
[Churn: how much of a PR's code survives](churn.md) for the full list of the other five states (no measurable
lines, a rebase merge, too many files, a retried error) and what each one means.
