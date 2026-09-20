from django import forms
from django.utils.translation import gettext_lazy as _

from apps.ai_detection.models import Confidence, DetectionRule, SignalRule


class DetectionRuleForm(forms.ModelForm):
    class Meta:
        model = DetectionRule
        fields = ["name", "detector", "pattern", "tool", "confidence", "is_active", "notes"]
        labels = {
            "name": _("Name"),
            "detector": _("Detector"),
            "pattern": _("Pattern"),
            "tool": _("Tool"),
            "confidence": _("Confidence"),
            "is_active": _("Is active"),
            "notes": _("Notes"),
        }

    def clean_pattern(self) -> str:
        instance = DetectionRule(pattern=self.cleaned_data["pattern"])
        try:
            instance.clean()
        except forms.ValidationError as exc:
            raise forms.ValidationError(exc.message_dict.get("pattern", exc.messages)) from exc
        return self.cleaned_data["pattern"]


class DryRunForm(forms.ModelForm):
    """Same fields as `DetectionRuleForm`, unbound from any instance: the dry run tests a
    pattern before it is ever saved as a `DetectionRule` row."""

    class Meta:
        model = DetectionRule
        fields = ["detector", "pattern", "tool", "confidence"]
        labels = {
            "detector": _("Detector"),
            "pattern": _("Pattern"),
            "tool": _("Tool"),
            "confidence": _("Confidence"),
        }

    def clean_pattern(self) -> str:
        instance = DetectionRule(pattern=self.cleaned_data["pattern"])
        try:
            instance.clean()
        except forms.ValidationError as exc:
            raise forms.ValidationError(exc.message_dict.get("pattern", exc.messages)) from exc
        return self.cleaned_data["pattern"]


class _ParamsJSONField(forms.JSONField):
    """A friendlier error than Django's default. An admin typing thresholds into a textarea gets
    "Enter a valid JSON object, for example {...}" rather than a parser message about tokens."""

    default_error_messages = {
        "invalid": _('Enter the parameters as a JSON object, for example {"min_lines": 400}.'),
    }


class SignalRuleForm(forms.ModelForm):
    """`clean()` delegates to the model's own `clean()` so the form and the Django admin reject
    exactly the same things — and behind both, the database still refuses `confidence = high`."""

    params = _ParamsJSONField(required=False, initial=dict)

    class Meta:
        model = SignalRule
        fields = ["name", "kind", "params", "tool", "confidence", "is_active", "notes"]
        labels = {
            "name": _("Name"),
            "kind": _("Kind"),
            "params": _("Parameters"),
            "tool": _("Tool"),
            "confidence": _("Confidence"),
            "is_active": _("Is active"),
            "notes": _("Notes"),
        }
        help_texts = {
            "params": _("Thresholds for this kind, as JSON. Anything you leave out uses the default."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["confidence"].choices = [
            (value, label) for value, label in Confidence.choices if value != Confidence.HIGH
        ]

    def clean(self) -> dict:
        cleaned = super().clean()
        candidate = SignalRule(
            kind=cleaned.get("kind") or "",
            params=cleaned.get("params") or {},
            confidence=cleaned.get("confidence") or "",
        )
        try:
            candidate.clean()
        except forms.ValidationError as exc:
            for field, messages in exc.message_dict.items():
                self.add_error(field if field in self.fields else None, messages)
        return cleaned


class SignalDryRunForm(SignalRuleForm):
    """The same fields, unbound from any instance: the dry run tests thresholds before they are
    ever saved as a `SignalRule` row."""

    class Meta(SignalRuleForm.Meta):
        fields = ["kind", "params", "tool", "confidence"]
