# Set your AI policy

This page is for an admin. It walks through **Settings → AI policy** — what each group of switches turns on,
the order worth turning them on in, and how to read the four compliance numbers the dashboards gain once you
have. Closing the violations those switches produce is a separate page:
[Handle policy violations](handle-policy-violations.md). The full rule reference, code by code, is
[`../POLICY.md`](../POLICY.md).

## Everything is off until you turn it on

A fresh installation raises nothing beyond the nine original rules. The fifteen checks that encode the PLANEKS
AI Engineering Standards each sit behind their own switch, all of them off, on purpose: a console that greets
you with four hundred violations you never asked for is a console you learn to ignore.

Turn them on **one at a time**, and look at what each one produced before turning on the next. A check that
fires on half your merged pull requests is usually telling you about your PR template, not about your team.

## The form, group by group

Saving the form creates a **new policy version**. It never edits the old one, and a pull request is always
judged by the version that was in effect when it was created — so switching something on today does not
retroactively flag last quarter. (The single exception is the very first version you ever save, which is
stamped to cover everything already synced.)

| Group | What it covers | Start with |
|---|---|---|
| **Disclosure and tools** | Whether a PR must say AI helped, and which tools are allowed | The two original switches; leave *Allowed tools* empty until you know which tools actually appear |
| **Human review** | Minimum human approvals, the extra approval a high-risk change needs, bot-only approvals, rubber stamps, and who counts as a designated reviewer | `forbid_ai_only_approval` — it is the cheapest one to be right about |
| **AI review** | Whether an AI reviewer must look first, whether its comment threads must be resolved, and the logins that count as AI reviewers | Fill in **AI reviewer logins** first; both checks stay silent until you do |
| **What a description must state** | Risk level, verification note, task link, and a plan for high-risk changes | Only after your PR template actually asks for those sections |
| **Quality gates and tests** | Merging over red checks or `[skip ci]`, weakened tests, missing tests, committed credential files | `forbid_ci_bypass` and `forbid_secret_artifacts` |
| **Size and scope** | Effective-line limits (flat, or one per risk level), scope creep, new dependencies, changed agent configuration | The per-risk limits — but read the numbers below before entering them |

**AI reviewer logins** is a list of GitHub logins, one per line (`@` optional, case does not matter) — the
account your AI reviewer posts as, for example `copilot`. Nothing about AI review is measured or checked until
at least one login is there.

**The per-risk line limits are empty by default.** The standards themselves say 800 lines at medium risk and 400
at high. They are not pre-filled because applying a limit automatically would raise a violation across your
whole history the moment you upgraded. Enter them when you want them.

## Risk levels come from sensitive paths

`high_risk_min_approvals`, the per-risk size limits and the "high-risk change needs a plan" check all read one
thing: a pull request's **risk level**, which is the highest level of any non-excluded file it touches.

Risk levels live on sensitive-path rules (**Settings → Sensitive paths**). To seed the standards' own table —
migrations, auth, payments, billing, workflows, settings, infra as high; api, serializers, manifests and
lockfiles as medium — run:

```
uv run python manage.py seed_sensitive_paths
```

They seed as `advisory`: they classify risk and raise no violation of their own. Read them as a starting point
and edit freely — `infra/**` is a dozen YAML files in one company and the whole product in another. A re-seed
never touches a rule you have edited.

## What you can watch afterwards

Four of the dashboard's KPI cards read the same facts these checks read, **whether or not you have switched the
checks on** — so you can look before you legislate. They are on every dashboard (Overview, a project, a
repository, a person), for whatever period the filter bar is set to:

| Card | What it counts |
|---|---|
| **Bot-only approval rate** | Of the PRs merged with any approval, the share whose approvals came only from bot accounts |
| **Quality-gate bypass rate** | Of the PRs merged in a repository that runs checks, the share whose last check rollup was red |
| **AI review coverage** | The share of merged PRs one of your AI reviewer logins reviewed. Empty ("—") until you name one |
| **High-risk AI PR rate** | The share of merged AI PRs touching a path your rules classify as high risk, with the structural-signal rate underneath it |

A useful order: watch a number for a fortnight, decide what it should be, then switch on the check that
enforces it. Formulas for all four are in [`../METRICS.md`](../METRICS.md).

## What none of this measures

The standards name four metrics. Review time and post-merge defects PR Radar measures directly. **AI cost per
task and how your team rates the tool are not derivable from GitHub data**, and PR Radar does not invent them.

The same document is explicit that the number of generated lines, prompts or AI pull requests is not used to
judge a person. That is built in rather than left to discipline: the person page leads with compliance and
quality and keeps volume below them, and no metric ranks people by how much they produced.

## After a change

Policy evaluation runs after every sync, so ordinary changes need nothing from you. If you switched something on
and want the existing history re-evaluated straight away:

```
uv run python manage.py recompute --from 2026-01-01
```

It makes no GitHub call. Remember that a new policy version governs pull requests created from the moment you
saved it — `recompute` re-applies the rules, it does not backdate the version that owns them.
