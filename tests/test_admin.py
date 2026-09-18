import pytest
from django.apps import apps
from django.contrib import admin
from django.db import connection, models
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import AuditEntry, UserPreference, UserProjectAccess
from apps.activity.factories import (
    CheckStatusFactory,
    PRFileFactory,
    PullRequestCommitFactory,
    ReviewCommentFactory,
    ReviewFactory,
)
from apps.activity.models import (
    CheckStatus,
    Commit,
    PRFile,
    PullRequest,
    PullRequestCommit,
    Review,
    ReviewComment,
)
from apps.ai_detection.factories import AISignalFactory
from apps.ai_detection.models import AISignal, DetectionRule
from apps.catalog.admin import PersonAdmin
from apps.catalog.models import AppSetting, Identity, Organization, Person, Project, Repository
from apps.churn.factories import ChurnResultFactory
from apps.churn.models import ChurnResult
from apps.connections.admin import GitHubConnectionAdmin
from apps.connections.models import GitHubConnection
from apps.dashboards.models import ExportJob
from apps.github_sync.models import SyncLock, SyncRun
from apps.metrics.models import DailyRollup, DataVersion, DirtyDay
from apps.policy.factories import PolicyViolationFactory
from apps.policy.models import AIPolicy, PolicyViolation, SensitivePathRule

READ_ONLY_MODELS = {
    Organization,
    Repository,
    PullRequest,
    Commit,
    PullRequestCommit,
    PRFile,
    Review,
    ReviewComment,
    CheckStatus,
    AISignal,
    DailyRollup,
    DataVersion,
    DirtyDay,
    ChurnResult,
    SyncRun,
    SyncLock,
    AuditEntry,
    GitHubConnection,
    ExportJob,
}

EDITABLE_MODELS = {
    Project,
    Person,
    Identity,
    AppSetting,
    DetectionRule,
    AIPolicy,
    SensitivePathRule,
    PolicyViolation,
    UserProjectAccess,
    UserPreference,
}


def _project_registered_models():
    return {
        model: model_admin
        for model, model_admin in admin.site._registry.items()
        if model._meta.app_config.name.startswith("apps.")
    }


def _project_models():
    return {model for model in apps.get_models() if model._meta.app_config.name.startswith("apps.")}


def _admin_url(model, action="changelist"):
    opts = model._meta
    return f"/admin/{opts.app_label}/{opts.model_name}/" + ("add/" if action == "add" else "")


def test_every_model_is_registered_and_classified():
    registered = set(_project_registered_models())
    assert registered == READ_ONLY_MODELS | EDITABLE_MODELS
    assert not (READ_ONLY_MODELS & EDITABLE_MODELS)
    # Deny-by-default: a concrete model that is never registered at all must still fail this
    # test, not just one that is registered but misclassified (same shape as test_factories.py).
    unregistered = _project_models() - registered
    assert not unregistered, f"models not registered in admin: {[m.__name__ for m in unregistered]}"


@pytest.mark.django_db
def test_every_changelist_returns_200(client, admin_user):
    client.force_login(admin_user)
    for model in _project_registered_models():
        response = client.get(_admin_url(model))
        assert response.status_code == 200, f"{model.__name__} changelist returned {response.status_code}"


@pytest.mark.django_db
def test_add_view_is_200_for_editable_models(client, admin_user):
    client.force_login(admin_user)
    for model in EDITABLE_MODELS:
        response = client.get(_admin_url(model, "add"))
        assert response.status_code == 200, f"{model.__name__} add view returned {response.status_code}"


@pytest.mark.django_db
def test_add_view_is_403_for_read_only_models(client, admin_user):
    client.force_login(admin_user)
    for model in READ_ONLY_MODELS:
        response = client.get(_admin_url(model, "add"))
        assert response.status_code == 403, f"{model.__name__} add view returned {response.status_code}"


