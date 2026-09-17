# Phase 6 — AI policy engine and violations console

**Goal:** Nine policy rules evaluated idempotently, violations a lead can acknowledge or waive, and an audit
trail of those judgements.

**User-facing:** yes — the Policy page (`/policy/`, every logged-in lead) plus Settings → AI policy and
Settings → Sensitive paths (admins only).

---

## Context

### What exists

- **Models, never written to.** `apps/policy/models.py` (phase 2) already defines:
  - `AIPolicy` — `allowed_tools` (JSON list), `require_disclosure`, `require_human_approval`,
    `min_human_approvals` (default 1), `require_tests_for_ai_prs`, `ai_pr_max_effective_lines` (nullable),
    `effective_from`, `created_at`, `ordering = ["-effective_from"]`.
  - `SensitivePathRule` — nullable `project` (empty = global), `glob`, `ai_mode`
    (`forbidden` / `needs_extra_review`), `description`, `is_active`, index on `(project, is_active)`.
  - `PolicyViolation` — `pull_request`, `rule_code` (**all nine codes already enumerated**), `severity`,
    `details_params` (JSON), `details_hash`, `status` (open/acknowledged/waived/resolved, default open),
    `resolved_by`, `resolution_comment`, `resolved_automatically`, timestamps, and the idempotency key
    `UniqueConstraint(pull_request, rule_code, details_hash)`.
- **Two service helpers already written and tested** in `apps/policy/services.py`: `details_hash(params)`
  (canonical `json.dumps(sort_keys=True)` → sha256) and `current_policy(at=None)` (newest row with
  `effective_from <= at`, else `None`). `apps/policy/tests/test_{models,services}.py` cover the unique
  constraint, hash stability and policy selection; `test_models.py::test_no_field_stores_a_rendered_message`
  already pins the field list.
- **`apps/policy/factories.py`**: `AIPolicyFactory`, `SensitivePathRuleFactory`,
  `SensitivePathRuleForProjectFactory`, `PolicyViolationFactory`.
- **Every input the nine rules need is already stored and derived.** `PullRequest.ai_status` (`AIStatus`),
  `ai_tools` (JSON list of `Tool` values ∪ raw strings), `ai_disclosure` (`AIDisclosure`),
  `effective_additions`/`effective_deletions`, `has_test_changes`, `is_self_merged`, `state`, `merged_at`,
  `created_at`, `merged_by`; `PRFile` (`path`, `is_test`, `is_excluded`, `additions`, `deletions`, and the
  **still-unwritten** `matched_sensitive_rule` FK to `SensitivePathRule`); `Review` (`state`, `reviewer`,
  `submitted_at`); `AISignal` (`confidence`, `tool`).
- **Glob machinery.** `apps/catalog/globs.py::compile_globs()/matches_any()` — the `**`-aware dialect
  `EXCLUDED_PATH_GLOBS`/`TEST_PATH_GLOBS` use, skipping an unusable pattern with a warning.
- **The pipeline hook.** `apps/github_sync/pipeline.py::process_pull_request()` runs
  `resolve_identities_for_pull_request → derive_pull_request → detect_pull_request` after the PR's transaction
  commits; a raising stage is logged, masked and counted by `github_sync/services.py::_run_post_processing`.
- **`manage.py recompute`** (`apps/github_sync/management/commands/recompute.py`, phase 5) with `--from`,
  `--to`, `--repo`, `--project`, calling `derive_pull_requests()` then `detect_pull_requests()`. It carries a
  local `_day_start()` whose docstring says phase 7's shared helper replaces it.
- **Idempotency worked twice already**: `activity/derive.py` (pure function of stored rows) and
  `ai_detection/services.py::detect_pull_request` (wanted-vs-existing **diff** over stored rows, bulk entry
  point `detect_pull_requests(queryset)`). This phase copies that shape, with one deliberate difference
  (auto-resolve instead of delete — see Design).
- **Scope choke point.** `apps/accounts/selectors.py::scope_for_user(user) -> ScopeFilter` (still
  `unrestricted=True` until phase 9) and `apps/activity/selectors.py::pull_requests_in_scope(scope)` /
  `pull_requests_for_metrics(scope)` as the base every new selector composes on top of.
- **AI cohort.** `ai_detection/services.py::ai_cohort_statuses()` → `{ai_explicit, ai_disclosed}` ∪
  `{ai_suspected}` when `AI_COHORT_INCLUDE_SUSPECTED`; `ai_detection/selectors.py::ai_cohort_pull_requests`.
- **Audit.** `apps/accounts/models.py::AuditEntry` (nullable `actor`, `created_at`, `action`, `object_type`,
  `object_id`, `changes={"before":…,"after":…}`) and `accounts/services.py::record_audit(actor, action, obj,
  before, after)`, used by `catalog`, `connections` and `ai_detection` with dotted action codes.
- **Settings.** `apps/catalog/setting_defs.py` + `catalog/services.py::get_int/get_bool/get_list/get_dict`.
  The `policy` group currently holds exactly one key: `NO_TESTS_MIN_LINES` (int, 20). New keys need a data
  migration mirroring `catalog/migrations/0004_ai_detection_settings.py`.
- **UI machinery.** `config/htmx.py::is_htmx()`, the `catalog.manage_settings` permission,
  `apps/ai_detection/{forms,views,urls}.py` + `templates/ai_detection/` as the worked settings-page example
  (full page vs `partials/` fragment on the same URL, `record_audit()` on every POST),
  `templates/partials/nav.html`, `static/css/tokens.css`, `static/js/{theme,formatting}.js`.
- **Gates that bind this phase.** `tests/test_urls_login.py` (every new named URL redirects anonymously),
  `tests/test_no_hardcoded_colors.py`, `tests/test_translations.py` (no empty/fuzzy msgstr, matching
  placeholders, uk canary), `tests/test_model_i18n.py`, `tests/test_docs.py`, `tests/test_admin.py`,
  `tests/test_http_guard.py` (any unmocked outbound request fails the suite — this phase makes none),
  `static/css/.build-manifest.sha256` (any new template or `.py` invalidates it → `make css`).
- **What does not exist yet:** `apps/policy/{rules,messages,selectors,forms,views,urls}.py`, any policy
  template, any `/policy/` URL, the metrics registry and `metrics.compute()` (phase 7), Chart.js
  (`static/vendor/chart.umd.js`) and `static/js/charts.js` (phase 8), any use of `django_tables2` or
  `django_filters` anywhere in the tree, `docs/POLICY.md`.

### What this phase changes

