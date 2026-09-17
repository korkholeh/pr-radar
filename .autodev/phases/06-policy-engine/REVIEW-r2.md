# Review — phase 6 round 2

**Verdict:** changes_requested

Round 2 fixes all six round-1 findings, and the phase is otherwise strong: nine evaluators match spec §7 verbatim, the wanted-vs-existing diff is idempotent and parametrized over all nine codes, auto-resolve is filtered to status=OPEN with a null-actor AuditEntry, details_params carries only data, every selector starts from a ScopeFilter, all seven acceptance criteria have a test that can actually fail, and the full suite plus the whole lint gate are green on an independent run. All 20 PLAN tasks are checked with no [~]; docs, uk parity and the CSS manifest are present. One major finding remains, created by the interaction of the round-1 blocker fix with the round-1 effective_from fix: a policy saved through Settings → AI policy is stamped effective_from=now and a PR is judged under the version in effect at its own created_at, so the first policy version ever saved governs no already-synced pull request — and the Settings UI offers no way to change that (only /admin/ does). The user doc states the opposite twice. Three minor findings: a near-unfalsifiable query-budget test whose name the code does not actually honour, a bulk-action view that ignores HX-Request, and filter dropdowns built from unscoped querysets.

## [MAJOR] A policy saved from the Settings page governs no existing pull request, ever
`apps/policy/services.py`

save_policy_version() always stamps effective_from = timezone.now() (services.py:~270, after the round-1 fix removed the form field), and evaluate_pull_request() judges a PR under _policy_at(pull_request.created_at, versions), i.e. the newest version whose effective_from <= the PR's own created_at. Both changes are individually right, but together they mean a version saved through Settings → AI policy applies only to pull requests whose GitHub created_at is later than the save — and every PR already in the database was created before it. Concretely: a lead installs PR Radar, syncs six months of history, opens Settings → AI policy, turns on require_disclosure and saves. The Policy console stays empty forever for that history; violations only start appearing for PRs opened on GitHub after the save. The only way to make a policy cover existing PRs is to create an AIPolicy row with a backdated effective_from through /admin/ (AIPolicyAdmin has no readonly_fields), which nothing documents. The test suite does not catch this because test_recompute_command.py::test_recompute_evaluates_a_policy_added_after_the_pr_was_synced constructs the policy with AIPolicyFactory(effective_from=now-30d) — a backdate the product itself cannot produce.

**Fix:** In save_policy_version, when no previous version exists, stamp effective_from at PullRequest.objects.aggregate(Min("created_at")) (falling back to timezone.now() on an empty database) so the first version covers everything synced so far; keep timezone.now() for subsequent versions. Alternatively, add one explicit, audited "applies to pull requests created since" date on AIPolicyForm with a clear warning that it re-judges past PRs. Either way add a test: sync/create a PR, then save a first policy version through save_policy_version, and assert evaluate_pull_request produces the violation.

## [MINOR] User doc contradicts the shipped effective_from behaviour in two places
`docs/user/handle-policy-violations.md`

"Change the rules" says a new AI policy version "takes effect from the date you choose" — the field was removed from AIPolicyForm in this same round, so no date is chosen. "Keep violations current" tells a lead that after changing the AI policy they can run `manage.py recompute` to have existing PRs "re-evaluated against the new settings right away"; with creation-time policy gating this is false for AIPolicy changes (it remains true for sensitive-path rules and POLICY_DISABLED_RULES, which are applied on every run regardless of version).

**Fix:** Rewrite both paragraphs: state that a saved version takes effect at the moment it is saved and governs PRs created from then on, and scope the recompute advice to sensitive-path rules and POLICY_DISABLED_RULES. Update once the major finding above is resolved so the two agree.

## [MINOR] test_evaluate_pull_requests_loads_policy_and_settings_once cannot fail on the bug it names
`apps/policy/tests/test_evaluate.py`

The assertion is `len(two) < 2 * len(one)`. With F fixed queries and P per-PR queries, one = F+P and two = F+2P, so the assertion reduces to F > 0 — it holds for any F >= 1, including the degenerate case where the policy and settings are reloaded inside the loop and F is just the single queryset evaluation. The name is also not fully true of the code today: load_context() calls ai_cohort_statuses() -> get_bool("AI_COHORT_INCLUDE_SUSPECTED"), which is one AppSetting SELECT per pull request, so one of the settings is in fact read per PR despite the bulk entry point's docstring.

**Fix:** Assert on the shared loads directly: capture queries and assert the count of statements touching catalog_appsetting and policy_aipolicy is identical for one PR and for two (e.g. `sum("catalog_appsetting" in q["sql"] for q in ctx.captured_queries)`). Then either pass the cohort statuses into load_context from evaluate_pull_requests, or accept the one-per-PR read and correct the docstring.

## [MINOR] violation_bulk_action always returns a fragment, ignoring HX-Request
`apps/policy/views.py`

The view renders policy/partials/violations.html unconditionally (views.py, violation_bulk_action). partials/violations.html carries a real <form method="post" action="{% url 'policy:violation_bulk_action' %}"> fallback alongside hx-post, so a submit without htmx (JS disabled, or a mid-load click) navigates the browser to a page whose body is a bare table fragment with no <html>, nav, theme or styling. CLAUDE.md requires the same URL to return a full page for a normal request and a fragment for an htmx one; the PLAN's T15 decision only covers the shape of the error block, not this.

**Fix:** Branch on config.htmx.is_htmx(request): keep the fragment for htmx, and for a normal POST render policy/console.html with the same notice/action_errors context (or do a PRG redirect to policy:console with the notice carried through django.contrib.messages). Add the non-htmx POST case to test_views_bulk_action.py.

## [MINOR] Filter dropdowns are built from unscoped Project/Repository querysets
`apps/policy/forms.py`

ViolationFilterForm declares project = _LenientModelChoiceField(queryset=Project.objects.order_by("name")) and repository = ... Repository.objects.order_by("full_name") — the only two querysets in this phase that do not start from scope_for_user(). It is harmless today because scope_for_user() is unrestricted until phase 9 and _filtered_violations applies the filter on top of the already-scoped violations_in_scope(scope), so no violation leaks. But the <select> renders every project and repository name in the installation, and "an out-of-scope id is dropped" is not actually enforced by the field (it resolves the object; the scoped violation queryset is what yields zero rows). When phase 9 turns scope narrowing on, a restricted lead sees the full project/repository list by name.

**Fix:** Pass the scoped querysets in from the view the way BulkViolationActionForm already does: `ViolationFilterForm(request.GET, projects=projects_in_scope(scope), repositories=repositories_in_scope(scope))` and assign them in __init__. The existing lenient-drop contract and its tests stay unchanged.

## [NIT] current_policy() is now dead production code
`apps/policy/services.py`

After the round-1 fix, current_policy() is referenced only by test_services.py and by docstrings (including save_policy_version's, which still says "current_policy() keeps working unchanged"). No production caller remains.

**Fix:** Either delete current_policy() and its two tests, or keep it with a comment saying it is the read-side helper phase 7/9 will call, and drop the stale docstring reference in save_policy_version.
