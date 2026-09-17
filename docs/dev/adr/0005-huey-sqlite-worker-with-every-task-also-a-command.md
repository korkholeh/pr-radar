# 0005. Run background work in a single huey/SQLite worker, and expose every task as a management command

- **Status:** accepted
- **Date:** 2026-09-17

## Context

Four things must happen outside a request: the hourly sync (minutes to tens of minutes), nightly churn (git clone,
fetch and blame per repository), large XLSX exports (above `EXPORT_SYNC_MAX_ROWS` = 20 000 rows), and daily
connection verification. The UI polls their progress over htmx (spec §5.5, §10.6).

Spec §2 names `huey` with `SqliteHuey` in a separate database file, and adds the requirement that matters most:
**every task is also available as a management command.** Spec §5.5 says `docs/SETUP.md` documents launchd/cron
entries for `sync` and `compute_churn`.

The operator is one person on a laptop. A background system they must remember to start is a background system
that will be found stopped.

## Decision

**One worker process**, `uv run python manage.py run_huey`, with `SqliteHuey` backed by `DATA_DIR/huey.sqlite3` —
a *different file* from the domain database, so queue bookkeeping never takes the domain write lock (ADR 0001).

**Task functions are thin.** Every `@huey.task()` is a three-line wrapper that calls a service function. The
management command calls the same service function directly. Therefore:

- nothing is lost if the worker is never started — `manage.py sync`, `compute_churn`, `recompute`,
  `verify_connections` and `export` all work synchronously;
- cron/launchd and the worker's own scheduler are interchangeable, which is what lets `docs/SETUP.md` offer both;
- tasks are testable without a queue.

**Serialisation is a feature, not a limit.** One worker thread by default. The sync lock (ADR 0003) already means
only one sync may run; churn is I/O-bound on `git` and caps its own parallelism at 4 workers *inside* the task;
exports are minutes at worst. With SQLite's single-writer model, a second worker would mostly produce
`database is locked`.

**Tasks must be re-runnable.** A task killed mid-flight leaves the same state as a crash mid-sync: upserts are
idempotent, rollups rebuild from the dirty-days set, and a half-written export file is discarded. On worker
startup a sweep marks `ExportJob` rows stuck in `running` for more than an hour as `failed`, so the UI's htmx
poll always terminates.

**In tests**, `HUEY = {"immediate": True}` so task code runs inline; the two paths that must *not* be inline (the
large-export hand-off, and the "button enqueues rather than blocks" behaviour) are tested by asserting the
enqueue, not the execution.

## Alternatives considered

- **Celery + Redis/RabbitMQ** — rejected: a broker daemon to install and supervise on a laptop, for an hourly job.
- **Django-Q2 or django-tasks** — rejected: no advantage over huey here, and the spec names huey.
- **Threads inside the web process** — rejected: `runserver` reloads kill them, progress is unobservable, and a
  long sync would hold a worker thread.
- **cron/launchd only, with no worker at all** — rejected: the spec requires a "Sync now" button with live
  progress, and a large export must not block a request. Kept as a *supported second path* instead, which is what
  the "every task is also a command" rule buys.
- **Multiple worker threads** — rejected for v1: SQLite has one writer; concurrency would trade throughput for
  lock contention.
- **`SqliteHuey` in the main database file** — rejected: queue writes are frequent and short, domain writes are
  rare and long; sharing one write lock between them is the worst of both.

## Consequences

- **Buys:** no extra service to install, a UI that can show progress, and a tool that still works completely when
  the worker is not running — which is the realistic steady state on a laptop.
- **Costs:** background work is serial. A nightly churn run and a large export queue behind each other. At the
  stated volume this is fine and the durations are documented.
- **Harder:** every task must stay idempotent and restartable, and the UI must treat "task never started because
  no worker is running" as a real state (the Sync page shows queued-but-not-running, and `docs/SETUP.md` says how
  to start the worker).
- **Revisit when:** the tool moves to a server (then: a real broker and separate worker containers, which the
  thin-task-wrapper shape makes a configuration change), or when exports routinely exceed ten minutes.
