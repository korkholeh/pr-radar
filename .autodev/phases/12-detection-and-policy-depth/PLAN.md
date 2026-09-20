# Phase 12 — Detection depth and policy depth

**Goal:** Move AI detection beyond "a regex over the PR description". Add the tool-written artefacts that
prove authorship, four new text detectors over data already stored, a repository-level tooling prior, a
second rule family for structural/behavioural heuristics, author-baseline signals, diff-level signals
computed from the clone the churn job already makes, and the fourteen policy rules that turn the PLANEKS
AI Engineering Standards into automatic checks.

**User-facing:** yes — Settings → Detection rules gains a second tab (Structural signals), the PR detail
page shows structural evidence and the new violations, the repository page shows its AI tooling, and
Settings → AI policy gains the new toggles.

**Source of the rule catalogue:** the review of `../planeks/AI-Setup` (`AI-ENGINEERING-STANDARDS.md`,
`AI-DEVELOPER-MANIFESTO.md`, `AGENTS.md`) against `apps/ai_detection` as built in phase 5.

---

## Context

### What exists

- **Detection, phase 5.** `apps/ai_detection/detectors.py` holds eight pure detectors keyed by
  `models.Detector`; each is `(compiled_pattern, DetectionContext) -> Iterator[DetectorMatch]`.
  `services.detect_pull_request` diffs wanted-vs-existing `AISignal` rows, then resolves `ai_status`,
  `ai_tools`, `ai_disclosure`. `rules.py` loads `fixtures/detection_rules.yaml` into `RuleDefinition`s;
  `seed_detection_rules` creates rows by name and only overwrites with `--update`.
- **Ten seeded rules**, all matching either a commit trailer, a bot login, a branch prefix, a label, an
  HTML comment or the PR body footer. Nothing matches a file path, a title, a reviewer, or any
  non-textual property of the PR.
- **`resolve_ai_status`:** `high` ⇒ `ai_explicit`; disclosure partial/substantial ⇒ `ai_disclosed`; any
  signal at all ⇒ `ai_suspected`; disclosure none ⇒ `no_ai`; else `unknown`.
- **Stored data the new detectors need is already there:** `PRFile.path` / `.status` / `.is_test`,
  `PullRequest.title`, `Review.reviewer → Identity`, `PullRequest.merged_by → Identity`,
  `Identity.person.is_bot`, `CheckStatus.rollup_state`, `Commit.trailers` / `.co_authors`.
- **Policy, phase 6.** `AIPolicy` (global, `effective_from`), `SensitivePathRule` (glob + `ai_mode`,
  optional project), `PolicyViolation` with nine `RuleCode`s, `apps/policy/messages.py` rendering
  `(rule_code, details_params)` into a translated sentence at read time.
- **Churn, phase 10.** `apps/churn/clones.py::ensure_clone` already makes a local clone per repository and
  `blame.py` already runs `git` against it under `GIT_ASKPASS`. **The working tree of every tracked
  repository is therefore already on disk** — diff-level analysis needs no new API budget.
- **Settings machinery.** `apps/catalog/setting_defs.py` + `catalog.services.get_*`. Group `ai` already
  carries `AI_COHORT_INCLUDE_SUSPECTED`, the `DISCLOSURE_*` keys and `DETECTION_DRY_RUN_PR_COUNT`.
- **Gates that bind this phase:** `tests/test_urls_login.py`, `tests/test_no_hardcoded_colors.py`,
  `tests/test_translations.py`, `tests/test_model_i18n.py`, `tests/test_http_guard.py` (**no unmocked
  outbound request**), `tests/test_admin.py`, `tests/test_docs.py`, the `metrics_doc` freshness test and
  `static/css/.build-manifest.sha256`.

### What this phase changes

`AISignal` stops meaning "a regex matched some text" and starts meaning "an observation about this PR",
carried either by a `DetectionRule` (regex) or by a `SignalRule` (structural). Confidence semantics are
preserved exactly: **only a tool-written artefact ever reaches `high`**, so `ai_explicit` keeps its current
meaning and no behavioural heuristic can forge it. Policy stops checking only disclosure and size and
starts checking the things the PLANEKS standards actually forbid — bypassed quality gates, weakened tests,
AI-only approval, unreviewed high-risk paths.

### Key files

