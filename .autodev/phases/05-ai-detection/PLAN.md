# Phase 5 — AI detection

**Goal:** Eight detectors over stored data, a tolerant disclosure parser, a resolved `ai_status` per PR, and
detection rules the admin edits without a release.

**User-facing:** yes — Settings → Detection rules (list, create/edit, activate/deactivate, dry run) and the PR
detail page's AI section (signals with evidence, disclosure, resolved status).

---

## Context

### What exists

- **Models, never written to.** `apps/ai_detection/models.py` (phase 2) already defines `Detector` (the exact
  eight detector codes), `Tool` (10 values incl. `other`), `Confidence` (high/medium/low), `DetectionRule`
  (`name`, `detector`, `pattern`, `tool`, `confidence`, `is_active`, `notes`, timestamps) and `AISignal`
  (`pull_request`, nullable `commit`, `rule` PROTECT, `tool`, `confidence`, `evidence` max_length=200,
  `detected_at`). **No uniqueness constraint exists on `AISignal` yet**, and no code has ever created one.
  `apps/ai_detection/factories.py` has `DetectionRuleFactory`/`AISignalFactory`.
- **PR fields waiting to be filled.** `PullRequest.ai_status` (`AIStatus`: ai_explicit / ai_disclosed /
  ai_suspected / no_ai / unknown), `ai_tools` (JSON list), `ai_disclosure` (`AIDisclosure`: none / partial /
  substantial / missing / ambiguous). All still at their defaults — phase 4 listed them as "phase 5" in its
  Out of scope.
- **The inputs every detector needs are already stored** by phase 3's sync: `PullRequest.body`, `title`,
  `head_ref`, `labels` (JSON list), `author` → `Identity`; `Commit.message`, `Commit.trailers`
  (`dict[str, list[str]]`, keys keep their original case — `mappers.parse_trailers`), `Commit.co_authors`
  (`[{"name","email"}]`), `Commit.author_identity` / `author_email_identity` / `committer_identity` →
  `Identity(kind, value)`. `PullRequestCommit` joins commits to a PR.
- **Settings machinery.** `apps/catalog/setting_defs.py` + `catalog.services.get_list/get_int/get_bool/get_dict`.
  The `ai` group **already carries** `AI_COHORT_INCLUDE_SUSPECTED` (False), `DISCLOSURE_SECTION_HEADINGS`
  (`["AI assistance"]`), `DISCLOSURE_LABELS_NONE` / `_PARTIAL` / `_SUBSTANTIAL`, `DISCLOSURE_TOOLS_LABELS`
  (`["AI tools used"]`). Two keys are missing (T1).
- **The pipeline hook.** `apps/github_sync/pipeline.py::process_pull_request(pull_request_id)` currently calls
  `resolve_identities_for_pull_request()` then `derive_pull_request()`. It runs after the PR's transaction
  commits; a raising hook is logged and masked, the run continues.
- **Derive as the worked example of an idempotent rule module**: `apps/activity/derive.py` — pure function of
  stored rows, `@transaction.atomic`, `save(update_fields=...)`, plus `derive_pull_requests(queryset)` as the
  bulk entry point. Detection copies that shape exactly.
- **Scope choke point.** `apps.accounts.selectors.scope_for_user(user) -> ScopeFilter` (still
  `unrestricted=True` until phase 9) and `apps/activity/selectors.py::_scoped()` as the pattern every new
  selector follows.
- **UI machinery.** `config.htmx.is_htmx()`, the `catalog.manage_settings` permission, `apps/catalog/views.py`
  as the worked settings-page example (full page vs `partials/` fragment on the same URL),
  `accounts.services.record_audit()`, `templates/partials/nav.html`.
- **Gates that bind this phase.** `tests/test_urls_login.py` (every new named URL must redirect anonymously),
  `tests/test_no_hardcoded_colors.py`, `tests/test_translations.py` (no empty/fuzzy msgstr, matching
  placeholders, uk canary), `tests/test_model_i18n.py`, `tests/test_http_guard.py` (**any unmocked outbound
  request fails the suite** — this phase makes none), `tests/test_admin.py`, `static/css/.build-manifest.sha256`
  (any new template or `.py` invalidates it → `make css`).
- **What does not exist yet:** `manage.py recompute`, any `fixtures/` directory at the repo root, any PR detail
  page, `apps/ai_detection/{detectors,disclosure,services,selectors,rules,forms,views,urls}.py`.

