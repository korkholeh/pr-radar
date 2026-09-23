# AI policy and violations

This is the reference for the policy rules — the nine from the spec and the fifteen that turn the PLANEKS AI
Engineering Standards into automatic checks — how a violation moves through its lifecycle, and what the two
Settings pages that shape both control. It doubles as the contract `tests/test_docs.py` checks against the code:
every `PolicyViolation.RuleCode`, `PolicyViolation.Status` and `SensitivePathRule.AiMode` value must appear here,
and the severity table below must match `apps.policy.rules.SEVERITY` exactly.

## The versioned AI policy

`AIPolicy` (Settings → AI policy) is a **versioned singleton**: saving the form never edits a row in place, it
always creates a new row — the field cannot be typed in or backdated by hand. History is append-only, shown
newest first on the settings page, and every save writes an `AuditEntry(action="ai_policy.update")` with the
previous version's field values as `before`. `effective_from` is stamped to the moment the form is saved, with
one exception: the very *first* version ever saved (no prior `AIPolicy` row exists) is stamped to the earliest
`PullRequest.created_at` already synced (or `now()` if nothing has been synced yet), so it covers every pull
request already in the database instead of starting out empty.

A pull request is evaluated under **the version in effect when it was created** — the newest version whose
`effective_from` was already in the past at the PR's own `created_at` — not the newest version overall. This
means saving a *second or later* policy version never changes how an already-existing PR is judged: it only
starts governing PRs created from that moment on. A pull request created before any policy existed at all is
never evaluated: no violation is created for it, and any violation still `open` on it auto-resolves.

## The nine spec rules

"AI PR" below means `pull_request.ai_status` is in the AI cohort (`ai_explicit`, `ai_disclosed` and `ai_suspected`;
turning `AI_COHORT_INCLUDE_SUSPECTED` off drops `ai_suspected`) — the same cohort the AI-adoption dashboards use,
not a second definition. "Human approval" is a `Review` with `state=approved` whose reviewer resolves to a `Person`
that is neither the pull request's author nor marked as a bot, counted once per person no matter how many times
they approved.

| Rule code | Severity | Fires when | Scope | Switched off by |
|---|---|---|---|---|
| `DISCLOSURE_MISSING` | medium | The AI-assistance disclosure is missing or ambiguous | Any PR | `AIPolicy.require_disclosure = False` |
| `DISCLOSURE_MISMATCH` | high | Disclosure says "none" but a high-confidence AI signal was detected | Any PR | Cannot be switched off (a mismatch is always worth a look) |
| `TOOL_NOT_ALLOWED` | high | A declared or detected tool is outside `AIPolicy.allowed_tools` (one violation per offending tool) | Any PR | Leaving `allowed_tools` empty (nothing is disallowed until something is allowed) |
| `SENSITIVE_PATH_FORBIDDEN` | high | The PR touches a file matching an active `forbidden` sensitive-path rule (one violation per matched rule) | AI only | Deactivating the matching `SensitivePathRule`, or `POLICY_DISABLED_RULES` |
| `SENSITIVE_PATH_REVIEW` | medium | The PR touches a `needs_extra_review` glob and has fewer than `min_human_approvals + 1` human approvals | AI only | Deactivating the matching rule, or `POLICY_DISABLED_RULES` |
| `NO_HUMAN_APPROVAL` | high | A merged PR has fewer human approvals than `AIPolicy.min_human_approvals` | AI only, merged only | `AIPolicy.require_human_approval = False` |
| `SELF_MERGE` | high | A merged, self-merged PR has zero approvals from another human | AI only, merged only | `POLICY_DISABLED_RULES` |
| `NO_TESTS` | low | More than `NO_TESTS_MIN_LINES` non-test, non-excluded lines changed with no test changes | AI only | `AIPolicy.require_tests_for_ai_prs = False` |
| `AI_PR_TOO_LARGE` | low | Effective additions + deletions exceed `AIPolicy.ai_pr_max_effective_lines` | AI only | Leaving `ai_pr_max_effective_lines` empty |

`POLICY_DISABLED_RULES` (Settings, see `docs/CONFIGURATION.md`) is a second, blunt off-switch that covers every
rule code, including the ones that have no dedicated `AIPolicy` field — an unrecognised code in the list is
ignored with a logged warning rather than raising.

## The PLANEKS standards as checks