The pipeline gains its fourth and last pre-rollup stage: after AI detection resolves *what* a PR is, policy
evaluation decides whether it *complies*. Evaluation is a pure function of stored rows plus the effective
`AIPolicy` and the active `SensitivePathRule`s, written through a wanted-vs-existing diff that may only
**create** an open violation or **auto-resolve** one — never overwrite a lead's judgement. On the read side a
new Policy console shows compliance KPIs, a by-rule distribution, a filterable paginated violation table and
bulk acknowledge/waive with a mandatory comment; each status change writes an `AuditEntry`. Two admin settings
pages let the policy be versioned and sensitive paths be managed without a release.

### Key files

| File | Change |
|---|---|
| `apps/catalog/setting_defs.py` + `catalog/migrations/0005_policy_settings.py` | three new `policy` settings |
| `apps/metrics/timeframe.py` | the shared `REPORT_TIMEZONE` day-boundary helper (extracted from `recompute`) |
| `apps/policy/models.py` + `migrations/0003_*` | `SensitivePathRule` validation/uniqueness/ordering, `AIPolicy.effective_from` uniqueness, one violation index |
| `apps/policy/messages.py` | `rule_code` + `details_params` → a translated sentence, rendered at read time |
| `apps/policy/rules.py` | `PolicyContext`, the nine evaluators, `RULES` registry, severities, identity-param declaration |
| `apps/policy/services.py` | `evaluate_pull_request`, `evaluate_pull_requests`, `match_sensitive_paths`, `apply_status_change`, `apply_bulk_status_change`, `save_policy_version` |
| `apps/policy/selectors.py` | scoped violation queryset, compliance KPIs, by-rule series, mismatch PR list |
| `apps/policy/{forms,views,urls}.py` + `templates/policy/**` | the console and the two settings pages |
| `apps/github_sync/pipeline.py`, `management/commands/recompute.py` | fourth stage; shared day helper |
| `apps/activity/models.py` (no change) / `PRFile.matched_sensitive_rule` | finally written, by `match_sensitive_paths` |
| `config/urls.py`, `templates/partials/nav.html` | new routes and nav entries |
| `docs/POLICY.md`, `docs/CONFIGURATION.md`, `docs/user/handle-policy-violations.md`, `docs/DECISIONS.md`, `CHANGELOG.md` | docs |
| `locale/uk/LC_MESSAGES/django.po`, `static/css/app.css` | regenerated in this phase |

---

## Design

### Data model

No new model. Five small changes, one migration (`apps/policy/migrations/0003_policy_engine.py`):

1. **`SensitivePathRule.clean()`** rejects an empty glob and a glob that `compile_globs()` cannot compile, so a
   typo is a visible field error instead of a rule that silently never matches (the same shape as
   `DetectionRule.clean()` from phase 5).
2. **`UniqueConstraint(project, glob, name="uniq_sensitive_path_project_glob")`** — one row per (scope, glob).
   SQLite treats two `NULL` projects as distinct, so the global case gets a second partial constraint
   `UniqueConstraint(fields=["glob"], condition=Q(project__isnull=True), name="uniq_sensitive_path_global_glob")`.
