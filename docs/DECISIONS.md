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
