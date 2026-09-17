from django.http import HttpRequest

from apps.accounts.models import UserPreference

THEME_COOKIE_NAME = "pr_radar_theme"


def theme_preference(request: HttpRequest) -> dict:
    """Resolution order: UserPreference.theme -> pr_radar_theme cookie -> "system"."""
    preference = None
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        try:
            preference = user.preference.theme
        except UserPreference.DoesNotExist:
            preference = None

    if preference is None:
        preference = request.COOKIES.get(THEME_COOKIE_NAME, UserPreference.Theme.SYSTEM)

    if preference not in UserPreference.Theme.values:
        preference = UserPreference.Theme.SYSTEM

    resolved = "dark" if preference == UserPreference.Theme.DARK else "light"

    return {
        "theme_preference": preference,
        "theme_resolved": resolved,
    }