| File | Change |
|---|---|
| `fixtures/detection_rules.yaml` | +11 Tier-A rules, +4 file-path rules, +5 stylometric rules, `provenance` key |
| `fixtures/detection_samples.yaml` | **new** — per-rule match / non-match corpus |
| `apps/ai_detection/rules.py` | `provenance` field, `unverified ⇒ not high`, sample validation |
| `apps/ai_detection/detectors.py` | +4 detectors, widened `DetectionContext`, bounded iteration |
| `apps/ai_detection/models.py` + migrations | `Detector` choices, `SignalRule`, `AISignal` dual FK + `evidence_code`/`evidence_params` |
| `apps/ai_detection/structural.py` | **new** — per-PR structural kinds |
| `apps/ai_detection/baselines.py` | **new** — author-history kinds |
| `apps/ai_detection/diffsignals.py` | **new** — clone-backed kinds |
| `apps/ai_detection/evidence.py` | **new** — code+params rendering (mirrors `policy/messages.py`) |
| `apps/ai_detection/services.py` | signal-family merge, composite scoring in `resolve_ai_status` |
| `apps/ai_detection/body_sections.py` | **new** — section parser extracted from `disclosure.py` |
| `apps/catalog/models.py` + migration | `Repository.ai_tooling_paths`, `ai_tooling_checked_at` |
| `apps/github_sync/queries.py` + fixtures | repo tree at HEAD, `reviewThreads.isResolved`, `REVIEW_REQUESTED_EVENT` |
| `apps/activity/models.py` + migration | `ReviewComment.is_resolved`, `PullRequest.review_requested_at` |
| `apps/policy/models.py` + migration | 14 `RuleCode`s, 8 `AIPolicy` fields, `SensitivePathRule.risk_level`, `designated_reviewers` |
| `apps/policy/rules.py`, `messages.py`, `services.py` | the new checks and their sentences |
| `apps/metrics/registry.py` | 5 new metrics |
| `apps/dashboards/`, `apps/ai_detection/{forms,views,urls}.py` + templates | Structural signals UI, PR detail |
| `docs/POLICY.md`, `docs/METRICS.md`, `docs/user/tune-ai-detection.md`, `docs/user/ai-policy.md`, `CHANGELOG.md` | docs |
| `locale/uk/LC_MESSAGES/django.po`, `static/css/app.css` | regenerated per stage |

### Note on running the gate

`.claude/tests-disabled` is present: test runs are switched off in this repository at the user's request.
Every stage below still specifies its tests as deliverables, and the gate command is unchanged, but the
executor must not run the test suite, Playwright or `make e2e` until that file is removed.

---

## Design

### D1. Provenance, and why it is not a database column

Several of the new patterns are recalled vendor behaviour, not behaviour read off a real PR in this
installation. Shipping them silently at `confidence: high` would fabricate `ai_explicit`. So
`RuleDefinition` gains:

```yaml
provenance: documented | observed | unverified
```

- `documented` — the vendor documents it; `notes` carries the URL.
- `observed` — seen in a real PR in this installation; `notes` carries `org/repo#N`.
- `unverified` — recalled or inferred. `seed_detection_rules` creates such a rule with
  `is_active=False`, so a lead turns it on only after confirming it against a real PR.

**Revised while implementing stage 1.** This section originally also had the loader reject
`unverified` combined with `confidence: high`. Writing the rules out showed that to be wrong. The
uncertainty an `unverified` rule carries is *"is this string ever emitted?"*, not *"what does it mean
when it is?"* — a tool that stamps a commit with its own name is conclusive whenever it fires. Capping
such a rule at `medium` would therefore not make it safer; it would convert every real match into a PR
that never reaches `ai_explicit`, which is a false negative in the product's headline metric. Seeding
the rule deactivated already removes all risk until a human confirms it, and the dry run gives them the
evidence to do so. So confidence and provenance stay orthogonal:

| | reliable signal | unreliable signal (a convention) |
|---|---|---|
| **string confirmed** | `high`, active | `low` + `disputed`, active |
| **string unconfirmed** | `high`, inactive | `low` + `disputed`, inactive |

`disputed` answers whether the signal means anything; `provenance` answers whether it ever appears.

It stays out of the database: `DetectionRule` is operator-owned once seeded, and a provenance column
would be a claim about a row the operator may have since rewritten. The YAML is the provenance record;
the UI shows it only on the seed list.

**A fixture is a regression lock, not proof.** `fixtures/detection_samples.yaml` gives every rule at least
one string it must match and one it must not, and a test enforces both. That catches a pattern edited into
uselessness; it does not confirm the vendor emits the string. Only `provenance` does.

