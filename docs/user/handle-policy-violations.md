# Handle policy violations

This page is for a lead. It walks through the Policy console (`/policy/`) — reading the compliance picture,
narrowing the violation table, and closing violations with a reason. See `docs/POLICY.md` for the full rule
reference if you need to know exactly why a violation fired.

## Read the console

Open **Policy** in the top bar. The top of the page shows four KPI cards for the selected period (last 30 days by
default): open violations, new violations, the AI-PR compliance rate (share of AI PRs with no open violation),
and the disclosure-mismatch count. A rate based on fewer than 5 AI PRs is shown greyed out with a note instead of
a misleadingly precise number — there just isn't enough data yet. Below the cards, **New violations by rule** shows
which rules fired most in the period. It counts every violation on a pull request opened in the period, whatever
its status now, so
each bar is split into the part still open and the part acknowledged, waived or resolved, with "open / total" next
to it. A rule can have a bar and still show nothing in the table below, which lists open violations only by
default — typically because a later recompute found the condition gone and resolved them. Click a rule's name to
open the table on exactly the violations its bar counts (every status, the same dates). A short list surfaces every PR with a `DISCLOSURE_MISMATCH`
(disclosure says "none" but a high-confidence AI signal was found) so you can go straight to the PRs most worth a
look.

## Filter the violation table

Use the **Filters** button to narrow by rule, severity, status (defaults to `open`), project, repository, the dates
the pull request was opened, or a PR title/number search. Filters apply on submit and stay set as you page through results or take an action, so
you don't lose your place. Clicking a violation's PR link opens that pull request.

## Acknowledge or waive

Tick one or more `open` rows, choose **Acknowledge** (you've seen it, you're not disputing it) or **Waive**
(you're accepting the risk on purpose), type a reason of at least 3 characters, and submit. An empty or
whitespace-only reason is rejected right there with a visible error and nothing changes. You can later move a
row between `acknowledged` and `waived` if you change your mind — a `resolved` row (its condition is already
gone) can't be acted on and is reported as skipped if you try to select it.

## Check the audit trail

Every acknowledge, waive, and automatic resolution writes an entry recording who acted (or nobody, for an
automatic resolution), when, and the status before and after. There's no separate audit UI yet — for now, check
`AuditEntry` rows for the violation via the Django admin if you need to see the full history.

## Keep violations current

Policy evaluation runs automatically after every sync, so violations reflect the latest PR state without you
doing anything. If you change a sensitive-path rule or `POLICY_DISABLED_RULES` and want existing PRs
re-evaluated against the new settings right away rather than waiting for the next sync, run:

```
uv run python manage.py recompute --from 2026-01-01
```

It makes no GitHub call, so it's safe to run as often as you like. This does **not** apply to an AI policy
change: see below for what a new version does and does not cover.

## Change the rules

- **Settings → AI policy** (admin only): edit the allowed tools, disclosure/approval/test requirements, and the
  size limit. Saving never overwrites history — it creates a new version that takes effect the moment you save
  it, and governs every pull request created from then on. A pull request created *before* that moment keeps
  being judged by whichever version applied to it at the time (or by none, if you are saving the very first
  version and that pull request predates it — the very first version is the one exception: it is stamped to
  cover every pull request already synced, so it does not start out empty). There is no way to change what an
  already-saved version covers; get the settings right before saving if the distinction matters to you.
- **Settings → Sensitive paths** (admin only): add, edit, or deactivate a glob marking a path as `forbidden` or
  `needs_extra_review`, globally or for one project. Deactivating a rule is the way to retire it — there's no
  delete, so the audit trail of who created or changed it stays readable.