3. **`SensitivePathRule.Meta.ordering = ["project_id", "glob"]`** so the settings list and every evaluation
   iterate in a deterministic order (the order decides which rule a file's `matched_sensitive_rule` points at).
4. **`AIPolicy.effective_from` becomes `unique=True`** — a version is identified by when it takes effect, and
   two rows with the same instant would make `current_policy()` order-dependent.
5. **`Index(fields=["pull_request", "status"])` on `PolicyViolation`** — the PR detail page and the
   compliance-rate KPI both ask "does this PR have an open violation".

`AIPolicy` stays a **versioned singleton**: the settings form never edits a row in place, it writes a new row
with `effective_from = now` (`services.save_policy_version`). History is therefore append-only and
`current_policy()` keeps working unchanged. Editing an existing *future* version in place is out of scope.

### Settings (`apps/catalog/setting_defs.py`, group `policy`)

| Key | Type | Default | Why |
|---|---|---|---|
| `POLICY_DISABLED_RULES` | list | `[]` | spec §7: "each rule can be switched off in settings". The four `AIPolicy` booleans/limits already switch off `DISCLOSURE_MISSING`, `NO_HUMAN_APPROVAL`, `NO_TESTS` and `AI_PR_TOO_LARGE`; this list covers the remaining five codes without adding five columns to a spec-fixed model. An unknown code is ignored with a warning. |
| `POLICY_VIOLATION_PATHS_IN_PARAMS` | int | 20 | caps how many paths a sensitive-path violation stores, so one 900-file PR cannot write a 60 kB JSON blob. The full count is kept as `path_count`. |
| `VIOLATIONS_PAGE_SIZE` | int | 50 | the console's page size |

`NO_TESTS_MIN_LINES` (20) already exists and is the `NO_TESTS` threshold.

### Module layout

```python
# apps/policy/rules.py — pure: reads a context, returns findings, writes nothing
@dataclass(frozen=True)
class PolicyContext:
    pull_request: PullRequest
    policy: AIPolicy
    files: tuple[PRFile, ...]  # prefetched once
    reviews: tuple[Review, ...]  # prefetched once
    signal_confidences: frozenset[str]
    signal_tools: frozenset[str]
    sensitive_rules: tuple[SensitivePathRule, ...]  # global + this PR's projects, active only
    is_ai: bool  # ai_status in ai_cohort_statuses()
    human_approver_person_ids: frozenset[int]
    no_tests_min_lines: int
    paths_in_params: int


@dataclass(frozen=True)
class Finding:
    rule_code: str
    severity: str
    details_params: dict[str, Any]  # data only: paths, tools, counts
    identity_params: dict[str, Any]  # the subset that defines this violation's identity


Evaluator = Callable[[PolicyContext], Iterable[Finding]]
RULES: dict[str, Evaluator]  # keyed by PolicyViolation.RuleCode, all nine
SEVERITY: dict[str, str]  # the spec §7 table, verbatim
MERGE_DEPENDENT: frozenset[str]  # {NO_HUMAN_APPROVAL, SELF_MERGE}
AI_ONLY: frozenset[str]  # everything except DISCLOSURE_MISSING/MISMATCH/TOOL_NOT_ALLOWED
```

`services.py` owns the writes (`evaluate_pull_request`, `match_sensitive_paths`, the status-change services),
`selectors.py` owns every read (always from a `ScopeFilter`), `messages.py` owns rendering, `views.py` stays
thin. `mypy` already globs `apps/*/services.py` and `apps/*/selectors.py`; T20 adds `rules.py` and
`messages.py`.

### The nine rules (spec §7, verbatim conditions)

| code | severity | fires when | gate |
|---|---|---|---|
| `DISCLOSURE_MISSING` | medium | `policy.require_disclosure` and `ai_disclosure ∈ {missing, ambiguous}` | any PR |
| `DISCLOSURE_MISMATCH` | high | `ai_disclosure == none` and a `high`-confidence signal exists | any PR |
| `TOOL_NOT_ALLOWED` | high | a detected or declared tool ∉ `policy.allowed_tools` (one finding per tool) | any PR; skipped when `allowed_tools` is empty (nothing has been declared allowed yet, so nothing can be disallowed) |
| `SENSITIVE_PATH_FORBIDDEN` | high | an AI PR touches a file matching an active `ai_mode=forbidden` glob (one finding per matched rule) | AI only |
| `SENSITIVE_PATH_REVIEW` | medium | an AI PR touches a `needs_extra_review` glob **and** has fewer than `min_human_approvals + 1` human approvals | AI only |
| `NO_HUMAN_APPROVAL` | high | a merged AI PR has fewer than `min_human_approvals` approvals from humans who are neither the author nor a bot | AI only, merged only, needs `require_human_approval` |
| `SELF_MERGE` | high | a merged AI PR has `is_self_merged` and zero human approvals from others | AI only, merged only |
| `NO_TESTS` | low | an AI PR changes more than `NO_TESTS_MIN_LINES` non-test, non-excluded lines and `has_test_changes` is false | AI only, needs `require_tests_for_ai_prs` |
| `AI_PR_TOO_LARGE` | low | `effective_additions + effective_deletions > policy.ai_pr_max_effective_lines` | AI only, needs the limit to be set |

"AI PR" is `pull_request.ai_status in ai_cohort_statuses()` — the same cohort definition the metrics use, not a
second one. "Human approval" is a `Review` with `state=APPROVED` whose reviewer resolves to a `Person` that is
neither the author's person nor `is_bot`, counted **per person** (three approvals from one reviewer are one).
Absent input stays absent: a PR with `effective_additions is None` cannot fire `AI_PR_TOO_LARGE`, and a PR with
no files cannot fire a sensitive-path or `NO_TESTS` rule.

**Two hard gates run before any evaluator** (`services.evaluate_pull_request`):

- **`effective_from`.** The applicable policy is `current_policy()` (newest row already in effect). If it is
  `None`, or if `pull_request.created_at < policy.effective_from`, the wanted set is **empty** — the PR is not
  evaluated, and any violation still `open` on it auto-resolves. A PR created before the policy therefore
  produces no violation, which is acceptance criterion 3.
- **Merge dependence.** A code in `MERGE_DEPENDENT` is skipped unless `state == merged and merged_at is not
  None`, which is acceptance criterion 4.

A code in `POLICY_DISABLED_RULES` is skipped the same way, so switching a rule off auto-resolves its open rows
on the next run.

### Sensitive-path matching writes `PRFile.matched_sensitive_rule`

`services.match_sensitive_paths(pr, sensitive_rules)` compiles each active rule's glob once, walks the PR's
non-excluded files in `Meta.ordering` order, and writes `matched_sensitive_rule_id` with a single
`bulk_update` when it differs from what is stored. It is idempotent (same inputs → same FK, no write), and it is
what finally fills the column phase 2 created. The file list, not the rules, is re-read per PR, so a deleted or
deactivated rule clears the mark on the next run. Evaluation then reads the marks it just wrote, so the console
and the PR page agree with the violation.

### Idempotent evaluation and auto-resolve

```python
@transaction.atomic
def evaluate_pull_request(pull_request_id, *, policy=None, sensitive_rules=None, settings_bundle=None) -> None:
    # wanted: {(rule_code, details_hash(identity_params)): Finding}
    # existing: {(rule_code, details_hash): PolicyViolation}
    # create   -> key in wanted, not in existing            → status=open
    # refresh  -> key in both, details_params differ         → update details_params only
    # resolve  -> key in existing (status=OPEN), not wanted  → resolved + resolved_automatically + AuditEntry
    # untouched-> existing acknowledged / waived / resolved
```

Three properties this buys, each asserted by test:

- **A second run creates no second row.** The key is the model's own unique constraint, computed from the
  finding's `identity_params` — deliberately **not** the whole `details_params`. Counters that move while the
  same fact persists (`approvals`, `path_count`) live in `details_params` only, so a PR gaining a comment does
  not mint a new violation. Where a rule's identity is "this rule fired at all" (`NO_HUMAN_APPROVAL`,
  `SELF_MERGE`, `NO_TESTS`, `AI_PR_TOO_LARGE`, `DISCLOSURE_MISSING`, `DISCLOSURE_MISMATCH`) the identity params
  are `{}`; `TOOL_NOT_ALLOWED` is identified by `{"tool": …}` and the two sensitive-path rules by
  `{"sensitive_rule_id": …}`, so two forbidden paths under two different rules are two rows a lead can waive
  independently.
- **Auto-resolve only moves `open → resolved`.** The queryset that resolves is filtered
  `status=PolicyViolation.Status.OPEN`; `acknowledged`, `waived` and already-`resolved` rows are never in it.
  It sets `resolved_automatically=True`, leaves `resolved_by=None` and `resolution_comment` empty, and writes an
  `AuditEntry(actor=None, action="policy_violation.auto_resolve")` — the "automatic marker" of the deliverable
  is both the boolean and the null actor.
- **Auto-resolve never reopens.** A `resolved` row whose condition returns stays resolved, per
  `.autodev/ARCHITECTURE.md` ("It never reopens or overwrites a human `acknowledged`/`waived`"); the unique
  constraint stops a duplicate open row from appearing beside it. This is a deliberate, logged trade-off
  (recurrence is visible on the PR and in the audit trail, not as a resurrected row).

