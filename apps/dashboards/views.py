from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from apps.accounts.selectors import scope_for_user
from apps.activity.selectors import pull_requests_in_scope
from apps.ai_detection.models import Tool
from apps.ai_detection.selectors import signals_for_pull_request


def overview(request: HttpRequest) -> HttpResponse:
    return render(request, "dashboards/overview.html")


def pull_request_detail(request: HttpRequest, pk: int) -> HttpResponse:
    scope = scope_for_user(request.user)
    pull_request = get_object_or_404(pull_requests_in_scope(scope).select_related("repository"), pk=pk)
    signals = list(
        signals_for_pull_request(scope, pk).select_related("rule", "commit").order_by("detected_at")
    )
    ai_tools_display = [
        Tool(value).label if value in Tool.values else value for value in pull_request.ai_tools
    ]
    return render(
        request,
        "dashboards/pull_request_detail.html",
        {"pull_request": pull_request, "signals": signals, "ai_tools_display": ai_tools_display},
    )
