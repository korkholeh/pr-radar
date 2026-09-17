# Review — phase 4 round 2

**Verdict:** approve

Phase 4 hits its goal and every acceptance criterion has a test that actually proves it: logins auto-map to people, bare emails queue with a rendered-row-count-equals-model-count assertion, bots leave the metric population and land in a separate counter, and all 16 derived PR fields plus the PRFile flags are computed by a pure, idempotent `activity.derive` with hand-built fixtures for the never-drafted PR, the revert chain, the excluded-path-only PR and the exact 10-minute rubber-stamp boundary. All three r1 majors are genuinely fixed and verified on this branch — the queue N+1 (choices materialised once, test now compares 5 vs 25 rows), the M2M fan-out (replaced by a Repository subquery, with a real restricted-scope test), and the two untested timestamp fields (new TestTimestamps). All four minors and the nit were addressed too, and the one deviation kept (bot_person_count on the People page) is logged in DECISIONS.md. The full suite passes and the whole CLAUDE.md lint gate is clean; Ukrainian parity, docs/user/map-people.md, the CHANGELOG entry and the derived-field definition table all shipped in-phase; all 17 PLAN tasks are checked with matching code and no environment claims. No blockers or majors. Remaining minors: is_rubber_stamp fires on a negative approval-minus-ready delta (a reviewer approving a draft); the queue still renders one full Person select per row with a duplicate element id; first_approval_at's "earliest" semantics and the title-based revert-target branch are untested; the rubber-stamp disqualifiers count bot/author comments unlike the timestamps they derive from; and auto-created people get a casefolded display name, a plan deviation with no DECISIONS entry.

## [MINOR] is_rubber_stamp is True when the approval predates ready_for_review_at
`apps/activity/derive.py`

`_is_rubber_stamp` (line 178) rejects only `first_approval_at - ready_for_review_at >= 10 minutes`. A negative delta is `< 10 minutes`, so it falls through to True. Reachable: GitHub permits submitting a review on a draft PR, so a reviewer who approves a large draft before the author clicks "Ready for review" produces approval < ready, and the PR is flagged a rubber stamp with no elapsed time at all. (`map_ready_for_review_at` takes `min()` of the ReadyForReviewEvents, so the convert-back-to-draft path is already safe — this is the remaining one.) Risk row 1 is exactly about a false number reaching a 1:1.

**Fix:** Guard the sign explicitly in `_is_rubber_stamp`: `delta = first_approval_at - ready_for_review_at; if not (datetime.timedelta(0) <= delta < datetime.timedelta(minutes=max_minutes)): return False`, and add a fixture test with an approval timestamped before `ready_for_review_at`.

## [MINOR] The queue renders a full Person <select> per row, with a duplicate element id
`apps/catalog/templates/catalog/partials/identity_row.html`

Line 8 renders `{{ assign_form.person }}` inside every row. The r1 fix removed the per-row *query*, but not the per-row *markup*: a 50-row page (`IDENTITY_PAGE_SIZE = 50`) emits 50 copies of the whole person list, so the HTML payload is rows x people — at a few hundred people that is tens of thousands of `<option>` elements on the one page whose whole job is bulk triage. Every copy also carries the same `id="id_person"`, which is invalid HTML and will break any future label/JS targeting.

**Fix:** Render the option list once and reference it from each row — e.g. an `<input list="people">` + a single shared `<datalist id="people">`, or a `<select>` cloned per row with a row-scoped id (`{{ identity.pk }}` suffix). Alternatively drop the inline select and make Assign a per-row link to a small assign form.

## [MINOR] first_approval_at's "earliest" semantics is never exercised
`apps/activity/tests/test_derive.py`

Every test that reaches `_first_approval_at` uses at most one APPROVED review (`TestRubberStamp`, `test_self_review_is_ignored`). Changing `min(timestamps)` to `max(timestamps)` at derive.py:136 leaves the whole suite green. This is the same class of gap r1 raised for `first_commit_at`/`last_activity_at`; `_first_review_at`'s min-ness is properly pinned by `test_review_comment_earlier_than_review_wins`, this one is not.

**Fix:** Add a test to `TestFirstReviewAndApproval` with two APPROVED reviews by different non-bot reviewers at, say, +2h and +5h, asserting `first_approval_at == _dt(hours=2)`.

## [MINOR] Rubber-stamp disqualifiers count the author's and bots' comments and review bodies
`apps/activity/derive.py`

`_is_rubber_stamp` lines 180-182 use `pr.review_comments.all()` and `pr.reviews.all()` unfiltered, while `_first_review_at`/`_first_approval_at` deliberately exclude the author and bots. So a coverage/lint bot leaving one review comment, or the author replying to themselves, silently clears the flag on a genuine rubber stamp — a false negative in the same metric, arrived at by a different rule than the two timestamps it is computed from.

**Fix:** Reuse the existing helpers: `if _non_author_non_bot_comments(pr): return False` and `if any((r.body_length or 0) > 0 for r in _non_author_non_bot_reviews(pr)): return False`. Add a fixture test with a bot review comment on an otherwise-rubber-stamped L PR.

## [MINOR] Auto-created people get a casefolded display name; the plan's original-case rule was dropped without a decision entry
`apps/catalog/identity.py`

`person_for_login` (line 43) sets `display_name=login_identity.value`, and `Identity.save()` runs `normalize_identity_value()` (`strip().casefold()`, apps/catalog/normalize.py), so GitHub login `OctoCat` becomes a person displayed as `octocat` on the People page, in the merge selects and (from phase 8) on every dashboard. PLAN.md §2 specified `display_name=<original-case login when known, else the stored value>`; the shipped code always takes the stored value, and DECISIONS.md has no entry recording that the original case is unobtainable because `upserts.py` never persists it.

**Fix:** Either keep the raw login: pass it through from `upserts.py::_identity_for_login` (or add an unnormalised `Identity.display_value`) and use it for `display_name`; or log a one-line DECISIONS.md entry stating the original case is not stored and leads are expected to fix the name from Settings → People.

## [MINOR] The title-based revert-target branch is never exercised
`apps/activity/derive.py`

`_revert_target_by_title` (line 235) is the third fallback in `_detect_revert`. `test_revert_chain_links_reverts_pr` seeds a title *and* a `This reverts commit <sha>` body, so the commit branch always wins and the title branch never runs; no other test reaches it. Its constraints (`state=MERGED`, `merged_at__lt=pr.created_at`, `-merged_at` ordering, exact title equality) are all unverified — including the exact-match semantics, which is what stops it linking to an unrelated PR with a similar title.

**Fix:** Add a test: a merged PR titled `Add widget` merged before the reverting PR's `created_at`, plus a second merged PR with the same title merged earlier, and a reverting PR titled `Revert "Add widget"` with no sha or number in its body — assert `reverts_pr_id` is the later-merged one, and a second case where the only candidate merged *after* `created_at` resolves to `None`.

## [NIT] T13 is checked but its body still says the UI is not started
`.autodev/phases/04-identity-and-derived-fields/PLAN.md`

Line 316 of PLAN.md reads "**Not started yet — no `forms.py`/`views.py`/`urls.py`/templates exist for `catalog` in this session.**" while the task is marked `[x]` and all of those files ship in the diff. Leftover planning text that contradicts the checkbox; a later reader auditing the phase has to diff the tree to know which is true.

**Fix:** Delete the "Not started yet" sentence and the "Planned shape (not yet written)" framing from T13.