Fifteen further rules map the PLANEKS **AI Engineering Standards** and **AI Developer Manifesto** onto data PR
Radar already holds. **Every one of them is off until you turn it on.** An upgrade raises nothing: a test builds
the worst pull request the codebase can describe — merged with red checks, a skip-CI commit, a deleted test, a
committed `.env`, a changed `CLAUDE.md`, a migration, an empty description, a bot-only approval — and asserts
that a default policy produces zero violations for it.

| Rule code | Severity | Standard | Fires when | Scope | Switched on by |
|---|---|---|---|---|---|
| `QUALITY_GATE_BYPASSED` | high | Security rules, "do not bypass controls" | Merged with a red check rollup, a `[skip ci]` commit, a CI configuration the diff relaxed, or a CI step the diff removed (one violation per reason) | Any PR, merged only | `forbid_ci_bypass` |
| `TEST_WEAKENED` | high | R6 | A test file deleted while non-test code changed, a skip/xfail marker added, or assertions removed with none added back | Any PR | `forbid_test_weakening` |
| `AI_ONLY_APPROVAL` | high | R10 | Every approval came from a bot account and none from a person | Any PR, merged only | `forbid_ai_only_approval` |
| `AI_REVIEW_MISSING` | medium | PR requirements, "the AI reviewer goes first" | None of the logins in `ai_reviewer_identities` reviewed it | Any PR, merged only | `require_ai_review_first` **and** at least one login in `ai_reviewer_identities` |
| `AI_REVIEW_UNRESOLVED` | medium | PR requirements, "the author answers every comment" | Merged with a thread opened by an AI reviewer that GitHub reports as unresolved | Any PR, merged only | `require_ai_comments_resolved` |
| `HIGH_RISK_NO_PLAN` | high | R3 | A high-risk change whose description states no plan, risks or rollback | Any PR | `require_high_risk_plan` **and** a `risk_level=high` sensitive-path rule that matches |
| `RISK_LEVEL_MISSING` | low | PR requirements | The description states no risk level | Any PR | `require_risk_level` |
| `VERIFICATION_MISSING` | low | PR requirements | The description says nothing about how the change was verified | Any PR | `require_verification_note` |
| `TASK_LINK_MISSING` | low | PR requirements | The description links no task, anywhere | Any PR | `require_task_link` |
| `NEW_DEPENDENCY_AI` | medium | R7 | An AI PR changes a dependency manifest or lockfile | AI only | `flag_new_dependencies_in_ai_prs` |
| `MIGRATION_AI_INSUFFICIENT_REVIEW` | high | "Requires extra human attention" | An AI PR carries a migration and no designated reviewer approved it | AI only, merged only | Naming at least one person in `designated_reviewers` |
| `SECRET_ARTIFACT_COMMITTED` | high | Security rules | The PR adds a credential file, a private key or an agent's chat history (`POLICY_SECRET_ARTIFACT_PATH_GLOBS`, minus the exceptions) | Any PR | `forbid_secret_artifacts` |
| `AGENT_CONFIG_CHANGED` | low | "Changes to these files go through normal review" | `CLAUDE.md`, `AGENTS.md`, `.claude/**` and friends changed | Any PR | `flag_agent_config_changes` |
| `SCOPE_CREEP` | medium | R4 | An AI PR trips `wholesale_reformat`, or reaches into top-level modules its stated scope never mentions | AI only | `forbid_scope_creep` |
| `RUBBER_STAMP_ON_AI_PR` | high | Manifesto, "we do not accept code we do not understand" | A merged AI PR approved with an empty review, no comments, inside `RUBBER_STAMP_MAX_MINUTES` | AI only, merged only | `forbid_rubber_stamp_approval` |

Eight rules are **merge-dependent** — `NO_HUMAN_APPROVAL`, `SELF_MERGE`, `QUALITY_GATE_BYPASSED`,
`AI_ONLY_APPROVAL`, `AI_REVIEW_MISSING`, `AI_REVIEW_UNRESOLVED`, `MIGRATION_AI_INSUFFICIENT_REVIEW` and
`RUBBER_STAMP_ON_AI_PR` — and never fire on an open pull request, whatever the policy says: an open PR can still
get its review, resolve its threads or fix its checks.

Note which rules are **not** AI-only. A bypassed quality gate, a weakened test, a committed credential and a
missing task link are no better for having been written by hand, so those checks apply to everybody. Restricting
them to the AI cohort would be measuring the tool rather than the engineering.

