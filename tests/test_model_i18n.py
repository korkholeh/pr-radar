from django.apps import apps
from django.db import models
from django.utils.functional import Promise

PROJECT_MODELS = [model for model in apps.get_models() if model._meta.app_config.name.startswith("apps.")]

# Project.color choices are CSS custom-property names (static/css/tokens.css), not user-facing
# text, so they are exempt from translation.
UNTRANSLATED_CHOICE_FIELDS = {("Project", "color")}


def _project_model_ids():
    return [m.__name__ for m in PROJECT_MODELS]


def test_every_field_verbose_name_is_lazy():
    for model in PROJECT_MODELS:
        for field in model._meta.get_fields():
            if not isinstance(field, models.Field) or field.auto_created:
                continue
            assert isinstance(field.verbose_name, Promise), (
                f"{model.__name__}.{field.name}.verbose_name is not gettext_lazy"
            )


def test_every_choice_label_is_lazy():
    for model in PROJECT_MODELS:
        for field in model._meta.get_fields():
            if not isinstance(field, models.Field) or field.auto_created or not field.choices:
                continue
            if (model.__name__, field.name) in UNTRANSLATED_CHOICE_FIELDS:
                continue
            for value, label in field.choices:
                assert isinstance(label, Promise), (
                    f"{model.__name__}.{field.name} choice {value!r} label is not gettext_lazy"
                )


def test_every_model_verbose_name_is_lazy():
    for model in PROJECT_MODELS:
        assert isinstance(model._meta.verbose_name, Promise), (
            f"{model.__name__}.Meta.verbose_name is not lazy"
        )
        assert isinstance(model._meta.verbose_name_plural, Promise), (
            f"{model.__name__}.Meta.verbose_name_plural is not lazy"
        )