### What this phase changes

Sync stops being the only thing that interprets a PR. After `derive`, a third pure step reads the stored rows,
matches admin-editable `DetectionRule`s against them, writes `AISignal` rows with an inspectable evidence
fragment, parses the PR-template disclosure, and resolves the three `ai_*` fields. Everything is re-runnable over
stored PRs (`manage.py recompute`) and configurable from the UI (Settings → Detection rules) without a release.

### Key files

| File | Change |
|---|---|
| `apps/catalog/setting_defs.py` + `migrations/000X` | two new `ai` settings |
| `apps/ai_detection/models.py` + `migrations/0002` | `AISignal.evidence_hash` + unique constraint; `DetectionRule.name` unique + pattern validation |
| `apps/ai_detection/detectors.py` | the eight detectors, the evidence helper, the detection context |
| `apps/ai_detection/disclosure.py` | the tolerant PR-template parser |
| `apps/ai_detection/services.py` | `detect_pull_request`, `detect_pull_requests`, `resolve_ai_status`, `dry_run_rule` |
| `apps/ai_detection/selectors.py` | `signals_for_pull_request`, `ai_cohort_pull_requests`, `recent_pull_requests` |
| `apps/ai_detection/rules.py` + `fixtures/detection_rules.yaml` | the seed rule set and its loader |
| `apps/ai_detection/management/commands/seed_detection_rules.py` | seeding command |
| `apps/github_sync/pipeline.py` | third pipeline step |
| `apps/github_sync/management/commands/recompute.py` | re-run derive + detect over stored PRs |
| `apps/ai_detection/{forms,views,urls}.py` + templates | Settings → Detection rules, with dry run |
| `apps/dashboards/views.py` + template | minimal PR detail page showing signals, evidence and disclosure |
| `docs/pull_request_template.md`, `docs/CONFIGURATION.md`, `docs/user/tune-ai-detection.md`, `CHANGELOG.md` | docs |
| `locale/uk/LC_MESSAGES/django.po`, `static/css/app.css` | regenerated in this phase |

---

## Design

### Data model

**`AISignal` gains `evidence_hash`** (`CharField(max_length=64)`, sha256 of the normalised evidence string) and
`UniqueConstraint(fields=["pull_request", "rule", "evidence_hash"], name="uniq_aisignal_pr_rule_evidence")` —
exactly the key `.autodev/ARCHITECTURE.md` (line 371) mandates. The hash, not the raw text, is in the key because
`evidence` is a 200-char CharField and SQLite indexes on long text are wasteful. `commit` stays outside the key:
if two commits of the same PR carry the identical trailer, that is **one** fact about the PR, and the signal
points at the first matching commit in a deterministic order (`committed_at`, then `sha`).

**`DetectionRule.name` becomes `unique=True`** so `seed_detection_rules` can `get_or_create(name=...)` and so two
rules cannot shadow each other in the settings list. `DetectionRule.clean()` rejects a pattern that does not
compile as a regex, so the admin gets a visible field error instead of a rule that silently never matches.
`notes` carries the **source comment** the deliverable asks for (a URL or a one-line provenance note); a seeded
rule always has one, asserted by test.

**No new model.** Dry-run results are computed and rendered, never stored.

### Settings (`apps/catalog/setting_defs.py`, group `ai`)

| Key | Type | Default | Why |
|---|---|---|---|
| `DETECTION_DRY_RUN_PR_COUNT` | int | 50 | the "last N stored PRs" the rule dry run scans |
| `DISCLOSURE_TOOL_ALIASES` | dict | `{"claude_code": ["claude code","claude"], "copilot": ["copilot","github copilot"], "cursor": ["cursor"], "codex": ["codex"], "devin": ["devin"], "gemini": ["gemini"], "aider": ["aider"], "windsurf": ["windsurf"], "chatgpt": ["chatgpt","gpt"]}` | maps free text in the template's "AI tools used" line onto `Tool` values |

The existing `AI_COHORT_INCLUDE_SUSPECTED`, `DISCLOSURE_SECTION_HEADINGS` and `DISCLOSURE_LABELS_*` keys are the
configurable synonyms the spec (§6.2) asks for; nothing new is needed for them.

### Module layout

