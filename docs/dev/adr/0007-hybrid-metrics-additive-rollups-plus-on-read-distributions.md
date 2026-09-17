# 0007. Compute metrics from a registry, rolling up only additive values and deriving distributions from raw rows at read time

- **Status:** accepted
- **Date:** 2026-09-17

## Context

Around thirty metrics (spec §8.2) must be available at four scopes (global, project, repository, person), in three
cohorts (all / AI / non-AI), for a day or an arbitrary period, with a comparison against the previous period of
equal length and a time series at day/week/month granularity — and the same numbers must appear identically in the
UI, in CSV, and in XLSX reports and their metric glossary sheet.

The metrics are not of one kind. `prs_merged` is a count and composes across days. `lead_time_p50` is a median and
**does not** — a median of daily medians is not the median of the period. `open_prs` is a state at a point in
time, reconstructed from dates rather than accumulated. `ai_pr_share` is a ratio whose numerator and denominator
compose but whose value does not.

Spec §8.3 states the consequence directly: `DailyRollup` holds "лише адитивні метрики", and medians and
percentiles for a period are computed from raw data. It also requires caching keyed on a `last_data_version` that
increments after every sync or recompute.

This is expensive to reverse because every dashboard, chart endpoint, table and export calls the same entry point,
and because getting it wrong produces numbers that are plausible and false — shown to a manager, about a named
person.

## Decision

**One registry.** `apps/metrics/registry.py` holds a `MetricDef` per metric:

```python
MetricDef(key, title, description, unit, direction, kind, levels,
          supports_cohorts, calculator, params)
```

`title` and `description` are lazy translations. `kind` is `counter | ratio | distribution | state` and it decides
the storage and computation strategy — it is not documentation. `docs/METRICS.md` is generated from the registry
by `manage.py metrics_doc`, and a test fails if the committed file is stale, so the glossary, the ⓘ tooltips and
the XLSX `Metrics` sheet cannot disagree.

**One entry point.**

```python
compute(metric_keys, scope, date_from, date_to, cohort, granularity) -> MetricResultSet
```

returning, per metric: value, previous-period value, delta, `sample_size`, and the series. Nothing reads metric
data any other way — not a view, not a chart endpoint, not an export.

**Storage by kind.**

- `counter` — summed from `DailyRollup(date, scope_type, scope_id, cohort, metric_key, value, sample_size)`.
- `ratio` — the numerator and denominator are stored as counters and divided at read time. The ratio itself is
  never stored.
- `distribution` (medians, percentiles, size buckets) — computed from raw rows for the whole period, via a
  queryset plus `statistics` in Python. Never rolled up.
- `state` (`open_prs`, `stale_prs`, `waiting_review_24h`, `wip_per_person`) — reconstructed from dates for the
  requested instant (`created_at ≤ D` and not closed by end of `D`), so history stays correct when a PR is
  back-filled later.

**Rollups are derived and disposable.** Post-processing records the affected `REPORT_TIMEZONE` dates in a
dirty-days set; the rollup task deletes and rewrites exactly those `(date, scope, scope_id, cohort, metric_key)`
rows. `manage.py recompute --from --to` rebuilds any range from raw data. A rollup row is never the only copy of
anything.

**Day attribution is fixed and single-sourced.** Opened → `created_at`; merged → `merged_at`; review →
`submitted_at`; a distribution metric → the day of the merge. All boundaries are computed in `REPORT_TIMEZONE`
(`Europe/Kyiv`) from UTC storage, by one helper, tested across a DST transition.

**Exclusions live in the calculators, not in the callers:** bot PRs and `exclude_from_metrics` people are out
(and counted separately), and `EXCLUDED_PATH_GLOBS` shrink `effective_additions`/`effective_deletions`. A missing
GitHub field yields `None` and drops out of the sample — never `0`. A sample below `MIN_SAMPLE` (5) still returns
its value but flags itself, and the UI greys it.

**Caching.** Django `FileBasedCache`, key = metric keys + scope + dates + cohort + granularity +
`last_data_version`. The version increments at the end of every sync and recompute, so a stale value cannot
outlive a write, and the cache is always safe to delete.

## Alternatives considered

- **Roll up everything, including percentiles** — rejected: mathematically wrong for medians and percentiles, and
  the spec forbids it. It would have been the fastest option and the most quietly incorrect one.
- **Compute everything from raw rows, no rollups** — rejected: counters over 90 days across 50 repositories and
  four scopes would miss the 1.5 s budget, and the daily series charts would each become a full table scan.
- **Store a t-digest or reservoir sketch per day to make percentiles composable** — rejected: real, but it adds an
  approximation (and an explanation of why the dashboard and a hand count differ) to buy performance the stated
  volume does not need. Reconsider at ~10× the data.
- **A materialised "PR fact" table denormalising repo/project/person/cohort** — rejected for v1: it is the natural
  next step if distributions get slow, and nothing here prevents adding it, because `compute()` is the only
  reader.
- **Cache invalidation by key deletion on write** — rejected: a version stamp in the key is one integer to get
  right instead of a matrix of key patterns to enumerate.
- **Computing the previous-period comparison in each view** — rejected: it is part of the metric's definition and
  belongs behind `compute()`, or two pages will eventually disagree about what "previous period" means.

## Consequences

- **Buys:** correct percentiles, fast counters, one definition of every number across UI and files, and a
  regeneratable derived layer — `recompute` fixes any corruption without touching GitHub.
- **Costs:** two code paths inside `compute()` (rollup-backed and raw-backed), and distribution metrics get slower
  as data grows, since they are the ones that scan.
- **Harder:** adding a metric means choosing a `kind` correctly. A `distribution` mislabelled as a `counter` would
  be rolled up and silently wrong — so each metric's test computes the expected value by hand on a `factory_boy`
  fixture, including the Kyiv day boundary and the DST transition, as spec §12 requires.
- **Revisit when:** distribution metrics exceed the latency budget (add the fact table), or a rollup rebuild for a
  full backfill becomes too slow to run nightly.
