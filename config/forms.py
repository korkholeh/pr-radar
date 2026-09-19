"""Form widgets shared across apps.

The one member so far is the date widget: a browser's native `<input type="date">` draws a
calendar no stylesheet can reach, so every date field in the UI is a plain text input carrying an
ISO date and `static/js/datepicker.js` draws the calendar instead. The submitted string is the
same `YYYY-MM-DD` the native control would have sent, and Django's `DateField` accepts ISO input
in every locale, so nothing downstream changes — and a page without JavaScript still takes a
typed date.
"""

from __future__ import annotations

from django import forms

ISO_DATE_FORMAT = "%Y-%m-%d"


def date_widget(**attrs: str) -> forms.DateInput:
    return forms.DateInput(
        format=ISO_DATE_FORMAT,
        attrs={
            "data-datepicker": "",
            "placeholder": "YYYY-MM-DD",
            "autocomplete": "off",
            **attrs,
        },
    )