```python
# apps/ai_detection/detectors.py  — pure, no DB writes
@dataclass(frozen=True)
class DetectionContext:                 # everything a detector may read, loaded once per PR
    pull_request: PullRequest
    commits: tuple[Commit, ...]         # ordered committed_at, sha
    author_values: tuple[str, ...]      # login and/or email of the PR author identity
    labels: tuple[str, ...]
    head_ref: str
    body: str

@dataclass(frozen=True)
class DetectorMatch:
    evidence: str                       # already truncated to <= EVIDENCE_MAX_LENGTH (200)
    commit_id: int | None

Detector = Callable[[re.Pattern[str], DetectionContext], Iterator[DetectorMatch]]
DETECTORS: dict[str, Detector]          # keyed by models.Detector values — all eight, no more

def load_context(pull_request_id: int) -> DetectionContext
def evidence_fragment(haystack: str, match: re.Match[str]) -> str   # <=200 chars, centred on the match
```

Every rule pattern is compiled **once per detection run** with `re.IGNORECASE | re.MULTILINE`; a pattern that
fails to compile is skipped with a `logger.warning` naming the rule (never raised — one bad admin-entered rule
must not stop detection for every PR).

What each detector reads:

| detector | haystack |
|---|---|
| `commit_trailer` | each commit's `trailers` re-rendered as `Key: value` lines |
| `commit_author` | each commit's `author_identity` / `author_email_identity` / `committer_identity` values plus `co_authors` names and emails |
| `pr_author` | the PR author identity's value |
| `pr_body_footer` | the PR body |
| `html_comment` | only the `<!-- ... -->` spans of the PR body |
| `label` | each label name |
| `branch_pattern` | `head_ref` |
| `commit_message` | each commit's `message` |

`pr_body_footer` and `html_comment` are deliberately different haystacks over the same body: a marker inside an
HTML comment is invisible to a reviewer and must be attributable to its own rule and confidence.

```python
# apps/ai_detection/disclosure.py  — pure; no DB access beyond the settings it is handed
@dataclass(frozen=True)
class DisclosureConfig:                 # built from AppSettings; injectable so tests need no DB writes
    headings: tuple[str, ...]
    none_labels: tuple[str, ...]
    partial_labels: tuple[str, ...]
    substantial_labels: tuple[str, ...]
    tools_labels: tuple[str, ...]
    tool_aliases: Mapping[str, tuple[str, ...]]

@dataclass(frozen=True)
class DisclosureResult:
    disclosure: str                     # AIDisclosure value
    tools: tuple[str, ...]              # canonical Tool values, else the raw text lowercased

def load_config() -> DisclosureConfig
def parse_disclosure(body: str, config: DisclosureConfig) -> DisclosureResult
```

Parser rules, deliberately tolerant (spec §6.2):

1. Find the section: any line whose text, stripped of leading `#`/`*`/`-` and surrounding whitespace and
   case-folded, equals a configured heading. No heading → `missing`.
2. The section runs to the next heading line or EOF.
3. A checkbox line is `[ ]` / `[x]` / `[X]` (any leading bullet, any spacing) followed by a label; the label
   matches a configured synonym by case-folded **prefix** (so `Partial (autocomplete, snippets, review)` matches
   `Partial`).
4. Zero ticked boxes → `missing`. Exactly one → its category. Two or more → `ambiguous` (even if they agree —
   two ticks means the author did not answer the question).
5. A ticked box whose label matches no synonym is counted toward ambiguity but maps to no category; if it is the
   only tick the result is `missing` (nothing was actually disclosed) — the malformed-template case.
6. Tools: the first line **anywhere in the body** starting with a configured tools label — not scoped to the
   AI-assistance section, because the shipped `docs/pull_request_template.md` puts "AI tools used" in its own
   ATX section outside it, and an in-section-only scan would find no tools for the project's own recommended
   template. When the label's line has no remainder, the next non-blank line is used instead, bounded by the
   same section-end predicate (so a fallback never reads past the next heading). The remainder is split on
   `,` / `/` / ` and `, HTML comments are stripped, each part is mapped through `tool_aliases`, and anything
   unmatched is kept as its lowercased text (capped at 40 chars, max 10 tools). The template's placeholder
   comment alone yields no tools.

```python
# apps/ai_detection/services.py
def detect_pull_request(pull_request_id: int) -> None      # atomic; the pipeline's third step
def detect_pull_requests(queryset: QuerySet[PullRequest]) -> int
def resolve_ai_status(confidences: Collection[str], disclosure: str) -> str
def ai_cohort_statuses() -> frozenset[str]
def dry_run_rule(rule: DetectionRule, scope: ScopeFilter, limit: int) -> list[DryRunMatch]   # never writes
```

