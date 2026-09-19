import logging
from typing import Any

import httpx
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.accounts.services import record_audit
from apps.catalog.models import Project, Repository
from apps.catalog.services import create_repositories_from_discovery, rebind_repository
from apps.connections.auth import ConnectionNotUsableError
from apps.connections.forms import ConnectionForm, RepositoryConnectionForm
from apps.connections.models import GitHubConnection
from apps.connections.services import set_token, verify_connection
from apps.github_sync.errors import GitHubError, require
from config.htmx import is_htmx

logger = logging.getLogger(__name__)

PERMISSION = "catalog.manage_settings"
DISCOVERY_PAGE_SIZE = 100


def _connections_context() -> dict:
    return {"connections": list(GitHubConnection.objects.order_by("name"))}


@login_required
@permission_required(PERMISSION, raise_exception=True)
def connection_list(request: HttpRequest) -> HttpResponse:
    template = "connections/partials/list_content.html" if is_htmx(request) else "connections/list.html"
    return render(request, template, _connections_context())


def _save_connection(
    request: HttpRequest, form: ConnectionForm, connection: GitHubConnection, *, creating: bool
) -> None:
    """Writes name/kind/owner_login, replaces the token when one was submitted, then verifies
    unless the admin opted out — refusing to persist an invalid token unless they explicitly
    asked to save it unverified. Runs inside one transaction so a refused save leaves nothing."""
    connection.name = form.cleaned_data["name"]
    connection.kind = form.cleaned_data["kind"]
    connection.owner_login = form.cleaned_data["owner_login"]
    connection.created_by = connection.created_by or request.user
    connection.save()

    token = form.cleaned_data["token"]
    if token:
        set_token(connection, token, actor=request.user)

    save_unverified = form.cleaned_data["save_unverified"]
    if token and not save_unverified:
        try:
            verify_connection(connection, force=True)
        except (GitHubError, ConnectionNotUsableError, httpx.TransportError):
            # The exception carries no token (see apps/github_sync/errors.py), so it is safe to log,
            # and it is the only place an operator can see why verification failed.
            logger.exception("Verification of connection %s failed.", connection.pk)
            transaction.set_rollback(True)
            form.add_error(
                None,
                _(
                    "Could not reach GitHub to verify this token. Try again, or tick "
                    "“save without verifying”."
                ),
            )
            return
        if connection.status == GitHubConnection.Status.INVALID:
            transaction.set_rollback(True)
            form.add_error(
                None,
                _("GitHub rejected this token. Fix it and try again, or tick “save without verifying”."),
            )
            return

    action = "connection.create" if creating else "connection.update"
    record_audit(request.user, action, connection, after={"name": connection.name})


@login_required
@permission_required(PERMISSION, raise_exception=True)
def connection_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = ConnectionForm(request.POST, require_token=True)
        if form.is_valid():
            with transaction.atomic():
                connection = GitHubConnection(created_by=request.user)
                _save_connection(request, form, connection, creating=True)
            if not form.errors:
                return redirect("connections:list")
    else:
        form = ConnectionForm(require_token=True)

    template = "connections/partials/form.html" if is_htmx(request) else "connections/form.html"
    return render(request, template, {"form": form, "connection": None})


@login_required
@permission_required(PERMISSION, raise_exception=True)
def connection_edit(request: HttpRequest, pk: int) -> HttpResponse:
    connection = get_object_or_404(GitHubConnection, pk=pk)

    if request.method == "POST":
        form = ConnectionForm(request.POST, require_token=False)
        if form.is_valid():
            with transaction.atomic():
                _save_connection(request, form, connection, creating=False)
            if not form.errors:
                return redirect("connections:list")
    else:
        form = ConnectionForm(
            require_token=False,
            initial={
                "name": connection.name,
                "kind": connection.kind,
                "owner_login": connection.owner_login,
            },
        )

    template = "connections/partials/form.html" if is_htmx(request) else "connections/form.html"
    return render(request, template, {"form": form, "connection": connection})


@login_required
@permission_required(PERMISSION, raise_exception=True)
@require_POST
def connection_check(request: HttpRequest, pk: int) -> HttpResponse:
    connection = get_object_or_404(GitHubConnection, pk=pk)
    check_error = None
    try:
        verify_connection(connection, force=True)
    except (GitHubError, ConnectionNotUsableError, httpx.TransportError):
        logger.exception("Check of connection %s failed.", connection.pk)
        check_error = _("Could not reach GitHub to check this connection. Check your network and try again.")
    if is_htmx(request):
        return render(
            request, "connections/partials/row.html", {"connection": connection, "check_error": check_error}
        )
    return redirect("connections:list")


