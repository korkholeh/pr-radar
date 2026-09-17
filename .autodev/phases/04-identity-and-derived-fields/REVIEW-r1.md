# Review — phase 4 round 1

**Verdict:** changes_requested

Phase 4 delivers the goal: logins auto-map to people, bare emails queue, bots are flagged and counted separately, and every derived PR field is computed by a pure, idempotent `activity.derive`. The full suite passes (exit 0) and the whole CLAUDE.md lint gate is clean; Ukrainian parity, the user doc, the CHANGELOG and the derived-field definition table all shipped in-phase, and the one architecture deviation (`Commit.author_email_identity`) is justified in PLAN.md §4 and logged in DECISIONS.md. Acceptance criteria 1a-1d, 2, 3, 4 and 5 each have a test that actually proves them. Three majors block: (1) the unmapped-identity queue has a per-row N+1 — measured 14 queries for 5 rows, 34 for 25, ~59 for a full 50-row page — because each row re-renders a `ModelChoiceField`, and the `assertNumQueries` guard passes at exactly 14 < 15 so it cannot catch it; (2) `pull_requests_for_metrics`/`bot_pull_request_count` fan out over the `Repository.projects` M2M without `.distinct()`, returning one PR twice when its repo is in two projects (verified), and no test exercises a restricted `ScopeFilter` — the one that claims to is a tautology; (3) acceptance criterion 1 is not fully met: `first_commit_at` and `last_activity_at` are asserted nowhere. Minors cover revert-target resolution preferring an unmerged PR, a non-atomic derive write, a mis-named test, and an undocumented counter deviation from the plan.

## [MAJOR] N+1 on the unmapped-identity queue: one query per rendered row
`apps/catalog/templates/catalog/partials/identity_row.html`

Each queue row renders `{{ assign_form.person }}` (line 8). Django's `ModelChoiceIterator.__iter__` calls `queryset.iterator()`, which issues a fresh SELECT every time the widget is rendered, so the shared `AssignIdentityForm` built once in `views.py::_queue_context` (line 93) still costs one query per row. Measured on this branch: 14 queries for 5 rows, 34 for 25 rows — a full `IDENTITY_PAGE_SIZE = 50` page is ~59. `apps/catalog/tests/test_views_people.py::test_queue_query_count` seeds 5 identities and asserts `< 15`; the actual value is 14, so it passes by one query and cannot fail on this N+1. That defeats CLAUDE.md's rule that list views carry an `assertNumQueries` test so an N+1 fails the build, and it is the risk-register row 10 this phase said it was touching.

**Fix:** Evaluate the person list once and reuse it: in `_queue_context`, build the choices from a materialised list (e.g. set `form.fields['person'].choices = [('', ...)] + [(p.pk, p.display_name) for p in Person.objects.order_by('display_name')]`, or pass a single pre-rendered `<select>` into the row include). Then tighten the test to a fixed budget that is independent of row count — assert the same query count for 5 and for 25 seeded identities, or use `assertNumQueries(n)` with an exact number.

## [MAJOR] Metric-population selectors return duplicate PRs under a restricted scope
`apps/activity/selectors.py`

`_scoped()` (line 12-16) filters on `repository__projects__id__in`, a many-to-many join (`Project.repositories`, `apps/catalog/models.py:67`). A repository that belongs to two projects makes every one of its PRs appear twice in the result set. Verified on this branch: one PR, repo in two projects, restricted `ScopeFilter` -> `qs.count() == 2` and `len(list(qs)) == 2`. `bot_pull_request_count()` inherits the same inflation. `scope_for_user()` is unrestricted today (phase 8 narrows it), so no number is wrong yet — but this is the single population definition `metrics.compute()` is required to build on, and phase 8 will only set `project_ids`, not revisit the join, so the double-counting lands silently. Risk row 7 ("metric correctness silently breaks") is exactly this. No test exercises the restricted branch at all: `apps/activity/tests/test_selectors.py::test_selectors_take_a_scope_filter` asserts `list(qs) == list(qs)`, a tautology that proves nothing about scoping.

**Fix:** Add `.distinct()` to the restricted branch of `_scoped()` (or filter via a `Repository` subquery: `queryset.filter(repository__in=Repository.objects.filter(projects__id__in=...))`, which avoids the fan-out). Replace `test_selectors_take_a_scope_filter` with a real test: a repo in two projects, a restricted `ScopeFilter(unrestricted=False, project_ids=...)`, asserting `pull_requests_for_metrics(scope).count()` equals the number of distinct PRs, plus a PR outside the scoped projects that is absent.

## [MAJOR] Two derived fields have no test, against acceptance criterion 1
`apps/activity/tests/test_derive.py`