`detect_pull_request` is the same shape as `derive_pull_request`:

```
ctx      = load_context(pr_id)
wanted   = {(rule_id, evidence_hash): payload for every match of every ACTIVE rule}
existing = {(rule_id, evidence_hash): signal for pr.ai_signals.all()}
create   AISignal rows for wanted - existing
delete   signals for existing - wanted          # deactivated rule, edited pattern, edited PR body
disclosure = parse_disclosure(pr.body, load_config())
pr.ai_status    = resolve_ai_status({s.confidence for s in signals}, disclosure.disclosure)
pr.ai_tools     = sorted(set(signal tools) | set(disclosure.tools))
pr.ai_disclosure = disclosure.disclosure
pr.save(update_fields=["ai_status", "ai_tools", "ai_disclosure"])
```

The create/delete diff is what makes both "re-running twice creates no duplicate rows" and "deactivating a rule
removes its signals on the next run" true by construction, not by a cleanup pass. The unique constraint is the
belt to that braces: a concurrent double-run raises `IntegrityError` rather than duplicating.

`resolve_ai_status` implements spec §6.3 verbatim, in order: any `high` signal → `ai_explicit`; else disclosure
in {`partial`, `substantial`} → `ai_disclosed`; else any signal at all → `ai_suspected`; else disclosure `none` →
`no_ai`; else → `unknown`. It is a pure function of two arguments so all five outcomes are testable without a DB.

`ai_cohort_statuses()` returns `{ai_explicit, ai_disclosed}` plus `ai_suspected` when
`AI_COHORT_INCLUDE_SUSPECTED` is on; `selectors.ai_cohort_pull_requests(scope)` filters
`activity.selectors.pull_requests_for_metrics(scope)` by it, so the cohort inherits the bot/excluded-person rule
rather than restating it.

### Re-running over stored PRs

`manage.py recompute [--from DATE] [--to DATE] [--repo owner/name ...] [--project slug]` (spec §5.5) selects
stored PRs by `updated_at_github`/`created_at` and runs `derive_pull_requests()` then `detect_pull_requests()`.
It makes **no** HTTP call — asserted by the suite's own outbound-request guard. Phase 6 adds policy evaluation and
phase 7 adds rollups to the same command. Phase 4's plan deferred `recompute` to phase 7; creating it here is the
smallest thing that satisfies this phase's "re-running detection over stored PRs" deliverable, and it is the
command `CLAUDE.md` and spec §5.5 already name (logged in DECISIONS.md).

### Settings UI (`/settings/detection-rules/`)

Thin views behind `@login_required` + `@permission_required("catalog.manage_settings")`, following
`apps/catalog/views.py`:

| URL | name | method | returns |
|---|---|---|---|
| `settings/detection-rules/` | `ai_detection:rules` | GET | full page, or the list fragment for htmx |
| `settings/detection-rules/new/` | `ai_detection:rule_create` | GET/POST | form page / fragment |
| `settings/detection-rules/<pk>/edit/` | `ai_detection:rule_edit` | GET/POST | form page / fragment |
| `settings/detection-rules/<pk>/toggle/` | `ai_detection:rule_toggle` | POST | list fragment |
| `settings/detection-rules/dry-run/` | `ai_detection:rule_dry_run` | POST | dry-run result fragment |

The dry run posts the **form**, not a saved row, so an admin can test a pattern before saving it. It builds an
unsaved `DetectionRule`, runs `dry_run_rule()` over the last `DETECTION_DRY_RUN_PR_COUNT` PRs in the user's
scope, and renders match count + per-PR evidence. It never touches `AISignal` (asserted by test). An invalid
regex renders a visible error fragment with the `re.error` message, never an empty 400 body. Every mutation is
POST and writes an `AuditEntry` (`detection_rule.create` / `.update` / `.toggle`) — `DetectionRule` is
operator-owned data per ARCHITECTURE.

### PR detail page

Acceptance criterion 6 requires the evidence to be visible on the PR page, and no PR page exists (phase 9 owns
the full one). This phase ships a **minimal** `dashboards:pull_request_detail` at `/prs/<int:pk>/`: header
(repository, number, title, GitHub link), the resolved `ai_status`, `ai_disclosure`, `ai_tools`, and the signal
list (detector, tool, confidence, evidence fragment, commit sha when present). It is built from
`scope_for_user()` via `ai_detection.selectors`, carries an `assertNumQueries` test, and is the same URL name and
template phase 9 extends with the timeline, files, metrics, violations and churn.

