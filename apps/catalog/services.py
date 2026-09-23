import datetime
import logging
from typing import TYPE_CHECKING, Any

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.text import slugify

from apps.catalog.models import AppSetting, Organization, Project, Repository
from apps.catalog.setting_defs import SETTING_DEFS

if TYPE_CHECKING:
    from apps.connections.models import GitHubConnection

logger = logging.getLogger(__name__)

_DEFS_BY_KEY = {d.key: d for d in SETTING_DEFS}

_PYTHON_TYPES: dict[str, type | tuple[type, ...]] = {
    "bool": bool,
    "int": int,
    "float": (int, float),
    "str": str,
    "list": list,
    "dict": dict,
}


class UnknownSettingError(KeyError):
    pass


def _validate_type(value_type: str, value: object) -> None:
    expected = _PYTHON_TYPES.get(value_type)
    if expected is None:
        raise ValidationError(f"Unknown value_type {value_type!r}.")
    # bool is a subclass of int in Python; keep the two apart explicitly.
    if value_type != "bool" and isinstance(value, bool):
        raise ValidationError(f"Expected {value_type} for value_type {value_type!r}, got bool.")
    if not isinstance(value, expected):
        raise ValidationError(
            f"Expected {value_type} for value_type {value_type!r}, got {type(value).__name__}."
        )


_SETTINGS_CACHE_KEY = "catalog:app_settings:v1"


def _all_setting_rows() -> dict[str, AppSetting]:
    """One `AppSetting` query per process/cache-generation instead of one per `get_*()` call —
    every metric, chart and table read reaches a handful of settings (MIN_SAMPLE, STALE_DAYS, …),
    often once per series bucket, so an uncached read is a guaranteed N+1 (RISKS row 10). Cached
    forever in the shared `FileBasedCache`, invalidated by `set_setting()` — cross-process safe
    since the cache is filesystem-backed, unlike a module-level dict."""
    rows = cache.get(_SETTINGS_CACHE_KEY)
    if rows is None:
        rows = {row.key: row for row in AppSetting.objects.all()}
        cache.set(_SETTINGS_CACHE_KEY, rows, None)
    return rows


def invalidate_settings_cache(**_kwargs: Any) -> None:
    """Connected to `AppSetting`'s `post_save`/`post_delete` in `CatalogConfig.ready()` — covers
    every write path (the Django admin, `loaddata`, a migration), not just `set_setting()`, which
    is the only in-app caller and was otherwise the sole way to invalidate `_all_setting_rows()`'s
    process-wide cache. Without this, an admin edit to MIN_SAMPLE/STALE_DAYS/etc. never took effect
    until the cache directory was wiped."""
    cache.delete(_SETTINGS_CACHE_KEY)


def get_setting(key: str) -> Any:
    setting_def = _DEFS_BY_KEY.get(key)
    if setting_def is None:
        raise UnknownSettingError(key)
    row = _all_setting_rows().get(key)
    if row is None:
        return setting_def.default
    try:
        _validate_type(setting_def.value_type, row.value)
    except ValidationError:
        logger.warning("AppSetting %r has a corrupt value; falling back to the code default.", key)
        return setting_def.default
    return row.value


def get_int(key: str) -> int:
    return int(get_setting(key))


def get_float(key: str) -> float:
    return float(get_setting(key))


def get_bool(key: str) -> bool:
    return bool(get_setting(key))


def get_str(key: str) -> str:
    return str(get_setting(key))


def get_list(key: str) -> list[Any]:
    value = get_setting(key)
    assert isinstance(value, list)
    return value


def get_dict(key: str) -> dict[str, Any]:
    value = get_setting(key)
    assert isinstance(value, dict)
    return value


# Lives in the "ai" group but decides which pull requests the metrics' AI/non-AI cohorts hold, so a
# change must reach compute()'s cache as a metrics-group change does.
_COHORT_SETTING_KEYS = frozenset({"AI_COHORT_INCLUDE_SUSPECTED"})


def set_setting(key: str, value: object) -> AppSetting:
    setting_def = _DEFS_BY_KEY.get(key)
    if setting_def is None:
        raise UnknownSettingError(key)
    _validate_type(setting_def.value_type, value)
    row, _created = AppSetting.objects.update_or_create(
        key=key,
        defaults={
            "value_type": setting_def.value_type,
            "value": value,
        },
    )
    invalidate_settings_cache()
    if setting_def.group == "metrics" or key in _COHORT_SETTING_KEYS:
        # A metrics-group setting (MIN_SAMPLE, AI_COHORT_INCLUDE_SUSPECTED, PR_SIZE_BUCKETS, ...)
        # is baked into compute()'s cache and, for the cohort/bucket-affecting ones, into stored
        # DailyRollup rows. Bumping here at least invalidates the cache immediately; existing
        # rollups still need a manage.py recompute (docs/CONFIGURATION.md says so).
        from apps.metrics.services import bump_data_version

        bump_data_version()
    return row


