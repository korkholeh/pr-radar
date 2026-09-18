"""Parses `request.GET` into the raw fields `params.parse()` resolves into a `DashboardParams`
(RISKS row 3: nothing is read from a query string without a form). Mirrors
`apps.policy.forms.ViolationFilterForm`: an unknown value, a malformed date or an out-of-scope id
is dropped, never a validation error, so a stale bookmark or a hand-edited URL never breaks the
page."""

from __future__ import annotations

from django import forms

from apps.activity.models import AIStatus, PullRequest, SizeBucket
from apps.ai_detection.models import Tool
from apps.catalog.models import Person, Project, Repository


class _LenientChoiceField(forms.ChoiceField):
    def validate(self, value):
        pass


class _LenientDateField(forms.DateField):
    """A malformed date resolves to `None` instead of a form error, so `cleaned_data` always
    carries the key `params.parse()` reads."""

    def to_python(self, value):
        try:
            return super().to_python(value)
        except forms.ValidationError:
            return None


class _LenientIntegerField(forms.IntegerField):
    """An unparsable page number resolves to `None` (the default page) instead of a form error;
    an out-of-range one is caught by `clean_page` since no `min_value` validator is attached
    here."""

    def to_python(self, value):
        try:
            return super().to_python(value)
        except forms.ValidationError:
            return None


class _LenientModelMultipleChoiceField(forms.ModelMultipleChoiceField):
    """An id outside `queryset` (unknown, or out of the caller's scope) is dropped silently
    instead of becoming a form error."""

    def clean(self, value):
        try:
            return super().clean(value)
        except forms.ValidationError:
            allowed_ids = set(self.queryset.values_list("pk", flat=True))
            kept = [v for v in (value or []) if str(v) in {str(pk) for pk in allowed_ids}]
            return self.queryset.filter(pk__in=kept)


class DashboardFilterForm(forms.Form):
    mode = _LenientChoiceField(choices=[("period", "period"), ("day", "day")], required=False)
    preset = _LenientChoiceField(
        choices=[
            ("7d", "7d"),
            ("30d", "30d"),
            ("90d", "90d"),
            ("this_month", "this_month"),
            ("last_month", "last_month"),
            ("quarter", "quarter"),
            ("custom", "custom"),
        ],
        required=False,
    )
    from_date = _LenientDateField(required=False)
    to_date = _LenientDateField(required=False)
    day = _LenientDateField(required=False)
    granularity = _LenientChoiceField(
        choices=[("day", "day"), ("week", "week"), ("month", "month")], required=False
    )
    cohort = _LenientChoiceField(
        choices=[("all", "all"), ("ai", "ai"), ("non_ai", "non_ai"), ("compare", "compare")], required=False
    )
    project = _LenientModelMultipleChoiceField(queryset=Project.objects.none(), required=False)
    repository = _LenientModelMultipleChoiceField(queryset=Repository.objects.none(), required=False)
    q = forms.CharField(required=False)
    sort = forms.CharField(required=False)
    page = _LenientIntegerField(required=False)
    table = forms.CharField(required=False)

    def __init__(self, *args, projects=None, repositories=None, **kwargs):
        """`from`/`to` are the query string's names for the custom-range bounds; `from` shadows
        the Python keyword so the form field (and `cleaned_data` key) is `from_date`/`to_date`
        instead. `QueryDict.pop()` returns the value list, so `setlist()` (not `[]=`, which would
        wrap that list a second time) is what keeps a single-valued field single-valued."""
        data = args[0] if args else kwargs.get("data")
        if data is not None:
            renamed = data.copy()
            if "from" in renamed:
                renamed.setlist("from_date", renamed.pop("from"))
            if "to" in renamed:
                renamed.setlist("to_date", renamed.pop("to"))
            if args:
                args = (renamed, *args[1:])
            else:
                kwargs["data"] = renamed
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = projects if projects is not None else Project.objects.none()
        self.fields["repository"].queryset = (
            repositories if repositories is not None else Repository.objects.none()
        )

    def clean_mode(self) -> str:
        value = self.cleaned_data.get("mode", "")
        return value if value in {"period", "day"} else ""

    def clean_preset(self) -> str:
        value = self.cleaned_data.get("preset", "")
        allowed = {"7d", "30d", "90d", "this_month", "last_month", "quarter", "custom"}
        return value if value in allowed else ""

    def clean_granularity(self) -> str:
        value = self.cleaned_data.get("granularity", "")
        return value if value in {"day", "week", "month"} else ""

    def clean_cohort(self) -> str:
        value = self.cleaned_data.get("cohort", "")
        return value if value in {"all", "ai", "non_ai", "compare"} else ""

    def clean_page(self) -> int | None:
        value = self.cleaned_data.get("page")
        return value if value and value >= 1 else None


class _LenientMultipleChoiceField(forms.MultipleChoiceField):
    """Like `_LenientChoiceField` but for a multi-select: an unknown value among several valid
    ones is dropped rather than invalidating the whole field."""

    def valid_value(self, value: str) -> bool:
        return True  # never raises here; clean() below filters afterwards.

    def clean(self, value):
        cleaned = super().clean(value)
        allowed = {choice_value for choice_value, _label in self.choices}
        return [item for item in cleaned if item in allowed]


class PullRequestFilterForm(forms.Form):
    """The PR list's filter fields (plan §3): an unknown enum value or an out-of-scope author id
    is dropped, never a validation error — same contract as `DashboardFilterForm`."""

    author = _LenientModelMultipleChoiceField(queryset=Person.objects.none(), required=False)
    state = _LenientMultipleChoiceField(choices=PullRequest.State.choices, required=False)
    ai_status = _LenientMultipleChoiceField(choices=AIStatus.choices, required=False)
    tool = _LenientMultipleChoiceField(choices=Tool.choices, required=False)
    size = _LenientMultipleChoiceField(choices=SizeBucket.choices, required=False)
    has_violations = _LenientChoiceField(choices=[("", "any"), ("yes", "yes"), ("no", "no")], required=False)

    def __init__(self, *args, people=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["author"].queryset = people if people is not None else Person.objects.none()

    def clean_has_violations(self) -> str:
        value = self.cleaned_data.get("has_violations", "")
        return value if value in {"", "yes", "no"} else ""
