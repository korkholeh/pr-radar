# Decisions

Notable choices made while building the app, for the next person who wonders "why is it done this way?". Larger,
structural decisions get a full ADR under `docs/dev/adr/`; this file covers narrower, phase-level calls. The
complete, terse decision log the autonomous build kept as it went is `.autodev/DECISIONS.md` — this page is the
curated, human-facing subset.

## Phase 1

**`apps/dashboards` exists from phase 1**, with a single placeholder Overview page at `/`. `base.html`, the
navigation, the post-login redirect target and the e2e login spec all need one real page to point at; creating it
anywhere else would just mean moving it once phase 8 (Dashboards, charts, themes) arrives. The navigation renders
only the items whose pages already exist — a full spec-§10.1 nav with dead links would 404 instead of redirecting
an anonymous visitor to login, and would mislead an operator into clicking something that isn't built yet.

**Language persistence ignores the browser.** `Accept-Language` detection is deliberately off:
`apps.accounts.middleware.UserLanguageMiddleware` copies `UserPreference.language` into the cookie
`LocaleMiddleware` reads, so a lead's chosen language follows them to a new device rather than being silently
overridden by that device's browser locale. The middleware writes to `request.COOKIES` rather than a session key,
because this Django version (5.2.17) no longer has session-based i18n storage — `LocaleMiddleware` resolves
language from the cookie, `Accept-Language`, and `LANGUAGE_CODE` only.

**Colour literals live in exactly one file.** `static/css/tokens.css` defines every colour for both themes; a test
greps the rest of the codebase (templates, Python, `static/js/`, and CSS other than `app.css`/`vendor/`) for
`#hex`/`rgb(`/`hsl(` and fails the build on a hit. Dark mode is bound to `[data-theme="dark"]` written by the
server, not to `prefers-color-scheme`, so the three-state preference (system/light/dark) can be represented
without JavaScript owning the source of truth.

**Duration formatting has two implementations, proven equal by a shared fixture** rather than one implementation
called from both runtimes: `apps/dashboards/formatting.py::format_duration` (Python, `ngettext`-driven) and
`static/js/formatting.js::window.prRadar.formatDuration` (hand-written pluralisation, since there is no Node
build in this project). `tests/fixtures/duration_cases.json` is asserted against both — a pytest test for Python,
a Playwright spec for JS — because the project has no other JS test runtime, and the e2e browser already is one.

**No live GitHub call is possible, enforced, not just intended.** The root `conftest.py` wraps every unit/
integration test in `respx.mock(assert_all_mocked=True)`, so an unmocked outbound HTTP request raises immediately
instead of silently reaching the network. This exists before `github_sync` (phase 3) writes a single line of
client code, because the guarantee needs to hold from the first test onward, not be retrofitted.

**e2e personas are shared, persistent database rows**, not recreated per test (`manage.py seed_e2e` is
idempotent). Specs that mutate a persona's state (language, theme) restore it — through the product's own
switcher, never a direct database write — so the suite stays order-independent across repeated
`make e2e-up && pytest e2e -q; make e2e-down` cycles.

## Phase 4

**A GitHub login is always auto-mapped to a person; a bare git email never is.** A login identifies exactly one
GitHub account, so attributing it is a fact, not a guess — `apps.catalog.identity.resolve_identity()` creates a
`Person` for every new `github_login` identity it sees. A `git_email` identity is only adopted automatically when
GitHub itself links it to a login: a `…@users.noreply.github.com` address (`login_from_noreply_email()`), or a
commit where GitHub paired the email with a login author (`Commit.author_email_identity`, see below). Anything
else lands in Settings → People → **Unmapped identities** for a lead to resolve.

**An identity, once mapped, is never re-pointed by a sync**, and a bot flag is written only when a `Person` is
auto-created (or explicitly by a lead, from the UI). A lead's assignment — and a lead's "not a bot" correction —
is operator-owned data; a re-sync or `recompute` must not silently undo it.

**`activity.Commit` carries an auxiliary FK, `author_email_identity`**, next to the existing `author_identity`.
`upserts.py` now materialises the commit-author *email* identity even when a login is also present, so GitHub's
own email↔login pairing survives into post-processing instead of being discarded by the old `login or email`
fallback. This is the one place phase 4 adds a column the architecture's model set didn't list; spec §4 allows
auxiliary fields for exactly this kind of bookkeeping.

**Every cached PR field `metrics.compute()` (phase 7) will read is derived once, by `apps.activity.derive`, from
stored rows only — never from a live GitHub call.** Re-running derive over the same rows is a no-op; a re-sync
that changes a row causes the next derive pass to update the field, and nothing else. An absent input yields
`None`, never `0`.