### D2. New text detectors

Four additions to `Detector`, taking the total from eight to twelve:

| Code | Reads | Bound |
|---|---|---|
| `file_path` | `PRFile.path`, non-excluded | first match per rule; iteration stops there |
| `pr_title` | `PullRequest.title` | single string |
| `reviewer_identity` | `Review.reviewer.value` for reviews with a reviewer | first match per rule |
| `merged_by_identity` | `PullRequest.merged_by.value` | single string |

`DetectionContext` gains `file_paths`, `title`, `reviewer_values`, `merged_by_values`.
`_prefetch_for_detection` gains `files`, `reviews__reviewer`, `merged_by`; the test suite keeps an
`assertNumQueries` case so the widened prefetch cannot become an N+1.

**A PR with 400 files must not produce 400 signals.** `file_path` and `reviewer_identity` yield at most one
match per rule — the first in a deterministic order (path ascending; review `submitted_at` then `github_id`).
Evidence is the matching path or login, which is already short.

**`review_body` is deliberately not added.** `Review.body` is not stored (only `body_length`), and adding it
would put reviewer prose — the most opinion-bearing text in the dataset — into a tool whose users are the
reviewers' managers. Every AI reviewer worth detecting (CodeRabbit, Copilot review, Gemini Code Assist,
Qodo, Ellipsis) is identifiable from its bot login, so `reviewer_identity` covers the case without it.

### D3. Repository tooling prior

"The repository has a `CLAUDE.md`" proves the repository is configured for an agent. It says nothing about
any individual PR, so it must not become an `AISignal` — on a per-PR detector it would fire on every PR in
the repository and drown the real signals.

`Repository` gains `ai_tooling_paths` (JSON list) and `ai_tooling_checked_at`. Sync resolves the tree at
`HEAD` once per repository per run (one extra GraphQL node on the repository query, `object(expression:
"HEAD:") { ... on Tree { entries { name type } } }`, one level deep plus a probe of `.github/`), matches it
against a configurable glob list, and stores the hits. Rendered on the repository page and available as a
dashboard filter. Not a signal, not part of `ai_status`.

A *change* to one of those paths inside a PR is a different fact and stays a `file_path` rule, at
`confidence: low, disputed: true`.

The glob list must cover `.agents/` and `.claude/skills/`, not only `.claude/`: the probe over the
synced data found `.agents/skills/**` and `AGENTS.md` in six PRs and no `.cursor/`, `.windsurf/` or
`.aider/` at all. The seeded list is therefore ordered by what this installation actually contains,
with the rest kept as inactive `unverified` entries.

### D4. The second rule family

```python
class SignalRule(models.Model):
    name = CharField(unique=True)
    kind = CharField(choices=SignalKind.choices)  # the heuristic, not a regex
    params = JSONField(default=dict)  # thresholds, validated per kind
    tool = CharField(choices=Tool.choices, default=Tool.OTHER)
    confidence = CharField(choices=Confidence.choices)  # CheckConstraint: != high
    is_active = BooleanField(default=True)
    notes = TextField(blank=True)
```

A `CheckConstraint` forbids `confidence = high` at the database level, not just in a validator: the
invariant "only a tool-written artefact proves AI authorship" is the one property of this feature that
must not be reachable by editing a row in the admin.

`AISignal` becomes dual-sourced:

- `rule` → `DetectionRule`, now `null=True`.
- `signal_rule` → `SignalRule`, `null=True`, `on_delete=PROTECT`.
- `CheckConstraint`: exactly one of the two is set.
- The existing `uniq_aisignal_pr_rule_evidence` becomes conditional on `rule__isnull=False`; a mirrored
  `uniq_aisignal_pr_signal_rule_evidence` covers the other family.

**Structural evidence is generated text, so it is stored as a code plus parameters** (CLAUDE.md). `AISignal`
gains `evidence_code` (blank for regex signals) and `evidence_params` (JSON). `apps/ai_detection/evidence.py`
renders `(evidence_code, evidence_params)` into a full translated sentence with named placeholders, exactly
as `policy/messages.py` does — unknown code renders the code, missing param renders `?`, neither raises.
`evidence_hash` for a structural signal is the sha256 of the canonical JSON of `(code, sorted params)`, so
re-running with the same inputs is idempotent and a threshold change produces a new row and retires the old.

