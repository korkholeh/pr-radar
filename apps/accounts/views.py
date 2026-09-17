from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext
from django.views.decorators.http import require_POST

try:
    from django.contrib.auth.decorators import login_not_required
except ImportError:  # pragma: no cover - Django < 5.1 fallback, not expected on this pin

    def login_not_required(view):
        view.login_required = False
        return view


from apps.accounts.context_processors import THEME_COOKIE_NAME
from apps.accounts.models import UserPreference
from apps.accounts.services import set_language, set_theme


def _safe_next(request: HttpRequest) -> str:
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/"
    if url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return "/"


@login_not_required
@require_POST
def set_theme_view(request: HttpRequest) -> HttpResponse:
    value = request.POST.get("theme", "")
    if value not in UserPreference.Theme.values:
        return render(
            request,
            "partials/preference_error.html",
            {"message": gettext("Invalid theme value.")},
            status=400,
        )
    if request.user.is_authenticated:
        set_theme(request.user, value)
    response = redirect(_safe_next(request))
    response.set_cookie(THEME_COOKIE_NAME, value, max_age=60 * 60 * 24 * 365, samesite="Lax")
    return response


@login_not_required
@require_POST
def set_language_view(request: HttpRequest) -> HttpResponse:
    value = request.POST.get("language", "")
    if value not in UserPreference.Language.values:
        return render(
            request,
            "partials/preference_error.html",
            {"message": gettext("Invalid language value.")},
            status=400,
        )
    if request.user.is_authenticated:
        set_language(request.user, value)
    response = redirect(_safe_next(request))
    response.set_cookie(settings.LANGUAGE_COOKIE_NAME, value, max_age=60 * 60 * 24 * 365, samesite="Lax")
    return response