| Field | Rule |
|---|---|
| `PRFile.is_excluded` (`is_excluded`) / `PRFile.is_test` (`is_test`) | path matched against `EXCLUDED_PATH_GLOBS` / `TEST_PATH_GLOBS` (`apps.catalog.globs`) |
| `effective_additions` / `effective_deletions` | sum over non-excluded files; `None` when the PR has no file rows at all |
| `has_test_changes` | any non-excluded file flagged `is_test` |
| `size_bucket` | effective lines against `PR_SIZE_BUCKETS` (XS/S/M/L/XL); `None` when effective lines are `None` |
| `ready_for_review_at` | the stored timeline value; else `None` while still a draft; else `created_at` |
| `first_commit_at` | earliest commit `authored_at` (or `committed_at`) |
| `first_review_at` | earliest review or review-comment by a non-author, non-bot identity |
| `first_approval_at` | earliest `APPROVED` review by a non-author, non-bot identity |
| `last_activity_at` | latest of creation, update, merge, close, last commit, last review, last comment |
| `review_rounds` | count of `CHANGES_REQUESTED` reviews by a non-author, plus one |
| `commits_after_first_review` | commits committed after `first_review_at`; `None` when there was no review |
| `is_rubber_stamp` | large PR, approved in under `RUBBER_STAMP_MAX_MINUTES` from `ready_for_review_at` (strict `<` — exactly the boundary does **not** count), with no review comment and no review body |
| `is_self_merged` | `merged_by` resolves to the same `Person` as `author` |
| `is_hotfix` | title, branch prefix (`hotfix/`, `fix/`) or a `hotfix` label |
| `is_revert` / `reverts_pr` | title/body matches a revert pattern; the target PR is resolved by issue reference, commit sha or matching merged title — unresolved still sets `is_revert=True` with `reverts_pr=None`, never a guess |

`merge_method` is deliberately **not** derived in this phase — it stays `unknown` until the churn phase (10),
which owns the git-clone evidence (commit parent counts) needed to tell a merge commit from a squash or a
rebase. No metric before then reads it, and guessing it from the GraphQL payload alone would write a wrong value
into a cached field.

## Phase 5

**Detection is a third pipeline step, in the same idempotent shape as `derive`.** After `derive_pull_request()`,
`apps.ai_detection.services.detect_pull_request()` matches every active, admin-editable `DetectionRule` against
the PR's stored rows and writes/deletes `AISignal` rows so the stored set equals the wanted set exactly — a
wanted-vs-existing diff, not an append. `AISignal` carries `evidence_hash` (a sha256 of the evidence text) under
`UniqueConstraint(pull_request, rule, evidence_hash)`, so re-running detection twice never duplicates a row and
deactivating a rule (or editing its pattern so it no longer matches) removes its signals on the very next run.

