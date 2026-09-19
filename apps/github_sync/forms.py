"""The backfill form behind the Sync page's "Load historical data" panel: a preset window or an
explicit start date, optionally narrowed to a few repositories. It resolves both shapes into one
`since` date so the view — and `run_sync()` behind it — only ever sees a single watermark override
(the same one `manage.py sync --since` passes)."""

import datetime

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Repository
from apps.metrics.timeframe import today

CUSTOM_WINDOW = "custom"

WINDOW_CHOICES = [
    ("7", _("Last 7 days")),
    ("14", _("Last 14 days")),
    ("30", _("Last 30 days")),
    ("90", _("Last 90 days")),
    (CUSTOM_WINDOW, _("From a specific date")),
]


class BackfillForm(forms.Form):
    window = forms.ChoiceField(
        label=_("Period"),
        choices=WINDOW_CHOICES,
        initial="14",
        widget=forms.RadioSelect,
    )
    since = forms.DateField(
        label=_("Start date"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text=_("Pull requests updated on or after this date are fetched again."),
    )
    repositories = forms.ModelMultipleChoiceField(
        label=_("Repositories"),
        queryset=Repository.objects.none(),
        required=False,
        help_text=_("Leave empty to backfill every active repository."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["repositories"].queryset = Repository.objects.filter(is_active=True).order_by("full_name")

    def clean(self):
        cleaned = super().clean()
        window = cleaned.get("window")
        since = cleaned.get("since")
        if window == CUSTOM_WINDOW:
            if not since:
                self.add_error("since", _("Pick a start date."))
        elif window:
            since = today() - datetime.timedelta(days=int(window))
        if since and since > today():
            self.add_error("since", _("The start date cannot be in the future."))
            since = None
        cleaned["since"] = since
        return cleaned