The regex `evidence` field keeps its meaning: it is a **quote of source data**, not generated prose, so it
needs no translation and stays as it is.

### D5. The three structural families and where each runs

| Family | Depends on | Runs in | Module |
|---|---|---|---|
| Per-PR | only this PR's own rows | `detect_pull_request`, inside the sync pipeline | `structural.py` |
| Baseline | the author's other PRs | nightly task + `recompute` | `baselines.py` |
| Diff | the file contents of the change | the churn job, from the existing clone | `diffsignals.py` |

The split is forced, not stylistic. A baseline signal's truth changes when *other* PRs arrive, so computing
it during one PR's sync would freeze a stale verdict; it is recomputed over a rolling window instead. A diff
signal needs bytes that are not in the database at all, and the only place those bytes already exist locally
is the churn clone.

Every kind is a pure function returning `Iterator[StructuralMatch(code, params)]`; the writer is shared, so
all three families converge on the same wanted-vs-existing diff `detect_pull_request` already implements.

**Per-PR kinds** (`structural.py`):

| Kind | Fires when | Default params |
|---|---|---|
| `fast_large_pr` | `merged_at − first_commit_at` too small for the size | `min_lines: 400, min_files: 8, max_hours: 2` |
| `commit_burst` | many near-simultaneous substantial commits | `min_commits: 5, max_gap_seconds: 90, min_lines_per_commit: 20` |
| `single_large_commit` | one commit carries a large multi-file change | `min_lines: 300, min_files: 5` |
| `mass_file_creation` | many files added at once across directories | `min_added_files: 10, min_directories: 3` |
| `unused_new_dependency` | manifest/lockfile changed, nothing in the diff imports the package | `manifests: [pyproject.toml, package.json, requirements*.txt]` |
| `instant_review_response` | repeated new commit moments after a review comment | `max_minutes: 5, min_occurrences: 3` |

`unused_new_dependency` needs the diff, so it is registered in stage 4 and returns nothing until stage 6
wires the clone — registered-but-empty rather than absent, the same convention phase 6 used for
`ci_first_pass_rate`.

**Baseline kinds** (`baselines.py`), all computed per author in `REPORT_TIMEZONE`:

| Kind | Fires when | Default params |
|---|---|---|
| `throughput_shift` | PRs/week or effective lines/PR jump against the author's own trailing median, normalised by the team median so a busy sprint does not fire it | `window_weeks: 8, ratio: 2.5, sustained_weeks: 2` |
| `off_hours_volume` | commits outside the author's own typical hours carrying above-median volume | `percentile: 0.9, min_share: 0.4` |
| `test_ratio_lockstep` | test:impl line ratio nearly constant across many PRs | `min_prs: 10, max_variance: 0.15` |
| `body_style_shift` | PR-body length and structure break from the author's own history | `window_weeks: 8, min_prs: 10, length_ratio: 4` |

`body_style_shift` is where stylometry belongs. A fixed phrase list ("comprehensive", "delve", an em dash)
is a weak per-PR signal and a good per-author *change* signal; it ships as a handful of `disputed: true`,
`confidence: low` regex rules **and** as this baseline kind, and only the baseline kind carries weight in
scoring.

Every baseline kind refuses to emit below `MIN_SAMPLE` (5) observations, per the project-wide rule that a
thin sample produces `None`, never a number.

**Diff kinds** (`diffsignals.py`), off the churn clone:

| Kind | Fires when |
|---|---|
| `comment_density_outlier` | comment/docstring lines far above the repository median for that language |
| `duplicated_blocks` | near-identical blocks repeated across files of one PR |
| `wholesale_reformat` | large `changed_files` with small semantic delta (`git diff -w` far smaller than `git diff`) |

`wholesale_reformat` is the cheapest and most reliable of the three — it is one extra `git diff -w --stat`
against a tree that is already checked out.

### D6. Composite scoring

`resolve_ai_status` gains one rule and keeps every existing one:

```
high confidence signal                               -> ai_explicit     (unchanged)
disclosure partial/substantial                       -> ai_disclosed    (unchanged)
>= AI_SUSPECTED_MIN_STRUCTURAL_KINDS distinct
  structural kinds at medium, or any regex signal    -> ai_suspected    (new left branch)
disclosure none                                      -> no_ai           (unchanged)
otherwise                                            -> unknown         (unchanged)
```

