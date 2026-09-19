from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.github_sync.forms import BackfillForm
from apps.github_sync.models import SyncRun
from apps.github_sync.tasks import backfill_task, sync_task
from apps.metrics.timeframe import day_start
from config.htmx import is_htmx

_RUN_LIST_LIMIT = 20
_TRIGGER_PERMISSION = "catalog.manage_settings"


def _sync_context(backfill_form: BackfillForm | None = None) -> dict:
    runs = list(SyncRun.objects.order_by("-started_at")[:_RUN_LIST_LIMIT])
    running = next((run for run in runs if run.status == SyncRun.Status.RUNNING), None)
    return {
        "runs": runs,
        "running": running,
        "backfill_form": backfill_form if backfill_form is not None else BackfillForm(),
    }


@login_required
def sync_page(request: HttpRequest) -> HttpResponse:
    template = "github_sync/partials/content.html" if is_htmx(request) else "github_sync/sync.html"
    return render(request, template, _sync_context())


@login_required
@permission_required(_TRIGGER_PERMISSION, raise_exception=True)
@require_POST
def sync_run(request: HttpRequest) -> HttpResponse:
    sync_task()
    if is_htmx(request):
        return render(request, "github_sync/partials/status.html", _sync_context())
    return redirect("github_sync:sync")


@login_required
@permission_required(_TRIGGER_PERMISSION, raise_exception=True)
@require_POST
def sync_backfill(request: HttpRequest) -> HttpResponse:
    """Re-fetches every pull request updated since a chosen date. The form's date is a
    REPORT_TIMEZONE calendar day; `day_start()` turns it into the UTC instant `run_sync()`
    compares `updatedAt` against, so "from the 5th" means the 5th in Kyiv, not in UTC."""
    form = BackfillForm(request.POST)
    if not form.is_valid():
        context = _sync_context(form)
        if is_htmx(request):
            return render(request, "github_sync/partials/content.html", context)
        return render(request, "github_sync/sync.html", context)

    repositories = form.cleaned_data["repositories"]
    backfill_task(
        day_start(form.cleaned_data["since"]),
        [repository.full_name for repository in repositories],
    )
    if is_htmx(request):
        return render(request, "github_sync/partials/content.html", _sync_context())
    return redirect("github_sync:sync")


@login_required
def sync_status(request: HttpRequest) -> HttpResponse:
    return render(request, "github_sync/partials/status.html", _sync_context())
