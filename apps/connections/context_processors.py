"""The expiry/invalid connections banner (spec §5.1), visible only to users who can act on it.
Gated on the catalog.manage_settings permission alone — not on is_staff, which nothing in this
app ties to admin-group membership (docs/SETUP.md's onboarding flow can leave a non-superuser
admin with is_staff=False after they are added to the admin group from /admin/). A superuser's
has_perm() costs no query (PermissionsMixin short-circuits it); any other user's costs one."""

import datetime

from django.db.models import Q
from django.http import HttpRequest
from django.utils import timezone

from apps.catalog.services import get_int
from apps.connections.models import GitHubConnection


def connection_alerts(request: HttpRequest) -> dict:
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    if not user.has_perm("catalog.manage_settings"):
        return {}

    threshold = timezone.now() + datetime.timedelta(days=get_int("TOKEN_EXPIRY_WARNING_DAYS"))
    alerts = list(
        GitHubConnection.objects.filter(is_active=True)
        .filter(
            Q(status__in=[GitHubConnection.Status.INVALID, GitHubConnection.Status.EXPIRED])
            | Q(expires_at__isnull=False, expires_at__lte=threshold)
        )
        .order_by("name")
    )
    return {"connection_alerts": alerts}