Default `AI_SUSPECTED_MIN_STRUCTURAL_KINDS = 2`, counted over **distinct kinds**, not rows: five commit
bursts in one PR are one kind of evidence. A single structural signal never moves `ai_status`; it is still
written, still shown on the PR page, and still exportable — it is evidence a lead reads, not a verdict.

This changes `ai_status` for existing rows, therefore every AI metric. It is behind a setting, announced in
`CHANGELOG.md`, and stage 5 ends with `manage.py recompute` over all PRs.

### D7. Structural signals are never fed back from outcome data

`ChurnResult.churn_ratio`, `has_followup_fix` and `is_revert` are **outcomes measured for the AI cohort**,
never inputs to deciding who is in it. Using them as detectors would make the AI-quality dashboards
self-proving. A test asserts no `SignalKind` reads `churn`, `is_revert` or `has_followup_fix`.

### D8. Policy: the PLANEKS standards as checks

Fourteen new `RuleCode`s. Each maps to a numbered rule or a named section of
`AI-ENGINEERING-STANDARDS.md`; the mapping is recorded in `docs/POLICY.md`.

| Code | Standard | Evaluated from | Severity |
|---|---|---|---|
| `QUALITY_GATE_BYPASSED` | Security rules, "Do not bypass controls" | merged while `CheckStatus.rollup_state != SUCCESS`; `[skip ci]` in a commit message; diff adds `continue-on-error: true` or removes a CI step | high |
| `TEST_WEAKENED` | R6 | `PRFile(is_test=True, status=removed)` while non-test files changed; diff adds a skip/xfail marker | high |
| `AI_ONLY_APPROVAL` | R10 | every approving review is from an identity whose person `is_bot` | high |
| `AI_REVIEW_MISSING` | PR requirements, "Copilot is the first reviewer" | no review from any identity in `ai_reviewer_identities` | medium |
| `AI_REVIEW_UNRESOLVED` | PR requirements, "the author replies to every comment" | merged with unresolved threads opened by an AI reviewer | medium |
| `HIGH_RISK_NO_PLAN` | R3 | touches a `risk_level=high` path, body has no plan/risk section | high |
| `RISK_LEVEL_MISSING` | PR requirements | body states no risk level | low |
| `VERIFICATION_MISSING` | PR requirements | body states no checks run / how verified | low |
| `TASK_LINK_MISSING` | PR requirements | body links no task | low |
| `NEW_DEPENDENCY_AI` | R7 | AI PR changes a manifest or lockfile | medium |
| `MIGRATION_AI_INSUFFICIENT_REVIEW` | "Requires extra human attention" | AI PR with a migration and no approval from `designated_reviewers` | high |
| `SECRET_ARTIFACT_COMMITTED` | Security rules | diff adds `.env`, a key file, `.claude/settings.local.json`, an agent chat-history file | high |
| `AGENT_CONFIG_CHANGED` | "Changes to these files go through the normal PR review" | `CLAUDE.md` / `AGENTS.md` / `.claude/**` changed | low |
| `SCOPE_CREEP` | R4 | AI PR spans more top-level modules than the body's stated scope, or trips `wholesale_reformat` | medium |

`RUBBER_STAMP_ON_AI_PR` is promoted from the existing `PullRequest.is_rubber_stamp` boolean: an AI PR
approved with an empty review body, zero comments, inside `RUBBER_STAMP_MAX_MINUTES`. It is the most direct
measurement of the manifesto's "we do not accept code we do not understand" and it needs no new data.

`AIPolicy` gains `require_ai_review_first`, `forbid_ai_only_approval`, `require_risk_level`,
`require_task_link`, `require_verification_note`, `forbid_ci_bypass`, `high_risk_min_approvals` (default 2),
`max_effective_lines_by_risk` (JSON, default `{"low": null, "medium": 800, "high": 400}`), plus
`designated_reviewers` (M2M to `catalog.Person`) and `ai_reviewer_identities` (JSON list of logins).
Every new check is **off by default**, so an upgrade produces no violations until a lead enables it.

`SensitivePathRule` gains `risk_level` (low / medium / high) alongside `ai_mode`. The PLANEKS risk table
seeds as globs: high — `**/migrations/**`, `**/auth*/**`, `**/payments/**`, `**/billing/**`,
`.github/workflows/**`, `**/settings/**`, `infra/**`, `ansible/**`, `docker/**`; medium — `**/api/**`,
`**/serializers.py`, `pyproject.toml`, `package.json`, `*.lock`. A PR's risk level is the maximum over its
non-excluded files, and it drives `max_effective_lines_by_risk` and `high_risk_min_approvals`.

