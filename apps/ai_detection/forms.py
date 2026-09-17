from django import forms
from django.utils.translation import gettext_lazy as _

from apps.ai_detection.models import DetectionRule


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