**Eight detectors, each a pure function of `(compiled pattern, DetectionContext)`:** `commit_trailer`,
`commit_author`, `pr_author`, `pr_body_footer`, `html_comment`, `label`, `branch_pattern`, `commit_message`
(`apps/ai_detection/detectors.py`, keyed by `Detector`'s exact values, one detector per `Detector.choices`
entry, checked by test so the registry can't silently fall short). `pr_body_footer` and `html_comment` read the
same PR body through two different lenses on purpose: a marker inside an HTML comment is invisible to a human
reviewer and must be attributable to its own rule and confidence rather than merged into the visible-text
detector. Every `AISignal.evidence` is truncated to `EVIDENCE_MAX_LENGTH` (200 chars), centred on the match, so
it always fits the field and is still readable on the PR page.

**The disclosure parser (`apps/ai_detection/disclosure.py`) is deliberately tolerant, not strict**, because it
reads free-form Markdown a human filled in, not a fixed form: the heading, the three checkbox labels and the
tools-line label are all configurable settings (`DISCLOSURE_SECTION_HEADINGS`, `DISCLOSURE_LABELS_NONE`/
`_PARTIAL`/`_SUBSTANTIAL`, `DISCLOSURE_TOOLS_LABELS`, `DISCLOSURE_TOOL_ALIASES`; see `docs/CONFIGURATION.md`),
matched case-insensitively and by label prefix. Two ticked boxes resolve to `ambiguous` even if they happen to
agree, because two ticks means the author did not answer the question as asked. No matching section at all, or
an empty PR body, resolves to `missing` — never silently to "no AI used". `docs/pull_request_template.md` ships
the recommended template matching these defaults verbatim, and a test parses it both unticked and with the
`Substantial` box ticked so the doc and the parser can never drift apart.

**`resolve_ai_status()` implements spec §6.3 as a pure function of two arguments** (the set of signal
confidences and the resolved disclosure), so all five outcomes are unit-tested without touching the database:
`ai_explicit` (any `high`-confidence signal, regardless of what disclosure says — the mismatch case phase 6
turns into a `DISCLOSURE_MISMATCH` violation), `ai_disclosed` (disclosure `partial`/`substantial`, no high
signal), `ai_suspected` (any signal at all, lower confidence, no disclosure), `no_ai` (no signal, disclosure
`none`), `unknown` (nothing rules it either way — including an `ambiguous` disclosure with no signals). A
missing/ambiguous disclosure is `unknown`, never `no_ai`, so a lead never sees a false negative on the AI
cohort. `ai_suspected` is excluded from the AI cohort by default (`AI_COHORT_INCLUDE_SUSPECTED=False`).

**`DetectionRule` is operator-owned runtime data, not a release artefact.** `notes` carries a mandatory source
comment; `name` is unique so `manage.py seed_detection_rules` can `get_or_create` by name without shadowing; a
non-compiling `pattern` is rejected at the model's `clean()` — surfaced as a visible form error in the settings
UI — rather than silently skipped at detection time (a rule already saved with a bad pattern, e.g. edited
outside the UI, is skipped with a `logger.warning` naming the rule, so one broken rule never stops every other
rule from running). `AISignal.rule` is `PROTECT`, so a rule with signals cannot be deleted — the UI offers
**deactivate**, which removes its signals on the next `detect`/`recompute` run, instead. The seed set
(`fixtures/detection_rules.yaml`, loaded by `apps/ai_detection/rules.py`) covers all eight non-`other` `Tool`
values (Claude Code, Copilot, Cursor, Codex, Devin, Gemini, Aider, Windsurf), each with a sourced `notes` field;
disputed rules ship at `confidence: low` rather than `high`, since only `high` can push a PR to `ai_explicit`.

**The settings page's dry run never touches `AISignal`.** `services.dry_run_rule(rule, scope, limit)` runs the
rule's single detector over the last `DETECTION_DRY_RUN_PR_COUNT` PRs in the caller's scope and returns matches
in memory; it accepts an unsaved `DetectionRule` instance so an admin can test a pattern before saving it, and
raises `ValueError` for a pattern that fails to compile (the settings form also validates this itself, so the
common path is a visible field error, not an exception).

**`manage.py recompute` and a minimal PR detail page land in this phase instead of their originally planned
phases** (7 and 9 respectively) — both were the smallest concrete way to satisfy this phase's own acceptance
criteria ("re-running detection over stored PRs", "evidence visible on the PR page") without inventing a
throwaway harness. `recompute` runs `derive_pull_requests()` then `detect_pull_requests()` over a
`--from`/`--to`/`--repo`/`--project`-filtered queryset and makes no GitHub call, asserted by the same
unmocked-request guard every other test runs under; policy evaluation (phase 6) and rollup rebuilding (phase 7)
extend the same command later. The PR detail page (`dashboards:pull_request_detail`) shows only the AI section
(status, disclosure, tools, the signal list with evidence); phase 9 extends the same URL and template with the
timeline, metrics, violations and churn.

`Detector` values: `commit_trailer`, `commit_author`, `pr_author`, `pr_body_footer`, `html_comment`, `label`,
`branch_pattern`, `commit_message`. `AIStatus` values: `ai_explicit`, `ai_disclosed`, `ai_suspected`, `no_ai`,
`unknown`. `AIDisclosure` values: `none`, `partial`, `substantial`, `missing`, `ambiguous`.

## Phase 6

**Policy evaluation is the pipeline's fourth stage, in the same wanted-vs-existing diff shape as detection.**
`apps.policy.services.evaluate_pull_request()` runs right after `detect_pull_request()`, computes the exact set
of violations a PR's current state should have (one `Finding` per firing rule, from the nine pure evaluators in
`apps/policy/rules.py`), and diffs it against what's already stored, keyed by `(pull_request, rule_code,
details_hash)` where `details_hash` is computed only from a finding's declared `identity_params` — not its whole
`details_params`. This is the one deliberate difference from detection's diff: instead of deleting a row whose
key disappeared, policy **auto-resolves** it (`status=resolved`, `resolved_automatically=True`,
`resolved_by=None`, `AuditEntry(actor=None)`), because a violation is a judgement a lead may already have acted
on, and a delete would silently erase that judgement along with the row. The queryset that auto-resolves is
filtered `status=open`, so an `acknowledged`/`waived`/already-`resolved` row is never touched and a `resolved`
row is never reopened even if its condition returns — recurrence stays visible in the audit trail instead of
resurrecting the old row. See `docs/POLICY.md` for the full nine-rule reference; full detail is in
`.autodev/phases/06-policy-engine/PLAN.md`'s Design section.

**Two hard gates run before any of the nine evaluators.** A PR is judged under the `AIPolicy` version in effect
**at its own `created_at`** — the newest version whose `effective_from` was already in the past at that moment —
never against the newest version overall. Picking the newest version unconditionally was a review-caught bug:
every existing PR predates a freshly-saved version's `effective_from`, so the wanted set would go empty for the
whole database and auto-resolve every open violation the first time an admin changed a policy field. With no
policy in effect at a PR's `created_at` at all, the wanted set is empty, so it produces no violation and any
violation still `open` on it auto-resolves; this is what makes backdating a stricter policy non-retroactive.
A code in `MERGE_DEPENDENT` (`NO_HUMAN_APPROVAL`, `SELF_MERGE`) is skipped unless the PR is merged, so an open PR
never carries a merge-dependent violation. A code listed in the `POLICY_DISABLED_RULES` setting is skipped the
same way as a gate failure, so switching a rule off auto-resolves its open rows on the very next run rather than
leaving them stranded.

**`AIPolicy` is a versioned singleton, never edited in place.** Saving the settings form always inserts a new
row with `effective_from` stamped to the moment it is saved (`services.save_policy_version`) — the field is not
exposed on `AIPolicyForm`, so an admin can neither backdate a version over already-judged PRs nor schedule one
for the future with no marker in the history list. `effective_from` is unique so two versions can never tie and
make `current_policy()` order-dependent. History is therefore append-only and auditable
(`AuditEntry(action="ai_policy.update")` with the previous version's values as `before`) without a separate
history table.

**Sensitive-path matching writes the column phase 2 defined but left empty.**
`services.match_sensitive_paths(pr, sensitive_rules)` walks a PR's non-excluded files against every active,
in-scope `SensitivePathRule` (global plus the PR's projects) in `Meta.ordering` order and writes
`PRFile.matched_sensitive_rule` with one `bulk_update`, re-deriving the file list (not the rules) on every run so
a deactivated or edited rule clears or moves the mark on the next pass. The two sensitive-path rules
(`SENSITIVE_PATH_FORBIDDEN`, `SENSITIVE_PATH_REVIEW`) then read the marks the same run just wrote, so the console
and a PR's file list can never disagree about which rule matched.

**`details_params` carries data only, never prose.** `apps/policy/messages.py::render_violation(rule_code,
params)` turns a stored code and its parameters into a full, `ngettext`-pluralised sentence at read time, in the
reader's language; an unknown `rule_code` renders the raw code instead of raising, so a stale row from a removed
rule can never 500 the console. This is asserted two ways in `test_details_params.py`: a schema check that every
evaluator's declared `PARAM_SCHEMA` keys and value types (`str | int | float | bool | list[str]`) match what's
actually stored, across all nine rules fired at once, and a prose check that no stored value contains a word from
any rule's message template — so a future evaluator can't smuggle a rendered sentence into the JSON by accident.

**Compliance KPIs and the by-rule chart live in `apps/policy/selectors.py`, not `metrics.compute()` — logged
deviation, corrected in phase 7.** The metrics registry doesn't exist until phase 7, so `violations_in_scope`,
`violations_by_rule`, `compliance_kpis` and `disclosure_mismatch_pull_requests` are this phase's read entry point
for the Policy console, each starting from `scope_for_user()` like every other selector in the tree. Phase 7
registers `violations_open`/`violations_new`/`violations_by_rule` as metrics with calculators that call these
same functions rather than rewriting the queries. `compliance_kpis`' rate is `None` (never `0`) with an empty
AI-PR denominator, and is greyed in the template below `MIN_SAMPLE` — the same convention every other metric in
the codebase follows.

**The by-rule chart is server-rendered bars plus a data table, not Chart.js — logged deviation, corrected in
phase 8.** Vendoring Chart.js and `static/js/charts.js` for one bar chart would duplicate a phase-8 deliverable;
`selectors.violations_by_rule()` already returns exactly the series a phase-8 JSON endpoint will serialise, and
the text-alternative table satisfies the "every chart needs an accessible alternative" rule in the meantime.

**The violation table is a plain form + `Paginator`, not `django-tables2`/`django-filter` — logged deviation,
corrected in phase 8.** Both packages are installed but otherwise unused anywhere in the tree; phase 8 adopts
them for the sortable, exportable table this one will be replaced by. `ViolationFilterForm` drops an unknown or
out-of-scope filter id silently rather than 403ing (RISKS row 3's rule for this phase), and the bulk-action form
validates every selected id against `violations_in_scope` the same way.

**The `REPORT_TIMEZONE` day-boundary helper moves to `apps/metrics/timeframe.py` now, a phase early.** It was a
private, undocumented `_day_start()` inside `manage.py recompute` (phase 5); this phase needed the same Kyiv-day
logic for the console's KPI period and extracted it rather than writing a second copy, so phase 7's metrics
registry inherits a tested helper (including a DST-transition test) instead of writing the first one.

`PolicyViolation.RuleCode` values: `DISCLOSURE_MISSING`, `DISCLOSURE_MISMATCH`, `TOOL_NOT_ALLOWED`,
`SENSITIVE_PATH_FORBIDDEN`, `SENSITIVE_PATH_REVIEW`, `NO_HUMAN_APPROVAL`, `SELF_MERGE`, `NO_TESTS`,
`AI_PR_TOO_LARGE`. `PolicyViolation.Status` values: `open`, `acknowledged`, `waived`, `resolved`.
`SensitivePathRule.AiMode` values: `forbidden`, `needs_extra_review`. See `docs/POLICY.md` for the severity of
each rule, which `tests/test_docs.py::test_policy_severity_table_matches_the_code` pins against
`apps.policy.rules.SEVERITY`.

## Phase 7

**The registry's `kind` is derived from the calculator's strategy type, not a hand-set string.** `MetricDef`
holds one of four frozen strategy dataclasses — `CounterCalc`, `RatioCalc`, `DistributionCalc`, `StateCalc` —
and `_register()` validates `kind` against the strategy's actual class at import time, alongside a unique key,
`levels ⊆ ScopeType.values`, lazy `title`/`description`, and an allowed `unit`/`direction`. This turns the
phase's central risk (a distribution-kind metric silently rolled up into `DailyRollup`) into something the
type system and `rollups.py` refuse rather than something a reviewer has to catch: `rollups.write_rollups()`
iterates only `CounterCalc`/`RatioCalc` defs and raises `NonAdditiveMetricError` on anything else. Three
metrics that read like plain counts — `ai_active_people` (a distinct-people count, not summable across days),
`review_load_share` (top-N reviewers selected over the whole period), `wip_per_person` (a point-in-time
snapshot) — are registered as `distribution`/`state` for exactly this reason, and `docs/METRICS.md` states it.

**A ratio's numerator and denominator are stored as two `DailyRollup` rows under reserved keys
`"<key>__num"`/`"<key>__den"`, divided only at read time; the ratio itself is never a stored value.** Several
components (`ai_prs_merged`, `review_rounds_sum`, `test_lines`) are not metrics anyone reads directly, and
registering them as their own `MetricDef`s would put internal keys into `docs/METRICS.md`, the ⓘ tooltip and
the XLSX glossary. `ratio_value()` treats a missing (`None`) numerator as `0.0` once a denominator exists,
because `count_value()` (see below) already turns "zero matches" into "no rollup row", so a real-activity
period with zero numerator hits must read as `0`, not `None`.

**A `DailyRollup` row is written only when `sample_size > 0`; `count_value()` in `calculators/base.py`
enforces the same "no data → `None`, never `0`" rule for every counter and state calculator, not just ratios
and distributions.** An absent row is the only representation of "no activity that day", which keeps the
table proportional to actual activity (not scopes × cohorts × metrics × days) and lets `compute()` sum
existing rows to get the right answer without a separate "was this really zero" flag.

**`ci_first_pass_rate` and `churn_21d` are implemented in this phase, ahead of the roadmap's assumption that
only `followup_fix_rate` would be deferred.** Their inputs — `CheckStatus.is_first_ci_commit` and
`ChurnResult.churn_ratio` — are already stored by earlier phases, so each calculator is a short ratio or
median over existing rows. `followup_fix_rate` alone ships as a registered `RatioCalc` returning
`MetricValue.empty()`, its `(heuristic)` description already carrying the caveat, because its 14-day /
≥50%-file-overlap logic is phase 10's own deliverable.

**`DataVersion` and `DirtyDay` are their own models, not an `AppSetting` or a cache entry.**
`metrics.DataVersion(id=1, version, updated_at)` is a singleton row bumped with an atomic `F("version") + 1`;
it must be shared between the web process and the huey worker and must survive deleting `DATA_DIR/cache/`,
which the architecture declares safe to wipe at any time — neither an `AppSetting` (operator-facing
configuration with its own settings UI) nor a plain cache entry (reset by that same wipe) fits.
`metrics.DirtyDay(date unique)` materialises ADR 0007's "dirty-days" set: `github_sync/pipeline.py` calls
`metrics.mark_dirty(pr)` after every derive→detect→evaluate chain, recording every Kyiv day the PR could have
touched (`created_at`, `merged_at`, `closed_at`, each review's `submitted_at`), and `run_sync()` calls
`rollups.rebuild_dirty()` then `bump_data_version()` before returning — on both the success and the failure
path, so an incremental sync rewrites only the days it touched and a rollup bug is as visible as any other
sync bug. `manage.py recompute` calls the same pair, with `--rollups-only`/`--skip-rollups` to decouple the
rollup rebuild from derive/detect/evaluate.

**The metric cache key embeds an access fingerprint from the caller's `ScopeFilter`** (`"*"` when
unrestricted, else the sorted `project_ids`) **alongside `last_data_version`.** `scope_for_user()` stays
unrestricted until phase 9, but the cache key is already shaped so that once per-project narrowing turns on,
an admin's cached result can never be served to a restricted lead — the exact failure the codebase's
authorization choke point exists to prevent, reached here through a cache instead of a missing `scope_for_user()`
call. `compute()` is a read-through wrapper: a cache hit costs zero queries, including the `last_data_version`
lookup itself, which is cached under its own fixed key in the same `FileBasedCache` `bump_data_version()`
writes to.

Two new `metrics`-group settings, `METRICS_CACHE_TTL_SECONDS` (3600) and `DEFAULT_PERIOD_DAYS` (30); the
latter replaces `apps/policy/views.py`'s hard-coded 30-day console default, closing the deferral phase 6 logged.
`docs/METRICS.md` is generated by `apps/metrics/docs.py::render_metrics_doc()` (rendered in English under
`translation.override("en")` regardless of the active UI language, per CLAUDE.md) and gated by a freshness
test the same way `app.css` and the `.po`/`.mo` files already are; `manage.py metrics_doc --check` exits
non-zero on a stale file. Full detail, including the calculator strategy signatures and the storage rules by
kind, is in `.autodev/phases/07-metrics-registry/PLAN.md`'s Design section and the `## p07-plan`/
`## p07-implement` entries of `.autodev/DECISIONS.md`.

