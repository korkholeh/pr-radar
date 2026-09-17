# Review — phase 5 round 1

**Verdict:** changes_requested

Phase 5 delivers what it promised: eight detectors with paired positive/negative tests, a resolved ai_status with all five outcomes tested, an idempotent wanted-vs-existing diff backed by a real unique constraint, YAML-seeded operator-owned rules, a non-writing dry run, and a minimal PR page showing the evidence. All seven acceptance criteria have a test that actually proves them; the full suite and the whole lint gate are green; both roadmap deviations are logged in DECISIONS.md. The defect worth blocking on is in the tolerant disclosure parser: the AI-assistance section only ends at an ATX heading while it starts on any loose heading, so ticks from a following `**Checklist**`/`---` section are counted as disclosure ticks and an honest `- [x] None` is stored as `ambiguous` — a per-person wrong value that phase 6 will turn into a violation (RISKS rows 1 and 5). Two smaller parser gaps (bold/setext headings never found; the tools line read from outside the section and falling through to the next heading, yielding `ai_tools = ["### checklist"]`), a last-commit-wins attribution that contradicts DECISIONS.md, one vacuous test, an unbudgeted ~200-query dry run, a UTC day boundary in `recompute`, and four staged `.DS_Store` blobs round out the list.

## [MAJOR] AI-assistance section only ends at an ATX heading, so a later checklist's ticks are read as disclosure ticks
`apps/ai_detection/disclosure.py`

`_find_section` (disclosure.py:50-65) starts the section on a *loose* heading (`_LOOSE_HEADING_RE`, leading `#`/`*`/`-`) but ends it only on `_ATX_HEADING_RE` (`#` only). Any following section headed by `**Checklist**`, a `---` rule or a setext underline therefore stays inside the AI section, and its ticked boxes are counted as disclosure ticks. Verified by running the parser with the shipped default config:

  body: "### AI assistance\n- [x] None\n\n**Checklist**\n\n- [x] Tests added\n- [x] Docs updated\n"
  → DisclosureResult(disclosure=AIDisclosure.AMBIGUOUS, tools=())   # expected NONE

  body: "### AI assistance\n- [x] None\n\n---\n\n- [x] I ran the tests\n"
  → AMBIGUOUS   # expected NONE

Both are ordinary PR templates. The consequence is a wrong per-person value: an author who honestly ticks "None" and then ticks the team checklist gets `ai_disclosure=ambiguous`, which phase 6's DISCLOSURE_MISSING rule treats like a missing disclosure and raises against a named developer (RISKS rows 1 and 5). PLAN.md's own parser rule 2 says the section "runs to the next heading line", where rule 1 defines a heading as including `*`/`-` prefixes — the implementation is asymmetric with its own spec. No test covers a section followed by anything other than an ATX heading.

**Fix:** Give the section a proper end predicate instead of reusing the ATX regex: end at an ATX heading, a bold-only line (`^\s*\*\*.+\*\*\s*$`), a horizontal rule (`^\s*(-{3,}|\*{3,}|_{3,})\s*$`) or a setext underline — but explicitly not at a list item or checkbox line (which `_LOOSE_HEADING_RE` would swallow, hence the current ATX-only shortcut). Add tests for each of the three terminator shapes asserting the later checklist's ticks do not reach `_resolve_disclosure`.

## [MINOR] A bold or setext section heading is never found, so the disclosure reads as `missing`
`apps/ai_detection/disclosure.py`

`_LOOSE_HEADING_RE` (disclosure.py:18) strips only *leading* `#`/`*`/`-`, so `**AI assistance**` normalises to `AI assistance**`, which never equals a configured heading. Verified:

  parse_disclosure("**AI assistance**\n- [x] Partial\n", cfg) → MISSING
  parse_disclosure("AI assistance\n-------------\n- [x] Partial\n", cfg) → MISSING

A bold heading is the most common non-ATX template style, and an entire repository using one would silently report every PR as `missing` — indistinguishable from a team that never adopted the template at all. The parser is advertised as tolerant in `docs/user/tune-ai-detection.md` and `docs/pull_request_template.md`.

**Fix:** Normalise a heading candidate by stripping `#`/`*`/`_`/`-` and whitespace from *both* ends before the case-folded comparison, and recognise a setext underline on the following line. Add `**AI assistance**` and the setext form to `test_disclosure.py`.

## [MINOR] `_extract_tools` scans the whole body and falls through past a heading, storing a heading as a tool
`apps/ai_detection/disclosure.py`

`_extract_tools` (disclosure.py:123-136) iterates over every line of the body rather than the section, and when the tools label line has no inline remainder it takes the next non-empty line whatever it is — including the next heading. Verified:

  "### AI assistance\n- [x] Partial\n\n### AI tools used\n\n### Checklist\n- [ ] Tests\n"
  → tools=('### checklist',)

  "## Description\nAI tools used: Copilot\n\nNothing else.\n"   (no AI section at all)
  → disclosure=MISSING, tools=('copilot',)

The first case writes literal markdown into `PullRequest.ai_tools`, which is rendered on the PR page and becomes the population for phase 7's tool breakdown. PLAN.md rule 6 specifies "the first line **in the section**".

**Fix:** Pass the resolved `(start, end)` span into `_extract_tools` and search only inside it, and stop the next-non-empty-line fallback at any heading/horizontal-rule line (reusing the section-end predicate from the first finding). Add the two bodies above as regression tests.

## [MINOR] Identical evidence on several commits attributes the signal to the last commit, not the first as documented
`apps/ai_detection/services.py`

