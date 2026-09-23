# Read the dashboards

This page is for a lead. It covers the Overview, Projects, and Repositories dashboards — reading a KPI card,
narrowing what you see, sharing a link to exactly that view, and exporting a table.

## The three pages

**Overview** (`/`) is the global picture. **Projects** and **Repositories** in the top bar list every project and
repository you have access to, each with the same KPI-and-table shape; click one to drill into its own page. A
repository that belongs to two projects contributes its full activity to each project's page, but is counted only
once on Overview — the two numbers are not expected to add up, and that is by design.

## Create or edit a project

A project is a name over a set of repositories; every project dashboard, filter and export follows from that set.
Admins (anyone who may manage settings) get two controls on the **Projects** page:

- **New project** beside the page title.
- **Edit** at the end of each row in the projects table.

The form asks for a name, a slug, an optional description, the repositories the project reports on, and an active
flag. The slug is what `manage.py sync --project <slug>` and
`recompute --project <slug>` are typed with; leave it empty and it is derived from the name, unless the name has
no Latin letters to derive it from, in which case type one yourself. Unticking **Is active** hides the project
from dashboards and filters without deleting anything — its repositories and their pull requests are untouched.
Archived repositories are offered in the form too, so editing a project never silently unlinks one.

Every create and edit is written to the audit log with the repository set before and after it.

## Read a KPI card

Each card shows one metric's value for the selected period, a delta against the immediately preceding period of
the same length, and a small trend line. The delta's arrow (▲/▼/–) tells you the direction of change; colour is
never the only signal, since not every reader can rely on colour. A green delta means the metric moved the way
that's good for that particular metric — for `lead_time_p50`, going *down* is green, while for `prs_merged`, going
*up* is green. A delta smaller than 5% is shown in neutral grey either way — treated as noise, not a trend.

Next to the card's title is an **info icon**. Hover it, or tab to it, and it opens that metric's definition —
the same sentence `docs/METRICS.md` carries, so the card and the reference can never disagree — with a line
saying which direction is the good one for that metric. The bubble opens where there is room: below the icon
normally, above it near the bottom of the window, and never past the edge of the screen.

A card showing an em dash (—) instead of a number means there is no data for that period yet, not zero. A card
with a "small sample" badge is based on fewer than 5 data points; the number is shown but greyed, since a rate or
median computed from that few PRs is not reliable enough to act on.

The quality row (rework rate, CI first-pass rate, revert rate, churn) can show AI-authored and non-AI PRs
side by side — switch the filter panel's **Cohort** to "AI vs non-AI" to turn this on for every card on the page.

The last row is about compliance: the share of merges approved only by a bot, the share that went in over a red
check rollup, how much of the work an AI reviewer looked at, and how much AI work lands on high-risk paths (with
the structural-signal rate under it). These read the pull requests themselves, not your policy settings, so they
are worth watching *before* you switch a check on — [Set your AI policy](ai-policy.md) explains what each one
enforces. AI review coverage stays an em dash until you tell PR Radar which login your AI reviewer posts as.

## Read a chart

Every chart card carries the same **info icon** next to its title. It opens a paragraph saying what the chart
measures, how the number is computed and how to read it: which population the bars or lines are drawn from,
whether the series stack, what the buckets on the x axis are, and the caveat worth
knowing — that a percentile describes its own bucket only, or that churn stays empty until `compute_churn` has
run. The text is the same explanation in English and Ukrainian, and it is the fastest way to settle "what exactly
is this counting?" without leaving the page.

Below each chart, screen readers get the same numbers as a data table, so nothing on a chart is available only as
a picture.

## Change what you see

The filters live behind the **Filters** button above the page; the button opens a panel on the right, and the
badges beside it say what is currently applied. The panel controls the period (a preset like "Last 30 days" or a
custom range), granularity of the charts
(day/week/month, or automatic based on how long the period is), and cohort. On a project or repository page you
can also switch between **Period** mode (the KPIs and charts above) and **Day** mode, which shows exactly what
happened on one calendar day — PRs opened, PRs merged, reviews given — using the day as it falls in Kyiv time, not
UTC.

Every filter you set is written into the page's URL. This means a link you copy from the address bar while
looking at, say, the last 90 days for one repository with the AI cohort selected reopens to exactly that view for
whoever you send it to — no need to explain which filters to set.

**Which pull requests are "AI".** Under the filters, the collapsed **How a pull request gets into the AI cohort**
block explains it on the page itself: each pull request gets one AI status — explicit (a high-confidence rule
matched something the tool wrote, such as a co-author trailer), disclosed (the PR template's "AI assistance" box),
suspected (only weaker signals), no AI or unknown — and the AI cohort is the explicit, disclosed and suspected ones
(turning `AI_COHORT_INCLUDE_SUSPECTED` off leaves suspected out). The block reads the live settings and lists the
tools the active rules recognise; an admin also gets links to the rules. How to tune them is in [tune-ai-
detection.md](tune-ai-detection.md).

## Export a table

Every table (projects, repositories, people, recent PRs) has an **Export** control offering CSV or XLSX. Both
formats export **every row matching the table's current filters, search and sort** — not just the page you're
looking at. The XLSX file has a bold header row, frozen so it stays visible while you scroll, a working
autofilter, real numeric cells for percentages and durations (not text), and a clickable link on every PR number.
A value that would otherwise be mistaken for a spreadsheet formula (starting with `=`, `+`, `-` or `@`) is
neutralised in the CSV so opening it in Excel never runs anything.

A very large export (tens of thousands of rows) may take a few seconds to generate since it is built synchronously.
Past a configured row cap it is instead queued as a background job — see `docs/user/exports.md`.