## Phase 8

**`compute_many()` is an additive batching wrapper inside `apps/metrics`, not a second read entry point.**
A table of dozens of rows × six metrics must not cost one `compute()` call, and one `DailyRollup` query, per
row. `compute_many(metric_keys, scope_type, scope_ids, access, date_from, date_to, cohort, granularity)`
batches counter/ratio metrics for an unrestricted caller into a single `DailyRollup` query with
`scope_id__in=scope_ids`, reusing the existing per-scope arithmetic; distribution/state metrics, and every
metric for a restricted `ScopeFilter`, fall back to the existing per-scope path rather than trying to be
clever about percentiles across scopes. `dashboards/` still never touches `DailyRollup` or a domain model
directly — every number reaches a template through `apps/metrics`. A contract test asserts
`compute_many(...)[id]` equals a direct `compute()` call, field by field, for every scope.

**Chart JSON and KPI cards call the same `compute()` (or `compute_many()`) with the same `DashboardParams`,
so they cannot disagree.** Colours never reach the browser as literals: a `ChartPayload` dataset carries a
`color_token` (e.g. `--series-ai`), resolved client-side with `getComputedStyle` and re-resolved on a
`themechange` event, which is what keeps `tests/test_no_hardcoded_colors.py` green for a Chart.js config built
entirely in JS. The same discipline applies to server-rendered KPI-card delta colours: `direction_class()`
returns one of three whole literal Tailwind class strings from a fixed dict rather than building one by
f-string interpolation — Tailwind's static scanner only sees literal strings in source, so a dynamically
assembled class name is invisible to it and silently ships unstyled markup for whichever value no other
template happens to spell out literally. This phase's `make css` run caught exactly that: the `neutral` delta
colour had no generated CSS rule anywhere in the codebase until the fix, since no other template's literal
`text-[var(--bad)]`/`text-[var(--good)]` usage happened to cover it too.