services.py:73-84 keys `wanted` on `(rule.pk, evidence_hash)` and assigns with `wanted[key] = {...}`, so when two commits of a PR render identical evidence (the common case — every commit of a Claude Code branch carries the same `Co-Authored-By` trailer) the *last* commit in the sorted order overwrites the earlier payload. PLAN.md ("the signal points at the first matching commit in a deterministic order") and the corresponding DECISIONS.md entry both state the first. Worse, the value is history-dependent: a PR synced incrementally keeps whichever commit existed on the first run (the `existing` branch never updates `commit_id`), while a fresh `recompute` of the same PR would pick the last — so the commit column on the PR page differs depending on how the PR was ingested. No test covers two commits with identical evidence.

**Fix:** Use `wanted.setdefault(key, payload)` so the first commit in `(committed_at, sha)` order wins, and add a test with two commits carrying the same trailer asserting the signal points at the earlier one and stays there across a re-run.

## [MINOR] `test_every_seeded_rule_has_high_or_disputed_low_confidence_consistent_with_notes` cannot fail
`apps/ai_detection/tests/test_seed_detection_rules.py`

The test body is `assert rule.confidence in Confidence.values` for every seeded rule. `confidence` is a choices-constrained field and `load_rule_definitions` already rejects anything outside `Confidence.values` before the row is created, so the assertion is true by construction — no edit to `fixtures/detection_rules.yaml` can make it red. Its name claims to check that disputed rules ship at `low`, which is exactly the deliverable PLAN.md's verification table attributes to this file ("disputed ones `low`"). That property is currently unproven: someone could raise the Windsurf or Cursor convention rule to `high` and the suite would stay green — and a `high` rule alone yields `ai_explicit` (RISKS row 5).

**Fix:** Assert the real invariant: mark the disputed rules in the YAML (e.g. a `disputed: true` key parsed into `RuleDefinition`) and assert every disputed rule has `confidence == Confidence.LOW`; or, minimally, pin the confidence of the two convention-based rules by name.

## [MINOR] Dry run issues ~4 queries per scanned PR with no query budget
`apps/ai_detection/services.py`

`dry_run_rule` (services.py:150-155) calls `load_context(pk)` inside the loop, and `load_context` is one `select_related` fetch plus three prefetches. With the default `DETECTION_DRY_RUN_PR_COUNT = 50`, one click on **Run** costs roughly 200 queries, and an admin tuning a pattern clicks it repeatedly. RISKS row 10 is the N+1/budget row this phase was meant to guard, and the plan gave `assertNumQueries` budgets to the rules list and the PR page but not to the dry run. `test_dry_run_reports_matches_for_the_last_n_prs` uses two PRs, so nothing notices.

**Fix:** Batch the context load — one `PullRequest` queryset with the same `prefetch_related` for the whole page of PRs, building `DetectionContext`s from the already-prefetched rows — and add a `CaptureQueriesContext` budget test with ~20 PRs so the cost is pinned.

## [MINOR] `--from`/`--to` interpret the day boundary in UTC, not REPORT_TIMEZONE
`apps/github_sync/management/commands/recompute.py`

recompute.py:33-36 filters `effective_updated_at__date__gte/lte`. Django's `__date` lookup converts using `settings.TIME_ZONE`, which is `"UTC"` (config/settings/base.py:116), while a lead typing `--from 2026-01-01` means the Europe/Kyiv day (`REPORT_TIMEZONE`, base.py:22). CLAUDE.md requires day boundaries to use `REPORT_TIMEZONE` through one helper. PRs updated in the first ~2-3 hours of the Kyiv start day fall outside the window and are silently not recomputed. No helper exists yet (phase 7 owns rollup day boundaries), so this is where the convention first bites.

**Fix:** Convert the parsed dates into an aware `[start, end)` datetime range in `REPORT_TIMEZONE` and filter `effective_updated_at__gte/__lt` on that range; when phase 7 introduces the shared day-boundary helper, route this through it. Add a test with a PR updated at 01:00 Kyiv on the `--from` day.

## [MINOR] Four `.DS_Store` blobs are staged for the phase commit and nothing ignores them
`.gitignore`

`git diff --diff-filter=A` against the phase-4 commit shows `.DS_Store`, `.autodev/.DS_Store`, `e2e/.DS_Store` and `static/.DS_Store` staged as new binary files, and `grep -n DS_Store .gitignore` returns nothing. As the orchestrator commits the index, these macOS artefacts land in history and will keep reappearing as noisy diffs in every later phase.

**Fix:** Add `.DS_Store` to `.gitignore` and `git rm --cached` the four staged blobs before the phase commit.

## [NIT] Two unreachable branches: the htmx path in `rule_toggle` and the `ValueError` path in `rule_dry_run`
`apps/ai_detection/views.py`

`rule_toggle` (views.py:80-82) renders the list fragment for an htmx request, but the toggle form in `partials/rule_row.html` is a plain `<form method="post">` with no `hx-post`, so the branch never runs and `test_toggle_flips_is_active_and_writes_audit` asserts 302. Likewise `rule_dry_run`'s `except ValueError` (views.py:97-98) is dead through the UI: `DryRunForm.clean_pattern` already rejects a non-compiling regex, so `test_dry_run_invalid_regex_renders_error_fragment` exercises the form error, not this handler. Neither is a bug — just two paths that will rot untested.

**Fix:** Either give the toggle button `hx-post`/`hx-target="#rules-page"` so the fragment branch is real (and assert it in a test), or drop the branch; keep the `ValueError` guard as a defensive net but note in a comment that the form is the primary gate.
