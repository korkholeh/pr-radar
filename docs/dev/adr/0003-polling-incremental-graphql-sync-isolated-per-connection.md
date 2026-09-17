# 0003. Ingest GitHub by polling GraphQL incrementally, with a per-connection rate budget and a repository-level watermark

- **Status:** accepted
- **Date:** 2026-09-17

## Context

Spec §5 fixes most of this: GraphQL v4 paged by `updatedAt DESC`, stopping at
`last_synced_at - SYNC_OVERLAP` (1 h) or `sync_since`; idempotent upserts in one transaction per PR;
`last_synced_at` advanced only after a repository finishes; `rateLimit` requested in every query with a pause at
`remaining < 200`; a DB lock so only one sync runs; and — the part that shapes the code — **rate limiting tracked
separately per connection**, so a connection waiting on its reset does not stall repositories reached through a
different token.

Webhooks are out of scope (§1), which is right for a tool with no public URL.

The reason this deserves an ADR is not the protocol choice, it is the **failure semantics**: what a crashed,
retried or half-finished run leaves behind. That is expensive to change once nine phases of code assume it.

## Decision

**Poll, never receive.** `manage.py sync` and the UI button (via huey) are the only entry points. There is no
inbound endpoint.

**Watermark per repository, overlap by time.** A repository is synced from `max(last_synced_at - SYNC_OVERLAP,
sync_since)`. `Repository.last_synced_at` is written **only after the repository completes successfully**. A crash
therefore re-reads a repository from its previous watermark; the one-hour overlap covers the boundary. Re-reading
is free because every write is an upsert.

**Idempotency is a key, not a convention.** `update_or_create` on `github_id` for GitHub-owned entities and on
`(repository, sha)` for commits. `AISignal` is unique per `(pull_request, rule, evidence_hash)`;
`PolicyViolation` per `(pull_request, rule_code, details_hash)`. A second identical sync creates zero rows — a
test asserts exactly that.

**Transaction boundary is one pull request.** All of a PR's rows commit together; post-processing
(identity → derived fields → AI detection → policy → dirty-day marking) runs on commit. Failure of one PR is
recorded in `SyncRun.error_log` and the repository continues; failure of one repository does not end the run.

**Rate budget is per `GitHubConnection`.** The client holds a budget object keyed by `GitHubAuth.rate_limit_key`,
updated from the `rateLimit` block of every GraphQL response. Below 200 remaining points the budget sleeps until
`resetAt` and records it on the `SyncRun`; repositories belonging to other connections continue in the meantime.
Retries use exponential backoff with jitter, honouring `Retry-After`, on 502/503 and secondary rate limits.

**Credential failures quarantine a connection, not the run.** A 401 sets the connection to `invalid` and skips its
repositories for the remainder of the run; a 403 carrying `X-GitHub-SSO` sets `degraded` and surfaces the
authorization link. Re-verification is throttled to once per hour per connection so a dead token cannot become a
request storm.

**Nested pagination is mandatory.** Reviews, review threads, commits and files are paginated past 100 items. A
nested page that cannot be completed fails that PR loudly rather than storing a truncated list — silently short
data would corrupt a metric, which is worse than a gap.

**One run at a time**, enforced by a lock row in the database; a second invocation exits with a message.

## Alternatives considered

- **Webhooks** — rejected: out of scope, and a laptop has no public URL.
- **REST-only ingestion** — rejected: the PR detail, timeline, reviews, commits, checks and files needed per PR
  would be six-plus round trips each. GraphQL fetches them in one query with an explicit cost. REST is kept only
  where GraphQL has no equivalent.
- **A global rate-limit budget shared by all connections** — rejected: it is the failure the spec calls out. One
  client organisation's exhausted token would freeze every other organisation's sync.
- **Advancing `last_synced_at` per page, to resume mid-repository** — rejected: it trades a cheap re-read for the
  possibility of a permanent gap if a page fails after the watermark moved. Re-reading is the safe direction.
- **A cursor-based resume token per repository (`sync_cursor`)** — kept as a field for future use but not relied
  on for correctness in v1; the time watermark plus overlap is simpler and self-healing.
- **Deleting and re-inserting a PR's children on each sync instead of upserting** — rejected: it would churn ids
  that `AISignal`, `PolicyViolation` and `ChurnResult` point at.

## Consequences

- **Buys:** a sync that can be killed at any moment and re-run with no reconciliation step, and a multi-tenant
  credential story where a broken client token is a contained incident.
- **Costs:** up to one hour of overlap is re-fetched per repository per run, and a repository that fails late
  re-reads everything next time. Both are paid in API points, which the budget already manages.
- **Harder:** every new entity added to ingestion must bring its own natural key, or idempotency quietly breaks.
  The "re-sync creates zero rows" test is the guard.
- **Revisit when:** a single repository's incremental window regularly exceeds the rate budget (then: per-page
  resume), or GitHub App installations replace PATs (then: the budget key changes, nothing else does).
