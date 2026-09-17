# Changelog

## Unreleased

### Added

- First runnable version of the app: sign in, sign out, and reset a forgotten password.
- Placeholder Overview page at `/` — the landing page after login; real dashboards arrive in later phases.
- Theme switcher (System / Light / Dark). The choice is saved to your account and applied before the page
  paints, so there is no flash of the wrong theme.
- Language switcher (English / Українська), also available on the login page. Your choice is saved to your
  account and follows you to a new device or browser session.
- GitHub connections (Settings → Connections): add, edit, verify and deactivate a connection; tokens are
  encrypted at rest and shown only as their last four characters. An admin banner warns about invalid, expired
  or soon-to-expire connections.
- Repository discovery and rebinding (Settings → Repositories): browse the repositories a connection can see,
  add the ones you want synced, and move a repository to a different connection without losing its history.
- GitHub sync (Sync page, `manage.py sync`, and a background task): pulls pull requests, reviews, commits,
  checks and files incrementally, with per-connection rate limiting and retry handling. See
  `docs/GITHUB_CONNECTIONS.md` and `docs/user/connect-github.md`.
- `manage.py bootstrap_connection` and `manage.py rotate_encryption_key` management commands.
