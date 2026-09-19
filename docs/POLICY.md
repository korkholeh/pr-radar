# AI policy and violations

This is the reference for the nine policy rules, how a violation moves through its lifecycle, and what the two
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

## The nine rules

"AI PR" below means `pull_request.ai_status` is in the AI cohort (`ai_explicit`, `ai_disclosed`, and
`ai_suspected` when `AI_COHORT_INCLUDE_SUSPECTED` is on) — the same cohort the AI-adoption dashboards use, not a
second definition. "Human approval" is a `Review` with `state=approved` whose reviewer resolves to a `Person`
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
rule code, including the five that have no dedicated `AIPolicy` field — an unrecognised code in the list is
ignored with a logged warning rather than raising. `NO_HUMAN_APPROVAL` and `SELF_MERGE` are the two
**merge-dependent** rules: on an open (not-yet-merged) PR they never fire, whatever the policy says.

## Sensitive paths

Settings → Sensitive paths lets an admin flag a path glob as `forbidden` (an AI PR must never touch it) or
`needs_extra_review` (an AI PR touching it needs one more human approval than usual). A rule is either global
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
