from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.accounts.selectors import scope_for_user
from apps.accounts.services import record_audit
from apps.catalog.forms import AssignIdentityForm, MergePeopleForm, PersonForm, ProjectForm
from apps.catalog.identity import create_person_from_identity, merge_people
from apps.catalog.models import Identity, Person, Project, Repository
from apps.catalog.selectors import (
    bot_person_count,
    people_for_settings,
    projects_in_scope,
    repositories_in_scope,
    unmapped_identities,
    unmapped_identity_count,
)
from apps.catalog.services import invalidate_project_metrics
from config.htmx import is_htmx

PERMISSION = "catalog.manage_settings"
IDENTITY_PAGE_SIZE = 50


def _render_project_form(request: HttpRequest, form: ProjectForm, project: Project | None) -> HttpResponse:
    template = "catalog/partials/project_form.html" if is_htmx(request) else "catalog/project_form.html"
    return render(request, template, {"form": form, "project": project})


def _editable_repositories(request: HttpRequest) -> QuerySet[Repository]:
    """Archived repositories are offered too: they are dropped from dashboards, not from a
    project, and leaving them out of the field would silently unlink them on the next save."""
    return repositories_in_scope(scope_for_user(request.user), include_inactive=True)


@login_required
@permission_required(PERMISSION, raise_exception=True)
def project_create(request: HttpRequest) -> HttpResponse:
    repositories = _editable_repositories(request)
    if request.method == "POST":
        form = ProjectForm(request.POST, repositories=repositories)
        if form.is_valid():
            project = form.save()
            record_audit(request.user, "project.create", project, after=_project_audit_values(form))
            return redirect("dashboards:projects_index")
    else:
        form = ProjectForm(repositories=repositories)
    return _render_project_form(request, form, None)


@login_required
@permission_required(PERMISSION, raise_exception=True)
def project_edit(request: HttpRequest, pk: int) -> HttpResponse:
    scope = scope_for_user(request.user)
    # `include_inactive=True`: an archived project is exactly the one a lead comes here to revive.
    project = get_object_or_404(projects_in_scope(scope, include_inactive=True), pk=pk)
    repositories = _editable_repositories(request)
    if request.method == "POST":
        before = _project_values(project)
        form = ProjectForm(request.POST, instance=project, repositories=repositories)
        if form.is_valid():
            form.save()
            after = _project_audit_values(form)
            record_audit(request.user, "project.update", project, before=before, after=after)
            if after["repositories"] != before["repositories"]:
                invalidate_project_metrics()
            return redirect("dashboards:projects_index")
    else:
        form = ProjectForm(instance=project, repositories=repositories)
    return _render_project_form(request, form, project)


def _project_values(project: Project) -> dict:
    return {
        "name": project.name,
        "slug": project.slug,
        "description": project.description,
        "is_active": project.is_active,
        "repositories": sorted(project.repositories.values_list("full_name", flat=True)),
    }


def _project_audit_values(form: ProjectForm) -> dict:
    """The audit entry stores repository names, not primary keys — `AuditEntry.changes` is read by
    a human, and a list of ids says nothing."""
    return {
        **{key: form.cleaned_data[key] for key in ("name", "slug", "description", "is_active")},
        "repositories": sorted(repository.full_name for repository in form.cleaned_data["repositories"]),
    }


def _people_context(request: HttpRequest) -> dict:
    scope = scope_for_user(request.user)
    return {
        "people": list(people_for_settings(scope)),
        "unmapped_count": unmapped_identity_count(scope),
        "bot_count": bot_person_count(scope),
    }


@login_required
@permission_required(PERMISSION, raise_exception=True)
def people_list(request: HttpRequest) -> HttpResponse:
    template = "catalog/partials/people_content.html" if is_htmx(request) else "catalog/people.html"
    return render(request, template, _people_context(request))


def _render_person_form(request: HttpRequest, form: PersonForm, person: Person | None) -> HttpResponse:
    template = "catalog/partials/person_form.html" if is_htmx(request) else "catalog/person_form.html"
    return render(request, template, {"form": form, "person": person})


@login_required
@permission_required(PERMISSION, raise_exception=True)
def person_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = PersonForm(request.POST)
        if form.is_valid():
            person = form.save()
            record_audit(request.user, "person.create", person, after=form.cleaned_data)
            return redirect("catalog:people")
    else:
        form = PersonForm()
    return _render_person_form(request, form, None)