### Error handling

- A rule whose pattern does not compile: skipped, warned, and rejected at form/model validation so it cannot be
  entered through the UI in the first place; a YAML rule with a bad pattern fails `seed_detection_rules` loudly.
- A PR whose body is empty or `None`: `missing` disclosure, no crash.
- Detection raising inside the sync pipeline: already contained by `_run_post_processing()` (logged, masked,
  counted, run continues).
- `AISignal.rule` is `PROTECT`, so a rule with signals cannot be deleted; the UI offers **deactivate**, not
  delete, and deactivation removes the signals on the next run.

### How this honours the architecture

Same pipeline position (`identity.resolve → activity.derive → ai_detection.detect`), same idempotency contract
(unique per `(pull_request, rule, evidence-hash)`), rules stay operator-owned runtime data, every selector starts
from `scope_for_user()`, views stay thin with rules in `services.py` and queries in `selectors.py`, system text
is stored as codes (`Detector`/`Tool`/`Confidence` choices) and rendered in the reader's language, and no
GitHub call is made anywhere. **Deviations**, both logged in DECISIONS.md: (1) `manage.py recompute` lands here
instead of phase 7; (2) a minimal PR detail page lands here instead of phase 9.

---

## Tasks

- [x] **T1: Two new AI settings.** `apps/catalog/setting_defs.py` (+ data migration mirroring
      `catalog/migrations/0002`): `DETECTION_DRY_RUN_PR_COUNT` (int, 50), `DISCLOSURE_TOOL_ALIASES` (dict).
      Tests: extend `apps/catalog/tests/test_settings.py` — both keys resolve to their defaults and type-validate.
- [x] **T2: `AISignal` uniqueness.** `evidence_hash` field + `UniqueConstraint(pull_request, rule,
      evidence_hash)`; migration `apps/ai_detection/migrations/0002_*`. Tests:
      `apps/ai_detection/tests/test_models.py` — a second identical `(pr, rule, evidence_hash)` raises
      `IntegrityError`; the same evidence on a *different* PR is allowed.
- [x] **T3: `DetectionRule` hardening.** `name` unique, `clean()` rejecting a non-compiling pattern; same
      migration as T2. Tests in `test_models.py` — duplicate name rejected, `[unclosed` pattern raises
      `ValidationError`, a valid regex passes.
- [x] **T4: Detection context + evidence helper.** `apps/ai_detection/detectors.py`: `DetectionContext`,
      `load_context()`, `EVIDENCE_MAX_LENGTH = 200`, `evidence_fragment()`. Tests:
      `apps/ai_detection/tests/test_evidence.py` — a 5 000-char body yields exactly ≤200 chars containing the
      match; a short match is returned untruncated; truncation is centred and ellipsised; `load_context()`
      orders commits deterministically.
- [x] **T5: The eight detectors.** `detectors.py`: `DETECTORS` registry with `commit_trailer`, `commit_author`,
      `pr_author`, `pr_body_footer`, `html_comment`, `label`, `branch_pattern`, `commit_message`. Tests:
      `apps/ai_detection/tests/test_detectors.py` — **one matching and one non-matching case per detector**
      (16), plus: `html_comment` does not match the same marker in visible body text, `pr_body_footer` does not
      match inside an HTML comment, `commit_author` matches a co-author email, a match on the 2nd of 3 commits
      records that commit, and `DETECTORS.keys() == set(Detector.values)`.
- [x] **T6: Disclosure parser.** `apps/ai_detection/disclosure.py`: `DisclosureConfig`, `load_config()`,
      `parse_disclosure()`. Tests: `apps/ai_detection/tests/test_disclosure.py` — `[x]` → partial; `[X]` →
      substantial; a mixed-case heading (`### ai ASSISTANCE`) still found; a malformed template (ticked box with
      an unknown label) → `missing`; **two ticked boxes → `ambiguous`**; **no section → `missing`**; empty body →
      `missing`; `None` category → `none`; tools line `Claude Code, Copilot` → `("claude_code","copilot")`; the
      bare placeholder comment → no tools; an unknown tool kept as raw text; a Ukrainian heading added to
      `DISCLOSURE_SECTION_HEADINGS` is found (configurable synonyms).
