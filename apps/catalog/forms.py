from django import forms
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Person


class PersonForm(forms.ModelForm):
    class Meta:
        model = Person
        fields = ["display_name", "team", "role_hint", "notes", "is_bot", "exclude_from_metrics"]
        labels = {
            "display_name": _("Display name"),
            "team": _("Team"),
            "role_hint": _("Role"),
            "notes": _("Notes"),
            "is_bot": _("Is bot"),
            "exclude_from_metrics": _("Exclude from metrics"),
        }


class AssignIdentityForm(forms.Form):
    person = forms.ModelChoiceField(label=_("Person"), queryset=Person.objects.order_by("display_name"))


class MergePeopleForm(forms.Form):
    source = forms.ModelChoiceField(
        label=_("Merge this person"), queryset=Person.objects.order_by("display_name")
    )
    target = forms.ModelChoiceField(
        label=_("Into this person"), queryset=Person.objects.order_by("display_name")
    )

    def clean(self):
        cleaned = super().clean()
        source = cleaned.get("source")
        target = cleaned.get("target")
        if source is not None and target is not None and source.pk == target.pk:
            self.add_error(None, _("Cannot merge a person into themselves."))
        return cleaned
