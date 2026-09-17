# Review — phase 5 round 2

**Verdict:** changes_requested

Phase 5 delivers the eight detectors, the tolerant parser, the resolved ai_status, YAML-seeded operator-owned rules, a non-writing dry run and a minimal PR page; all seven acceptance criteria have a test that really proves them, `uv run pytest -q` and the whole lint gate are green, and every blocker/major from round 1 is genuinely fixed (verified by re-running the parser against r1's own repro bodies, plus the new setdefault/disputed/load_contexts/REPORT_TIMEZONE/.gitignore fixes). One new defect of the same class survives in the section-end predicate that r1 asked for: a bold instruction line or a `---` divider placed between the AI-assistance heading and the checkboxes truncates the section to zero lines, so an honest tick is stored as `ai_disclosure=missing` — the same wrong-per-person value r1 blocked on, arriving from the opposite direction. The rest is a grammatically wrong English singular in the dry-run count, ~7 avoidable queries per PR in the detection hot path, raw untranslated tool codes on the PR page, a PLAN/DECISIONS text that no longer matches the tools-line implementation with no fix-round decision log, and an unguarded catastrophic-backtracking pattern path.

## [MAJOR] A bold lead-in or a `---` divider before the checkboxes truncates the AI section, so a ticked box is read as `missing`
`apps/ai_detection/disclosure.py`

`_find_section` (disclosure.py:105-111) ends the section at the first `_is_boundary_line` (disclosure.py:59-70) after the heading, and that predicate fires on any bold-only line or horizontal rule regardless of whether the section has produced content yet. A template that puts an instruction line or a divider between the heading and the boxes therefore yields an empty section and zero ticks. Verified with the shipped default config:

  '### AI assistance\n\n**Tick exactly one:**\n\n- [x] Partial\n'  -> MISSING   (expected PARTIAL)
  '### AI assistance\n\n---\n\n- [x] Partial\n'                    -> MISSING   (expected PARTIAL)
  '### AI assistance\n**Please choose one**\n- [x] Substantial\n'   -> MISSING   (expected SUBSTANTIAL)

A plain (non-bold) lead-in works, so the failure is specific to the two boundary shapes added in the r1 fix. The consequence is exactly r1 finding #1's: an author who answered the question honestly is stored as not having disclosed, and phase 6's DISCLOSURE_MISSING rule raises a violation against a named developer (RISKS rows 1 and 5). PR Radar reads whatever template a tracked repo already has, and a bold lead-in above a checkbox group is a common shape. No test covers a boundary-looking line before the first checkbox — the three new tests (test_section_ends_at_bold_heading_not_just_atx / _horizontal_rule / _setext_heading) all place it after the ticks.

**Fix:** Make the bold-line and horizontal-rule boundaries conditional on the section having already yielded at least one checkbox line: track `seen_checkbox` while scanning from `content_start` and only honour `_BOLD_HEADING_RE`/`_HR_RE` once it is true (ATX and setext headings stay unconditional boundaries). That keeps all three r1 cases green — the later `**Checklist**` / `---` still comes after the ticks — and fixes all three bodies above. Add them as regression tests.

## [MINOR] The dry-run singular reads "1 match against the last 50 pull request"
`apps/ai_detection/templates/ai_detection/partials/dry_run_result.html`

The `blocktranslate` at dry_run_result.html:10-16 pluralises on `counter` (the match count) but the sentence also carries `limit` (the PR count, normally 50), so the singular branch renders "1 match against the last 50 pull request". One match is the most common non-zero outcome when an admin is narrowing a pattern, so this is the string leads will usually see. The Ukrainian sidesteps it by translating to "PR" (locale/uk/LC_MESSAGES/django.po:771-776), which is why no gate caught it.

**Fix:** Split the sentence so each number pluralises its own noun, or reword to a form correct for every count — e.g. msgid "%(counter)s match in the last %(limit)s PRs" / msgid_plural "%(counter)s matches in the last %(limit)s PRs" — then re-run `make messages` and re-translate the entry.

## [MINOR] Detection re-queries the rules and re-reads six AppSetting rows for every PR
`apps/ai_detection/services.py`

`detect_pull_request` loads `DetectionRule.objects.filter(is_active=True)` (services.py:71) and calls `load_config()` (services.py:115) inside the per-PR unit of work, and `catalog.services.get_setting` does an uncached `AppSetting.objects.filter(key=...).first()` per key — so `load_config()` alone is six queries. `detect_pull_requests(queryset)` just loops over `detect_pull_request(pk)`, so a `recompute` or a large sync pays 1 rules query + 6 settings queries + `load_context`'s fetch-and-prefetches for every PR, re-reading the same seven rows thousands of times. PLAN.md states the opposite ("Every rule pattern is compiled once per detection run"), RISKS row 10 is the budget row this phase was meant to guard, and unlike the rules list, the PR page and the dry run, the detection path has no query-budget test at all.

**Fix:** Give `detect_pull_request` optional `rules` / `config` parameters (defaulting to loading them) and have `detect_pull_requests` load the compiled rules and the `DisclosureConfig` once before the loop, passing them in — the pipeline's single-PR call keeps its current signature. Add a `CaptureQueriesContext` budget test over ~20 PRs asserting the total does not grow by 7 per PR.

## [MINOR] `ai_tools` is rendered as raw codes, so the PR page shows `claude_code` in both languages
`apps/dashboards/templates/dashboards/pull_request_detail.html`

pull_request_detail.html:30 renders `{{ pull_request.ai_tools|join:", " }}`. The list holds canonical `Tool` values (`claude_code`, `copilot`) mixed with raw lowercased free text for unrecognised tools, so a lead sees the snake_case identifier rather than the `Tool` label, identically in the English and Ukrainian UI — the detector/tool/confidence columns right below it correctly use `get_*_display`. CLAUDE.md requires system-generated values to be stored as codes and rendered in the reader's language. `test_resolved_status_disclosure_and_tools_are_shown` asserts `"copilot" in content`, which locks the raw rendering in.

**Fix:** Map the list through `Tool` labels in the view (or a small template filter / `get_ai_tools_display()` model method), falling back to the raw text for a value that is not a `Tool` member, and assert `Tool.COPILOT.label` in the test instead of the raw code.

## [MINOR] PLAN rule 6 and DECISIONS.md no longer describe how the tools line is actually found, and the r1 fix round logged no decisions at all
`.autodev/phases/05-ai-detection/PLAN.md`

PLAN.md parser rule 6 says the tools line is "the first line **in the section**", and r1's minor #3 asked for the resolved span to be passed into `_extract_tools`. The implementation still scans the whole body (disclosure.py:170-186) — and it has to, because the shipped `docs/pull_request_template.md` puts `### AI tools used` in its own ATX section, i.e. outside the AI-assistance section, so an in-section-only scan would find no tools for the project's own recommended template. So the code is right and the documents are stale, but the deviation is recorded nowhere, and `test_tools_line_outside_any_section_still_parses` now asserts the whole-body behaviour as intent without a rationale. Related: `.autodev/DECISIONS.md` has `## p05-plan`, `## p05-implement` and an empty `## p05-review2` but no `p05-review_fix1` section, so none of the round-1 fixes that changed design — the `disputed:` YAML key and its loader invariant, the conditional section-end predicate, `load_contexts()`, `_day_start()` in `recompute` — are logged, unlike every earlier phase (`p01-review_fix1` … `p04-review_fix1`).

**Fix:** Correct PLAN rule 6 to say the tools label is searched over the whole body (bounded by the section-end predicate for the next-line fallback), state why in the test's docstring, and append the round-1 fix decisions to `.autodev/DECISIONS.md` under a `## p05-review_fix1` heading.

## [MINOR] An admin-entered pattern with catastrophic backtracking can hang the dry run and every later sync
`apps/ai_detection/models.py`

`DetectionRule.clean()` (models.py:57-65) only checks that the pattern compiles. A pattern such as `(a+)+$` saved through Settings → Detection rules is then applied with `re.search` to every PR body and commit message in `detect_pull_request`, inside the huey task, with no timeout — the sync stalls on the first large body and the containment in `_run_post_processing` never triggers because nothing raises. The same pattern typed into the dry run blocks the request thread. It is admin-only input, but making regexes admin-editable without a release is this phase's headline feature, and recovery requires spotting the rule and editing it from a UI whose worker is wedged.

**Fix:** Cheapest useful guard: reject the obvious nested-quantifier shapes in `clean()` (e.g. `(...+)+`, `(...*)*`) with a visible field error, and bound the dry run by searching at most the first N KB of each haystack. A durable fix is running the match under a timeout (the `regex` module's `timeout=` argument), which can be deferred with a note.
