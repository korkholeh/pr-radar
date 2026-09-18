# Export data and reports

This page is for a lead. It covers table exports, the multi-sheet report, background exports for large ones,
and "My exports".

## Table export (CSV / XLSX)

Every dashboard table (projects, repositories, people, pull requests, reviewer load) has an **Export** control.
Both formats export every row matching the table's current filters, search and sort — not just the page you're
looking at. See `docs/user/dashboards.md` for the file format details (frozen header, autofilter, real numeric
cells, CSV formula-injection neutralisation).

## The report (XLSX)

The "Download report (XLSX)" button on the Overview, Project, Repository and Person pages produces a single
workbook with the whole page's picture in one file:

| Sheet | Content |
|---|---|
| Summary | every KPI's value, previous value, delta, and AI/non-AI cohort split |
| Trends | one row per interval, plus native Excel charts for throughput, AI adoption and latency |
| Projects / Repositories / People | whichever of the page's own tables apply at that level |
| PRs | every PR in scope for the period |
| Violations | every policy violation in scope for the period |
| Metrics | a reference of every metric used on the page — key, formula, unit, direction |
| Parameters | the filters, generation time, your username, last successful sync, and report settings |

`Person.notes` never appears in this workbook or in any table export — private notes stay private regardless
of who downloads what.

## Large exports run in the background

An export past a configured row cap (`EXPORT_SYNC_MAX_ROWS` in `docs/CONFIGURATION.md`, default 20,000) does
not block your request. Instead it's queued as a job, and you're taken to **My exports** (`/exports/`) to watch
it finish — the page polls itself every few seconds while a job is still running and stops once every job is
done or failed.

## My exports (`/exports/`)

Lists only your own export jobs, newest first, with their status and, once done, a download link. A failed job
shows a reason in your own language. Jobs and their files are kept for a configurable retention window
(`EXPORT_RETENTION_DAYS`, default 7 days) and then deleted automatically; downloading someone else's export —
even if you know its link — is refused.

## Audit trail

Every export that produces a file — CSV, XLSX, report, synchronous or background — is recorded in the audit
trail: who ran it, what kind, the filters applied, and how many rows. The file's content is never logged, only
that it was produced.
