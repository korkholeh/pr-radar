# Phase 7 — Metrics registry, rollups and recompute

**Goal:** One registry that UI, docs and exports all read, with storage decided by metric kind and every day
boundary computed in `Europe/Kyiv`.

**User-facing:** no — a registry, a `compute()` entry point, two management commands and a generated document.
The first page that renders these numbers is phase 8.

---

## Context

### What exists

- **`apps/metrics/` is a stub.** `models.py` holds `ScopeType` (`global|project|repo|person`), `Cohort`
  (`all|ai|non_ai`) and `DailyRollup(date, scope_type, scope_id, cohort, metric_key, value, sample_size,
  computed_at)` with both uniqueness constraints (the partial one for `scope_id IS NULL`, see the p02 decision)
  and indexes on `(metric_key, date)` and `(scope_type, scope_id, date)`. `factories.py` has
  `DailyRollupFactory`. **Nothing writes a rollup row today.**
- **`apps/metrics/timeframe.py`** (extracted early in phase 6, the single day-boundary owner):
  `day_start(date)`, `day_end_exclusive(date)`, `today()`, all in `settings.REPORT_TIMEZONE` (`Europe/Kyiv`),
  accepting an ISO string. `apps/metrics/tests/test_timeframe.py` already covers the winter/summer UTC offsets,
  the 2026-03-29 DST transition (23 h day) and `today()` vs `localdate()`.
- **The metric population rule is already written and tested** (phase 4):
  `apps/activity/selectors.py::pull_requests_in_scope(scope)` / `pull_requests_for_metrics(scope)` (excludes
  `author__person__is_bot` and `author__person__exclude_from_metrics`) / `bot_pull_request_count(scope)`.
  `apps/ai_detection/selectors.py::ai_cohort_pull_requests(scope)` and
  `ai_detection/services.py::ai_cohort_statuses()` → `{ai_explicit, ai_disclosed}` (∪ `{ai_suspected}` when
  `AI_COHORT_INCLUDE_SUSPECTED`), which is spec §15's default cohort.
- **Every raw input the §8.2 metrics need is stored and derived.** `PullRequest`: `created_at`,
  `ready_for_review_at`, `first_commit_at`, `first_review_at`, `first_approval_at`, `merged_at`, `closed_at`,
  `last_activity_at`, `state`, `is_draft`, `effective_additions/deletions`, `size_bucket`, `review_rounds`,
  `commits_after_first_review`, `is_revert`/`reverts_pr`, `is_hotfix`, `has_test_changes`, `is_rubber_stamp`,
  `is_self_merged`, `ai_status`, `ai_tools`, `ai_disclosure`. `PRFile` (`is_test`, `is_excluded`, `additions`,
  `deletions`), `Review` (`state`, `reviewer`, `submitted_at`), `ReviewComment`, `CheckStatus`
  (`is_first_ci_commit`, `rollup_state` — **already populated by `github_sync/upserts.py`**), `ChurnResult`
  (model + factory exist; rows are produced in phase 10).
- **Policy read side, written as this phase's data source** (logged p06 deviation):
  `apps/policy/selectors.py::violations_in_scope / violations_by_rule(scope, start, end, status) /
  compliance_kpis(scope, start, end) / disclosure_mismatch_pull_requests(scope, start, end)`, all already using
  `day_start`/`day_end_exclusive`.
- **`manage.py recompute`** (`apps/github_sync/management/commands/recompute.py`) with `--from --to --repo
  --project`, running `derive → detect → evaluate` over stored PRs. Its help string says "Phase 7 adds rollup
  rebuilding to this command".
- **`apps/github_sync/pipeline.py::process_pull_request(pr_id)`** runs resolve → derive → detect → evaluate
  after the PR's transaction commits. `ARCHITECTURE.md`'s contract ends that chain with `metrics.mark_dirty`,
  which does not exist yet. `services.py::run_sync()` sets the terminal `SyncRun` status at one place near the
  end of its `try` block.
- **Settings.** `apps/catalog/setting_defs.py` already carries the `metrics` group this phase reads:
  `MIN_SAMPLE=5`, `STALE_DAYS=5`, `WAITING_REVIEW_HOURS=24`, `DURATION_MODE=calendar_hours`,
  `PR_SIZE_BUCKETS={"XS":10,"S":100,"M":400,"L":1000}`, `EXCLUDED_PATH_GLOBS`, `TEST_PATH_GLOBS`,
  `REVIEW_LOAD_TOP_N=2`, `FOLLOWUP_FIX_WINDOW_DAYS=14`, `FOLLOWUP_FIX_FILE_OVERLAP=0.5`,
  `RUBBER_STAMP_MAX_MINUTES=10`, plus `CHURN_WINDOW_DAYS=21`. Readers:
  `catalog/services.py::get_int/get_bool/get_str/get_list/get_dict`.
- **Authorization.** `apps/accounts/selectors.py::scope_for_user(user) -> ScopeFilter(unrestricted,
  project_ids)`; still unrestricted until phase 9, but every selector already threads it.
- **No cache is configured.** `config/settings/base.py` has no `CACHES` block; `DATA_DIR/cache/` is created on
  boot and `ARCHITECTURE.md` reserves it for the metric cache. There is no `last_data_version` anywhere.
