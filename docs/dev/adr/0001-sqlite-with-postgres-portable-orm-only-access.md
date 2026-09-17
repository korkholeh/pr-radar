# 0001. Store everything in a single SQLite database, accessed only through the Django ORM

- **Status:** accepted
- **Date:** 2026-09-17

## Context

PR Radar v1 runs on one lead's laptop (spec §1: "v1 працює локально"). It has one operator, at most a handful of
lead accounts, and the profiling target is ~50 repositories / ~20 000 pull requests (§14 phase 10). Two processes
write to it: the web process and the huey worker.

Spec §2 names SQLite with WAL and `busy_timeout`, and §13 requires that a later move to PostgreSQL be a change of
`DATABASE_URL` and nothing else. Those two requirements together are the whole decision: the engine is easy to
change, but *depending on the engine* is not.

## Decision

One SQLite file at `DATA_DIR/db.sqlite3`, opened with `journal_mode=WAL`, `busy_timeout=5000`,
`foreign_keys=ON`, `synchronous=NORMAL`, applied via a `connection_created` signal handler in `config`.

The queue gets its **own** file, `DATA_DIR/huey.sqlite3` (see ADR 0005), so worker bookkeeping never contends with
a dashboard read.

Portability is enforced, not hoped for:

- **No raw SQL with SQLite dialect.** No `RunSQL` in a migration that is not database-agnostic, no
  `connection.vendor` branches in application code, no `extra()`.
- Database URL and data directory come from the environment (`DATABASE_URL`, `DATA_DIR`) through `django-environ`.
- Aggregation that the ORM cannot express portably is done in Python over a queryset — medians via `statistics`,
  as spec §8.3 already requires.
- JSON fields use `models.JSONField`, which is portable.
- Uniqueness and ordering constraints are declared as `UniqueConstraint`/`Index` on the model, not as engine DDL.

Reads of any size are bounded by `select_related`/`prefetch_related`, and list views carry `assertNumQueries`
tests so an N+1 fails the build rather than the laptop.

## Alternatives considered

- **PostgreSQL from day one** — rejected: it makes the operator install and run a database server for a
  single-user desktop tool, in exchange for concurrency the stated volume does not need.
- **SQLite plus hand-written SQL for the metric aggregations** — rejected: it would be faster for percentiles, and
  it would silently make §13 impossible. The spec forbids it for exactly that reason.
- **DuckDB for the analytical side, SQLite for the operational side** — rejected: two engines, two migration
  stories and a synchronisation problem, to speed up a query budget of 1.5 s on 20 000 rows.
- **Per-connection or per-organisation database files** — rejected: cross-organisation dashboards are the product.

## Consequences

- **Buys:** zero-install operation, a backup that is a directory copy, fast tests, and a genuine one-line path to
  PostgreSQL.
- **Costs:** one writer at a time. Sync, `recompute` and a background export serialise against each other and
  against dashboard writes. At the stated volume this is acceptable; it is why the sync lock (ADR 0003) and the
  single-worker model (ADR 0005) exist rather than being limitations we tripped over.
- **Harder:** no `DISTINCT ON`, no window-function-heavy metric SQL, no partial indexes we can rely on across
  engines. Metric calculators must stay ORM-and-Python.
- **Revisit when:** the database passes ~2 GB, a sync routinely blocks dashboards, or the tool is put on a server
  for more than a handful of users. The move is `DATABASE_URL` plus a load test; the constraint above is what
  keeps it that cheap.