- [x] **T7: `ai_status` resolution.** `apps/ai_detection/services.py::resolve_ai_status()` + `ai_cohort_statuses()`.
      Tests: `apps/ai_detection/tests/test_ai_status.py` — **one test per outcome** (`ai_explicit`,
      `ai_disclosed`, `ai_suspected`, `no_ai`, `unknown`), plus: a high signal beats a `none` disclosure
      (→ `ai_explicit`, the mismatch case phase 6 turns into a violation), and `ambiguous` with no signals →
      `unknown`.
- [x] **T8: Detection run.** `services.detect_pull_request()` / `detect_pull_requests()`. Tests:
      `apps/ai_detection/tests/test_services.py` — a matching rule writes one signal with the right tool,
      confidence and evidence; **running twice creates no second row and no field drift**; **deactivating the
      rule and re-running deletes its signal and resets `ai_status`**; editing a pattern so it no longer matches
      deletes the stale signal; `ai_tools` is the union of signal tools and disclosed tools; a rule with an
      invalid pattern is skipped and the other rules still run; a PR with no rules gets `unknown`/`missing`;
      `detect_pull_requests(queryset)` returns the processed count.
- [x] **T9: Selectors.** `apps/ai_detection/selectors.py`: `signals_for_pull_request(scope, pk)`,
      `ai_cohort_pull_requests(scope)`, `recent_pull_requests(scope, limit)`. Tests:
      `apps/ai_detection/tests/test_selectors.py` — the cohort is `ai_explicit ∪ ai_disclosed` by default and
      gains `ai_suspected` when the setting is on; a bot-authored AI PR is not in the cohort; a restricted
      `ScopeFilter` hides another project's PR; `recent_pull_requests` respects the limit and orders newest first.
- [x] **T10: `fixtures/detection_rules.yaml` + loader.** The seed set covering **Claude Code, Copilot, Cursor,
      Codex, Devin, Gemini, Aider, Windsurf**, each rule with `notes` naming its source and disputed rules at
      `confidence: low`; `apps/ai_detection/rules.py::load_rule_definitions(path)` validating every row against
      the `Detector`/`Tool`/`Confidence` choices and compiling every pattern. Tests:
      `apps/ai_detection/tests/test_rules_yaml.py` — the file parses; all eight tools are represented; every rule
      has a non-empty `notes`; every pattern compiles; at least one rule exists per detector; a rule with an
      unknown detector/tool raises a named error.
- [x] **T11: `manage.py seed_detection_rules`.** `apps/ai_detection/management/commands/seed_detection_rules.py`
      (`--path`, `--update`). Creates missing rules by `name`; **never** overwrites an existing row unless
      `--update` is passed (rules are operator-owned). Tests:
      `apps/ai_detection/tests/test_seed_detection_rules.py` — seeding creates the full set; seeding twice
      creates nothing new; an admin-edited pattern survives a re-seed; `--update` restores it; a deactivated rule
      stays deactivated.
- [x] **T12: Pipeline wiring.** `apps/github_sync/pipeline.py` calls `detect_pull_request()` after
      `derive_pull_request()`. Tests: extend `apps/github_sync/tests/test_pipeline.py` — a fixture-driven sync
      leaves the PR with a resolved `ai_status` and its signals; **a second sync creates no duplicate
      `AISignal`**; a detection failure is logged and counted without aborting the run.
- [x] **T13: `manage.py recompute`.** `apps/github_sync/management/commands/recompute.py` with `--from`,
      `--to`, `--repo`, `--project`, running derive then detect over stored PRs. Tests:
      `apps/github_sync/tests/test_recompute_command.py` — it re-detects after a rule is added, with **no
      outbound HTTP** (the conftest guard is the assertion); `--from/--to` narrows the set; running it twice is a
      no-op on row counts.
- [x] **T14: Settings → Detection rules page.** `apps/ai_detection/{forms,views,urls}.py`, templates
      `ai_detection/rules.html` + `ai_detection/partials/{rules_content,rule_form,rule_row}.html`, wired into
      `config/urls.py` and `templates/partials/nav.html` behind `perms.catalog.manage_settings`. Tests:
      `apps/ai_detection/tests/test_views.py` — a lead without the permission gets 403 and no nav link while an
      admin gets 200 **on the same page** (paired refusal/positive); create/edit/toggle persist and write an
      `AuditEntry`; an htmx request gets the fragment and a normal request the full page; an invalid pattern
      renders a visible field error, not an empty 400; the list has an `assertNumQueries` budget.
