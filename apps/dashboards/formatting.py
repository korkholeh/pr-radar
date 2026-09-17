"""Duration formatting. static/js/formatting.js is the JS-side twin; their parity is
proved by the shared case table tests/fixtures/duration_cases.json."""

from contextlib import nullcontext

from django.utils import translation
from django.utils.translation import gettext, ngettext

EM_DASH = "—"


def format_duration(seconds: float | None, *, locale: str | None = None) -> str:
    if seconds is None:
        return EM_DASH

    with translation.override(locale) if locale else nullcontext():
        seconds = round(seconds)
        if seconds < 60:
            return ngettext("%(count)s second", "%(count)s seconds", seconds) % {"count": seconds}
        if seconds < 3600:
            # Round half up (banker's rounding would disagree with JS's Math.round on x.5).
            minutes = (seconds + 30) // 60
            if minutes < 60:
                return ngettext("%(count)s minute", "%(count)s minutes", minutes) % {"count": minutes}
            seconds = 3600
        days, remainder = divmod(seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes = remainder // 60
        if days:
            return gettext("%(days)dd %(hours)dh") % {"days": days, "hours": hours}
        return gettext("%(hours)dh %(minutes)dm") % {"hours": hours, "minutes": minutes}
