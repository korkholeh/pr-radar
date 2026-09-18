"""Lazy message per `ExportJob.error_code` (CLAUDE.md: never store a rendered English message).
`run_export_job()`/`cleanup_exports()` store only a code; "My exports" renders it in the reader's
own language."""

from django.utils.translation import gettext_lazy as _

EXPORT_ERROR_MESSAGES = {
    "failed": _("This export failed. Try again, or contact an administrator if it keeps failing."),
    "stuck": _("This export did not finish in time and was marked as failed. Try again."),
}


def render_export_error(error_code: str) -> str:
    return str(EXPORT_ERROR_MESSAGES.get(error_code, _("This export failed.")))