def create_repositories_from_discovery(
    nodes: list[dict[str, Any]], *, connection: "GitHubConnection", project: Project | None = None
) -> int:
    """nodes: GraphQL repository nodes from discovery (id, name, nameWithOwner, isPrivate, isArchived,
    defaultBranchRef, owner{id,login,__typename}). update_or_create on github_id, so re-submitting the
    same selection creates no second row and never resets sync_since on an existing repository. A node
    whose github_id already belongs to a different connection is skipped — bulk-add must never silently
    rebind; that requires the explicit confirm step in rebind_repository(). Returns the number of
    repositories created."""
    from apps.github_sync.errors import require

    sync_since = timezone.localdate() - datetime.timedelta(days=get_int("BACKFILL_DAYS"))
    github_ids = [require(node, "id") for node in nodes]
    bound_elsewhere = set(
        Repository.objects.filter(github_id__in=github_ids)
        .exclude(connection=connection)
        .values_list("github_id", flat=True)
    )
    created = 0
    for node in nodes:
        github_id = require(node, "id")
        if github_id in bound_elsewhere:
            continue
        owner = require(node, "owner")
        organization, _ = Organization.objects.update_or_create(
            github_id=require(owner, "id"),
            defaults={
                "login": require(owner, "login"),
                "type": Organization.Type.ORG
                if owner.get("__typename") == "Organization"
                else Organization.Type.USER,
            },
        )
        default_branch_ref = node.get("defaultBranchRef") or {}
        shared_fields = {
            "organization": organization,
            "connection": connection,
            "name": require(node, "name"),
            "full_name": require(node, "nameWithOwner"),
            "default_branch": default_branch_ref.get("name", ""),
            "is_private": require(node, "isPrivate"),
            "is_archived": require(node, "isArchived"),
        }
        repository, repo_created = Repository.objects.update_or_create(
            github_id=github_id,
            defaults=shared_fields,
            create_defaults={**shared_fields, "sync_since": sync_since},
        )
        if repo_created:
            created += 1
        if project is not None:
            project.repositories.add(repository)
    return created


def rebind_repository(repository: Repository, connection: "GitHubConnection", *, actor: Any = None) -> None:
    """Moves a repository to another connection. Historical activity rows are untouched — only the FK
    that decides which credentials future syncs use."""
    from apps.accounts.services import record_audit

    previous_connection_name = repository.connection.name
    repository.connection = connection
    repository.save(update_fields=["connection"])
    record_audit(
        actor,
        "repository.rebind",
        repository,
        before={"connection": previous_connection_name},
        after={"connection": connection.name},
    )


def seed_app_settings(model: type[AppSetting]) -> int:
    """Idempotent: creates a row for every SETTING_DEFS key that is missing, touches nothing that exists."""
    existing_keys = set(model.objects.values_list("key", flat=True))
    created = 0
    for setting_def in SETTING_DEFS:
        if setting_def.key in existing_keys:
            continue
        model.objects.create(
            key=setting_def.key,
            value_type=setting_def.value_type,
            value=setting_def.default,
        )
        created += 1
    return created


def invalidate_project_metrics() -> None:
    """A project's repository set is what every PROJECT-scope metric counts over, and `compute()`
    caches per `scope_id` — so changing that set has to invalidate the cache the way a sync does.
    Stored `DailyRollup` rows are per repository and per day, so none of them go stale."""
    from apps.metrics.services import bump_data_version

    bump_data_version()


def generate_project_slug(name: str, *, exclude_pk: int | None = None) -> str:
    """The slug a project gets when a lead leaves the field empty: `slugify(name)`, suffixed with
    `-2`, `-3`, … until it is free. `exclude_pk` keeps a project's own slug out of the way when it
    is being renamed, so re-saving a project without changing its name is a no-op.

    Returns `""` when the name has no ASCII letters or digits to slugify — a Ukrainian project
    name, say. A slug is what `sync --project` and `recompute --project` are typed with, so the
    caller asks for one rather than inventing a meaningless `project-4`."""
    base = slugify(name)[:200]
    if not base:
        return ""
    taken = Project.objects.exclude(pk=exclude_pk) if exclude_pk is not None else Project.objects.all()
    taken_slugs = set(taken.values_list("slug", flat=True))
    if base not in taken_slugs:
        return base
    suffix = 2
    while f"{base[:196]}-{suffix}" in taken_slugs:
        suffix += 1
    return f"{base[:196]}-{suffix}"