`evaluate_pull_requests(queryset)` is the bulk entry point `recompute` uses: it loads the policy, the sensitive
rules and the three settings **once** and passes them in, instead of paying that per PR (RISKS row 10, exactly
the fix phase 5's review applied to `detect_pull_requests`).

### `details_params` carries data only

Every `details_params` value is a path, a tool code, a rule id or a number. `apps/policy/messages.py` turns
`(rule_code, details_params)` into a sentence at read time:

```python
def render_violation(rule_code: str, params: Mapping[str, Any]) -> str:
    # full sentences with named placeholders; ngettext for every count; never a concatenation
```

Example: `NO_TESTS` stores `{"non_test_lines": 120, "threshold": 20}` and renders
`"Changes %(non_test_lines)s lines of non-test code with no test changes (threshold %(threshold)s)."` —
pluralised with `ngettext` on `non_test_lines`. An unknown `rule_code` renders the code itself rather than
raising, so a stale row can never 500 the console. Paths are joined by the template's `, ` separator as **data**
inside one placeholder, never by concatenating translated fragments.

The guarantee is tested two ways: a schema test asserts every evaluator's `details_params` keys are declared and
every value is `str | int | float | bool | list[str]`, and a prose test asserts no stored value contains a word
from any rule's message template (so a future evaluator cannot smuggle a sentence into the JSON).

### Read side — `apps/policy/selectors.py`

Every function takes a `ScopeFilter` and composes on `activity.selectors`:

```python
violations_in_scope(scope)                     -> QuerySet[PolicyViolation]   # PR ∈ pull_requests_in_scope
violations_for_pull_request(scope, pk)         -> QuerySet[PolicyViolation]
violations_by_rule(scope, start, end, status)  -> list[RuleCount]             # the chart series
compliance_kpis(scope, start, end)             -> ComplianceKPIs
disclosure_mismatch_pull_requests(scope, start, end) -> QuerySet[PullRequest]
```

`ComplianceKPIs` is a frozen dataclass of `violations_open`, `violations_new`, `ai_pr_compliance_rate`
(share of AI-cohort PRs in the period with **no** open violation), `disclosure_mismatch_count`, and
`sample_size` (the AI-PR denominator). Per CLAUDE.md, a rate with no denominator is `None`, never `0`, and the
template greys it when `sample_size < MIN_SAMPLE`. The period is closed-open in `REPORT_TIMEZONE` days through
`metrics/timeframe.py`.

**Deviation, logged:** CLAUDE.md says `metrics.compute()` is the only read entry point for dashboards, and it
does not exist until phase 7. These four selectors are the phase-7 calculators' data source: `violations_open`,
`violations_new` and `violations_by_rule` (spec §8.2) get registered there with calculators that call *these*
functions, rather than phase 7 rewriting the queries.

### The Policy console (`/policy/`)

| URL | name | method | returns |
|---|---|---|---|
| `policy/` | `policy:console` | GET | full page, or the `#violations` fragment for htmx (filters, sort, page) |
| `policy/violations/action/` | `policy:violation_bulk_action` | POST | the `#violations` fragment, or the same fragment carrying visible form errors |
| `settings/ai-policy/` | `policy:policy_settings` | GET/POST | full page / fragment |
| `settings/sensitive-paths/` | `policy:sensitive_paths` | GET/POST | list page / fragment |
| `settings/sensitive-paths/new/` | `policy:sensitive_path_create` | GET/POST | form page / fragment |
| `settings/sensitive-paths/<pk>/edit/` | `policy:sensitive_path_edit` | GET/POST | form page / fragment |
| `settings/sensitive-paths/<pk>/toggle/` | `policy:sensitive_path_toggle` | POST | list fragment |

The console is `@login_required` only — per `.autodev/ARCHITECTURE.md` the `lead` group holds violation
actions. The four settings URLs add `@permission_required("catalog.manage_settings", raise_exception=True)`;
a lead gets 403 and no nav link, asserted against a paired admin-200 test on the same URL.

Page layout, top to bottom: the four KPI cards (with `MIN_SAMPLE` greying and an `ⓘ` description), the
by-rule distribution chart, the `DISCLOSURE_MISMATCH` PR list, then the violation table — checkbox column,
PR link, rule (label + rendered sentence), severity, status, age, and the acknowledge/waive bar.

**Filters** are one `ViolationFilterForm` (`policy/forms.py`) parsing `request.GET`: `rule_code`, `severity`,
`status` (multi, default `open`), `project`, `repository`, `date_from`, `date_to`, `q` (PR title/number).
Unknown or out-of-scope ids are dropped silently rather than 403'd (RISKS row 3's stated rule); dates are
clamped and converted through `metrics/timeframe.py`. Pagination is Django's `Paginator` at
`VIOLATIONS_PAGE_SIZE`. **Deviation, logged:** `django-filter`/`django-tables2` are installed but unused, and
this phase keeps using plain forms + templates like every other page in the tree; phase 8 owns the sortable,
exportable `django-tables2` tables and can adopt this table then.

**The chart** is a server-rendered horizontal bar list: one row per rule code with an inline
`style="width: N%"` and a colour from a `tokens.css` variable, plus a `<table>` text alternative that carries
the same numbers (the a11y requirement for every chart). **Deviation, logged:** Chart.js is vendored in phase 8
together with `static/js/charts.js` and the chart JSON endpoints; building that infrastructure for one bar
chart would duplicate a phase-8 deliverable, while `selectors.violations_by_rule()` already returns exactly the
series a phase-8 JSON endpoint will serialise.

**Bulk actions.** `BulkViolationActionForm`: `violation_ids` (multi-choice, validated against
`violations_in_scope`), `action` (`acknowledge` / `waive`), `comment` (**required**, `min_length=3`, stripped).
An empty or whitespace-only comment fails validation and the view re-renders the fragment with a visible field
error — never an empty 400 body. `services.apply_bulk_status_change(user, violations, action, comment)`
allows `open → acknowledged|waived` and `acknowledged ↔ waived`, and refuses a `resolved` row with a counted,
rendered notice (a resolved violation is history). Each accepted row writes `status`, `resolved_by=user`,
`resolution_comment`, `resolved_automatically=False` and one
`AuditEntry(actor=user, action="policy_violation.acknowledge"|".waive",
changes={"before": {"status": …}, "after": {"status": …, "comment": …}})` — who, when (`created_at`), and the
before/after state, which is acceptance criterion 6.

### Settings UI

- **AI policy.** `AIPolicyForm` over `allowed_tools` (multi-select of `Tool` choices, stored as a list),
  the three booleans, `min_human_approvals`, `ai_pr_max_effective_lines` and `effective_from` (defaulting to
  now, rejecting a value that duplicates an existing version). Saving calls
  `services.save_policy_version(user, cleaned_data)`: it **creates a new row** and writes
  `AuditEntry(action="ai_policy.update")` with the previous version's field values as `before`. The page lists
  the version history read-only, newest first.
- **Sensitive paths.** List + create/edit/toggle, the exact shape of Settings → Detection rules, with
  `record_audit` actions `sensitive_path_rule.create|update|toggle`. Deleting is not offered (a rule referenced
  by `PRFile.matched_sensitive_rule` is `SET_NULL`, but a deactivated rule keeps the audit trail readable);
  `is_active=False` is the way to retire one.

