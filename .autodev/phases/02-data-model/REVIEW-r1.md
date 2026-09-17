# Review — phase 2 round 1

**Verdict:** changes_requested

Phase 2 delivers every spec §4 table with good constraints, indexes, factories, admin classification and i18n discipline, and I independently confirmed the whole gate: pytest (247 passed), ruff, ruff format, mypy, makemigrations --check, manage.py check, and migrate from an empty SQLite file both forward and in full reverse. All five acceptance criteria are backed by named tests, all §15 defaults are seeded, and all 19 PLAN tasks are [x] with no environment-blamed [~]. Two majors block: (a) eight admin changelists are measurably N+1 on PullRequest.__str__ -> repository, which is precisely the mitigation RISKS #10 and PLAN.md claim this phase shipped, and CLAUDE.md's assertNumQueries-on-list-views rule is unenforced here; (b) the AppSetting data migration stores the English rendering of each setting description in the database, violating CLAUDE.md's "never store a rendered English message" and stranding the 30 Ukrainian translations that were written for those same strings. Three minors around the reverse data migration deleting all operator settings, unvalidated/edit-anywhere AppSetting rows, and Identity normalisation living only in save().

## [MAJOR] Admin changelists are N+1 on pull_request -> repository, contradicting the phase's own risk mitigation
`apps/policy/admin.py`

Eight admins list `pull_request` in `list_display`. `PullRequest.__str__` is `f"{self.repository}#{self.number}"`, so rendering each row dereferences `repository`, but every one of them sets `list_select_related` only one level deep (`("pull_request", ...)`), never `pull_request__repository`.

Affected: `apps/policy/admin.py:PolicyViolationAdmin`, `apps/churn/admin.py:ChurnResultAdmin`, `apps/ai_detection/admin.py:AISignalAdmin`, and in `apps/activity/admin.py`: `PullRequestCommitAdmin`, `PRFileAdmin`, `ReviewAdmin`, `ReviewCommentAdmin`, `CheckStatusAdmin`.

Measured with a throwaway test against the PolicyViolation changelist: 3 rows = 9 queries, 12 rows = 18 queries — one extra SELECT per row. PLAN.md's Design section states "FK-heavy admins use raw_id_fields rather than a select rendering every identity, and list_select_related where list_display crosses a FK, so no admin page is an N+1", and the Risks table repeats it as the mitigation for risk #10. That claim is not true as shipped.

It slipped through because `tests/test_admin.py::test_every_changelist_returns_200` runs against an empty database, so no row is ever rendered — the changelist tests cannot see this, nor could they see a broken `list_display` entry or a `__str__` that raises. CLAUDE.md requires `assertNumQueries` tests on list views precisely so an N+1 fails the build; no admin changelist has one.

**Fix:** Set `list_select_related = ("pull_request__repository", ...)` on the eight admins above (keep the existing entries, e.g. `("pull_request__repository", "author")` for ReviewCommentAdmin). Then add a test in tests/test_admin.py that seeds ~3 and ~12 rows via the factories for each FK-heavy changelist and asserts the query count does not grow with the row count (CaptureQueriesContext or assertNumQueries), so the empty-DB blind spot is closed at the same time.

## [MAJOR] AppSetting descriptions are stored as rendered English, stranding their Ukrainian translations
`apps/catalog/services.py`

`seed_app_settings()` writes `description=str(setting_def.description)`, forcing the `gettext_lazy` proxy to a string at migration time, and `set_setting()` does the same. `catalog/0002_app_setting_defaults.py` therefore persists 30 rendered English sentences into the database on every fresh `migrate`.

This breaks CLAUDE.md's rule: "System-generated text ... is stored as a code plus parameters and rendered in the reader's language. Never store a rendered English message." The phase itself wrote Ukrainian for every one of these strings (e.g. locale/uk/LC_MESSAGES/django.po:883-884, "How many days of history a first sync pulls in." -> "Скільки днів історії тягне перша синхронізація."), and `tests/test_translations.py` keeps them non-empty — but `AppSettingAdmin.list_display` reads the `description` column, so a Ukrainian reader sees English today and the phase-8 settings UI will too unless it ignores the column entirely. Translation work already paid for, permanently unreachable.

Because the English text is written by a data migration, correcting it later needs another data migration rather than a code change, which is the "expensive to undo" case.

**Fix:** Stop persisting the description: drop `description` from `seed_app_settings()` and `set_setting()` (seed the column blank, or remove the column from the model and its migration). Render the description from the registry instead — `AppSettingAdmin` gets a `@admin.display` method returning `SETTING_DEFS`'s lazy `description` for `obj.key`, and the phase-8 settings UI reads the same registry. If a per-row free-text column is wanted for operator notes, keep it but name it `notes` and leave it empty at seed time.