Body-section checks (`RISK_LEVEL_MISSING`, `VERIFICATION_MISSING`, `TASK_LINK_MISSING`, `HIGH_RISK_NO_PLAN`)
reuse the tolerant heading parser already written for disclosure. `_find_section`, `_heading_candidate` and
the boundary helpers move from `disclosure.py` into `body_sections.py` unchanged; `disclosure.py` imports
them. Headings and labels are configurable settings, like the disclosure ones, because a client's PR
template will not use PR Radar's wording.

### D9. What this phase does not measure

`AI-ENGINEERING-STANDARDS.md` names four metrics. PR Radar covers review time and post-merge defects
directly. **AI cost per task and the team's rating of the tool are not derivable from GitHub data** and are
not invented here. `docs/POLICY.md` says so explicitly.

The same document states: *"We do not use the number of generated lines, prompts or AI PRs to evaluate an
employee's performance."* That is recorded in `docs/POLICY.md` as a product constraint, and the person-level
pages keep leading with compliance and quality, not volume.

---

## Stages

Each stage is independently shippable and ends with the full gate (ruff check, ruff format --check, mypy,
`makemigrations --check --dry-run`, `manage.py check`, the test suite), plus `make css` and `make messages`
when templates or strings changed, and the Ukrainian translation for every string introduced in that same
stage.

### Stage 1 — Seeded rule catalogue and provenance

**Deliverables**

- `provenance` in `RuleDefinition` and the YAML schema, required on every rule.
- `seed_detection_rules` creates an `unverified` rule with `is_active=False`; existing reconciliation
  (create-by-name, overwrite only under `--update`) is unchanged, and `is_active` is written only at
  creation so a lead's activation survives every later re-seed.
- +11 Tier-A rules: Claude Code session trailer and commit-message footer, Codex cloud task link, Devin
  session link, Cursor co-author trailer, Copilot coding-agent body marker, Copilot co-author trailer,
  Gemini CLI co-author trailer, Jules task link, CodeRabbit marker, robot-emoji (`low`, `disputed`).
- `fixtures/detection_samples.yaml` and a test asserting every seeded rule has at least one match and one
  non-match sample and behaves accordingly.
- `docs/user/tune-ai-detection.md`: how to confirm an `unverified` rule against a real PR and activate it.

**Acceptance**

- A rule with a missing or unknown `provenance` fails to load, naming the field.
- Seeding a fresh database produces the full catalogue; `unverified` rules land inactive, and a lead
  who activates one keeps it activated across a re-seed and a `--update`.
- Re-seeding changes no row; `--update` updates pattern/tool/confidence/notes and nothing else.
- Every rule matches its samples and rejects its counter-samples.

### Stage 2 — Four new text detectors

**Deliverables**

- `Detector` choices `file_path`, `pr_title`, `reviewer_identity`, `merged_by_identity` + migration.
- Detector functions, widened `DetectionContext`, widened prefetch, bounded iteration.
- +4 `file_path` rules: aider chat history, SpecStory transcript, `.claude/settings.local.json`,
  agent-configuration touched (`low`, `disputed`). The last one's glob covers
  `^\.(agents|claude|cursor|windsurf|codex|gemini|roo|cline|continue|junie|aider|specstory|kiro)/`
  plus `^(CLAUDE|AGENTS|GEMINI)\.md$`, `^\.cursorrules$`, `^\.windsurfrules$`,
  `^\.github/copilot-instructions\.md$`, `^\.mcp\.json$` — `.agents/` included, since it is the one
  this installation actually uses.
- One `reviewer_identity` rule per known AI reviewer bot login.
- +5 stylometric `pr_body_footer` rules, all `low` + `disputed` + `unverified`, therefore inactive on seed.
- Settings → Detection rules: the four new detectors appear in the form and the dry run works for each.

**Acceptance**

- A PR whose diff adds an aider chat-history file is `ai_explicit` with that path as evidence.
- A PR with 400 changed files produces at most one signal per `file_path` rule.
- `assertNumQueries` on `detect_pull_requests` over 20 PRs is unchanged by the widened prefetch.
- A stylometric rule alone never produces `ai_explicit`.

### Stage 3 — Repository tooling prior

**Deliverables**

- `Repository.ai_tooling_paths`, `ai_tooling_checked_at` + migration.
- Repository tree probe in the sync query and mapper; new JSON fixtures; setting `AI_TOOLING_PATH_GLOBS`.
- Repository page section and a dashboard filter "repositories configured for an agent".