Acceptance criterion 1 is "each derived field has a test over hand-built fixtures". `first_commit_at` and `last_activity_at` are never asserted: grepping `first_commit_at|last_activity_at` across `apps/` and `tests/` outside `derive.py` returns only `apps/activity/models.py`. `test_second_derive_pass_changes_nothing` snapshots every field, so it would catch drift between two passes, but not a wrong rule — `_last_activity_at` could return `None` unconditionally and the suite would stay green. Both rules have real content worth pinning: `first_commit_at` takes `authored_at or committed_at` (the fallback is untested), and `_last_activity_at` maxes over seven sources including `updated_at_github` and the last review comment.

**Fix:** Add two tests to `TestReviewShape` or a new `TestTimestamps` class: (a) a PR with two commits where the earlier one has `authored_at=None` and only `committed_at` set, asserting `first_commit_at` picks the fallback value; (b) a PR where the latest event is a review comment (later than `merged_at`, `closed_at`, the last commit and the last review), asserting `last_activity_at` equals that comment's `created_at`, plus a PR with nothing but `created_at`.

## [MINOR] Revert-by-commit can link to an unmerged PR and is non-deterministic on a short sha
`apps/activity/derive.py`

`_revert_target_by_commit` (lines 212-227) resolves the commit's PR with `.order_by("merged_at").first()`. In SQLite an ascending order puts NULLs first, so when the reverted commit also appears in an open or closed-unmerged PR (a rebased or cherry-picked branch), that PR wins over the merged one that actually introduced the code. Separately, the regex accepts a 7-40 character sha and the lookup is `sha__startswith=...` with an unordered `.first()`, so an ambiguous short prefix can resolve to a different `Commit` between two derive passes — a quiet idempotency hole in a field the plan promises is a total function of stored rows.

**Fix:** Prefer merged PRs explicitly: filter `state=PullRequest.State.MERGED` first and fall back to the unfiltered query, and order by `-merged_at` (or `F('merged_at').desc(nulls_last=True)`). Add `.order_by('sha')` (or require an exact 40-char match when the sha is full length) to the `Commit` lookup so an ambiguous prefix resolves the same way every pass.

## [MINOR] derive_pull_request writes PRFile flags and PR fields in two separate transactions
`apps/activity/derive.py`

`derive_pull_request` (line 332) issues `PRFile.objects.bulk_update(...)` and then `pr.save(update_fields=...)` under autocommit. If the process dies or the PR save fails between the two, the file flags are committed while the PR's `effective_additions`/`size_bucket` still hold the previous values — a PR whose cached fields disagree with its own file rows, which nothing later detects. The post-processing hook is called after the sync transaction commits, so there is no outer transaction to inherit.

**Fix:** Wrap the writing half in `with transaction.atomic():` (or decorate `derive_pull_request` with `@transaction.atomic`), so the file flags and the PR row land together.

## [MINOR] test_has_test_changes_ignores_excluded_test_files does not test exclusion
`apps/activity/tests/test_derive.py`

The test (line 79) creates a single `tests/test_app.py` file — matched by `TEST_PATH_GLOBS`, matched by nothing in `EXCLUDED_PATH_GLOBS` — and asserts `has_test_changes is True`. Nothing in it exercises the `and not f.is_excluded` half of the rule the name advertises; deleting that clause from `compute_derived` leaves the test green.

**Fix:** Add a file that is both a test and excluded (e.g. `app/migrations/test_data.py` against the default `**/migrations/**`) as the PR's only test file, and assert `has_test_changes is False`; keep the current case as a separate positive test.

## [MINOR] bot_pull_request_count has no caller; the People page counts bot people instead
`apps/catalog/selectors.py`

PLAN.md §5 says the two `activity` selectors "exist, are tested, and feed the People page's counters". In the shipped code the page uses a new `bot_person_count()` (line 27) that counts `Person` rows, while `bot_pull_request_count()` has no production caller — so the visible counter answers "how many bot accounts" rather than the acceptance criterion's "a bot-authored PR appears in the separate bot counter". The criterion is still proven at selector level by `test_bot_pr_is_out_of_the_metric_population_and_in_the_bot_counter`, so this is a plan deviation, not a broken criterion; but `bot_person_count` is a new selector that appears in neither PLAN.md nor DECISIONS.md.

**Fix:** Either surface `bot_pull_request_count(scope)` on the People page next to the person count (the string already reads "excluded from metrics automatically", which is about PRs), or add a one-line DECISIONS.md entry recording that the counter deliberately counts bot people and that `bot_pull_request_count` is staged for phase 7/8.

## [NIT] person.update audit entry records no before-state
`apps/catalog/views.py`

`person_edit` (line 68) calls `record_audit(..., after=form.cleaned_data)` with no `before`, so the audit trail for an edit shows the new values but not what they replaced. `merge_people` does pass a `before`, and `record_audit` supports it. ARCHITECTURE's "what must never be lost" #3 is about the operator's manual mapping work, which is exactly what this entry covers.

**Fix:** Snapshot the tracked fields off `person` before `form.save()` and pass them as `before={...}`.
