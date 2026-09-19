# Look up a person

This page is for a lead. It covers the People page and an individual person's own page — finding someone,
reading how they compare to their project and the organisation, and keeping a private note about them.

## The People page (`/people/`)

Lists every person you have access to, in name order by default — this is a directory, not a ranking. Search,
sort by any column, and export to CSV/XLSX the same way as any other dashboard table (see `docs/user/
dashboards.md`). Click a name to open that person's own page.

## The Person page (`/people/<id>/`)

The same KPI cards, charts and filters as a project or repository page, plus four sections specific to a
person:

**Comparison.** Each metric in the comparison row shows the person's own value next to their *primary project*
(the project where most of their PRs in the period landed) and the whole organisation you can see, plus the
delta from the previous period. Both baselines are real medians computed over raw rows at that level — never a
median of per-person medians, which would overstate how typical any one person's own number is. A value based
on fewer than 5 data points is shown but greyed ("small sample"); a person with no PRs in the period shows an
em dash, never a zero.

**Pull requests and violations.** The same PR list and violations you'd see filtered to this person elsewhere,
scoped to the period.

**Review load.** How many reviews this person gave in the period, for context — this is workload, not a
performance ranking.

**Private notes.** A free-text note visible only to leads, never exported to CSV/XLSX or the report, and never
shown to the person it's about. Editing it is recorded in the audit trail as a length change only — the note's
own text is never logged.

## What a restricted lead sees

If your account has been granted access to specific projects only (Django admin → Accounts → User project
access), the People page and every person's own page show only people who authored a PR in one of your
projects. A person outside your access is a 404 if you know their direct link; they simply don't appear in the
list or in any filter's choices.