**Acceptance**

- Syncing the fixtures records the expected paths; a repository with none records `[]`, not `null`.
- A test asserts no `AISignal` is created by the probe.
- Any unmocked outbound request still fails the suite.

### Stage 4 — Structural signal engine, per-PR kinds

**Deliverables**

- `SignalRule` model with the `confidence != high` `CheckConstraint`; per-kind `params` validation in
  `clean()`; admin registration.
- `AISignal`: nullable `rule`, new `signal_rule`, exactly-one `CheckConstraint`, `evidence_code`,
  `evidence_params`, the two conditional uniqueness constraints; data migration leaving existing rows
  untouched.
- `apps/ai_detection/evidence.py` with a full translated sentence per code, and its uk translations.
- `structural.py` with the six per-PR kinds (`unused_new_dependency` registered, returning nothing).
- `services.detect_pull_request` merges both families into one wanted-vs-existing diff.
- `fixtures/signal_rules.yaml` + `seed_signal_rules`, mirroring the detection-rule seeding.
- Settings → Structural signals: list, create/edit, activate/deactivate, dry run over the last N PRs.
- PR detail page renders structural evidence through `evidence.py`.

**Acceptance**

- `SignalRule(confidence="high")` is rejected by the database, not only by the form.
- An `AISignal` with both FKs set, or neither, is rejected.
- Re-running detection over unchanged data writes no row and deletes none.
- Changing a threshold retires the old signal and writes a new one.
- A test asserts every `evidence_code` has a message entry and a non-fuzzy uk translation.
- A grep test asserts no rendered English sentence is stored in `AISignal`.

### Stage 5 — Baseline kinds and composite scoring

**Deliverables**

- `baselines.py` with the four author-history kinds, `MIN_SAMPLE` floor, `REPORT_TIMEZONE` day boundaries
  through the existing helper.
- Nightly huey task + a `recompute --baselines` path.
- `AI_SUSPECTED_MIN_STRUCTURAL_KINDS` setting (default 2) and the extended `resolve_ai_status`.
- `manage.py recompute` over all PRs at the end of the stage; `CHANGELOG.md` entry stating that
  `ai_status` may change for historical PRs.
- A test asserting no `SignalKind` reads churn, revert or follow-up-fix data (D7).

**Acceptance**

- An author with fewer than `MIN_SAMPLE` PRs produces no baseline signal.
- A team-wide throughput rise (everyone doubled) produces no `throughput_shift`; one author doubling
  against a flat team does.
- One structural signal alone leaves `ai_status` unchanged; two distinct kinds make it `ai_suspected`.
- No structural signal, in any combination, produces `ai_explicit`.

### Stage 6 — Diff-level kinds from the churn clone

**Deliverables**

- `diffsignals.py` with `wholesale_reformat`, `comment_density_outlier`, `duplicated_blocks`.
- `unused_new_dependency` completed against the diff.
- A `churn`-pipeline hook so the analysis reuses `ensure_clone`; per-repository opt-in setting; the same
  timeout/size guards `blame.py` already applies; `git` still only ever sees credentials via `GIT_ASKPASS`.
- Diff-derived inputs for `QUALITY_GATE_BYPASSED`, `TEST_WEAKENED` and `SECRET_ARTIFACT_COMMITTED`,
  exposed as a small `DiffFacts` dataclass the policy engine consumes.

**Acceptance**

- A repository with no clone available produces no diff signals and no error.
- A PR that only reformats trips `wholesale_reformat` and nothing else.
- A leak test finds no token in any new subprocess argv, log line or stored field.
- The whole stage adds zero GitHub API calls.

### Stage 7 — Policy depth

**Deliverables**

- 14 `RuleCode`s, their `RULE_MESSAGES` entries and uk translations.
- 8 `AIPolicy` fields + `designated_reviewers` + `ai_reviewer_identities`, all defaulting to off.
- `SensitivePathRule.risk_level`, the seeded PLANEKS risk globs, PR risk level as the max over files.
- `body_sections.py` extracted from `disclosure.py`; the four body-section checks and their settings.
- `reviewThreads.isResolved`, `REVIEW_REQUESTED_EVENT` in the sync queries; `ReviewComment.is_resolved`,
  `PullRequest.review_requested_at`; new fixtures.
