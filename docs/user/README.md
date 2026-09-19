# PR Radar — user guide

PR Radar reads your team's pull requests from GitHub and turns them into two pictures:

- **AI adoption and policy compliance** — which pull requests were written with AI help, with which tool, whether
  the author said so, and which ones break your company's AI policy.
- **Delivery quality and dynamics** — throughput, lead and cycle time, review latency, PR size, rework, reverts,
  CI first-pass rate and how much of a merged PR's code is still there three weeks later.

Both are available for the whole organisation, one project, one repository, or one person, with AI-assisted and
non-AI pull requests side by side.

It is for team leads and engineering managers. The developers whose pull requests are measured **are not users**:
they have no account, see nothing, and are never notified. PR Radar only ever reads from GitHub — it never
comments, labels, approves or changes anything there.

## Install it

PR Radar runs on your own machine. You need Python 3.12 and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env    # edit SECRET_KEY at minimum
uv run python manage.py migrate
uv run python manage.py createsuperuser
```

Then start the two processes, each in its own terminal, from the repository root:

```bash
uv run python manage.py runserver 8000     # the web app
uv run python manage.py run_huey           # the background worker
```

Open <http://127.0.0.1:8000/> and log in. The full installation page — scheduling a nightly sync, backing up your
data, where the logs are — is [`../SETUP.md`](../SETUP.md).

If you just want to see what the dashboards look like before connecting anything real, run
`uv run python manage.py seed_demo` and reload the page.

## Your first hour, in order

Read these in sequence the first time. After that, [Find the right page](index.md) is the faster route.

1. **[Getting started](getting-started.md)** — log in, switch the theme, switch the language, log out.
2. **[Connect GitHub and sync your repositories](connect-github.md)** — add a connection with a personal access
   token, pick the repositories to watch, and run the first sync. Nothing else works until this is done.
3. **[Map people](map-people.md)** — clear the unmapped-identity queue so every pull request is attributed to the
   right person, merge duplicates, and mark the bots. Person-level numbers are only as good as this step.
4. **[Read the dashboards](dashboards.md)** — Overview, Projects and Repositories: how to read a KPI card, what a
   greyed "small sample" number means, how the filter panel works, and how to share a link to exactly what you're
   looking at.
5. **[Tune AI detection](tune-ai-detection.md)** — check a few pull requests whose AI status you can verify by
   hand, and adjust the detection rules before anyone acts on an adoption number.
6. **[Handle policy violations](handle-policy-violations.md)** — set your AI policy, then work the violations
   console: acknowledge, waive with a reason, or fix the underlying PR.

Once that's in place, the day-to-day pages:

- **[Look up a person](people.md)** — one person's pull requests, violations, review load and how they compare
  to their project and the organisation. This is the page you open before a 1:1.
- **[Find and inspect a pull request](pull-requests.md)** — filter every PR in scope, then read one PR's full
  timeline, files and violations.
- **[Read review load and backlog](reviews.md)** — who is carrying the reviews, who reviews whom, and what is
  still waiting.
- **[Churn: how much of a PR's code survives](churn.md)** — what the churn number means, and the four reasons it
  might be missing.
- **[Export data and reports](exports.md)** — any table as CSV or XLSX, or the seven-sheet dashboard report.

When something looks empty, stale or wrong, go to **[Troubleshooting](troubleshooting.md)** first — an empty
dashboard, a greyed number, a stale "Data as of" banner, a failed sync and a missing churn number all have
ordinary explanations.

## Reference, not instructions

These describe *what a thing is*, rather than how to use a page. They are written for whoever administers the
tool.

| | |
|---|---|
| [`../METRICS.md`](../METRICS.md) | Every metric, its formula, unit and direction |
| [`../POLICY.md`](../POLICY.md) | Every policy rule, when it fires, and its severity |
| [`../CONFIGURATION.md`](../CONFIGURATION.md) | Every `.env` key and every in-app setting |
| [`../GITHUB_CONNECTIONS.md`](../GITHUB_CONNECTIONS.md) | Token types and scopes, the first-run checklist, key rotation |
| [`../SETUP.md`](../SETUP.md) | Install, run, schedule, back up, restore |

## Two things to know before you show a number to anyone

**A small sample is not a measurement.** Any metric computed from fewer than five pull requests is greyed and
labelled. It is shown so you can see it exists, not so you can act on it.

**A missing number is not zero.** An em dash means PR Radar has nothing to compute from — an unsynced period, a
metric that needs data this repository doesn't produce, or a churn measurement that could not be made. It never
substitutes a zero to fill the gap.
