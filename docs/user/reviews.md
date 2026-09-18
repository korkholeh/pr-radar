# Read review load and backlog

This page is for a lead. It covers the Reviews page (`/reviews/`) — who is reviewing, who is reviewing whom,
and what's still waiting.

## Reviewer load

A table and matching chart of how many reviews each person gave in the period, most active first. This is a
**workload view, not a ranking** — it tells you who's carrying review load, not who's "best" at it. A
self-review never counts, and a bot account never appears here.

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
