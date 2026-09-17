# Connect GitHub and sync your repositories

This page is for a lead or admin setting up PR Radar for the first time, or adding a new repository. It assumes
you already have an account (see `getting-started.md`) and that whoever runs the server has followed
`docs/SETUP.md`'s "Connect GitHub" step. Connecting, discovering repositories and triggering a sync are
admin-only; if you don't see **Connections** or **Repositories** in the top bar, or the **Sync now** button on
the **Sync** page, ask an admin. Any lead can still open **Sync** to see progress and history — it shows no
per-project data.

## Connect

1. Open **Connections** in the top bar, then **New connection**.
2. Give it a name (e.g. "Acme org"), pick the token kind, and for a fine-grained token, the owner login the
   token was issued for.
3. Paste the token and select **Save**. PR Radar checks it against GitHub immediately; if the token is invalid,
   you'll see why and can fix it before saving. If you'd rather save it now and check later, tick "save without
   verifying".
4. Once saved, the connection's row shows its status and the token's last four characters — never the full
   token, anywhere.

## Discover and add repositories

1. Open **Repositories**.
2. Pick the connection you just created from the dropdown. PR Radar lists every repository that token can see,
   grouped by owner. Archived repositories are hidden until you tick "Show archived".
3. Tick the repositories you want PR Radar to sync, optionally choose a project to add them to, and select
   **Add selected repositories**.
4. A repository already synced through a different connection shows which one — you can rebind it here instead
   of adding a duplicate.

## Sync

Open **Sync** and select **Sync now**. The page shows the running sync's progress and, once finished, its
per-connection results. A first sync pulls the last several months of history (see `BACKFILL_DAYS` in
`docs/CONFIGURATION.md`); later syncs only pull what changed.

## If something looks wrong

- **A connection shows "Invalid" or "Degraded".** Open it from **Connections** and read the last check below the
  form — each line says exactly what failed and what to do about it.
- **A repository won't sync.** Check its connection's status first; a connection that has gone invalid stops
  syncing its repositories until fixed.
- For every verification code and what it means, see `docs/GITHUB_CONNECTIONS.md`.
