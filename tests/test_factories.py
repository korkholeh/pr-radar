import importlib
import pkgutil

import factory
import pytest
from django.apps import apps

import apps as apps_package


def _project_models():
    return [model for model in apps.get_models() if model._meta.app_config.name.startswith("apps.")]


def _discover_factories():
    registry: dict[type, type[factory.django.DjangoModelFactory]] = {}
    for module_info in pkgutil.iter_modules(apps_package.__path__):
        module_name = f"apps.{module_info.name}.factories"
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, factory.django.DjangoModelFactory)
                and attr is not factory.django.DjangoModelFactory
            ):
                model = attr._meta.model
                registry.setdefault(model, attr)
    return registry


FACTORY_REGISTRY = _discover_factories()
PROJECT_MODELS = _project_models()


def test_every_model_has_a_factory():
    missing = [m for m in PROJECT_MODELS if m not in FACTORY_REGISTRY]
    assert not missing, f"models without a factory: {[m.__name__ for m in missing]}"


@pytest.mark.django_db
@pytest.mark.parametrize("model", PROJECT_MODELS, ids=lambda m: m.__name__)
def test_every_factory_creates_a_valid_instance(model):
    factory_cls = FACTORY_REGISTRY[model]
    instance = factory_cls.create()
    instance.full_clean()


@pytest.mark.django_db
@pytest.mark.parametrize("model", PROJECT_MODELS, ids=lambda m: m.__name__)
def test_calling_each_factory_twice_does_not_raise(model):
    factory_cls = FACTORY_REGISTRY[model]
    factory_cls.create()
    factory_cls.create()