**All query-string state lives in `DashboardParams`, parsed by a lenient form that never raises on bad
input.** An unknown preset, a malformed date, an out-of-scope project id, or a bad granularity falls back to a
default instead of a validation error, matching `apps/policy/forms.py`'s existing pattern — the query string
is a link a lead shares, not a form a lead fills in, so it must degrade gracefully rather than 400. This is
also what makes "the query string alone restores the view" testable as a round trip:
`parse(params.to_query_dict()) == params`.

**A repository in two projects contributes its full activity to each project but is counted once globally** —
proved end to end by `test_scope_aggregation.py`, which found a missing `rollups.rebuild()` call in its first
draft: counter/ratio metrics read from `DailyRollup`, not live from `PullRequest`, so a test that only creates
PR rows and never rebuilds rollups sees `None` at every scope and passes its equality assertion vacuously.

**Every `AppSetting` read is cached as one dict in the shared `FileBasedCache`, invalidated by
`set_setting()`.** Profiling this phase's `assertNumQueries` tests found `catalog_appsetting` alone was 108 of
364 queries on a 2-person Overview render — `MIN_SAMPLE`, `STALE_DAYS`, `WAITING_REVIEW_HOURS`, `DURATION_MODE`
etc. are read once per metric per series bucket across KPI rows, charts and tables. A process-wide dict would
leak an `AppSetting` created in one test into the next; the existing `_metrics_cache` autouse fixture already
gives every test a fresh, isolated `FileBasedCache`, so caching there is isolated for free and stays correct
across the huey worker and the web process alike.

