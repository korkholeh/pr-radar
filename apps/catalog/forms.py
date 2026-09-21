from django import forms
from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Person, Project, Repository
from apps.catalog.services import generate_project_slug


class ProjectForm(forms.ModelForm):
    """Settings → project create/edit. `slug` is optional in the form even though the model
    requires it: an empty slug is derived from the name, so a lead never has to invent one."""

    class Meta:
        model = Project
        # `color` is left out on purpose: nothing reads `Project.color` yet — charts assign
        # `--series-N` by series position (`dashboards/charts.py`), not per project — so a
        # colour picker here would be a control with no visible effect.
        fields = ["name", "slug", "description", "repositories", "is_active"]
        labels = {
            "name": _("Name"),
            "slug": _("Slug"),
            "description": _("Description"),
            "repositories": _("Repositories"),
            "is_active": _("Is active"),
        }
        help_texts = {
            "slug": _("Used in URLs and exports. Leave it empty to derive one from the name."),
            "repositories": _("The repositories whose pull requests this project reports on."),
            "is_active": _("An inactive project is hidden from dashboards and filters."),
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            # `tagselect.js` enhances any `select[multiple]` carrying this attribute; the native
            # control stays the source of truth, so the form works without JavaScript too.
            "repositories": forms.SelectMultiple(attrs={"data-tagselect": ""}),
        }

    def __init__(self, *args, repositories: QuerySet[Repository] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False
        if repositories is not None:
            self.fields["repositories"].queryset = repositories

    def clean_slug(self) -> str:
        slug: str = self.cleaned_data.get("slug", "")
        if slug:
            return slug
        # `name` is cleaned before `slug` (Meta.fields order), so its value is available here.
        derived = generate_project_slug(self.cleaned_data.get("name", ""), exclude_pk=self.instance.pk)
        if not derived:
            raise ValidationError(
                _("This name gives no slug to derive. Type one using Latin letters, digits and hyphens.")
            )
        return derived


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

    def __init__(self, *args, people=None, **kwargs):
        super().__init__(*args, **kwargs)
        if people is not None:
            self.fields["source"].queryset = people
            self.fields["target"].queryset = people

    def clean(self):
        cleaned = super().clean()
        source = cleaned.get("source")
        target = cleaned.get("target")
        if source is not None and target is not None and source.pk == target.pk:
            self.add_error(None, _("Cannot merge a person into themselves."))
        return cleaned