- [x] **T15: Dry run.** `services.dry_run_rule()` + the `rule_dry_run` view and result fragment. Tests in
      `test_views.py` — the dry run reports N matches against the last `DETECTION_DRY_RUN_PR_COUNT` PRs with
      their evidence; **`AISignal.objects.count()` is unchanged before and after** and the PRs' `ai_status` is
      untouched; an unsaved (never-persisted) rule can be dry-run; zero matches renders an explicit empty state;
      an invalid regex renders the error fragment; a restricted scope's dry run sees only its own PRs.
- [x] **T16: Minimal PR detail page.** `apps/dashboards/views.py::pull_request_detail` + `urls.py` +
      `dashboards/pull_request_detail.html`. Tests: `apps/dashboards/tests/test_pr_detail.py` — the page shows
      each signal's detector, tool, confidence and **evidence fragment**, and the rendered evidence is ≤200
      chars; the resolved status, disclosure and tools are shown; a PR with no signals renders an empty state;
      `assertNumQueries` guards the signal list; an out-of-scope PR is not reachable.
- [x] **T17: `docs/pull_request_template.md`** (the recommended English template of spec §6.2, matching the
      parser's defaults) + `docs/CONFIGURATION.md` (the two new settings and the disclosure synonyms),
      `docs/user/tune-ai-detection.md` (task-shaped: read a signal, fix a false positive, dry-run a rule),
      `docs/DECISIONS.md` (phase 5 section), `CHANGELOG.md`, `CLAUDE.md` command table if `recompute` needs a
      row. Test: extend `tests/test_docs.py` — every `Detector` value and every `AIStatus`/`AIDisclosure` value
      is documented, and the shipped template parses to `missing` with all boxes unticked and to `substantial`
      with the third ticked (the template and the parser can never drift).
- [x] **T18: Ukrainian parity + CSS.** `make messages`, translate every new string (page, form labels, empty
      states, dry-run result, detector/tool/confidence labels, PR detail section), clear every fuzzy flag, then
      `make css` and commit `static/css/app.css` + the manifest. Tests: the existing
      `tests/test_translations.py` gates plus a new canary entry for a Detection-rules page string.
- [x] **T19: Gate.** Add `apps/ai_detection/detectors.py`, `disclosure.py` and `rules.py` to `[tool.mypy] files`
      in `pyproject.toml` (`services.py`/`selectors.py` are already globbed). Run the full gate and fix
      everything it reports.

---

## Verification

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run mypy \
  && uv run python manage.py makemigrations --check --dry-run \
  && uv run python manage.py check
uv run python manage.py seed_detection_rules            # then re-run: creates nothing new
uv run python manage.py recompute --from 2026-01-01     # no outbound request
make css && make messages                               # both must leave the tree clean
```

| # | Acceptance criterion | Test that proves it |
|---|---|---|
| 1 | Every detector has a matching and a non-matching test | `apps/ai_detection/tests/test_detectors.py` — 8 × `test_<detector>_matches` / `test_<detector>_does_not_match`, plus `test_registry_covers_every_detector_choice` |
| 2 | Parser tested on `[x]`, `[X]`, mixed case, malformed template, two ticks → ambiguous, missing section → missing | `test_disclosure.py::{test_lowercase_tick_partial, test_uppercase_tick_substantial, test_mixed_case_heading_is_found, test_malformed_template_is_missing, test_two_ticked_boxes_are_ambiguous, test_missing_section_is_missing}` |
| 3 | Each of the five `ai_status` outcomes has a test | `test_ai_status.py::test_{ai_explicit,ai_disclosed,ai_suspected,no_ai,unknown}` |
| 4 | Re-running detection twice creates no duplicate `AISignal` | `test_services.py::test_second_detection_run_creates_no_new_signal`; `apps/github_sync/tests/test_pipeline.py::test_second_sync_creates_no_duplicate_ai_signal`; `test_models.py::test_duplicate_signal_violates_unique_constraint` |
| 5 | Deactivating a rule removes its signals on the next run | `test_services.py::test_deactivating_a_rule_removes_its_signals_and_resets_status` |
| 6 | Every `AISignal` carries an evidence fragment ≤200 chars, shown on the PR page | `test_evidence.py::test_long_match_is_truncated_to_200_chars`; `test_services.py::test_signal_evidence_is_within_the_limit`; `apps/dashboards/tests/test_pr_detail.py::test_signal_evidence_is_shown_on_the_pr_page` |
| 7 | The dry-run page reports matches against the last N PRs without writing `AISignal` rows | `test_views.py::{test_dry_run_reports_matches_for_the_last_n_prs, test_dry_run_writes_no_signal_rows, test_dry_run_leaves_ai_status_untouched}` |
| — | Rules seeded from YAML for all eight tools, sourced, disputed ones `low` | `test_rules_yaml.py`, `test_seed_detection_rules.py` |
| — | Re-running detection over stored PRs touches no GitHub | `test_recompute_command.py` under `tests/test_http_guard.py`'s unmocked-request guard |
| — | Ukrainian parity for every new string | `tests/test_translations.py` (no empty/fuzzy, placeholders, uk canary) |

---

## Risks

| RISKS.md row | How this phase touches it | What the plan does |
|---|---|---|
| **5 — AI detection is wrong in both directions** (the phase's own row) | Every rule in this phase is a regex heuristic over vendor-controlled text | Rules are `DetectionRule` rows seeded from YAML, editable in the UI, so a false positive is an admin edit rather than a release; only `high` yields `ai_explicit`; disputed rules ship `confidence: low`; every signal stores an inspectable ≤200-char evidence fragment rendered on the PR page; the dry run tests a pattern against real stored PRs **before** it is saved; every detector has a positive *and* a negative test |
| **1 — a wrong number lands in a 1:1** | `ai_status` is about a named person's PR | A missing disclosure is `unknown`, never `no_ai`; `ai_suspected` is outside the AI cohort by default (`AI_COHORT_INCLUDE_SUSPECTED=False`); the cohort selector reuses `pull_requests_for_metrics()`, so bots and excluded people stay out |
| **3 — a restricted lead sees another project's data** | Two new read surfaces (the dry run and the PR page) | Both start from `scope_for_user()`; the dry run scopes its "last N PRs"; `test_selectors.py` and `test_pr_detail.py` assert a restricted `ScopeFilter` hides another project's PR |
| **6 — duplicated/resurrected rows** (phase 6's row, same shape) | `AISignal` had no uniqueness | Unique on `(pull_request, rule, evidence_hash)` plus a wanted-vs-existing diff that deletes stale rows; idempotency asserted at the service and the pipeline level |
| **8 — Ukrainian translation lags** | A whole new settings page plus a PR page | T18 is a task, not a cleanup: `make messages`, translations, no fuzzy entries, a new canary string |
| **10 — dashboards miss the budget / N+1** | The rules list, the dry run and the PR page all render per-row data | `assertNumQueries` tests on the rules list and the PR detail signal list; `load_context()` prefetches commits once per PR |
| **15 — a generated artefact goes stale** | New templates and new `.py` files invalidate `static/css/.build-manifest.sha256` | T18 re-runs `make css` and commits `app.css` + the manifest in this phase |

---

## Out of scope

- **The policy engine** — `AIPolicy`, `SensitivePathRule`, the nine `rule_code`s, `PolicyViolation` and the
  violations console are phase 6. This phase only produces the inputs they read (`ai_status`, `ai_tools`,
  `ai_disclosure`, the signals and their confidences). `DISCLOSURE_MISMATCH` is deliberately *not* evaluated
  here even though `resolve_ai_status` already sees the "high signal + `none` disclosure" case.
- **Metrics** — `ai_pr_count`, `ai_status_breakdown`, `disclosure_rate`, `disclosure_mismatch_count` are phase 7.
  This phase ships `ai_cohort_pull_requests(scope)` as the population those metrics will read.
- **The full PR detail page** — the timeline, PR metrics, the file list with test/excluded/sensitive marks,
  violations with actions and the churn slot are phase 9. This phase ships the URL, the view and the template
  that phase extends, carrying the AI section only.
- **The PR list with filters**, the People and Reviews pages, exports and `UserProjectAccess` — phase 9.
- **`manage.py recompute`'s remaining steps** — policy evaluation (phase 6) and rollup rebuilding with the
  dirty-days set and `--from/--to` repair semantics (phase 7). The command ships here with derive + detect only.
- **`seed_demo`** (phase 8) — this phase's tests build their own fixtures; `seed_e2e` gains detection rows in
  the e2e step, not here.
- **Detecting AI in review comments or in the diff itself** — the spec's detectors are the eight named ones; no
  content analysis, no model call, no network.
