from django.conf import settings
from django.http import HttpRequest, HttpResponse

from apps.accounts.models import UserPreference


class UserLanguageMiddleware:
    """Overwrites the language cookie LocaleMiddleware reads from, with
    UserPreference.language, so the stored profile choice outranks Accept-Language
    and survives a new session on another device. Placed before LocaleMiddleware
    (Django's LocaleMiddleware resolves language from the cookie only; there is no
    session-based i18n storage in this Django version)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            try:
                language = user.preference.language
            except UserPreference.DoesNotExist:
                language = None
            if language:
                request.COOKIES[settings.LANGUAGE_COOKIE_NAME] = language
        return self.get_response(request)