- **`docs/METRICS.md` does not exist** — deliberately (p04 decision: a generated file with no generator would
  contradict CLAUDE.md's freshness-test convention). `tests/test_docs.py` shows the grep-style doc tests; the
  freshness pattern lives in `tests/test_css_tokens.py` / `tests/test_translations.py`.
- **mypy** covers `apps/*/services.py`, `apps/*/selectors.py` plus a named file list in `pyproject.toml`.

### What this phase changes

`apps/metrics/` grows from three files into the component `ARCHITECTURE.md` describes: a registry of ~40
`MetricDef`s, four calculator strategies, a rollup writer restricted to additive kinds, one cached `compute()`
entry point, a data-version counter, a dirty-day set, and two management commands (`recompute` extended,
`metrics_doc` new). `config/settings/base.py` gains `CACHES`; `github_sync` gains two call sites
(`mark_dirty`, `rebuild_dirty` + version bump); `docs/METRICS.md` is created and gated by a freshness test; the
Ukrainian catalogue gains a title and a description per metric.

### Key files

| Path | Change |
|---|---|
| `apps/metrics/types.py` | new — `Scope`, `Granularity`, `MetricValue`, `MetricResult`, `MetricResultSet`, `SeriesPoint` |
| `apps/metrics/registry.py` | new — `MetricDef`, the four calculator strategies, `REGISTRY`, lookup helpers, integrity validation |
| `apps/metrics/calculators/` | new package — `base.py`, `adoption.py`, `flow.py`, `state.py`, `quality.py` |
| `apps/metrics/selectors.py` | new — scoped population querysets (PRs, reviews, review comments, violations) per `Scope` + `Cohort` |
| `apps/metrics/rollups.py` | new — additive-only write guard, `rebuild(date_from, date_to)`, dirty-day consumption |
| `apps/metrics/services.py` | new — `compute()`, cache key, `data_version()`, `bump_data_version()`, `mark_dirty()` |
| `apps/metrics/docs.py` | new — `render_metrics_doc()` returning the English Markdown |
| `apps/metrics/models.py` | `DataVersion` and `DirtyDay` added (+ migration) |
| `apps/metrics/timeframe.py` | `day_of()`, `previous_period()`, `bucket_ranges()` added |
| `apps/metrics/management/commands/metrics_doc.py` | new |
| `apps/github_sync/management/commands/recompute.py` | rollup rebuild + version bump |
| `apps/github_sync/pipeline.py`, `services.py` | `mark_dirty` at the end of the chain; `rebuild_dirty()` + bump at the end of a run |
| `config/settings/base.py` | `CACHES` (FileBasedCache under `DATA_DIR/cache/metrics`) |
| `apps/catalog/setting_defs.py` + migration | `METRICS_CACHE_TTL_SECONDS`, `DEFAULT_PERIOD_DAYS` |
| `conftest.py` | autouse cache isolation |
| `docs/METRICS.md` | new, generated |
| `locale/uk/LC_MESSAGES/django.po` + `.mo` | one title + one description per metric |
| `pyproject.toml` | mypy file list extended with the new `apps/metrics` modules |

---

## Design

### The registry (spec §8.1, ADR 0007)

```python
# apps/metrics/registry.py
@dataclass(frozen=True)
class MetricDef:
    key: str
    title: Promise  # gettext_lazy
    description: Promise  # gettext_lazy
    unit: str  # count | ratio | duration | lines | breakdown
    direction: str  # higher_is_better | lower_is_better | neutral
    kind: str  # counter | ratio | distribution | state
    levels: frozenset[str]  # subset of ScopeType.values
    supports_cohorts: bool
    calculator: CounterCalc | RatioCalc | DistributionCalc | StateCalc
    formula: str  # short English formula, shown in ⓘ and docs/METRICS.md
    params: Mapping[str, object] = field(default_factory=dict)  # e.g. {"percentile": 90}
```

`calculator` is exactly the spec's field name and holds **one of four strategy objects**, and the strategy type
is what decides storage. This is the structural defence for RISKS row 7 — a distribution cannot be rolled up by
accident, because `rollups.py` never sees anything but `CounterCalc`/`RatioCalc`:

```python
CounterCalc(daily: Callable[[DayContext], MetricValue])          # kind="counter"
RatioCalc(numerator: Callable[[DayContext], MetricValue],
          denominator: Callable[[DayContext], MetricValue])      # kind="ratio"
DistributionCalc(period: Callable[[PeriodContext], MetricValue]) # kind="distribution"
StateCalc(at_date: Callable[[DayContext], MetricValue])          # kind="state"
```

`REGISTRY: Mapping[str, MetricDef]` is built at import time by `_register(...)`, which validates: unique key,
`kind` matches the strategy class, `levels ⊆ ScopeType.values` and non-empty, `title`/`description` are lazy
(`isinstance(x, Promise)`), `direction`/`unit` in their allowed sets. A registry-integrity test re-asserts all
of it so a new metric cannot be added wrongly.

`MetricValue = NamedTuple(value: float | None, sample_size: int)` is what every calculator returns. `value is
None` when `sample_size == 0` — spec §15's "no data → `None`, never `0`" is enforced in one place
(`MetricValue.empty()`), not per calculator.

### The metrics (spec §8.2)

| Group | Keys | Kind |
|---|---|---|
| Adoption | `ai_pr_share` | ratio (AI merged / merged) |
| | `ai_pr_count`, `disclosure_mismatch_count`, `violations_new` | counter |
| | `disclosure_rate` | ratio (valid disclosure / all) |
| | `ai_status_breakdown`, `ai_tool_breakdown`, `violations_by_rule`, `ai_active_people` | distribution |
| | `violations_open` | state |
| Flow | `prs_opened`, `prs_merged`, `prs_closed_unmerged`, `reviews_given`, `prs_excluded_from_metrics` | counter |
| | `lead_time_p50`, `lead_time_p90`, `cycle_time_p50`, `time_to_first_review_p50`, `time_to_first_review_p90`, `pr_size_p50`, `pr_size_buckets`, `review_load_share`, `reviewer_response_p50` | distribution |
| | `open_prs`, `stale_prs`, `waiting_review_24h`, `wip_per_person` | state |
| Quality | `review_rounds_avg`, `rework_rate`, `revert_rate`, `ci_first_pass_rate`, `test_change_ratio`, `test_lines_ratio`, `review_comments_per_100_lines`, `rubber_stamp_rate`, `self_merge_rate`, `followup_fix_rate` | ratio |
| | `churn_21d` | distribution |

Three names that *sound* additive and are not, classified deliberately:

- `ai_active_people` — a distinct-people count. The sum of daily distinct counts is not the period's distinct
  count, so it is a **distribution** computed from raw rows.
- `review_load_share` — "share of reviews falling on the top-2 reviewers". Top-N is selected over the whole
  period, so it cannot compose from days: **distribution**.
- `wip_per_person` — open non-draft PRs per author at a point in time: **state**, reconstructed from dates.
  Value = open non-draft PRs ÷ distinct authors holding one; `sample_size` = that author count.

`violations_open` is a **state** metric: violations created on or before the end of the period that are still
`open`. Violation status carries no history table, so "open as of a past date" is not reconstructable; the
metric documents itself as a current snapshot bounded by `created_at`, matching what
`policy/selectors.py::compliance_kpis` already shows on the console.

`followup_fix_rate` ships as a registered `RatioCalc` whose components return `MetricValue.empty()` and whose
description carries the `(heuristic)` label spec §8.2 requires — the 14-day / ≥50 %-file-overlap service is
phase 10's own deliverable with its own acceptance criteria. `ci_first_pass_rate` and `churn_21d` are
**implemented here** (deviation from a roadmap assumption, justified below): their inputs
(`CheckStatus.is_first_ci_commit`, `ChurnResult.churn_ratio`) are already stored and each calculator is a
handful of lines, so deferring them would leave two holes in a registry that is otherwise complete.

`reviewer_response_p50` has no review-request timestamp in the data model (`activity` stores no
`ReviewRequest`; the GraphQL query never fetched one). It is computed from `ready_for_review_at` (falling back
to `created_at`) to that reviewer's first review on the PR, and its `description` states the approximation, so
the ⓘ tooltip, `docs/METRICS.md` and the XLSX glossary all carry the caveat.

### Storage by kind

- **counter** — one `DailyRollup` row per `(date, scope_type, scope_id, cohort, metric_key)`.
- **ratio** — the numerator and denominator are stored as two rollup rows under the reserved metric keys
  `"<key>__num"` and `"<key>__den"`, and divided at read time. The ratio itself is never stored, and no
  internal counter pollutes the registry or `docs/METRICS.md`.
- **distribution / state** — never written. Computed from raw rows for the requested period (distribution) or
  reconstructed at a date (state).

**A rollup row is written only when `sample_size > 0`.** An absent row is "no data" and reads back as `None`,
never `0` — which is also what keeps the table small (a repository with no activity on a day costs no rows).

`rollups.write_rollups(rows)` raises `NonAdditiveMetricError` if any row's `metric_key` does not belong to a
`CounterCalc`/`RatioCalc` def (after stripping the `__num`/`__den` suffix). That guard is the acceptance
criterion "no distribution-kind metric ever reaches `DailyRollup`", and it holds even if a future phase writes
rollups from somewhere else.

`rollups.rebuild(date_from, date_to)` iterates days, and for each day makes **one pass over that day's rows**,
bucketing into `(scope, cohort, metric)` accumulators, rather than looping over every scope × cohort × metric
combination. Scopes are derived from the rows themselves (the PR's repository, each project that repository
belongs to, the author's person, plus global). It deletes exactly the `(date, …)` rows in range and re-inserts,
inside one transaction per day, so a rebuild is idempotent and a crash leaves at most one day half-written.

### `compute()` — the single read entry point

```python
def compute(metric_keys: Sequence[str], scope: Scope, date_from: date, date_to: date,
            cohort: str = Cohort.ALL, granularity: str = "day") -> MetricResultSet
```

```python
@dataclass(frozen=True)
class Scope:
    scope_type: str  # ScopeType value
    scope_id: int | None
    access: ScopeFilter  # from accounts.scope_for_user(user)


@dataclass(frozen=True)
class MetricResult:
    key: str
    definition: MetricDef
    value: float | None
    previous_value: float | None
    delta: float | None
    delta_ratio: float | None
    sample_size: int
    previous_sample_size: int
    below_min_sample: bool
    series: tuple[SeriesPoint, ...]  # (date, value, sample_size) per granularity bucket
    breakdown: tuple[BreakdownItem, ...]  # non-empty only for unit="breakdown" metrics
```

- `metrics.services.scope_for(user, scope_type, scope_id)` builds a `Scope`: it calls
  `accounts.selectors.scope_for_user(user)` and **silently drops an out-of-scope id** back to the global scope
  (RISKS row 3's stated behaviour), so no view has to decide.
- The previous period is `[date_from - n, date_from - 1]` where `n = (date_to - date_from) + 1`, computed by
  `timeframe.previous_period()` — one definition, behind `compute()`, per ADR 0007.
- `delta = value - previous_value`, `delta_ratio = delta / previous_value`; both `None` if either side is
  `None` or the previous value is `0`.
- `below_min_sample = sample_size < get_int("MIN_SAMPLE")`; the raw `sample_size` is reported regardless so the
  UI can grey and label it.
- Series buckets come from `timeframe.bucket_ranges(date_from, date_to, granularity)` (`day|week|month`, weeks
  ISO, all boundaries in Kyiv). Counter/ratio series read rollups; distribution/state series call the
  calculator once per bucket.
- A metric whose `levels` excludes the requested scope type raises `MetricNotAvailableAtLevel` — a programming
  error, not a user error; views pass only keys the page declares.

### Cache

`CACHES["default"]` is a `FileBasedCache` at `DATA_DIR/cache/metrics` (`ARCHITECTURE.md`'s "safe to delete at
any time"). Key:

```
metrics:v1:<data_version>:<scope_type>:<scope_id>:<access-fingerprint>:<cohort>:<granularity>:<from>:<to>:<sha1(sorted metric keys)>
```

The **access fingerprint** (`"*"` when unrestricted, else the sorted `project_ids`) is in the key on purpose: a
restricted lead and an admin must never share an entry once phase 9 turns narrowing on. TTL comes from the new
`METRICS_CACHE_TTL_SECONDS` setting (default 3600); correctness does not depend on it, because the version
stamp already invalidates.

`last_data_version` lives in a singleton row `metrics.DataVersion(id=1, version, updated_at)`, bumped with an
atomic `F("version") + 1`. `bump_data_version()` is called at the end of `run_sync()` and at the end of
`manage.py recompute`. A DB row (rather than a cache entry or an `AppSetting`) survives a cache wipe, is
visible in the admin, and is shared correctly between the web process and the huey worker.

### Dirty days

`metrics.services.mark_dirty(pull_request)` records, in `metrics.DirtyDay(date unique)`, every Kyiv day a PR
can affect: `created_at`, `merged_at`, `closed_at`, and each review's `submitted_at` day. It is appended to
`github_sync/pipeline.py::process_pull_request`, completing `ARCHITECTURE.md`'s documented chain.
`rollups.rebuild_dirty()` rebuilds exactly those days and deletes the rows, and `run_sync()` calls it once
before bumping the version — so an incremental sync rewrites the days it touched, not the whole history.

### Module layout and error handling

`apps/metrics/` : `types.py`, `registry.py`, `calculators/{base,adoption,flow,state,quality}.py`,
`selectors.py`, `rollups.py`, `services.py`, `docs.py`, `models.py`, `timeframe.py`,
`management/commands/metrics_doc.py`. Views (phase 8) call `services.compute()` only; rules stay in
`calculators/`, queries in `selectors.py` — CLAUDE.md's services/selectors split.

Every selector in `apps/metrics/selectors.py` starts from `ScopeFilter` via the existing
`activity.selectors.pull_requests_for_metrics(scope.access)`, never `PullRequest.objects.all()`. The cohort
filter is applied there too (`ai_cohort_statuses()` for `ai`, its complement for `non_ai`), so no calculator
re-implements spec §15's default cohort. Exclusion (bots, `exclude_from_metrics`) is inherited from the same
selector, and the PRs that *are* excluded are surfaced by the counter `prs_excluded_from_metrics` (a new
`activity.selectors.excluded_pull_requests(scope)` beside the existing `bot_pull_request_count`).

Errors: `UnknownMetricError`, `MetricNotAvailableAtLevel`, `NonAdditiveMetricError` — all `ValueError`
subclasses defined in `apps/metrics/registry.py` / `rollups.py`. A calculator never swallows an exception; a
missing GitHub field yields `None` and drops out of the sample, which is a value, not an error.

### `docs/METRICS.md`

`metrics/docs.py::render_metrics_doc()` renders the registry under `translation.override("en")` (the document
is English per CLAUDE.md even when the operator's UI is Ukrainian): a header, a group table of every metric
with key / title / unit / kind / direction / levels / cohorts / formula, and one section per metric with its
description. `manage.py metrics_doc` writes it; `--check` exits non-zero when the committed file differs. The
freshness test compares `render_metrics_doc()` against the committed bytes, so adding a metric without
regenerating fails the build (RISKS row 15).

### Deviations from `.autodev/ARCHITECTURE.md` / the roadmap

1. **Roadmap assumption "`ci_first_pass_rate` and `followup_fix_rate` are registered returning `None` and
   computed later"** — only `followup_fix_rate` is deferred here; `ci_first_pass_rate` (and `churn_21d`) are
   implemented, because their inputs are already stored and populated.
2. **Ratio components are stored as `<key>__num` / `<key>__den` rollup rows**, which `ARCHITECTURE.md` leaves
   unspecified ("the numerator and denominator are stored as counters").
3. **`last_data_version` gets its own model**; `ARCHITECTURE.md` names the key ingredient but not its home.
4. **A `DirtyDay` model** materialises the ADR's "dirty-days set".

All four are appended to `.autodev/DECISIONS.md` under `## p07-plan`.

---

## Tasks

- [x] **T1: Cache, two settings and test isolation.** `config/settings/base.py`: `CACHES["default"]` =
  `FileBasedCache` at `DATA_DIR/cache/metrics` (`MAX_ENTRIES` 5000); `config/settings/e2e.py` points it at its
  own `DATA_DIR`. `apps/catalog/setting_defs.py`: `METRICS_CACHE_TTL_SECONDS` (int, 3600) and
  `DEFAULT_PERIOD_DAYS` (int, 30) in the `metrics` group + a `catalog` data migration mirroring
  `0004_ai_detection_settings.py`. `conftest.py`: autouse fixture pointing `CACHES` at a per-test `tmp_path`
  and clearing it. `pyproject.toml`: add the new `apps/metrics` modules to the mypy file list.
  *Tests:* extend `apps/catalog/tests/test_app_settings.py` for the two new keys (defaults + seeding);
  `apps/metrics/tests/test_cache_config.py` asserting the configured backend is file-based and writable.
- [x] **T2: `timeframe` extensions.** Add `day_of(dt) -> date` (UTC instant → Kyiv calendar day),
  `previous_period(date_from, date_to) -> tuple[date, date]`, `bucket_ranges(date_from, date_to, granularity)
  -> list[tuple[date, date]]` (`day|week|month`), `date_range(date_from, date_to)`.
  *Tests:* `apps/metrics/tests/test_timeframe.py` — `day_of` for an instant at 23:59 Kyiv and one at 00:01
  Kyiv on the following day (they must land on different days, and the UTC-naive reading would not);
  `day_of` across the 2026-03-29 DST transition; `previous_period` for a 1-day and a 30-day window; week and
  month bucketing including a partial trailing bucket.
- [x] **T3: Types and registry core.** `apps/metrics/types.py` (`Scope`, `MetricValue`, `SeriesPoint`,
  `BreakdownItem`, `MetricResult`, `MetricResultSet`, `Granularity`), `apps/metrics/registry.py` (`MetricDef`,
  the four strategy dataclasses, `_register`, `REGISTRY`, `get_metric`, `metrics_for_level`,
  `UnknownMetricError`, `MetricNotAvailableAtLevel`). No metrics registered yet.
  *Tests:* `apps/metrics/tests/test_registry.py` — `_register` rejects a duplicate key, a `kind` that
  contradicts its strategy, an empty/unknown `levels`, a non-lazy title, an unknown unit/direction;
  `MetricValue.empty()` is `(None, 0)`.
- [x] **T4: Scoped population selectors.** `apps/metrics/selectors.py`: `scoped_pull_requests(scope, cohort)`,
  `scoped_reviews(scope, cohort)`, `scoped_review_comments(scope, cohort)`, `scoped_violations(scope)`, each
  starting from the phase-4 selectors and narrowing by `scope_type`/`scope_id` (repo, project via the M2M,
  person via `author__person`) and by cohort. `apps/activity/selectors.py`: add
  `excluded_pull_requests(scope)`. `apps/metrics/services.py`: `scope_for(user, scope_type, scope_id)`
  dropping an out-of-scope id to global.
  *Tests:* `apps/metrics/tests/test_selectors.py` — bot and `exclude_from_metrics` PRs are out of every
  population and present in `excluded_pull_requests`; the `ai` cohort is exactly `ai_explicit|ai_disclosed`
  under the default setting and gains `ai_suspected` when `AI_COHORT_INCLUDE_SUSPECTED` is on; `non_ai` is its
  exact complement; a repo in two projects appears once at project scope; `scope_for` drops an id a restricted
  `ScopeFilter` does not contain.
- [x] **T5: `DataVersion` and `DirtyDay`.** Models + migration + admin registration;
  `services.data_version()`, `services.bump_data_version()` (atomic `F`), `services.mark_dirty(pr)`.
  *Tests:* `apps/metrics/tests/test_data_version.py` — first read creates version 1; a bump increments and is
  visible to a second connection-less read; `mark_dirty` records the Kyiv days of `created_at`, `merged_at`,
  `closed_at` and each review, de-duplicated, and a PR merged at 23:59 Kyiv marks that day, not the next.
- [x] **T6: Calculator base helpers.** `calculators/base.py`: `DayContext` / `PeriodContext` (scope, cohort,
  date bounds, resolved settings), `median()`/`percentile()` over an iterable dropping `None`s,
  `duration_hours(start, end)` honouring `DURATION_MODE`, `ratio_value(num, den)`, `breakdown_value(counter)`.
  *Tests:* `apps/metrics/tests/test_calculator_base.py` — p50/p90 against hand-computed values for even and
  odd samples, `None`s excluded from both the value and the sample size, empty input → `MetricValue.empty()`,
  a negative duration (merged before ready, i.e. bad data) is dropped rather than counted.
- [x] **T7: Adoption calculators.**
  `calculators/adoption.py` + registration: `ai_pr_share`, `ai_pr_count`,
  `ai_status_breakdown`, `ai_tool_breakdown`, `disclosure_rate`, `disclosure_mismatch_count`,
  `ai_active_people`.
  *Tests:* `apps/metrics/tests/test_metrics_adoption.py` — one hand-built `factory_boy` dataset, one assertion
  per metric against a number computed by hand in the test's docstring; `ai_active_people` over two days
  proves the distinct count is not the sum of daily counts; a PR with `ai_tools=[]` is absent from the tool
  breakdown rather than a zero row.
- [x] **T8: Policy metrics.**
  `calculators/adoption.py` (same module): `violations_open` (state),
  `violations_new` (counter), `violations_by_rule` (distribution), each delegating to the existing
  `apps/policy/selectors.py` functions so the console and the registry cannot disagree.

  **Handoff note (session 1 stopped here):** `apps/metrics/calculators/adoption.py` now has all 10
  metrics from T7+T8 registered (`ai_pr_share`, `ai_pr_count`, `ai_status_breakdown`,
  `ai_tool_breakdown`, `disclosure_rate`, `disclosure_mismatch_count`, `ai_active_people`,
  `violations_open`, `violations_new`, `violations_by_rule`), `manage.py check` passes, `ruff
  check`/`ruff format --check` pass on `apps/metrics`, and the rest of the suite is green (only
  pre-existing, unrelated failure: `tests/test_css_tokens.py::test_app_css_is_not_stale`, present
  on the base commit before this session touched anything — not caused by this phase, needs a
  `make css` + commit whenever CSS is next touched, out of this phase's scope). **What is missing:
  `apps/metrics/tests/test_metrics_adoption.py` and `test_metrics_policy.py` do not exist yet** —
  write them against the already-implemented calculators before checking T7/T8 done. Two design
  decisions baked into the implementation the tests must match (also logged in DECISIONS.md):
  (1) a `count_value(count)` helper in `calculators/base.py` collapses a zero count to
  `MetricValue.empty()` (None, 0) for every counter/state calculator, not just ratios/distributions
  — applied uniformly so "no rows" and "verified zero" are the same fact, consistent with the
  rollup storage rule "a row is written only when sample_size > 0"; (2) `DistributionCalc` gained an
  optional second field `breakdown: Callable[[PeriodContext], tuple[BreakdownItem, ...]] | None`
  (registry.py) beyond the plan's literal `period` field, because a breakdown-unit metric needs to
  return per-label items, not a single `MetricValue` — `period` still returns the scalar
  value/sample_size for `below_min_sample`, `breakdown` returns the items. `disclosure_rate`'s
  "valid disclosure" is defined as `ai_disclosure in {PARTIAL, SUBSTANTIAL}` (own definition, no
  literal spec text was available beyond the plan's one-line gloss) — keep the test consistent with
  that definition rather than re-deriving a different one. `ratio_value()` was also changed to treat
  a `None` numerator as 0 rather than asserting (a missing numerator rollup row means "zero", per
  the same storage rule) — `test_calculator_base.py` already covers the empty/zero-denominator
  cases but not this specific "None numerator, real denominator" branch; consider adding it.
  *Tests:* `apps/metrics/tests/test_metrics_policy.py` — `violations_new` counts by `created_at` in Kyiv days;
  an acknowledged violation leaves `violations_open` but stays in `violations_new`;
  `violations_by_rule` matches `policy.selectors.violations_by_rule` for the same window.
- [x] **T9: Flow counters.** `calculators/flow.py`: `prs_opened` (`created_at`), `prs_merged` (`merged_at`),
  `prs_closed_unmerged` (`closed_at`, state `closed`), `reviews_given` (`submitted_at`),
  `prs_excluded_from_metrics`.
  *Tests:* `apps/metrics/tests/test_metrics_flow_counters.py` — **a PR merged at 23:59 Kyiv and one merged at
  00:01 Kyiv the next day land in different days**, and a day query for the earlier date returns exactly one;
  the same pair around the 2026-03-29 DST transition; a bot PR is in `prs_excluded_from_metrics` and in no
  other counter.
- [x] **T10: Flow distributions.** `calculators/flow.py`: `lead_time_p50/p90`, `cycle_time_p50`,
  `time_to_first_review_p50/p90`, `pr_size_p50`, `pr_size_buckets`, `review_load_share`,
  `reviewer_response_p50`. All merge-day attributed per spec §8.3.
  *Tests:* `apps/metrics/tests/test_metrics_flow_distributions.py` — hand-computed medians/percentiles; a PR
  with `ready_for_review_at = None` drops out of `lead_time` and out of its sample size (and the metric is
  `None`, not `0`, when every PR lacks it); size buckets match the `PR_SIZE_BUCKETS` boundaries at exactly 10 /
  100 / 400 / 1000; `review_load_share` with three reviewers and `REVIEW_LOAD_TOP_N=2`.
- [x] **T11: State calculators.** `calculators/state.py`: `open_prs`, `stale_prs`, `waiting_review_24h`,
  `wip_per_person`, all reconstructed from dates for the end of a given day.
  *Tests:* `apps/metrics/tests/test_metrics_state.py` — a PR created on D-3 and merged on D-1 is open on D-2
  and not on D (reconstruction on a past date); a draft PR is out of `stale_prs` and `wip_per_person`;
  `STALE_DAYS`/`WAITING_REVIEW_HOURS` boundaries asserted at exactly the threshold and one second past it;
  `wip_per_person` is `None` with no open PRs, never `0`.
- [x] **T12: Quality metrics.** `calculators/quality.py`: `review_rounds_avg`, `rework_rate`, `revert_rate`,
  `ci_first_pass_rate`, `test_change_ratio`, `test_lines_ratio`, `review_comments_per_100_lines`,
  `rubber_stamp_rate`, `self_merge_rate`, `churn_21d`, and `followup_fix_rate` as the deferred `RatioCalc`
  returning `MetricValue.empty()` with a `(heuristic)` description.
  *Tests:* `apps/metrics/tests/test_metrics_quality.py` — a hand-computed dataset per metric, each with a
  positive and a negative row; `ci_first_pass_rate` ignores PRs with no `is_first_ci_commit` CheckStatus;
  `churn_21d` is `None` with no `ChurnResult` rows and the median of the ratios with them;
  `followup_fix_rate` returns `None` and is present in the registry.
**Handoff note (session 2 stopped here, context exhausted):** T1-T12 are done — the full registry (39
`MetricDef`s across `adoption.py`, `flow.py`, `state.py`, `quality.py`) is implemented and every calculator has a
passing hand-computed test (`apps/metrics/tests/test_metrics_{adoption,policy,flow_counters,flow_distributions,
state,quality}.py`, 96 tests total in `apps/metrics/`, all green; `uv run ruff check apps/metrics/` and
`uv run ruff format --check apps/metrics/` both pass). **Nothing in T13-T20 has been started** — `rollups.py`,
`compute()`'s dispatch logic, caching, the pipeline/`recompute` wiring, `metrics_doc`, `docs/METRICS.md` and the
Ukrainian translations for the ~80 new lazy strings all remain to do, in that order (T14 needs T13's rollup
rows to read from for counters; T16/T17 need T13's `rebuild`/`rebuild_dirty`; T18 needs the finished registry,
which is now ready). Two design decisions from this session, logged in `.autodev/DECISIONS.md` under
`## p07-implement`, matter for T13/T14: (1) `flow.py::_reviews_given_in_scope()` narrows PERSON scope by
`reviewer__person_id`, not through `selectors.scoped_reviews()`'s author-based narrowing — `rollups.rebuild()`'s
"derive scopes from the rows" pass must use the *reviewer's* person for `reviews_given`/`review_load_share`
rows, not the PR author's, or a person-scope rollup for those two keys would be silently wrong; (2)
`review_load_share` is registered with `levels={global, project, repo}` and `reviewer_response_p50` with
`levels={person}` only — `compute()`'s `MetricNotAvailableAtLevel` guard (T14) is what enforces this, nothing
else does. Also note: `apps/metrics/calculators/quality.py::_ci_first_pass_denominator` and
`_effective_lines_denominator` both re-run `_merged_on_day(ctx).count()` — a minor duplicate query per call,
harmless correctness-wise but worth folding into one query if T14's `assertNumQueries` budget gets tight.
Before starting T13, run the full suite once (`uv run pytest -q`) and the lint gate from `CLAUDE.md` to confirm
this session's changes didn't regress anything outside `apps/metrics/` (not done this session due to context
running out — everything verified was scoped to `apps/metrics/` only).
- [x] **T13: Rollups.** `apps/metrics/rollups.py`: `rollup_metric_keys()`, `write_rollups()` with the
  `NonAdditiveMetricError` guard, `rebuild(date_from, date_to)` (one pass per day, scopes derived from rows,
  `sample_size > 0` rows only, one transaction per day), `rebuild_dirty()`.
  *Tests:* `apps/metrics/tests/test_rollups.py` — **no distribution- or state-kind key appears in
  `rollup_metric_keys()`**, and `write_rollups` raises `NonAdditiveMetricError` for one;
  **deleting every rollup in a range and rebuilding reproduces byte-identical rows** (compared on every field
  except `id`/`computed_at`); a day with no activity writes no rows; a repo in two projects produces one global
  row and one row per project; `rebuild_dirty()` consumes and deletes the `DirtyDay` rows.
- [x] **T14: `compute()` core (uncached).** `apps/metrics/services.py`: dispatch by kind, previous period,
  delta/`delta_ratio`, `sample_size`, `below_min_sample`, the series per granularity, `breakdown` for
  `unit="breakdown"` metrics, `MetricNotAvailableAtLevel`.
  *Tests:* `apps/metrics/tests/test_compute.py` — a counter read from rollups equals the same number computed
  from raw rows; a distribution ignores rollups entirely (rollups deleted mid-test, value unchanged); the
  previous period of a 7-day window is the 7 days before it; `delta` is `None` when the previous period is
  empty; a metric with no data is `None` and `sample_size == 0`; `below_min_sample` flips at `MIN_SAMPLE`;
  week and month granularity bucket counts; an `assertNumQueries` bound on a multi-metric call.
- [x] **T15: Caching.** Cache key builder (metric keys, scope, access fingerprint, cohort, granularity, dates,
  `data_version`), read-through in `compute()`, TTL from `METRICS_CACHE_TTL_SECONDS`.
  *Tests:* `apps/metrics/tests/test_compute_cache.py` — **a second identical `compute()` performs no queries**
  (`assertNumQueries(0)`); after `bump_data_version()` it misses and recomputes; two different `ScopeFilter`s
  never share an entry; the real `FileBasedCache` backend round-trips a `MetricResultSet`.

  **Handoff note (session 3 stopped here, context exhausted):** the implementation is done —
  `services.py` gained `_cache_key`/`_access_fingerprint`/`_serialize_result`/`_deserialize_result`,
  `compute()` is now a read-through cache wrapper around the renamed `_compute_uncached()` (the old
  T14 body, unchanged), and `data_version()`/`bump_data_version()` are themselves cached under a
  fixed key in the same `FileBasedCache` so a repeat `compute()` call costs zero DB queries (not
  just zero *extra* queries — see the docstring on `data_version()`). `uv run pytest apps/metrics -q`,
  `ruff check/format`, and `uv run mypy` are all green with this code in place (mypy was also fixed
  to actually run over `apps/metrics` at all — see the `[p07/T1 (fix)]` DECISIONS entry — which
  surfaced and fixed two pre-existing type errors in `flow.py`/`services.py::mark_dirty` from
  earlier sessions). **What is missing: `apps/metrics/tests/test_compute_cache.py` does not exist
  yet** — write it against the already-implemented cache before checking T15 done. Two things the
  next session must know before writing it: (1) `MetricResult.definition` (a `MetricDef`, which can
  hold a `lambda` calculator — not picklable) is deliberately *not* part of the cached payload;
  `_serialize_result`/`_deserialize_result` strip it out and re-attach it from the live `REGISTRY`
  on every read, so don't assert on cached-payload internals, assert on the `MetricResultSet` `compute()`
  returns. (2) Fixing T15 changed T14's own tests: `test_below_min_sample_flips_at_min_sample` now
  calls `bump_data_version()` between its two `compute()` calls (without it, the second call was
  silently served a stale cached result from before the third PR was merged — a real bug this
  caught, not a test artifact: any caller that mutates rollups via `rebuild()` without also bumping
  the version will see stale `compute()` results, which is why `T16`/`T17` must always pair
  `rebuild()`/`rebuild_dirty()` with `bump_data_version()`), and
  `test_compute_query_count_is_bounded_for_a_multi_metric_call`'s bound changed from an exact `2` to
  `django_assert_num_queries(8, exact=False)` (a cold call now also touches `MIN_SAMPLE`,
  `METRICS_CACHE_TTL_SECONDS` and `DataVersion.get_or_create`) — both changes are already applied
  and passing in `apps/metrics/tests/test_compute.py`.
- [x] **T16: Pipeline and sync wiring.** `github_sync/pipeline.py` appends `metrics.mark_dirty`;
  `github_sync/services.py::run_sync` calls `rollups.rebuild_dirty()` then `bump_data_version()` before
  returning, inside the existing terminal-status block (and still on the failure path, so a partial sync's
  rollups are not left stale).
  *Tests:* extend `apps/github_sync/tests/test_pipeline.py` (a processed PR leaves `DirtyDay` rows) and
  `test_sync.py` (a fixture-driven sync ends with rollup rows for the synced days and a bumped data version;
  a failed sync still bumps).
- [x] **T17: `manage.py recompute` extension.** After derive/detect/evaluate: rebuild rollups for the
  requested range (default = earliest PR day → `timeframe.today()`), then bump the version. Add
  `--rollups-only` and `--skip-rollups`; report rebuilt day and row counts.
  *Tests:* extend `apps/github_sync/tests/test_recompute_command.py` — `--from/--to` rebuilds only that range;
  `--rollups-only` runs no derive/detect/evaluate; the command bumps the data version; running it twice is
  idempotent at the row level.
- [x] **T18: `metrics_doc` and `docs/METRICS.md`.** `apps/metrics/docs.py::render_metrics_doc()` (English
  under `translation.override`), `apps/metrics/management/commands/metrics_doc.py` (writes, `--check` exits
  1 when stale), and the generated `docs/METRICS.md` committed.
  *Tests:* `apps/metrics/tests/test_metrics_doc.py` — the committed file equals `render_metrics_doc()`
  (**a stale file fails**); every registry key appears in the file; the document renders in English while the
  active language is `uk`; `--check` exits non-zero on a mutated file.
- [x] **T19: Ukrainian translations.** `make messages`, then a Ukrainian `msgstr` for every new metric title
  and description (full sentences, no concatenation), then `compilemessages`; commit `.po` and `.mo`.
  *Tests:* the existing `tests/test_translations.py` gates (no empty/fuzzy `msgstr`, placeholder parity, `.mo`
  freshness) must pass with the new strings.
- [x] **T20: Documentation.** `docs/CONFIGURATION.md` rows for `METRICS_CACHE_TTL_SECONDS` and
  `DEFAULT_PERIOD_DAYS` (and point `apps/policy/views.py`'s hard-coded 30-day default at the latter, closing
  the p06 loose end); the phase-7 section of `docs/DECISIONS.md`; a `CHANGELOG.md` entry; confirm `CLAUDE.md`'s
  command table already lists `recompute` and `metrics_doc`.
  *Tests:* extend `tests/test_docs.py` with `test_every_metric_key_is_documented` (registry keys vs
  `docs/METRICS.md`) and the settings-documentation check for the two new keys.

  **Handoff note (session 4 stopped here, context exhausted):** done so far — `docs/CONFIGURATION.md`
  got its two new `metrics` rows (`METRICS_CACHE_TTL_SECONDS`, `DEFAULT_PERIOD_DAYS`);
  `apps/policy/views.py`'s module-level `PERIOD_DAYS = 30` constant is removed and `_period()` now
  calls `get_int("DEFAULT_PERIOD_DAYS")` (closes the p06 loose end the plan names); `tests/test_docs.py`
  gained `test_every_setting_is_documented` (every `SETTING_DEFS` key vs `docs/CONFIGURATION.md` —
  a new general-purpose gate, not just the two new keys, since no such check existed before this
  phase) and `test_every_metric_key_is_documented` (registry keys vs `docs/METRICS.md`); both pass,
  and `uv run pytest apps/policy tests/test_docs.py -q` is green. **Still to do, in this order:**
  (1) the phase-7 section of `docs/DECISIONS.md` — follow the file's existing style (prose blocks
  with a **bold lede**, no `## Phase N` headers were found before the search got interrupted —
  re-check `grep -n "^#" docs/DECISIONS.md` and the phase-6 section already in the file for the
  exact convention before writing); cover at minimum: the registry/calculator-strategy design
  (kind derived from calculator type, RISKS row 7's structural guard), the `<key>__num`/`<key>__den`
  rollup storage choice, `DataVersion`/`DirtyDay` as their own models, and the four
  `.autodev/DECISIONS.md` `## p07-plan`/`## p07-implement` deviations already logged (T15-T17
  additions are logged there too — grep `^## p07` in `.autodev/DECISIONS.md` for the full set
  rather than re-deriving it); (2) a `CHANGELOG.md` entry (check the file's existing format first);
  (3) confirm (no edit expected) `CLAUDE.md`'s command table already lists `recompute` and
  `metrics_doc` — it does (`CLAUDE.md` line ~34), so this is a no-op check, not a task. **After
  T20: run the full suite once** (`uv run pytest -q`) **and the full lint gate**
  (`uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python manage.py
  makemigrations --check --dry-run && uv run python manage.py check`) — neither has been run in
  this session since T15 (only narrow, per-task test files were run); also run
  `uv run python manage.py metrics_doc --check` and `make messages && git diff --exit-code locale/`
  per the plan's own Verification block, since both files were freshly (re)generated in T18/T19 and
  must be re-confirmed fresh after any further edits. T13-T19 are otherwise complete and were each
  verified narrowly (their own test files green) during this session — no known regressions, but
  the project-wide gates above have not been re-run end-to-end since before T15.

---

## Verification

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run python manage.py makemigrations --check --dry-run && uv run python manage.py check
uv run python manage.py metrics_doc --check           # docs/METRICS.md is fresh
make messages && git diff --exit-code locale/          # .po/.mo are committed and fresh
```

| # | Acceptance criterion | Test that proves it |
|---|---|---|
| 1 | Every metric has a test over a hand-computed `factory_boy` dataset | `test_metrics_adoption.py`, `test_metrics_policy.py`, `test_metrics_flow_counters.py`, `test_metrics_flow_distributions.py`, `test_metrics_state.py`, `test_metrics_quality.py`, plus `test_registry.py::test_every_registered_metric_has_a_test` (parametrized over `REGISTRY`, asserting each key is referenced by a test module) |
| 2 | Day-boundary tests cover a PR merged at 23:59 and at 00:01 Kyiv and a DST day | `test_timeframe.py::test_day_of_splits_kyiv_midnight`, `test_day_of_on_the_dst_transition_day`; `test_metrics_flow_counters.py::test_merge_at_2359_and_0001_kyiv_land_in_different_days`, `::test_day_attribution_across_the_dst_transition` |
| 3 | No distribution-kind metric ever reaches `DailyRollup` | `test_rollups.py::test_rollup_metric_keys_are_additive_only`, `::test_write_rollups_rejects_a_non_additive_key`, `::test_rebuild_writes_no_row_for_a_distribution_metric` |
| 4 | Deleting all rollups for a range and running `recompute` reproduces identical rows | `test_rollups.py::test_rebuild_is_deterministic_after_a_full_delete`; `test_recompute_command.py::test_recompute_rebuilds_identical_rollups` |
| 5 | `compute()` reports `sample_size` so a small sample can be greyed | `test_compute.py::test_result_reports_sample_size`, `::test_below_min_sample_flips_at_min_sample` |
| 6 | A metric with no underlying data returns `None`, never `0` | `test_calculator_base.py::test_empty_sample_is_none_not_zero`; `test_compute.py::test_metric_without_data_is_none`; `test_metrics_state.py::test_wip_per_person_without_open_prs_is_none` |
| 7 | A stale `docs/METRICS.md` fails its freshness test | `test_metrics_doc.py::test_committed_metrics_doc_is_fresh`, `::test_check_flag_fails_on_a_stale_file` |
| 8 | A second `compute()` is served from cache; a bumped `last_data_version` misses | `test_compute_cache.py::test_second_identical_compute_hits_the_cache`, `::test_bumped_data_version_misses_the_cache`, `::test_two_scope_filters_do_not_share_an_entry` |

---

## Risks

- **Row 7 — metric correctness silently breaks (distribution rolled up, boundary in UTC).** The primary risk of
  this phase. Mitigated structurally, not by discipline: `kind` is derived from the calculator strategy type
  and validated at registration; `rollups.py` iterates only `CounterCalc`/`RatioCalc` defs and
  `write_rollups()` raises on anything else (T13); every boundary goes through `apps/metrics/timeframe.py`,
  which no calculator may bypass — the 23:59 / 00:01 / DST tests are in both the helper's and the counters'
  test modules (T2, T9).
- **Row 1 — a wrong number lands in a 1:1.** `sample_size` and `below_min_sample` on every result (T14); `None`
  instead of `0` enforced in `MetricValue.empty()` (T6); bots and `exclude_from_metrics` people out of every
  population and visible in `prs_excluded_from_metrics` (T4, T9); `reviewer_response_p50`'s approximation and
  `followup_fix_rate`'s heuristic status stated in their descriptions, which is what `docs/METRICS.md` and the
  ⓘ tooltip render (T7, T12, T18).
- **Row 10 — the 1.5 s budget.** Counters from rollups; rollup rows only where `sample_size > 0`, so the table
  stays proportional to activity, not to scopes × days; the version-stamped cache (T15); an `assertNumQueries`
  bound on `compute()` (T14). Profiling at 50 repos / 20 k PRs stays phase 11's task.
- **Row 15 — a generated artefact rots.** `docs/METRICS.md` gets its freshness test and a `--check` flag in the
  same task that creates it (T18).
- **Row 8 — Ukrainian lags.** ~80 new lazy strings are translated in this phase (T19), under the existing
  `.po` gates.
- **Row 3 — a restricted lead sees another project's data.** `compute()` takes a `Scope` carrying the
  `ScopeFilter`, every selector composes on it, out-of-scope ids drop to global, and the access fingerprint is
  part of the cache key so a cached admin result can never be served to a restricted lead (T4, T15).

---

## Out of scope

- **All dashboard UI** — pages, KPI rows, sparklines, chart JSON endpoints, tables, the ⓘ tooltip, `seed_demo`,
  and the CSV/XLSX export layer: phase 8.
- **MIN_SAMPLE greying and empty states in the interface** — this phase only reports `sample_size`; rendering
  it is phase 8, and the product-wide audit is phase 11.
- **`followup_fix_rate`'s heuristic** (14-day window, hotfix pattern, ≥50 % file overlap) and the churn
  pipeline (`manage.py compute_churn`, bare clones, `GIT_ASKPASS`, blame): phase 10. `churn_21d` reads
  `ChurnResult` rows here; it does not produce them.
- **Per-user project narrowing** — `scope_for_user()` stays unrestricted; phase 9 turns it on, and this phase's
  `Scope`/cache-key design is what makes that a one-place change.
- **Background/exported reports and `ExportJob`**: phase 9.
- **Index and `select_related` profiling at 50 repos / 20 k PRs**: phase 11.
