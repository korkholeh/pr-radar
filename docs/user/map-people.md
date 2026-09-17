# Map people

This page is for a lead or admin. PR Radar reports on **people**, not on GitHub accounts or git email addresses
directly — every metric is attributed to a `Person`, and Settings → **People** is where you keep that mapping
correct. If you don't see **People** in the top bar, ask an admin for the `catalog.manage_settings` permission.

## Why there's a queue at all

A GitHub login (e.g. `octocat`) identifies exactly one GitHub account, so PR Radar maps it to a person
automatically the first time it's seen — there's nothing to confirm. A bare git commit email is different: the
same person can commit under several emails, and an email alone doesn't prove who it belongs to. PR Radar
auto-maps an email only when GitHub itself vouches for the link — a `…@users.noreply.github.com` address, or a
commit where GitHub paired the email with a login. Every other email lands in **Unmapped identities** for you to
resolve.

## Resolve the queue

Open Settings → People → **Unmapped identities**. Each row is one identity (a git email PR Radar couldn't
auto-map) with four actions:

- **Assign** — attach it to an existing person. Use this when the same contributor already has a mapped GitHub
  login and this is just another email they commit from.
- **New person** — create a person from this identity alone, when it's a real contributor with no other mapped
  identity yet.
- **Mark as bot** — create a person flagged as a bot and attach it. Bot-authored PRs are counted separately and
  never appear in delivery or AI-adoption metrics.
- **Exclude** — create a person flagged "exclude from metrics" and attach it. Use this for service accounts or
  anyone else whose PRs shouldn't count, without labelling them a bot.

Once an identity is assigned, it leaves the queue. Nothing you assign is ever undone by a later sync — a re-sync
only adds new identities, it never re-points ones you've already resolved.

## Fix a person's flags later

Open Settings → **People** and select **Edit** on any person to change their display name, team, role, notes,
bot flag or metrics exclusion — including one PR Radar auto-created and got wrong.

## Merge two people

If the same contributor ended up as two `Person` records — most often because an email was force-assigned before
its login was ever seen — open Settings → People → **Merge people**. Pick the person to merge *from* (the
duplicate) and the person to merge *into* (the one to keep). Every identity the duplicate holds moves to the
kept person, and the duplicate is deleted. This cannot be undone, so double-check both names before confirming.

## What a bot flag changes

A person flagged **is bot** is excluded from every dashboard and export, and counted separately in a dedicated
bot counter instead. A person flagged **exclude from metrics** (without being a bot) is excluded the same way,
but does not add to the bot count — use it for anything that isn't really a contributor's own work.