### Error handling

- A glob that does not compile: rejected by `clean()` at entry, skipped with a warning at evaluation, so one
  bad rule can never stop the pipeline for every PR.
- A rule code in `POLICY_DISABLED_RULES` that is not a `RuleCode`: ignored with one warning.
- Evaluation raising inside the sync pipeline: already contained by `_run_post_processing()` (logged, masked,
  counted, run continues).
- A PR with `created_at` before every policy version, or with no policy at all: no violation, existing open rows
  auto-resolve.
- A stale `rule_code` in the database (a code removed from the enum by a future release): the console renders
  the raw code instead of raising.
- Every console POST returns a visible fragment; every mutation is POST.

### How this honours the architecture

Same pipeline position and idempotency contract (`identity → derive → detect → **policy** → dirty-days`, unique
per `(pull_request, rule_code, details_hash)`); auto-resolve is `open → resolved` only and marked automatic, so
the "must never be lost" list (`status`, `resolved_by`, `resolution_comment`, `AuditEntry`) is honoured by
construction; every selector starts from `scope_for_user()`; views stay thin with rules in `rules.py`/
`services.py` and queries in `selectors.py`; violation text is stored as `rule_code` + params and rendered in
the reader's language; policy and sensitive paths stay operator-owned runtime data, not code; no GitHub call is
made anywhere.