## [MINOR] Reverse data migration deletes every AppSetting row, including operator-tuned and non-registry ones
`apps/catalog/migrations/0002_app_setting_defaults.py`

`unseed_defaults()` runs `AppSetting.objects.all().delete()`. Reversing past `catalog.0002` therefore discards an operator's tuned values (MIN_SAMPLE, PR_SIZE_BUCKETS, bot lists, disclosure synonyms) and any row whose key is not in SETTING_DEFS. `.autodev/ARCHITECTURE.md` lists "Project definitions and AppSetting values" as item 5 of "What must never be lost", and the forward direction is deliberately non-destructive (`seed_app_settings` touches nothing that exists) — the reverse is not symmetric with it.

**Fix:** Scope the delete to the registry: `AppSetting.objects.filter(key__in=[d.key for d in SETTING_DEFS]).delete()`, or make the reverse `migrations.RunPython.noop` since the CreateModel in the same migration drops the table anyway.

## [MINOR] get_setting() returns the stored value unvalidated, and the admin lets key/value_type be edited freely
`apps/catalog/services.py`

PLAN.md specifies `get_setting(key) -> object  # row -> validated value`, but the implementation returns `row.value` with no `_validate_type` call — only writes through `set_setting()` and `AppSetting.clean()` are checked. Meanwhile `AppSettingAdmin` exposes `key` and `value_type` as ordinary editable fields (`value_type` is a plain CharField with no `choices`). An operator can rename `MIN_SAMPLE` (orphaning the row while `get_setting` silently falls back to the code default) or flip `BACKFILL_DAYS` to `value_type="str"` with value `"soon"` — `clean()` accepts that pair, and a later phase's `get_int("BACKFILL_DAYS")` then raises ValueError deep inside a sync or a dashboard render.

**Fix:** Put `key` and `value_type` in `AppSettingAdmin.readonly_fields` — both are code-side contracts owned by SETTING_DEFS, not operator data — and have `get_setting()` call `_validate_type(setting_def.value_type, row.value)` and fall back to the code default (with a logged warning) when a row is corrupt.

## [MINOR] Identity normalisation only runs in save(), which bulk writers bypass
`apps/catalog/models.py`

`Identity.save()` is the single normalisation choke point for risk #1 (`Foo` and `foo` becoming two people). `bulk_create()`, `bulk_update()` and `QuerySet.update()` never call `save()`, so any of them writes an unnormalised value straight past the `(kind, value)` constraint. The next phase (github_sync) is exactly the place where identities arrive in batches and `bulk_create` is the obvious implementation.

**Fix:** Also normalise in `Identity.clean()` so `full_clean()` catches it, and add a short comment on `save()` plus a note in the phase-3 plan that any bulk identity writer must call `normalize_identity_value()` itself. A test that `Identity.objects.bulk_create([...])` with "Foo" produces a normalised row would pin it.

## [MINOR] Historical migration imports live application code that imports the live model
`apps/catalog/migrations/0002_app_setting_defaults.py`

The migration does `from apps.catalog.services import seed_app_settings` at module level, and `services.py` does `from apps.catalog.models import AppSetting` at module level. Passing the historical model into the helper (the logged p02-plan decision) solves the model-state half, but the import still pins this historical migration to current application code: if `AppSetting` is ever renamed, moved or removed, `migrate` from zero fails at import time rather than at a model lookup. `setting_defs.py` itself is model-free and safe to import.

**Fix:** Import only the model-free registry in the migration (`from apps.catalog.setting_defs import SETTING_DEFS`) and inline the eight-line get_or_create loop, or move `seed_app_settings` into a model-free module alongside `normalize.py` and import that. Keep the existing `seed_app_settings(AppSetting)` unit test pointing at whichever module ends up owning it.

## [MINOR] File the plan declared out of scope is now staged for commit with no decision bullet
`tests/test_logging.py`

`.autodev/phases/02-data-model/PLAN.md`'s "Out of scope" says tests/test_logging.py was "untracked because the orchestrator held it back from the phase-1 commit over a token-shaped literal: left exactly as found", and that deciding its fate "belongs to whoever owns that phase-1 loose end, not to a data-model phase". It appears in this phase's diff as a staged new file (69 lines), so the phase-2 commit will resolve that loose end silently. There is no `[p02-implement/...]` bullet in `.autodev/DECISIONS.md` recording the reversal. The contents are safe — six obviously synthetic token literals used to exercise `SecretMaskingFilter` and `mask_secrets`, which is what the file is for — so this is bookkeeping, not a leak.

**Fix:** Either unstage it and leave it untracked as the plan says, or keep it and add a `[p02-implement/decision]` bullet in .autodev/DECISIONS.md stating that the phase-1 hold is released because the literals are synthetic fixtures for the masking filter, so the orchestrator's scan has a recorded answer.