- Settings → AI policy form sections; PR detail and the violations console show the new codes.

**Acceptance**

- Upgrading an existing database and running `recompute` produces zero new violations until a toggle is
  enabled — a test asserts this explicitly.
- A PR approved only by a bot raises `AI_ONLY_APPROVAL` when `forbid_ai_only_approval` is on.
- A merged PR with a failing check rollup raises `QUALITY_GATE_BYPASSED`.
- An AI PR with a migration and no designated-reviewer approval raises
  `MIGRATION_AI_INSUFFICIENT_REVIEW`; the same PR with that approval raises nothing.
- A 500-line AI PR touching `**/migrations/**` exceeds the high-risk limit while the same PR touching only
  docs does not.
- Every violation renders in Ukrainian with correct placeholders and plurals.

### Stage 8 — Metrics, dashboards, docs

**Deliverables**

- Five metrics in the registry: `ai_only_approval_rate`, `quality_gate_bypass_rate`,
  `ai_review_coverage`, `high_risk_ai_pr_rate`, `structural_signal_rate`. `MIN_SAMPLE` greying applies.
- Regenerated `docs/METRICS.md` (`manage.py metrics_doc`) and its freshness test.
- AI-policy dashboard section for the new codes; repository page tooling section; person page keeps
  compliance and quality ahead of volume.
- `docs/POLICY.md`: the standards-to-code mapping table, the two metrics PR Radar cannot compute, and the
  "volume is not a performance measure" constraint.
- `docs/user/ai-policy.md` task page; `docs/user/README.md` index entry; `CHANGELOG.md`.
- `docs/dev/architecture.md`: the two signal families and the three execution sites.
- ADR in `docs/dev/adr/`: **"Structural signals never reach high confidence"** — the one decision here that
  is expensive to reverse.
- e2e specs: enable a structural rule and see a PR's status change; enable a policy toggle and see a
  violation appear and be waived.
- `make css`, `make messages`, both regenerated artefacts committed.

**Acceptance**

- `metrics_doc` output matches the committed file.
- Project isolation holds for every new metric across page, chart JSON, CSV and XLSX.
- Every new page redirects an anonymous user to login.
- No colour literal outside `static/css/tokens.css`.
- The full gate is green.

---

## Risks

| # | Risk | Mitigation |
|---|---|---|
| 1 | A recalled vendor pattern is wrong and silently never fires, or fires on innocent text | `provenance`; `unverified` cannot be `high` and seeds inactive; per-rule match/non-match corpus |
| 2 | Structural heuristics accuse an honest developer | Never `high`, enforced by a `CheckConstraint`; two distinct kinds required for `ai_suspected`; every signal shows its evidence with the thresholds that produced it; violations are waivable |
| 3 | `resolve_ai_status` change silently rewrites history | Behind a setting, announced in `CHANGELOG.md`, stage ends with an explicit `recompute` |
| 4 | Detection cost per PR grows with files × rules | Bounded iteration (one match per rule), rules compiled once per run, `assertNumQueries` on the widened prefetch |
| 5 | Diff analysis makes the sync slow or flaky | It runs in the churn job, not in sync; per-repository opt-in; reuses the existing clone, timeout and size guards |
| 6 | 14 new violation codes flood an existing installation on upgrade | Every new check defaults to off; a test asserts a zero-violation upgrade |
| 7 | Detection quality becomes self-proving | D7: outcome data is never a detector input, asserted by test |
| 8 | The new GraphQL fields are unavailable on an older GHES | The client already raises a named `GitHubSchemaError` on a missing path; the tree probe and thread resolution degrade to "unknown" rather than failing the sync |
| 9 | English text stored for structural signals, breaking the bilingual rule | `evidence_code` + `evidence_params`; a grep test rejects a stored sentence |

## Out of scope

- Any LLM-based judgement of the diff (for example, "does the description match the change?").
- AI cost per task and team satisfaction — not derivable from GitHub data (D9).
- Per-project AI policies; `AIPolicy` stays global with `effective_from`.
- Storing review or comment prose (D2).
- Writing anything back to GitHub: no PR comment, no label, no status check.

## Ordering

Stages 1 and 2 are independent of everything else and can ship first for immediate value. Stage 4 depends
on 2 only for the shared dry-run UI. Stage 5 depends on 4. Stage 6 depends on 4 and on the churn job.
Stage 7 depends on 6 for its three diff-derived checks and can otherwise proceed in parallel. Stage 8 is
last.
