# Audit of the round-2 fixes — phase 2

**Verdict:** approve

All three r2 findings (one major, two minors) are fixed in the product, each with a test that fails without the fix. I re-ran the whole gate myself: pytest passed (286), ruff, ruff format --check, mypy, makemigrations --check --dry-run, manage.py check, plus migrate from an empty SQLite file — `raw` columns are present on activity_commit/review/reviewcomment, the admin group still receives catalog.manage_settings, and an explicitly assigned SyncRun.started_at survives the round-trip. The one partial rejection (no `raw` on PRFile/CheckStatus) is legitimate: both genuinely lack a `github_id`, so spec §4's «Усі сутності з GitHub мають `github_id` (unique) і `raw`» does not cover them, and the exemption is logged as a `[p02-review_fix2/decision]` bullet. No collateral damage: SyncRunAdmin uses ReadOnlyAdminMixin so the now-editable started_at stays read-only in admin, and the .build-manifest.sha256 shift is expected because the manifest hashes apps/**/*.py. Two minors remain, both about how durably the new rule is enforced rather than about behaviour.

## [MINOR] raw-field test hardcodes three models instead of deriving the rule
`apps/activity/tests/test_models.py`

`test_github_node_models_have_a_raw_payload_field` is parametrized over a literal `[Commit, Review, ReviewComment]`. The review asked for a test that derives the set (every GitHub-owned / `github_id`-bearing model) so the convention is enforced rather than remembered. As written, a model added in phase 3+ with a unique `github_id` and no `raw` passes the suite silently — exactly the drift this finding existed to stop.

**Fix:** Derive the set: iterate `django.apps.apps.get_models()`, select models that declare a `github_id` field, and assert each also has `raw`. Keeps the current three covered and fails automatically on the next GitHub-owned model that forgets the column.

## [MINOR] PLAN.md still says `raw` on "every GitHub-owned model", now contradicted by the shipped schema
`.autodev/phases/02-data-model/PLAN.md`

The DECISIONS bullet narrows the rule to "every GitHub-owned model with its own node id" and states "this bullet is the narrowing", but PLAN.md's Design section still reads "`raw = JSONField(null=True, blank=True)` on **every** GitHub-owned model. … the column is always present." A phase-3 session reading the plan rather than the decisions log will expect `PRFile.raw` / `CheckStatus.raw` to exist and find them missing.

**Fix:** Amend PLAN.md's field-convention sentence to "every GitHub-owned model that has its own `github_id`", with a pointer to the `[p02-review_fix2/decision]` bullet.