@login_required
@permission_required(PERMISSION, raise_exception=True)
@require_POST
def connection_deactivate(request: HttpRequest, pk: int) -> HttpResponse:
    connection = get_object_or_404(GitHubConnection, pk=pk)
    connection.is_active = not connection.is_active
    connection.save(update_fields=["is_active"])
    record_audit(
        request.user,
        "connection.deactivate" if not connection.is_active else "connection.activate",
        connection,
        after={"is_active": connection.is_active},
    )
    if is_htmx(request):
        return render(request, "connections/partials/row.html", {"connection": connection})
    return redirect("connections:list")


@login_required
@permission_required(PERMISSION, raise_exception=True)
@require_POST
def connection_delete(request: HttpRequest, pk: int) -> HttpResponse:
    connection = get_object_or_404(GitHubConnection, pk=pk)
    try:
        connection.delete()
    except ProtectedError:
        context = {
            **_connections_context(),
            "blocked_connection": connection,
            "blocking_repositories": list(connection.repositories.order_by("full_name")),
        }
    else:
        context = _connections_context()
    template = "connections/partials/list_content.html" if is_htmx(request) else "connections/list.html"
    return render(request, template, context)


@login_required
@permission_required(PERMISSION, raise_exception=True)
def repository_list(request: HttpRequest) -> HttpResponse:
    """Every repository already added, with the connection whose token syncs it. Discovery lists what
    a token can see on GitHub; this lists what PR Radar holds, so a repository can be moved to another
    connection without that connection having to see it first."""
    connection_id = request.GET.get("connection", "")
    connection = (
        GitHubConnection.objects.filter(pk=connection_id).first() if connection_id.isdigit() else None
    )
    repositories = Repository.objects.select_related("connection", "organization").order_by("full_name")
    if connection is not None:
        repositories = repositories.filter(connection=connection)
    context = {
        "repositories": list(repositories),
        "connections": list(GitHubConnection.objects.order_by("name")),
        "connection": connection,
    }
    template = (
        "connections/partials/repositories_content.html"
        if is_htmx(request)
        else "connections/repositories.html"
    )
    return render(request, template, context)


@login_required
@permission_required(PERMISSION, raise_exception=True)
def repository_connection(request: HttpRequest, pk: int) -> HttpResponse:
    """Rebinds one repository from its own page. The confirmation lives in the form, so a stray POST
    cannot move a repository without it."""
    repository = get_object_or_404(Repository.objects.select_related("connection", "organization"), pk=pk)

    if request.method == "POST":
        form = RepositoryConnectionForm(request.POST, repository=repository)
        if form.is_valid():
            rebind_repository(repository, form.cleaned_data["connection"], actor=request.user)
            return redirect("connections:repositories")
    else:
        form = RepositoryConnectionForm(repository=repository)

    template = (
        "connections/partials/repository_connection_form.html"
        if is_htmx(request)
        else "connections/repository_connection.html"
    )
    context = {
        "form": form,
        "repository": repository,
        "has_targets": form.fields["connection"].queryset.exists(),
    }
    return render(request, template, context)


def _discover_nodes(connection: GitHubConnection) -> list[dict[str, Any]]:
    """Every repository the connection's token can read, whoever owns it. A lead working on a
    client-owned repository has access but not ownership, so the list is never narrowed to the
    connection's owner_login — that field labels the connection, it does not scope discovery."""
    from apps.connections.auth import auth_for_connection
    from apps.github_sync.client import GitHubClient
    from apps.github_sync.queries import VIEWER_REPOSITORIES_QUERY
    from apps.github_sync.rate_limit import RateBudget

    auth = auth_for_connection(connection)
    client = GitHubClient(auth, RateBudget(key=auth.rate_limit_key))
    return list(
        client.paginate(
            VIEWER_REPOSITORIES_QUERY, {}, page_path="viewer.repositories", page_size=DISCOVERY_PAGE_SIZE
        )
    )


def _discovery_owners(nodes: list[dict[str, Any]]) -> list[str]:
    return sorted({require(node, "owner.login") for node in nodes})