**Two of these read the diff**, and only where diff analysis runs (`DIFF_ANALYSIS_REPOSITORIES`, see
`docs/user/tune-ai-detection.md`): the "relaxed CI configuration" and "removed CI step" reasons behind
`QUALITY_GATE_BYPASSED`, and the skip-marker and assertion counts behind `TEST_WEAKENED`. In a repository that is
not opted in, those reasons simply never fire — a repository whose diff was never read is not one that weakened
its tests. Every other check works from data the sync already stores.

## Risk levels and the limits that read them

A `SensitivePathRule` can carry a `risk_level` of `low`, `medium` or `high`. A pull request's risk is the
**highest** of the non-excluded paths it touches: a hundred lines of documentation plus one line under
`payments/` is a payments change. A rule with no risk level says nothing about risk, and a change matching no
risk rule has no risk level at all.

Three things read it:

- `require_high_risk_plan` → `HIGH_RISK_NO_PLAN`, for high-risk changes only.
- `high_risk_min_approvals` (default 2) raises `min_human_approvals` for a high-risk change. Only ever raises it:
  a lead who asks for three approvals everywhere does not mean two on the riskiest paths.
- `max_effective_lines_by_risk`, a per-level size limit that overrides the flat `ai_pr_max_effective_lines` for
  `AI_PR_TOO_LARGE`. It is **empty by default**. The standards' own numbers are 800 lines at medium risk and 400
  at high; enter them in Settings → AI policy if you want them, because applying them automatically would raise a
  violation across an installation's whole history the moment it upgraded.

`manage.py seed_sensitive_paths` seeds the standards' risk table as `advisory` rules — `**/migrations/**`,
`**/auth*/**`, `**/payments/**`, `**/billing/**`, `.github/workflows/**`, `**/settings/**`, `infra/**`,
`ansible/**` and `docker/**` at high risk; `**/api/**`, `**/serializers.py`, `pyproject.toml`, `package.json` and
`*.lock` at medium. Read them as a starting point: `infra/**` is a dozen YAML files in one company and the whole
product in another. A re-seed never touches a rule you have edited.

## The compliance metrics

Five metrics read the same facts these checks read, and they are computed **whether or not the matching check is
switched on** — a rate that only moved when somebody ticked a checkbox would measure the configuration rather
than the engineering. They are `ai_only_approval_rate`, `quality_gate_bypass_rate`, `ai_review_coverage`,
`high_risk_ai_pr_rate` and `structural_signal_rate`; four of them are a KPI row on every dashboard, at every
scope, and all five are in `docs/METRICS.md` with their exact formulas.

Two of them read a policy field rather than raw pull-request data. `ai_review_coverage` is empty until at least
one login is entered in `ai_reviewer_identities` — with nobody named, "no AI reviewer looked" is not a fact, it
is a missing setting, and the metric says `None` rather than 0%. `high_risk_ai_pr_rate` depends on an active
`SensitivePathRule` carrying `risk_level=high` whose glob matches something; with no risk rules seeded it reads
0%. It classifies a path the same way the policy engine does — from the globs, advisory rules included — rather
than from `PRFile.matched_sensitive_rule`, which advisory rules never reach.

They are deliberately not violation counts: `violations_by_rule` already reports what the policy raised, and the
two answer different questions — "how often does this happen here" versus "how much of it have we decided to
act on".

## What PR Radar does not measure

`AI-ENGINEERING-STANDARDS.md` names four metrics. Review time and post-merge defects PR Radar measures directly.
**AI cost per task and the team's own rating of the tool are not derivable from GitHub data**, and they are not
invented here — no metric in this product estimates either.

The same document is explicit that *the number of generated lines, prompts or AI pull requests is not used to
evaluate an employee's performance*. That is a product constraint, not a preference: the person page leads with
compliance and quality and keeps volume below them, the AI-adoption dashboards report cohort shares rather than
per-person counts, and no metric ranks people by how much they produced.

## Sensitive paths

