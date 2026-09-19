# Connect GitHub and sync your repositories

This page is for a lead or admin setting up PR Radar for the first time, or adding a new repository. It assumes
you already have an account (see `getting-started.md`) and that whoever runs the server has followed
`docs/SETUP.md`'s "Connect GitHub" step. Connecting, discovering repositories and triggering a sync are
admin-only; if you don't see **Connections**, **Repository settings** or **Discover repositories** in the
Settings menu, or the **Sync now** button on the **Sync** page, ask an admin. Any lead can still open **Sync** to see progress and history — it shows no
per-project data.

## Connect

1. Open **Connections** in the top bar, then **New connection**.
2. Give it a name (e.g. "Acme org"), pick the token kind, and for a fine-grained token, the owner login the
   token was issued for. The owner login is only a label for the connection — it never limits which
   repositories you can discover.
3. Paste the token and select **Save**. PR Radar checks it against GitHub immediately; if the token is invalid,
   you'll see why and can fix it before saving. If you'd rather save it now and check later, tick "save without
   verifying".
4. Once saved, the connection's row shows its status and the token's last four characters — never the full
   token, anywhere.

## Discover and add repositories

1. Open **Settings → Discover repositories**.
2. Pick the connection you just created from the dropdown. PR Radar lists every repository that token can see,
   grouped by owner — including repositories owned by a client or another organization, as long as the token
   can read them. Use the **Owner** dropdown to narrow a long list to one owner. Archived repositories are
   hidden until you tick "Show archived".
3. Tick the repositories you want PR Radar to sync, optionally choose a project to add them to, and select
   **Add selected repositories**.
4. A repository already synced through a different connection shows which one — you can rebind it here instead
   of adding a duplicate.

## Move a repository to another connection

When you issue a replacement token, add it as a second connection and move the repositories over rather than
removing and re-adding them — re-adding would lose nothing already synced, but moving is one step and keeps the
repository's sync state.

1. Open **Settings → Repository settings**. It lists every repository PR Radar holds, the connection that syncs
   it and that connection's status. Use the **Connection** dropdown to see only one connection's repositories.
2. Select **Change connection** on the repository you want to move.
3. Pick the new connection, tick the confirmation and save.

Only active connections are offered, and the new connection's token must be able to read the repository — check
it from **Connections** if a sync starts failing afterwards. Everything already synced stays: pull requests,
reviews, commits, metrics and history are untouched, and only future syncs use the new token.

## Sync

Open **Sync** and select **Sync now**. The page shows the running sync's progress and, once finished, its
per-connection results. A first sync pulls the last several months of history (see `BACKFILL_DAYS` in
`docs/CONFIGURATION.md`); later syncs only pull what changed.

## Load historical data

"Sync now" only fetches what changed since the last run. To pull older pull requests — after adding a
repository, after a sync window that was too narrow, or when a dashboard looks empty further back — use
**Load historical data** on the same page:

1. Pick a period: the last 7, 14, 30 or 90 days, or **From a specific date** and a date of your own.
2. Optionally select one or more repositories. Leave the list empty to backfill every active repository.
3. Select **Start backfill**.

The backfill re-fetches every pull request updated on or after that date, in Kyiv time. It uses the same
queue, lock and rate-limit budget as an ordinary sync, so only one of them runs at a time — the button is
disabled while a sync is in flight. Finished backfills appear in the run list with their start date in the
**Since** column.

The equivalent from the command line is `uv run python manage.py sync --since 2026-09-05`, optionally with
`--repo owner/name` (repeatable) or `--project slug`.

A wide backfill over many repositories costs a lot of GitHub API calls and may pause part of an hour waiting
out a rate limit. That is expected — narrow it to the repositories you need if you'd rather not wait.

## If something looks wrong

- **A connection shows "Invalid" or "Degraded".** Open it from **Connections** and read the last check below the
  form — each line says exactly what failed and what to do about it.
- **A repository won't sync.** Check its connection's status first; a connection that has gone invalid stops
  syncing its repositories until fixed.
- For every verification code and what it means, see `docs/GITHUB_CONNECTIONS.md`.
