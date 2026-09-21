# Read review load and backlog

This page is for a lead. It covers the Reviews page (`/reviews/`) — who is reviewing, who is reviewing whom,
and what's still waiting.

## Reviewer load

A table and matching chart of how much reviewing each person did in the period, most active first. This is a
**workload view, not a ranking** — it tells you who's carrying review load, not who's "best" at it. A
self-review never counts, and a bot account never appears here.

Both carry two numbers per reviewer: **reviews given**, every review they submitted, and **pull requests
reviewed**, the distinct PRs those reviews landed on. Read the gap between them. Equal numbers mean one pass per
PR. 43 reviews across 17 PRs means repeated rounds on the same pull requests — back-and-forth with a handful of
authors, not broad cover — which is a different kind of load than 43 reviews across 43 PRs, and often a sign the
PRs are too big or the feedback is arriving in instalments. Both numbers are in the CSV and XLSX exports, and the
table sorts by either.

## Author × reviewer

A heat map of who reviews whose PRs: darker means more reviews between that author/reviewer pair in the
period. Only the busiest people per axis are shown individually (configurable via `REVIEW_HEATMAP_TOP_N`,
default 15); everyone else folds into an "Other" row and column so the table stays readable regardless of team
size. Every cell shows its number as well as its colour, since colour alone is never the only signal.

## Waiting for review

Open, non-draft PRs that have not received a first review yet, longest-waiting first — this is a live
snapshot ("waiting right now"), not narrowed to the selected period the way the rest of the page is.

## Scope

Like every other page, the Reviews page only shows what your account has access to — global by default, or
narrowed to a project/repository the same way a dashboard page is (`?scope_type=project&scope_id=…`).