Full detail — the six chart specs, the `ExportColumn`/`TABLE_SPECS` shape, the XLSX formatting rules, and the
`seed_demo` design — is in `.autodev/phases/08-dashboards-and-charts/PLAN.md`'s Design section and the
`## p08-plan`/`## p08-implement` entries of `.autodev/DECISIONS.md`.

## Phase 9

**`scope_for_user()` became a real grant list in one change, not a gradual rollout.** Every domain selector
already composed on `ScopeFilter` from phase 1 onward (`projects_in_scope`, `pull_requests_in_scope`,
`violations_in_scope`, `scoped_pull_requests`/`scoped_reviews`/`scoped_violations`, …) — the stub that always
returned `unrestricted=True` was the only thing making the restriction inert. Flipping it to a real
`UserProjectAccess` grant list therefore activated scoping everywhere at once: pages, chart JSON, CSV/XLSX
exports and the report all narrow through the same choke point, and `tests/test_scope_isolation.py` proves
each exit separately with a positive control, so a page that happened to return nothing could not be mistaken
for "isolated". A user with **no** grant rows sees everything (a grant list, not a role) — a superuser with
grant rows is restricted like anyone else, which is the spec's rule, not an oversight.

**A restricted lead's PR-page violation action posts to its own view, not the Policy console's.**
`policy:violation_bulk_action`'s htmx branch always re-renders the console's own filtered/paginated table, which
has no notion of "this one PR" — reusing it from the PR page would have swapped the PR's violations block with
console-wide content. `dashboards:pull_request_violation_action` calls the same
`policy.services.apply_bulk_status_change()` and the same form/wording, so behaviour and the audit trail are
identical; it binds `violation_ids` to `violations_for_pull_request(scope, pk)`, so a violation id from another
PR is a validation error, not silently applied.

**A per-team comparison baseline on the Person page is deferred, not built.** The page compares a person against
their primary project (the project with most of their PRs in the period) and the whole organisation — both real
medians over raw rows at that level, never a median of per-person medians (ADR 0007). A per-team baseline would
need a `team` dimension threaded through `Scope`/`DailyRollup`, which is a bigger structural change than this
phase's read-surface work; `Person.team` is shown on the page as a label only.

**`ExportJob.file`'s storage is a callable, not a bare `FileSystemStorage` instance.** Django's migration
serializer bakes an instantiated storage's `location` kwarg into the migration file as a literal absolute path
(this machine's `DATA_DIR`), which would break `makemigrations --check --dry-run` and the file's real location
on every other machine. A zero-argument callable that reads `settings.DATA_DIR` at call time is instead recorded
as an import reference and re-evaluated wherever the app runs — table exports and reports keep writing under
`DATA_DIR/exports/`, never `MEDIA_ROOT`, so no URL can serve a file directly, only the author-only download view.

**A background export job re-resolves `scope_for_user(job.user)` and re-parses the query string at run time,
not at enqueue time.** `ExportJob.params` stores the canonical query string plus `scope_type`/`scope_id`/
`table_key`/`fmt` — enough to rebuild the `Scope` from scratch — rather than a pre-resolved row set, so a grant
revoked between enqueue and run is honoured by the time the huey worker (or `manage.py process_exports`, for
when no worker is running) actually processes it (RISKS row 3). The task always leaves the job in a terminal
state: any exception inside `run_export_job()` is caught and recorded as `status=FAILED, error_code="failed"`
rather than propagating, since an unhandled exception in a background task has no request to surface it to.

**The report's Violations sheet reads `metrics.selectors.scoped_violations(scope)`, not
`policy.selectors.violations_in_scope(scope.access)`.** The latter applies only the caller's *access* filter,
not the report's own project/repository/person narrowing — a project- or person-scoped report would otherwise
list every visible project's violations alongside its own, the one place in the workbook that didn't match what
the rest of the sheets (and the page itself) show. Every other sheet already went through a `scope`-aware
selector (`compute()` for Summary, `scoped_pull_requests`-backed builders for the PRs/table sheets); Violations
now does too.

Full detail — the `PRFilters` shape, the timeline/per-PR-metrics split from `metrics.compute()`, the reviewer
heat map's "Other" fold and heat-level tokens, and the seven-sheet report's sheet-by-sheet design — is in
`.autodev/phases/09-people-prs-and-access/PLAN.md`'s Design section and the `## p09-*` entries of
`.autodev/DECISIONS.md`.

## Phase 10

**A PR with zero attributable lines gets a settled `ChurnResult` row, not no row at all.** Spec §9.6 reads
literally as "write no row"; the first implementation did exactly that, and it turned out to mean
`eligible_pull_requests` would treat the PR as still-eligible forever, re-cloning and re-blaming it on every
future nightly run. The shipped behaviour instead writes `status=ok, lines_at_merge=0, churn_ratio=None` —
settled, so it is never retried, and `churn_ratio=None` so `churn_21d`'s median (which already drops `None`
values) and the PR detail page never show a fabricated `0%`. This is a deliberate, twice-reviewed deviation
from the spec's literal text that still honours its intent (never a made-up ratio).

