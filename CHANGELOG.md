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
- Automatic identity resolution: a synced GitHub login is mapped to a person immediately, a git email only when
  GitHub itself links it to a login; bot accounts are detected from a configurable login list.
- People management (Settings → People): list every person with their identities, team, role and PR count; edit
  a person's details, bot flag and metrics exclusion; merge two people into one. See `docs/user/map-people.md`.
- The unmapped-identity queue (Settings → People → Unmapped identities): assign an email to an existing person,
  create a new person from it, mark it as a bot, or exclude it from metrics.
- Every pull request now carries its derived fields (size bucket, effective lines, test-file flag, rubber-stamp,
  self-merge, hotfix, revert, review timing and counts), recomputed idempotently after each sync.
- AI detection: eight regex-based signal detectors, a tolerant PR-template disclosure parser and a resolved
  `ai_status`/`ai_disclosure`/`ai_tools` per PR, recomputed idempotently after each sync alongside derive. See
  `docs/user/tune-ai-detection.md`.
- Settings → Detection rules: list, create, edit and activate/deactivate a detection rule, with a dry run against
  recently stored PRs before saving.
- A minimal PR detail page (`/prs/<id>/`) showing the resolved AI status, disclosure, tools and every matched
  signal with its evidence.
- `manage.py recompute` and `manage.py seed_detection_rules` management commands.
- `docs/pull_request_template.md`, a recommended PR template matching the disclosure parser's defaults.
- AI policy engine: nine configurable rules (missing/mismatched disclosure, a disallowed tool, a forbidden or
  extra-review sensitive path, missing human approval, self-merge, missing tests, an oversized AI PR) evaluated
  idempotently after every sync, with results that auto-resolve when the condition clears without ever
  overwriting a lead's own judgement. See `docs/POLICY.md`.
- Policy console (`/policy/`): compliance KPIs, a by-rule violation distribution, a disclosure-mismatch PR list,
  and a filterable, paginated violation table with bulk acknowledge/waive (a reason is required); every status
  change is recorded in the audit trail. See `docs/user/handle-policy-violations.md`.
- Settings → AI policy: a versioned, append-only AI policy (allowed tools, disclosure/approval/test requirements,
  size limit) — saving creates a new version rather than editing history.
- Settings → Sensitive paths: list, create, edit and activate/deactivate a path glob marked `forbidden` or
  `needs_extra_review`, globally or per project.