@login_required
@permission_required(PERMISSION, raise_exception=True)
def person_edit(request: HttpRequest, pk: int) -> HttpResponse:
    scope = scope_for_user(request.user)
    person = get_object_or_404(people_for_settings(scope), pk=pk)
    if request.method == "POST":
        before = {field: getattr(person, field) for field in PersonForm.Meta.fields}
        form = PersonForm(request.POST, instance=person)
        if form.is_valid():
            form.save()
            record_audit(request.user, "person.update", person, before=before, after=form.cleaned_data)
            return redirect("catalog:people")
    else:
        form = PersonForm(instance=person)
    return _render_person_form(request, form, person)


@login_required
@permission_required(PERMISSION, raise_exception=True)
def person_merge(request: HttpRequest) -> HttpResponse:
    scope = scope_for_user(request.user)
    people = people_for_settings(scope)
    if request.method == "POST":
        form = MergePeopleForm(request.POST, people=people)
        if form.is_valid():
            merge_people(form.cleaned_data["source"], form.cleaned_data["target"], request.user)
            return redirect("catalog:people")
    else:
        form = MergePeopleForm(people=people)
    template = "catalog/partials/merge_form.html" if is_htmx(request) else "catalog/merge.html"
    return render(request, template, {"form": form})


def _queue_context(request: HttpRequest) -> dict:
    scope = scope_for_user(request.user)
    paginator = Paginator(unmapped_identities(scope), IDENTITY_PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    assign_form = AssignIdentityForm()
    # Materialise the choices once so the widget is not re-queried on every row render
    # (ModelChoiceIterator otherwise re-evaluates the queryset per `{{ assign_form.person }}` access).
    assign_form.fields["person"].choices = list(assign_form.fields["person"].choices)
    return {"page": page, "identities": page.object_list, "assign_form": assign_form}


def _render_queue(request: HttpRequest, *, error: str | None = None) -> HttpResponse:
    context = {**_queue_context(request), "error": error}
    template = "catalog/partials/identities_content.html" if is_htmx(request) else "catalog/identities.html"
    return render(request, template, context)


@login_required
@permission_required(PERMISSION, raise_exception=True)
def identity_queue(request: HttpRequest) -> HttpResponse:
    return _render_queue(request)


@login_required
@permission_required(PERMISSION, raise_exception=True)
@require_POST
def identity_assign(request: HttpRequest, pk: int) -> HttpResponse:
    identity = get_object_or_404(Identity, pk=pk)
    form = AssignIdentityForm(request.POST)
    if not form.is_valid():
        return _render_queue(request, error=_("Choose a person to assign this identity to."))
    identity.person = form.cleaned_data["person"]
    identity.save(update_fields=["person"])
    record_audit(request.user, "identity.assign", identity, after={"person": identity.person.display_name})
    if is_htmx(request):
        return _render_queue(request)
    return redirect("catalog:identity_queue")


@login_required
@permission_required(PERMISSION, raise_exception=True)
@require_POST
def identity_create_person(request: HttpRequest, pk: int) -> HttpResponse:
    identity = get_object_or_404(Identity, pk=pk)
    person = create_person_from_identity(identity)
    record_audit(request.user, "person.create", person, after={"identity": identity.value})
    if is_htmx(request):
        return _render_queue(request)
    return redirect("catalog:identity_queue")


@login_required
@permission_required(PERMISSION, raise_exception=True)
@require_POST
def identity_mark_bot(request: HttpRequest, pk: int) -> HttpResponse:
    identity = get_object_or_404(Identity, pk=pk)
    person = create_person_from_identity(identity, is_bot=True)
    record_audit(request.user, "identity.mark_bot", identity, after={"person": person.display_name})
    if is_htmx(request):
        return _render_queue(request)
    return redirect("catalog:identity_queue")


@login_required
@permission_required(PERMISSION, raise_exception=True)
@require_POST
def identity_exclude(request: HttpRequest, pk: int) -> HttpResponse:
    identity = get_object_or_404(Identity, pk=pk)
    person = create_person_from_identity(identity, exclude_from_metrics=True)
    record_audit(request.user, "identity.exclude", identity, after={"person": person.display_name})
    if is_htmx(request):
        return _render_queue(request)
    return redirect("catalog:identity_queue")