**Churn's rules live in `apps/churn/services.py`, not spec §9's parenthetical `churn/service.py`.** Every other
app in this codebase puts its rules in `services.py`; the spec's parenthetical is a naming hint, not a contract,
and one app spelled differently would be a permanent inconsistency for no benefit.

**`followup_fix_rate` is a derived boolean cached at sync time (`PullRequest.has_followup_fix`,
`apps/activity/followup.py`), not a join computed inside the metric's rollup.** The metric is a `RatioCalc`, so
its batch function runs once per day per scope per cohort inside `rollups.rebuild()`; computing the overlap
there would mean a `PRFile`×`PRFile` self-join on `path`, the one genuinely quadratic query the metrics registry
would otherwise contain. The heuristic reuses `PullRequest.is_hotfix` (phase 4's title/branch pattern) rather
than a second regex, and its overlap is `|paths(A) ∩ paths(B)| / |paths(A)|` — anchored on the *original* PR's
own files, so the claim is "half of what this PR touched got fixed again," not diluted by the size of whatever
fixed it.

**Churn is the one component that shells out to `git` and touches the filesystem at scale, so it gets its own
concurrency and credential rules.** Git work (clone/fetch/blame) runs in a `ThreadPoolExecutor` across
repositories, but every `ChurnResult` write happens on the main thread after a future resolves — the single
writer that keeps SQLite's write-contention risk out of the most parallel component in the system. The token
reaches `git` only through `GIT_ASKPASS` and two process-only environment variables (ADR 0004); the same run
also sets `GIT_CONFIG_NOSYSTEM=1`/`GIT_CONFIG_GLOBAL=/dev/null` so a developer's own credential helper is never
offered the token to store. `CHURN_MAX_WORKERS`, `CHURN_GIT_TIMEOUT_SECONDS` and `CHURN_REPO_TIME_BUDGET_SECONDS`
are read once per run on the main thread and passed down explicitly, never read fresh inside a worker thread —
an early version's per-thread `get_int()` calls could each open their own SQLite connection concurrently, the
exact contention the single-writer design exists to avoid.

**A rebase merge has no churn number, by design, not by omission.** GitHub does not keep a rebased PR's
pre-rebase commit history around long enough to reconstruct its original line set, so `merge_method == rebase`
is `unsupported_merge_method` rather than an approximation. `merge_method == unknown` (GitHub's GraphQL leaves
this absent often enough to matter) is instead resolved by asking `git` directly — a merge commit's parent count
— rather than being treated as unsupported, since dropping it would erase most of the metric.

Full detail — the churn algorithm, the credential handoff, the test fixture that builds a real temporary git
repository instead of a JSON fixture, and every review-round fix — is in
`.autodev/phases/10-ci-and-churn/PLAN.md`'s Design section and the `## p10-*` entries of `.autodev/DECISIONS.md`.

## Post-1.0 fixes

**Repository discovery lists everything a token can read, not what its account owns.** A tech lead usually
tracks repositories owned by a client or by an organization they merely belong to; access is the condition
worth checking, ownership is not. Discovery therefore always runs `VIEWER_REPOSITORIES_QUERY` with both
`affiliations` and `ownerAffiliations` widened to `[OWNER, COLLABORATOR, ORGANIZATION_MEMBER]`, and the
owner-scoped query it used whenever a connection carried an `owner_login` is gone. `owner_login` survives as a
label on the connection (and for the fine-grained token that was issued per resource owner) but no longer
filters anything; the discovery page groups by owner as before and offers an **Owner** dropdown, defaulting to
all owners, when more than one shows up. The previous behaviour hid a client's repositories entirely and looked
like an empty list rather than a filter.

**A token that can see no repository verifies as `degraded`, not `ok`.** `REPOS_VISIBLE` with `count: 0` was
recorded as a passing check, so the only symptom of a token without repository access was an empty discovery
page. Zero visible repositories is now its own code, `REPOS_VISIBLE_NONE`, with the hint that names the two
usual causes (repositories not selected on a fine-grained token, SSO not authorized).

**A backfill is an ordinary sync with the watermark forced back, not a second code path.** The Sync page's
**Load historical data** panel resolves its preset window or explicit date into one `since` date and hands it to
the same `run_sync(since=...)` that `manage.py sync --since` already used, through a huey task that swallows
`SyncAlreadyRunning` exactly like `sync_task`. So a backfill inherits the global lock, the per-connection rate
budgets, the per-PR transaction and the rollup rebuild for free, and it cannot race an incremental sync. The
alternative — a dedicated backfill pipeline that paginates backwards — would have needed its own lock story and
its own error containment for no behavioural gain, since `pullRequests(orderBy: UPDATED_AT DESC)` already walks
history in the direction a backfill wants.

`SyncRun` gained `since` so the run list can say what a run actually asked for (the **Since** column) and a
`backfill` trigger so it is distinguishable from **Sync now**. The date is stored as the UTC instant that
`day_start()` produces from the operator's Kyiv calendar day, and rendered back through `day_of()` — storing the
raw date would have made "from the 5th" mean 03:00 on the 5th Kyiv time for every user east of UTC.

A backfill deliberately does **not** widen `Repository.sync_since`. That field is the discovery-time backfill
floor a later `--full` starts from; moving it on every ad-hoc backfill would silently turn a one-off look at old
data into a permanent, ever-growing full-sync window.

## UI redesign

**The UI is built from component classes, not ad-hoc utilities.** `static/css/src/input.css` defines a small
vocabulary — `.card`, `.btn`/`.btn-primary`/`.btn-secondary`, `.input`/`.select`/`.textarea`/`.label`, `.badge`,
`.data-table`/`.table-wrap`, `.nav-link`, `.pager`, `.filter-grid`, `.form-stack`, `.page-title` — and templates
compose those instead of repeating long utility strings. Two reasons: a visual change is then one edit rather
than 80, and the classes hide no colour literal, because each one is written in terms of the tokens. The base
layer also styles bare `<input>`/`<select>/<textarea>` elements, since most controls in this app are rendered by
Django forms and carry no class of their own. `@apply` cannot reference another component class in Tailwind v4,
so a composite like `.filter-grid` spells its own declarations out.

## Read-only guarantee

**The read-only promise is enforced on the GraphQL document, not on the HTTP verb.** PR Radar reads GitHub and
never writes to it, but GraphQL sends a read and a write over the same `POST /graphql`, so the request carries
nothing that could be checked. `GitHubClient.graphql()` therefore runs `assert_read_only()` over the document
before it leaves: every operation definition must be a `query`, and a `mutation` or `subscription` keyword raises
`GitHubWriteAttemptError` without a request being made. REST needs no equivalent — `rest_get()` is the only REST
entry point and hardcodes `GET`.

The check is deliberately blunt and line-based rather than a real GraphQL parse: it also rejects the legal
anonymous `{ viewer { login } }` shorthand, which no document in `queries.py` uses. A guard whose job is to fail
closed should reject a shape it cannot confidently classify, and pulling in a GraphQL parser to be permissive
about a form the project does not use would be the wrong trade.

`apps/github_sync/tests/test_read_only.py` pins both halves: it walks the documents in `queries.py` by reflection,
so a document added later is covered without anyone remembering to list it, and it asserts the public surface of
`GitHubClient` is exactly `graphql`/`rest_get`/`paginate` — a `rest_post()` added beside them would be a new write
path that no other test would notice.

**Filters live in a right-hand drawer, and the page says what is applied.** Every filtered page (the dashboards, the
Pull requests, Reviews and index pages, the Policy console) keeps its filter form in a `.drawer` panel that
slides in from the right and is closed until the page's **Filters** button opens it. The form itself is
unchanged — the same field names, the same `hx-get` to the current path with `hx-push-url` — so the query
string stays the single source of filter state. The drawer is rendered outside the region htmx swaps, so
applying a filter never re-renders the panel under the reader's hands; `static/js/filters.js` closes it on
submit. Because the controls are now hidden by default, `filter_toolbar.html` renders a strip of badges (the
period, the cohort, how many projects or repositories are selected) *inside* the swapped fragment, so it is
always redrawn together with the numbers it describes.

**Date fields are text inputs with a calendar drawn by the app.** A native `<input type="date">` opens a popup
that no stylesheet can reach, so it cannot follow the app's tokens, its dark theme or its language. Every date
field is therefore a plain text input holding an ISO date (`config.forms.date_widget()` for Django-rendered
fields, `data-datepicker` in hand-written templates) and `static/js/datepicker.js` draws the calendar from the
`.datepicker-*` classes. The submitted string is the same `YYYY-MM-DD` the native control would have sent, and
Django's `DateField` accepts ISO input in every locale (the `uk` locale's own `DATE_INPUT_FORMATS` does not list
it, but `BaseTemporalField` falls back to `date.fromisoformat`), so nothing downstream changed and a page whose
JavaScript failed still takes a typed date. Month and weekday names come from `Intl.DateTimeFormat` with the
document's `lang`; the six UI strings go through the `djangojs` catalog. The panel is positioned `fixed` against
the field's box rather than nested beside it, because the drawer body scrolls and would otherwise clip it.

**A multi-value filter is a tag box over the native `<select multiple>`.** `static/js/tagselect.js` hides the
select, keeps it in the DOM and draws a box of removable tags plus a searchable drop-down beside it. The select
stays the only source of truth: the script only flips `option.selected` and fires `change`, so the submitted
query string, `DashboardFilterForm`/`ViolationFilterForm` and the page without JavaScript are all unchanged —
the native list is exactly what a reader without the script gets, which is why the `.filter-form
select[multiple]` sizing rule stays in the stylesheet. The widget is applied to every `select[multiple]` inside
a `.filter-form` (or one marked `data-tagselect`), never to every multi-select in the app. Its menu is
positioned `fixed` for the same reason the date picker's is: the drawer body scrolls. Because the native
control is hidden, Playwright can no longer drive it, so the box carries `data-testid="tagselect-<name>"` and
`e2e/web/test_policy_console.py::_choose_tag` goes through the widget the way a reader would.
