from django.conf import settings
from huey.contrib.djhuey import HUEY as huey_instance
from huey.contrib.djhuey import task


def test_huey_filename_lives_under_data_dir_and_differs_from_db():
    huey_path = settings.HUEY["filename"]
    assert huey_path.startswith(str(settings.DATA_DIR))
    db_path = settings.DATABASES["default"]["NAME"]
    assert huey_path != str(db_path)


def test_huey_is_immediate_under_test_settings():
    assert huey_instance.immediate is True


def test_huey_task_executes_inline_when_immediate():
    calls = []

    @task()
    def _record(value):
        calls.append(value)
        return value

    _record("ok")

    assert calls == ["ok"]