**Deviations from `.autodev/ARCHITECTURE.md` / CLAUDE.md, each appended to DECISIONS.md:** (1) compliance KPIs
come from `policy/selectors.py` because `metrics.compute()` does not exist until phase 7, which will register
them as metrics over these selectors; (2) the violation chart is server-rendered bars + a data table instead of
Chart.js, which phase 8 vendors; (3) the console's views, templates and table live in `apps/policy` rather than
`apps/dashboards`, because every mutation on the page is a policy-domain write and the settings pages are
already this app's; (4) plain forms + `Paginator` instead of the installed-but-unused
`django-filter`/`django-tables2`, which phase 8 owns; (5) the `REPORT_TIMEZONE` day-boundary helper is extracted
to `apps/metrics/timeframe.py` now (phase 7's deliverable) rather than writing a second copy next to
`recompute`'s.

---

## Tasks

- [x] **T1: Three new `policy` settings.** `apps/catalog/setting_defs.py` + data migration
      `catalog/migrations/0005_policy_settings.py` (mirroring `0004`): `POLICY_DISABLED_RULES` (list, `[]`),
      `POLICY_VIOLATION_PATHS_IN_PARAMS` (int, 20), `VIOLATIONS_PAGE_SIZE` (int, 50). Tests: extend
      `apps/catalog/tests/test_app_settings.py` — all three resolve to their defaults, type-validate, and a
      corrupt stored value falls back to the code default.
- [x] **T2: Shared day-boundary helper.** New `apps/metrics/timeframe.py` with `day_start(date)` and
      `day_end_exclusive(date)` in `settings.REPORT_TIMEZONE`; `recompute._day_start` deleted and replaced by
      the import. Tests: `apps/metrics/tests/test_timeframe.py` — midnight Kyiv maps to the right UTC instant
      in winter **and** summer, a DST-transition day is handled, `day_end_exclusive` is the next day's start,
      and a `str` date is accepted; `apps/github_sync/tests/test_recompute_command.py` still green.
- [x] **T3: Model hardening.** `apps/policy/models.py` + `migrations/0003_policy_engine.py`:
      `SensitivePathRule.clean()` (empty/uncompilable glob), the two uniqueness constraints, `Meta.ordering`,
      `AIPolicy.effective_from` unique, `PolicyViolation` index `(pull_request, status)`. Tests: extend
      `apps/policy/tests/test_models.py` — `[unclosed` glob raises `ValidationError`, an empty glob raises,
      a valid `**/secrets/**` passes; two global rules with the same glob raise `IntegrityError`; the same glob
      under two different projects is allowed; two `AIPolicy` rows with the same `effective_from` raise.
- [x] **T4: Message rendering.** (uk translation added in T20; `test_message_renders_in_ukrainian` red until then) `apps/policy/messages.py`: `RULE_MESSAGES`, `render_violation(rule_code,
      params)`, `rule_label(rule_code)`. Tests: `apps/policy/tests/test_messages.py` — every one of the nine
      codes renders a non-empty sentence from its documented params; `ngettext` gives the singular for 1 and
      the plural for 2; an unknown code renders the code itself; a missing param renders without raising; the
      rendered string changes under `override("uk")` (proof the text is not baked).
- [x] **T5: Rule scaffolding.** `apps/policy/rules.py`: `PolicyContext`, `Finding`, `load_context()`,
      `RULES`, `SEVERITY`, `MERGE_DEPENDENT`, `AI_ONLY`, `PARAM_SCHEMA`. Tests:
      `apps/policy/tests/test_rules_registry.py` — `RULES.keys() == set(PolicyViolation.RuleCode.values)`
      (all nine, none extra), `SEVERITY` matches the spec §7 table verbatim, every rule has a `PARAM_SCHEMA`
      entry, and `load_context()` loads files/reviews/signals with a bounded query count.
- [x] **T6: Disclosure and tool evaluators.** `DISCLOSURE_MISSING`, `DISCLOSURE_MISMATCH`,
      `TOOL_NOT_ALLOWED` in `rules.py`. Tests: `apps/policy/tests/test_rules_disclosure.py` — positive and
      negative for each (missing/ambiguous fires, `partial` does not, `require_disclosure=False` does not;
      `none` + a high signal fires, `none` + a medium signal does not; a tool outside `allowed_tools` fires
      one finding per offending tool, a tool inside does not, and an empty `allowed_tools` fires nothing).
- [x] **T7: Sensitive-path matching and its two rules.** `services.match_sensitive_paths()` and
      `test_rules_sensitive.py` (matching + both evaluators) done and green. `services.match_sensitive_paths()` writing
      `PRFile.matched_sensitive_rule`, plus `SENSITIVE_PATH_FORBIDDEN` and `SENSITIVE_PATH_REVIEW`. Tests:
      `apps/policy/tests/test_rules_sensitive.py` — a global rule matches, a project rule matches only that
      project's repositories, a non-matching path does not fire, an excluded file does not fire, a non-AI PR
      does not fire; `needs_extra_review` with `min_human_approvals + 1` human approvals does **not** fire and
      with one fewer does; two matched rules produce two rows with different `details_hash`; the `paths` list
      is capped at `POLICY_VIOLATION_PATHS_IN_PARAMS` with the real total in `path_count`; running twice
      writes the same `matched_sensitive_rule` and no extra queries' worth of updates; deactivating the rule
      clears the mark.
- [x] **T8: Merge, test and size evaluators.** `NO_HUMAN_APPROVAL`, `SELF_MERGE`, `NO_TESTS`,
      `AI_PR_TOO_LARGE`. Tests: `apps/policy/tests/test_rules_merge.py` — positive and negative for each;
      an approval from the author does not count; an approval from a bot does not count; two approvals from
      one person count once; `SELF_MERGE` does not fire when another human approved; `NO_TESTS` respects
      `NO_TESTS_MIN_LINES` and `has_test_changes`, and counts only non-test non-excluded lines;
      `AI_PR_TOO_LARGE` does not fire when the limit is `None` or the effective lines are `None`; **both
      merge-dependent rules produce nothing on an open PR** (acceptance criterion 4).
- [x] **T9: Evaluation, auto-resolve and the bulk entry point.** `services.evaluate_pull_request()`,
      `evaluate_pull_requests(queryset)`. Tests: `apps/policy/tests/test_evaluate.py` — a violating PR gets
      one row with the right code, severity and params; **a second run creates no second row and no status
      change** (parametrized over all nine codes); fixing the condition auto-resolves the row with
      `resolved_automatically=True`, `resolved_by=None` and an `AuditEntry(actor=None)`; **an acknowledged and
      a waived row are untouched by the same re-run** (acceptance criterion 2); a `resolved` row is not
      reopened; a PR created before `effective_from` produces nothing (criterion 3); no policy at all produces
      nothing and auto-resolves an open row; a code in `POLICY_DISABLED_RULES` auto-resolves; a moved counter
      (`approvals`) refreshes `details_params` without a new row; `evaluate_pull_requests` loads the policy and
      the settings once (`assertNumQueries` budget that does not grow with the PR count).
- [x] **T10: Pipeline and `recompute` wiring.** `apps/github_sync/pipeline.py` calls
      `evaluate_pull_request()` after `detect_pull_request()`; `recompute` runs
      `derive → detect → evaluate`. Tests: extend `apps/github_sync/tests/test_pipeline.py` — a
      fixture-driven sync leaves the PR with its violations; **a second sync creates no duplicate row**; a
      failing evaluation is logged and counted without aborting the run; extend
      `test_recompute_command.py` — a policy added after the sync produces violations on the next
      `recompute`, with no outbound HTTP, and a second `recompute` changes no row counts.
- [x] **T11: Selectors.** `apps/policy/selectors.py`: `violations_in_scope`,
      `violations_for_pull_request`, `violations_by_rule`, `compliance_kpis`,
      `disclosure_mismatch_pull_requests`. Tests: `apps/policy/tests/test_selectors.py` — a restricted
      `ScopeFilter` hides another project's violation, its chart series **and** its KPI counts;
      `violations_new` counts by `created_at` inside the Kyiv period and excludes a row one second before it;
      `ai_pr_compliance_rate` is `None` with an empty denominator (never `0`) and reports `sample_size`;
      an acknowledged violation does not count as open; bots and `exclude_from_metrics` people stay out of the
      AI-PR denominator.
- [x] **T12: Status-change services and the audit trail.** (the "out-of-scope id is refused" case
      belongs to `BulkViolationActionForm` — `apply_bulk_status_change` takes an already-filtered
      `violations` iterable and has no scope of its own to refuse against; T13/T15 own that test.) `services.apply_status_change()`,
      `apply_bulk_status_change()`. Tests: `apps/policy/tests/test_status_change.py` — acknowledge and waive
      persist `status`, `resolved_by`, `resolution_comment`, `resolved_automatically=False`; **each writes one
      `AuditEntry` with the actor, `created_at` and before/after status** (criterion 6); `acknowledged ↔
      waived` is allowed; a `resolved` row is refused and reported, not silently skipped; a bulk call over
      three rows writes three entries; an out-of-scope id is refused.
- [x] **T13: Forms.** `apps/policy/forms.py`: `ViolationFilterForm`, `BulkViolationActionForm`,
      `AIPolicyForm`, `SensitivePathRuleForm`. Tests: `apps/policy/tests/test_forms.py` — **an empty comment
      and a whitespace-only comment are both rejected** (criterion 5); an unknown `rule_code` or
      out-of-scope project id in the filter is dropped, not an error; `date_from > date_to` is clamped;
      `AIPolicyForm` rejects a duplicate `effective_from` and a negative `min_human_approvals`;
      `SensitivePathRuleForm` rejects an uncompilable glob with a field error.
- [x] **T14: The Policy console.** `apps/policy/views.py::console` + `urls.py` + `templates/policy/console.html`
      and `partials/{violations.html,kpis.html,rule_chart.html,mismatch_list.html}`, wired into
      `config/urls.py` and `templates/partials/nav.html`. Tests: `apps/policy/tests/test_views_console.py` —
      200 for a lead, anonymous redirect (also covered by `tests/test_urls_login.py`); the table shows the
      **rendered** sentence for each violation; the chart's text-alternative table carries the same numbers as
      `violations_by_rule`; a KPI with `sample_size < MIN_SAMPLE` renders its greyed marker; an empty state
      renders when there are no violations; filters narrow the table and survive in the fragment; an htmx
      request gets the fragment and a normal request the full page; `assertNumQueries` budgets the table.
- [x] **T15: Bulk acknowledge/waive in the UI.** `views.violation_bulk_action` + the action bar in
      `partials/violations.html`. Tests: extend `test_views_console.py` — POST acknowledging two rows updates
      both, writes two `AuditEntry` rows and re-renders the fragment with a success notice; **a missing
      comment re-renders the fragment with a visible field error and changes nothing** (criterion 5); a GET on
      the action URL is 405; a `resolved` row in the selection is reported as skipped; an out-of-scope id
      changes nothing.
- [x] **T16: Settings → AI policy.** `services.save_policy_version()`, `views.policy_settings`,
      `templates/policy/policy_settings.html` + `partials/policy_form.html`. Tests:
      `apps/policy/tests/test_views_policy_settings.py` — a lead gets 403 and no nav link while an admin gets
      200 on the same URL; saving **creates a new version** rather than editing the old one, and writes
      `AuditEntry(action="ai_policy.update")` with the previous values as `before`; the history list shows
      both versions newest first; an invalid form re-renders with visible errors and creates nothing.
- [x] **T17: Settings → Sensitive paths.** `views.sensitive_paths/…_create/…_edit/…_toggle`,
      `templates/policy/sensitive_paths.html` + `partials/{sensitive_paths_content,sensitive_path_form,sensitive_path_row}.html`.
      Tests: `apps/policy/tests/test_views_sensitive_paths.py` — permission pair (lead 403 / admin 200);
      create, edit and toggle persist and each writes its `AuditEntry`; a bad glob renders a visible field
      error, not an empty 400; a global and a per-project rule are both creatable; htmx vs full page;
      `assertNumQueries` on the list.
- [x] **T18: `details_params` carries no prose.** Tests: `apps/policy/tests/test_details_params.py` — a
      dataset that fires **all nine** rules, then: every stored `details_params` key is in that rule's
      `PARAM_SCHEMA`; every value is `str | int | float | bool | list[str]`; no value contains any word from
      any entry of `RULE_MESSAGES`; `render_violation()` output appears in **no** database column
      (criterion 7). Also re-assert the existing field-list test after the migration.
- [x] **T19: Docs.** `docs/POLICY.md` (the nine rules, their conditions, severities,
      params, what switches each off, how auto-resolve behaves, what acknowledge vs waive means),
      `docs/CONFIGURATION.md` (the three new settings), `docs/user/handle-policy-violations.md` (task-shaped:
      read the console, filter, waive with a reason, check the audit trail), `docs/DECISIONS.md` (phase 6
      section), `CHANGELOG.md`. Test: extend `tests/test_docs.py` — every `RuleCode` value, every `Status` value
      and every `SensitivePathRule.AiMode` value is documented in `docs/POLICY.md`, and the severity table there
      matches `rules.SEVERITY` (the doc and the code can never drift).
      - [x] `docs/POLICY.md` written (nine rules table, sensitive paths, auto-resolve, acknowledge/waive, statuses).
      - [x] `tests/test_docs.py` extended with `test_every_rule_code_status_and_ai_mode_is_documented` and
            `test_policy_severity_table_matches_the_code` — both green.
      - [x] `docs/CONFIGURATION.md` — added `POLICY_DISABLED_RULES`, `POLICY_VIOLATION_PATHS_IN_PARAMS`,
            `VIOLATIONS_PAGE_SIZE` to the `policy` group table; also fixed the stale `REPORT_TIMEZONE` row
            (said "phase 7 adds", now points at `apps/metrics/timeframe.py`).
      - [x] `docs/user/handle-policy-violations.md` written.
      - [x] `docs/DECISIONS.md` phase 6 section written.
      - [x] `CHANGELOG.md` — "Added" bullet block for the policy engine, console and settings pages.
- [x] **T20: Ukrainian parity, CSS and the gate.** `make messages` with every new string translated and no
      fuzzy entries (console, KPI labels and descriptions, chart alternative, table headers, filter labels,
      action bar, notices, both settings pages, the nine rule messages and labels), a new uk canary string for
      the console; `make css` and commit `static/css/app.css` + the manifest; add `apps/policy/rules.py` and
      `apps/policy/messages.py` to `[tool.mypy] files`; run the full gate and fix everything it reports.
      Found and fixed a real bug along the way: `RULE_MESSAGES`' plural entries (`SENSITIVE_PATH_FORBIDDEN`,
      `SENSITIVE_PATH_REVIEW`, `NO_HUMAN_APPROVAL`, `NO_TESTS`, `AI_PR_TOO_LARGE`) were plain strings passed to
      `ngettext()` through a variable, which `makemessages` cannot see, so they were never extracted at all —
      `test_message_renders_in_ukrainian` was red for the wrong reason (a missing catalog entry, not a missing
      translation). Fixed by adding `apps.policy.messages._register_plural_forms_for_makemessages`, a
      never-called function with literal `ngettext()` calls using the same strings, purely so extraction
      records the singular/plural pairing and the four Ukrainian forms (the catalog is keyed by string content,
      not call site, so `render_violation`'s dynamic call finds them); the 4 singular-only rules already worked
      via `gettext_noop()` directly in `RULE_MESSAGES`. Also fixed two accidental false positives surfaced by
      the full gate: `apps/policy/templates/policy/partials/kpis.html`'s `&#9432;` info-icon entity matched
      `tests/test_no_hardcoded_colors.py`'s `#[0-9a-fA-F]{3,8}` color regex (decimal entities look like hex
      colors) — switched to the hex entity `&#x24D8;`, which the regex's `#` **directly** followed by a hex
      digit rule doesn't match (`x` isn't one); and two pre-existing ruff `E501`/import-sort violations in
      `apps/policy/views.py`/`tests/test_views_console.py`/`tests/test_docs.py` left over from earlier T14-T19
      sessions (the gate hadn't been run repeat-clean since T13, per the T13 handoff note).

---

## Verification

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run mypy \
  && uv run python manage.py makemigrations --check --dry-run \
  && uv run python manage.py check
uv run python manage.py recompute --from 2026-01-01   # evaluates policy, makes no outbound request
uv run python manage.py recompute --from 2026-01-01   # second run: no new violation rows
make css && make messages                             # both must leave the tree clean
```

| # | Acceptance criterion | Test that proves it |
|---|---|---|
| 1 | Each of the nine rules has a positive, a negative, an idempotency and an auto-resolve test | positive/negative: `test_rules_disclosure.py` (3 rules), `test_rules_sensitive.py` (2), `test_rules_merge.py` (4) — `test_<code>_fires` / `test_<code>_does_not_fire`; idempotency and auto-resolve: `test_evaluate.py::test_second_run_creates_no_second_row` and `::test_condition_gone_auto_resolves`, both parametrized over all nine `RuleCode` values |
| 2 | Auto-resolve moves only `open → resolved`, never an acknowledged or waived violation | `test_evaluate.py::{test_auto_resolve_marks_open_row_automatic, test_auto_resolve_leaves_acknowledged_untouched, test_auto_resolve_leaves_waived_untouched, test_auto_resolve_does_not_reopen_resolved}` |
| 3 | A PR created before `AIPolicy.effective_from` produces no violation | `test_evaluate.py::{test_pr_created_before_effective_from_is_not_evaluated, test_no_policy_produces_no_violation}` |
| 4 | A merge-dependent rule produces no violation on an open PR | `test_rules_merge.py::{test_no_human_approval_does_not_fire_on_open_pr, test_self_merge_does_not_fire_on_open_pr}` plus `test_rules_registry.py::test_merge_dependent_set_is_exactly_the_two_merge_rules` |
| 5 | Acknowledging or waiving without a comment is rejected by the form | `test_forms.py::{test_empty_comment_rejected, test_whitespace_only_comment_rejected}`; end-to-end at the view in `test_views_console.py::test_missing_comment_renders_visible_error_and_changes_nothing` |
| 6 | Every status change writes an `AuditEntry` with who, when and before/after | `test_status_change.py::{test_acknowledge_writes_audit_entry, test_waive_writes_audit_entry, test_bulk_action_writes_one_entry_per_violation}`, `test_evaluate.py::test_auto_resolve_writes_audit_entry_with_no_actor`; settings changes: `test_views_policy_settings.py::test_saving_writes_audit_entry`, `test_views_sensitive_paths.py::test_create_edit_toggle_write_audit_entries` |
| 7 | No `PolicyViolation` row stores rendered message text | `test_details_params.py::{test_every_param_key_is_declared, test_every_param_value_is_data, test_no_param_value_contains_message_prose, test_rendered_message_is_in_no_column}` |
| — | Compliance KPIs, chart and table are scope-isolated | `test_selectors.py::test_restricted_scope_hides_another_projects_violation` (+ series and KPI variants) |
| — | A small AI-PR sample greys the compliance rate; an empty one is `None`, not `0` | `test_selectors.py::{test_compliance_rate_is_none_without_denominator, test_kpis_report_sample_size}`, `test_views_console.py::test_low_sample_kpi_is_marked` |
| — | Policy evaluation touches no GitHub | `test_recompute_command.py` and the whole suite under `tests/test_http_guard.py`'s unmocked-request guard |
| — | Ukrainian parity for every new string | `tests/test_translations.py` (no empty/fuzzy, matching placeholders, uk canary) + `test_messages.py::test_message_renders_in_ukrainian` |
| — | No N+1 on the console or the settings lists | `assertNumQueries` in `test_views_console.py`, `test_views_sensitive_paths.py`, `test_rules_registry.py::test_load_context_query_budget` |

---

## Risks

| RISKS.md row | How this phase touches it | What the plan does |
|---|---|---|
| **6 — policy evaluation duplicates or resurrects violations** (this phase's own row) | Every write in this phase is on that path | The unique key is computed from a finding's declared `identity_params`, so a moving counter refreshes `details_params` instead of minting a row; auto-resolve is a queryset filtered `status=OPEN` and never reopens; idempotency and auto-resolve are parametrized over all nine codes; merge-dependent rules gate on `merged`; `effective_from` and `POLICY_DISABLED_RULES` gate the whole evaluation |
| **1 — a wrong number lands in a 1:1** | A violation is an accusation attached to a named person's PR | Every violation is inspectable: `details_params` names the paths, tools and counts behind it, rendered in the reader's language, with the PR one click away; the compliance rate is `None` (never `0`) with an empty denominator and greyed below `MIN_SAMPLE`; the AI-PR denominator reuses `pull_requests_for_metrics()`, so bots and excluded people stay out; a lead can waive with a reason instead of arguing with the tool |
| **3 — a restricted lead sees another project's data** | Three new read surfaces (console, chart series, mismatch list) and one new write surface (bulk actions) | All four start from `scope_for_user()`; the bulk-action form validates every id against `violations_in_scope`; out-of-scope filter ids are dropped silently; `test_selectors.py` asserts isolation on the table, the chart series **and** the KPI numbers |
| **8 — Ukrainian translation lags** | Nine rule messages plus three whole pages | Messages are `rule_code` + params rendered at read time, so the Ukrainian console is total rather than partial; T20 is a task with a canary string, and `test_messages.py` asserts a rule sentence actually changes under `override("uk")` |
| **10 — dashboards miss the budget / N+1** | The console renders per-row rendered messages, and evaluation runs once per PR in a sync | `load_context()` prefetches files, reviews and signals once per PR; `evaluate_pull_requests` loads the policy, the sensitive rules and the settings once per run, not per PR; `assertNumQueries` budgets the console table, the settings lists and `load_context` |
| **15 — a generated artefact goes stale** | New templates and new `.py` files invalidate `static/css/.build-manifest.sha256`; `docs/POLICY.md` can drift from `rules.SEVERITY` | T20 re-runs `make css` and commits `app.css` + the manifest; T19 adds a `tests/test_docs.py` check that every rule code, status and severity in the code is documented and the severities match |
| **7 — metric correctness silently breaks** | This phase computes period counts and a rate before the metrics registry exists | Every period boundary goes through the one `metrics/timeframe.py` helper (extracted, not copied), tested across a DST transition; the four selectors are written to be phase 7's calculators, so the registry inherits tested queries instead of new ones |

---

## Out of scope

- **The metrics registry** — `MetricDef`, `metrics.compute()`, `DailyRollup` writes, caching and
  `manage.py metrics_doc` are phase 7. This phase ships `policy/selectors.py` as the data source phase 7's
  `violations_open`, `violations_new` and `violations_by_rule` calculators read, and `metrics/timeframe.py` as
  the day-boundary helper phase 7 extends.
- **Chart.js, `static/js/charts.js` and the chart JSON endpoints** — phase 8. The by-rule distribution ships as
  server-rendered bars plus a data table; `violations_by_rule()` returns the series a phase-8 endpoint will
  serialise.
- **`django-tables2` / `django-filter` adoption, sorting and CSV/XLSX export of the violation table** —
  phase 8, together with the export layer and its `AuditEntry` rows.
- **The tool-distribution chart on the Policy page** (spec §10) — it is the `ai_tool_breakdown` metric, so it
  lands with the registry in phase 7 / the charts in phase 8. The `DISCLOSURE_MISMATCH` PR list, which needs
  no metric, ships here.
- **Violations on the PR detail page with per-row actions** — phase 9 extends
  `dashboards:pull_request_detail`; `selectors.violations_for_pull_request()` ships here as the query it calls.
- **Per-project policy overrides, scheduled/future policy versions with an editor, and rule severity overrides**
  — the spec defines one global versioned policy with fixed severities; nothing beyond that is invented here.
- **`UserProjectAccess`-driven scope narrowing** — `scope_for_user()` still returns `unrestricted=True` until
  phase 9; this phase composes on it and tests isolation by constructing a restricted `ScopeFilter` directly.
- **Notifications, digests or anything that tells a developer about a violation** — developers are not users of
  this system (spec §1).
- **`seed_demo` / `seed_e2e` policy rows and the e2e specs** — the e2e step of this phase and phase 8's demo
  seed own those; this phase's tests build their own fixtures with `factory_boy`.
