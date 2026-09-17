# Review — phase 6 round 1

**Verdict:** changes_requested

Phase 6 is well-built and well-tested: the nine evaluators match spec §7 verbatim, the wanted-vs-existing diff is idempotent and parametrized over all nine codes, auto-resolve is filtered to status=OPEN with an AuditEntry, details_params carries only data, every selector starts from a ScopeFilter, and the full test suite plus the whole lint gate are green on an independent run. All 20 PLAN tasks are checked with no [~], and the docs/translations/CSS deliverables are present. One blocker, though: evaluation gates each PR against current_policy() (the newest version) rather than the policy in effect when the PR was created, so the first time an admin saves a second AIPolicy version, every PR in the database falls before the new effective_from and its entire open-violation backlog auto-resolves irreversibly (resolved rows never reopen and the unique constraint blocks a replacement row). No test covers more than one policy version. Five smaller findings: an editable effective_from that contradicts a logged decision, a docs/POLICY.md sentence that describes a different rule than the code, filter fields that require typing raw database ids and enum values, a KPI card and its list counting different things, and two console tests that are near-unfalsifiable.

## [BLOCKER] Saving a second AIPolicy version permanently auto-resolves the whole violation backlog
`apps/policy/services.py`

evaluate_pull_request gates on `pr.created_at >= policy.effective_from` (services.py:130) where `policy = current_policy()` — the newest version already in effect (services.py:47). save_policy_version creates a new row with effective_from defaulting to now. The first time an admin changes any policy field, every PR in the database is older than the new effective_from, so the wanted set is empty and every open violation on every PR auto-resolves with resolved_automatically=True on the next sync/recompute. Per the phase's own decision an auto-resolved row is never reopened, and the UniqueConstraint(pull_request, rule_code, details_hash) blocks a fresh open row beside it — so the backlog is lost irreversibly, and versioning degenerates to 'only PRs created after the last save are ever judged'. No test exercises two AIPolicy versions: every RULE_SETUPS helper in test_evaluate.py creates exactly one.

**Fix:** Evaluate each PR under the policy in effect when it was created: `policy = current_policy(at=pull_request.created_at)` (the `at` parameter already exists and is currently never used), gating only on 'no policy existed at that time'. Add a regression test: v1 effective T0, PR created T1, v2 saved at T2 — the PR is still evaluated under v1 and its open violation survives.

## [MINOR] AIPolicyForm exposes an editable effective_from, contradicting a logged decision
`apps/policy/forms.py`

.autodev/DECISIONS.md [p06/plan] states the settings form 'writes a new row with effective_from = now' and explicitly rejects the alternative 'let the admin pick an arbitrary past effective_from (backdating a policy over already-judged PRs)'. The shipped form (forms.py:101, initial set at forms.py:114) makes the field editable, so backdating is possible; a future-dated value silently creates a version current_policy() ignores, with no marker in the read-only history list.

**Fix:** Drop effective_from from AIPolicyForm.Meta.fields and set `effective_from=timezone.now()` inside save_policy_version, or validate `effective_from >= now` and render a 'not yet in effect' marker for future versions in the history list.

## [MINOR] Doc describes a different effective_from rule than the code implements
`docs/POLICY.md`

docs/POLICY.md:16 says 'A pull request created before the earliest policy version's effective_from … is never evaluated', but the code compares against the newest version returned by current_policy(). The same paragraph claims 'backdating a stricter policy never retroactively flags old work', which the editable effective_from field makes false.

**Fix:** Rewrite the paragraph once the evaluation gate is fixed, naming exactly which version governs a PR (the one in effect at the PR's creation time).

## [MINOR] Console filters require typing raw database ids and enum values
`apps/policy/forms.py`

ViolationFilterForm declares project and repository as CharFields parsed as primary keys (clean_project: Project.objects.filter(pk=raw)), and rule_code/severity as CharFields matched against enum values. console.html renders all four as plain text inputs, so a lead has to type a numeric project id or the literal string NO_TESTS. docs/user/handle-policy-violations.md advertises filtering by rule, severity, project and repository as a normal workflow.

**Fix:** Use ModelChoiceField over the scoped project/repository querysets and ChoiceField(choices=PolicyViolation.RuleCode.choices / Severity.choices), keeping the lenient-drop behaviour for unknown values, so all four render as selects.

## [MINOR] Disclosure-mismatch KPI and the list under it count different objects
`apps/policy/selectors.py`

compliance_kpis.disclosure_mismatch_count counts PolicyViolation rows by the violation's created_at inside the period, while disclosure_mismatch_pull_requests filters PullRequest.created_at inside the period. On the same console page the card number and the number of rows in the list beneath it can disagree, with no explanation of why.

**Fix:** Anchor both on the same timestamp (either the violation's created_at or the PR's), and have the KPI count distinct PRs if the list below it is a PR list.

## [MINOR] Two console tests are near-unfalsifiable
`apps/policy/tests/test_views_console.py`

test_chart_table_alternative_matches_violations_by_rule asserts `str(row.count) in content` against the entire rendered page — the digits '1' and '2' also appear in the pagination block ('Page 1 of 1'), so the assertion holds even if the chart table is empty or wrong. test_low_sample_kpi_is_marked runs with zero AI PRs, so it always satisfies the `data-compliance-rate="none"` half of its `or`, and the greyed low-sample branch (sample_size 1..4) is never exercised.

**Fix:** Parse or slice the `#policy-rule-chart-table` fragment and compare its rows to violations_by_rule; build 1-4 AI-cohort PRs so the compliance rate is a real number below MIN_SAMPLE and assert on `data-low-sample` alone.