Settings → Sensitive paths lets an admin flag a path glob as `forbidden` (an AI PR must never touch it),
`needs_extra_review` (an AI PR touching it needs one more human approval than usual) or `advisory` (it only
classifies the path's risk and raises no violation of its own). An advisory rule is deliberately left out of
`PRFile.matched_sensitive_rule`: the seeded risk table is broad, and if it took part in the first-match-wins loop
it would shadow a narrow rule somebody wrote to forbid a path. A rule is either global
(empty project) or scoped to one project; on every sync, each of a pull request's non-excluded files is matched
against the active rules that apply to it (global, plus any of the PR's repository's projects) and the match is
recorded on `PRFile.matched_sensitive_rule`. Deactivating a rule clears the mark and auto-resolves any violation
that depended on it on the next run — there is no delete, so its audit trail (who created or toggled it, and
when) stays readable.

## How a violation is found — and un-found

Policy evaluation is the pipeline's fourth stage, right after AI detection: `identity → derive → detect →
**policy**`. It computes the exact set of violations a PR's current state should have and diffs it against what
is already stored, keyed by `(pull_request, rule_code, details_hash)` where `details_hash` is computed only from
the parts of a finding that define its *identity* (a moving counter like an approval count never changes the
key). The diff can only:

- **create** a new `open` violation for a key that should exist but doesn't yet;
- **refresh** an existing violation's stored numbers when the key is unchanged but a counter moved (e.g. the
  approval count in a `SENSITIVE_PATH_REVIEW` violation) — the row's `status` and identity never change;
- **auto-resolve** an `open` violation whose key no longer should exist, setting
  `resolved_automatically=True`, `resolved_by=None`, and writing an `AuditEntry(actor=None,
  action="policy_violation.auto_resolve")`.

Auto-resolve **never** touches an `acknowledged` or `waived` row (a lead's judgement is never overwritten by a
re-run), and it never reopens a `resolved` row even if the condition comes back — recurrence is visible on the
PR and in the audit trail instead of resurrecting the old row. Re-running evaluation on an unchanged PR creates
no new row and changes no status.

## Acknowledge vs. waive

On the Policy console (`/policy/`), a lead selects one or more `open` violations, picks **Acknowledge** or
**Waive**, and must give a reason (at least 3 characters — an empty or whitespace-only comment is rejected with a
visible field error and changes nothing). Both actions record who acted, when, and the before/after status as an
`AuditEntry`; the two differ only in label, not in mechanism (`acknowledged ↔ waived` is a normal transition, so
a lead can change their mind later). A `resolved` violation cannot be acted on — its condition is already gone,
so judging it would be meaningless — and is reported as skipped rather than silently ignored.

## Statuses

| Status | Meaning |
|---|---|
| `open` | The condition holds and no lead has judged it yet. |
| `acknowledged` | A lead has seen it and recorded a reason, without disputing that it happened. |
| `waived` | A lead has recorded a reason for accepting the risk anyway. |
| `resolved` | The condition no longer holds — either because a human fixed the code, or automatically because it re-evaluated to gone (`resolved_automatically=True`). |

## Reading a greyed compliance number

A KPI or table cell showing the `≈` marker (or the fuller "Small sample" sentence on the Policy console's own
KPIs) means the underlying count is below `MIN_SAMPLE` (default 5, see `docs/CONFIGURATION.md`) — the number
shown is still the real value, not a placeholder, but a rate or share computed from fewer than five PRs swings
wildly on the next PR and should not be read as a trend on its own. Treat it as "not enough evidence yet" rather
than "good" or "bad": a project with one AI PR and zero violations is not meaningfully more compliant than one
with one AI PR and one violation, and a leadership conversation built on either number alone will not survive the
sixth PR arriving. Widening the period or looking at a parent scope (project instead of repository, organisation
instead of project) is usually how a greyed number stops being greyed — the underlying population growing large
enough, not a setting to change.

## Reading auto-resolve

An auto-resolved violation (`resolved_automatically=True`, `resolved_by=None`) is not deleted and not the same as
a lead waiving it — it means the *condition* went away on its own (a later push added the missing tests, a
sensitive-path rule was deactivated, the policy version governing that PR changed), not that anyone judged it. It
is visible in the violation's own history and in the audit trail like any other status change, so "why did this
disappear from the open list" always has an answer to point to. Two things it deliberately does **not** do:
overwrite an `acknowledged`/`waived` row (a lead's own judgement always outranks a re-evaluation), and reopen a
`resolved` row if the same condition recurs later — a recurrence shows up as a fresh violation on the PR instead,
so the history stays append-only and a lead reading the violation table never has to wonder whether "resolved"
quietly became "open" again behind their back.