def test_readonly_admin_covers_every_editable_field():
    # GitHubConnection is classified read-only (add view answers 403) but keeps a normal change
    # form for its non-token fields, so ReadOnlyAdminMixin does not apply to it.
    registry = _project_registered_models()
    for model in READ_ONLY_MODELS - {GitHubConnection}:
        model_admin = registry[model]
        readonly_fields = set(model_admin.get_readonly_fields(request=None))
        concrete_fields = {f.name for f in model._meta.get_fields() if isinstance(f, models.Field)}
        assert concrete_fields <= readonly_fields, (
            f"{model.__name__} leaves editable fields: {concrete_fields - readonly_fields}"
        )


@pytest.mark.django_db
def test_github_connection_admin_form_has_no_token_field(rf, admin_user):
    request = rf.get(_admin_url(GitHubConnection, "add"))
    request.user = admin_user
    form_class = GitHubConnectionAdmin(GitHubConnection, admin.site).get_form(request)
    field_names = " ".join(form_class.base_fields.keys()).lower()
    assert "token" not in field_names
    assert "private_key" not in field_names


@pytest.mark.django_db
def test_github_connection_changelist_html_has_no_token(client, admin_user):
    client.force_login(admin_user)
    GitHubConnection.objects.create(
        name="prod",
        kind=GitHubConnection.Kind.FINE_GRAINED_PAT,
        token_encrypted=b"deadbeef-secret",
        token_last4="beef",
    )
    response = client.get(_admin_url(GitHubConnection))
    content = response.content.decode()
    assert "deadbeef-secret" not in content
    assert "token_encrypted" not in content
    assert "private_key" not in content


def test_person_notes_absent_from_list_display():
    assert "notes" not in PersonAdmin.list_display


@pytest.mark.django_db
def test_app_setting_admin_key_and_value_type_are_readonly():
    from apps.catalog.admin import AppSettingAdmin

    model_admin = AppSettingAdmin(AppSetting, admin.site)
    assert set(model_admin.get_readonly_fields(request=None)) >= {"key", "value_type"}


@pytest.mark.django_db
def test_app_setting_admin_renders_description_from_registry_not_the_row():
    from apps.catalog.admin import AppSettingAdmin

    row = AppSetting.objects.get(key="BACKFILL_DAYS")
    assert row.description == ""
    model_admin = AppSettingAdmin(AppSetting, admin.site)
    rendered = model_admin.registry_description(row)
    assert rendered
    assert rendered != row.description


FK_HEAVY_CHANGELIST_FACTORIES = [
    PolicyViolationFactory,
    ChurnResultFactory,
    AISignalFactory,
    PullRequestCommitFactory,
    PRFileFactory,
    ReviewFactory,
    ReviewCommentFactory,
    CheckStatusFactory,
]


@pytest.mark.django_db
@pytest.mark.parametrize("factory_cls", FK_HEAVY_CHANGELIST_FACTORIES)
def test_fk_heavy_changelist_query_count_does_not_grow_with_rows(client, admin_user, factory_cls):
    # Each row's pull_request -> repository must be select_related, not fetched per row
    # (regression for the N+1 flagged in phase-2 review round 1).
    client.force_login(admin_user)
    model = factory_cls._meta.model
    url = _admin_url(model)

    # Warm-up request: `connection_alerts` (apps/connections/context_processors.py) reads
    # AppSetting on every page via apps.catalog.services.get_int(), which caches all settings
    # rows for the rest of the process (apps/catalog/services.py::_all_setting_rows()). Without
    # this call, the *first* of the two measured requests below would pay that one-time query
    # and the second wouldn't, making the counts differ for a reason unrelated to row count.
    client.get(url)

    factory_cls.create_batch(3)
    with CaptureQueriesContext(connection) as few_rows:
        response = client.get(url)
    assert response.status_code == 200

    factory_cls.create_batch(9)
    with CaptureQueriesContext(connection) as many_rows:
        response = client.get(url)
    assert response.status_code == 200

    assert len(many_rows.captured_queries) == len(few_rows.captured_queries), (
        f"{model.__name__} changelist queries grew with row count: "
        f"{len(few_rows.captured_queries)} -> {len(many_rows.captured_queries)}"
    )