def _discovery_rows(
    nodes: list[dict[str, Any]],
    connection: GitHubConnection,
    *,
    show_archived: bool,
    owner: str = "",
) -> list[tuple[str, list[dict[str, Any]]]]:
    if not show_archived:
        nodes = [node for node in nodes if not require(node, "isArchived")]
    if owner:
        nodes = [node for node in nodes if require(node, "owner.login") == owner]
    github_ids = [require(node, "id") for node in nodes]
    bound_elsewhere = {
        github_id: {"connection_name": connection_name, "repository_pk": repository_pk}
        for github_id, connection_name, repository_pk in Repository.objects.filter(github_id__in=github_ids)
        .exclude(connection=connection)
        .values_list("github_id", "connection__name", "pk")
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        grouped.setdefault(require(node, "owner.login"), []).append(
            {"node": node, "bound_to": bound_elsewhere.get(require(node, "id"))}
        )
    return sorted(grouped.items())


@login_required
@permission_required(PERMISSION, raise_exception=True)
def repository_discover(request: HttpRequest) -> HttpResponse:
    params = request.POST if request.method == "POST" else request.GET
    connection_id = params.get("connection")
    connection = get_object_or_404(GitHubConnection, pk=connection_id) if connection_id else None
    show_archived = params.get("show_archived") == "1"
    owner = params.get("owner", "")
    projects = Project.objects.order_by("name")
    created = None

    nodes: list[dict[str, Any]] = []
    error = None
    if connection is not None:
        try:
            nodes = _discover_nodes(connection)
            if request.method == "POST":
                selected_ids = set(request.POST.getlist("repo"))
                project_slug = request.POST.get("project", "")
                project = projects.filter(slug=project_slug).first() if project_slug else None
                chosen = [node for node in nodes if require(node, "id") in selected_ids]
                created = create_repositories_from_discovery(chosen, connection=connection, project=project)
                record_audit(
                    request.user,
                    "repository.discover_add",
                    connection,
                    after={"count": len(chosen)},
                )
        except (GitHubError, ConnectionNotUsableError, httpx.TransportError):
            error = _(
                "Could not reach GitHub to list repositories for this connection. Try again in a moment."
            )

    owners = _discovery_owners(nodes)
    if owner not in owners:
        owner = ""
    context = {
        "connections": GitHubConnection.objects.filter(is_active=True).order_by("name"),
        "connection": connection,
        "rows": (
            _discovery_rows(nodes, connection, show_archived=show_archived, owner=owner) if connection else []
        ),
        "show_archived": show_archived,
        "owners": owners,
        "owner": owner,
        "projects": projects,
        "created": created,
        "error": error,
    }
    template = (
        "connections/partials/discovery_content.html" if is_htmx(request) else "connections/discovery.html"
    )
    return render(request, template, context)


@login_required
@permission_required(PERMISSION, raise_exception=True)
@require_POST
def repository_rebind(request: HttpRequest, pk: int) -> HttpResponse:
    repository = get_object_or_404(Repository, pk=pk)
    connection_id = request.POST.get("connection")
    confirm = request.POST.get("confirm") == "1"
    connection = get_object_or_404(GitHubConnection, pk=connection_id) if connection_id else None

    error = None
    if connection is None:
        error = _("Choose a connection to rebind to.")
    elif not confirm:
        error = _("Tick the confirmation box to rebind this repository.")
    else:
        rebind_repository(repository, connection, actor=request.user)

    if connection is None:
        return redirect("connections:discover")

    nodes: list[dict[str, Any]] = []
    try:
        nodes = _discover_nodes(connection)
    except (GitHubError, ConnectionNotUsableError, httpx.TransportError):
        error = error or _(
            "Could not reach GitHub to list repositories for this connection. Try again in a moment."
        )

    context = {
        "connections": GitHubConnection.objects.filter(is_active=True).order_by("name"),
        "connection": connection,
        "rows": _discovery_rows(nodes, connection, show_archived=False),
        "show_archived": False,
        "owners": _discovery_owners(nodes),
        "owner": "",
        "projects": Project.objects.order_by("name"),
        "created": None,
        "error": error,
    }
    template = (
        "connections/partials/discovery_content.html" if is_htmx(request) else "connections/discovery.html"
    )
    return render(request, template, context)
