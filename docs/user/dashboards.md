# Read the dashboards

This page is for a lead. It covers the Overview, Projects, and Repositories dashboards — reading a KPI card,
narrowing what you see, sharing a link to exactly that view, and exporting a table.

## The three pages

**Overview** (`/`) is the global picture. **Projects** and **Repositories** in the top bar list every project and
repository you have access to, each with the same KPI-and-table shape; click one to drill into its own page. A
repository that belongs to two projects contributes its full activity to each project's page, but is counted only
once on Overview — the two numbers are not expected to add up, and that is by design.

## Read a KPI card

Each card shows one metric's value for the selected period, a delta against the immediately preceding period of
the same length, and a small trend line. The delta's arrow (▲/▼/–) tells you the direction of change; colour is
never the only signal, since not every reader can rely on colour. A green delta means the metric moved the way
that's good for that particular metric — for `lead_time_p50`, going *down* is green, while for `prs_merged`, going
*up* is green. A delta smaller than 5% is shown in neutral grey either way — treated as noise, not a trend.

A card showing an em dash (—) instead of a number means there is no data for that period yet, not zero. A card
with a "small sample" badge is based on fewer than 5 data points; the number is shown but greyed, since a rate or
median computed from that few PRs is not reliable enough to act on.

The quality row (rework rate, CI first-pass rate, revert rate, churn) can show AI-authored and non-AI PRs
side by side — switch the filter bar's **Cohort** to "AI vs non-AI" to turn this on for every card on the page.

## Change what you see

The filter bar controls the period (a preset like "Last 30 days" or a custom range), granularity of the charts
(day/week/month, or automatic based on how long the period is), and cohort. On a project or repository page you
can also switch between **Period** mode (the KPIs and charts above) and **Day** mode, which shows exactly what
happened on one calendar day — PRs opened, PRs merged, reviews given — using the day as it falls in Kyiv time, not
UTC.

Every filter you set is written into the page's URL. This means a link you copy from the address bar while
looking at, say, the last 90 days for one repository with the AI cohort selected reopens to exactly that view for
whoever you send it to — no need to explain which filters to set.

## Export a table

Every table (projects, repositories, people, recent PRs) has an **Export** control offering CSV or XLSX. Both
formats export **every row matching the table's current filters, search and sort** — not just the page you're
looking at. The XLSX file has a bold header row, frozen so it stays visible while you scroll, a working
autofilter, real numeric cells for percentages and durations (not text), and a clickable link on every PR number.
A value that would otherwise be mistaken for a spreadsheet formula (starting with `=`, `+`, `-` or `@`) is
neutralised in the CSV so opening it in Excel never runs anything.

A very large export (tens of thousands of rows) may take a few seconds to generate since it is built synchronously.
Past a configured row cap it is instead queued as a background job — see `docs/user/exports.md`.
