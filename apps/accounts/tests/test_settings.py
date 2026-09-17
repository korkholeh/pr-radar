import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from django.conf import settings
from django.db import connection as django_connection

from config.db import apply_sqlite_pragmas, configure_sqlite_transaction_mode


class _FakeSqliteConnection:
    """Wraps a raw sqlite3 connection so apply_sqlite_pragmas' vendor guard sees it as SQLite."""

    vendor = "sqlite"

    def __init__(self, raw: sqlite3.Connection) -> None:
        self._raw = raw

    def cursor(self):
        return self._raw.cursor()


def test_apply_sqlite_pragmas_sets_wal_and_related_pragmas_on_a_real_connection():
    with tempfile.TemporaryDirectory() as tmp:
        raw = sqlite3.connect(str(Path(tmp) / "pragma-test.sqlite3"))
        try:
            apply_sqlite_pragmas(sender=None, connection=_FakeSqliteConnection(raw))
            cursor = raw.cursor()
            cursor.execute("PRAGMA journal_mode;")
            assert cursor.fetchone()[0].lower() == "wal"
            cursor.execute("PRAGMA foreign_keys;")
            assert cursor.fetchone()[0] == 1
            cursor.execute("PRAGMA busy_timeout;")
            assert cursor.fetchone()[0] == 5000
        finally:
            raw.close()


def test_apply_sqlite_pragmas_is_a_noop_for_non_sqlite_vendor():
    executed = []

    class _Cursor:
        def execute(self, sql):
            executed.append(sql)

        def close(self):
            pass

    class _PostgresConnection:
        vendor = "postgresql"

        def cursor(self):
            return _Cursor()

    apply_sqlite_pragmas(sender=None, connection=_PostgresConnection())
    assert executed == []


@pytest.mark.django_db
def test_django_sqlite_connection_has_pragmas_applied():
    django_connection.ensure_connection()
    with django_connection.cursor() as cursor:
        cursor.execute("PRAGMA busy_timeout;")
        assert cursor.fetchone()[0] == 5000
        cursor.execute("PRAGMA foreign_keys;")
        assert cursor.fetchone()[0] == 1


def test_configure_sqlite_transaction_mode_merges_immediate_for_sqlite():
    config = {"ENGINE": "django.db.backends.sqlite3"}
    configure_sqlite_transaction_mode(config)
    assert config["OPTIONS"]["transaction_mode"] == "IMMEDIATE"


def test_configure_sqlite_transaction_mode_is_a_noop_for_other_engines():
    config = {"ENGINE": "django.db.backends.postgresql"}
    configure_sqlite_transaction_mode(config)
    assert "OPTIONS" not in config


def test_data_dir_subdirectories_created():
    data_dir = settings.DATA_DIR
    for subdir in ("logs", "cache", "exports", "repos", "staticfiles"):
        assert (data_dir / subdir).is_dir()


@pytest.mark.parametrize("module", ["config.settings.e2e"])
def test_e2e_settings_debug_false_and_own_data_dir(module, settings_module_loader):
    e2e_settings = settings_module_loader(module)
    assert e2e_settings.DEBUG is False
    assert e2e_settings.ALLOWED_HOSTS == ["127.0.0.1", "localhost"]
    assert str(e2e_settings.DATA_DIR).endswith(".e2e/data")
    assert e2e_settings.DATA_DIR != settings.DATA_DIR


def test_prod_settings_use_manifest_static_storage(settings_module_loader):
    prod_settings = settings_module_loader("config.settings.prod")
    assert (
        prod_settings.STORAGES["staticfiles"]["BACKEND"]
        == "whitenoise.storage.CompressedManifestStaticFilesStorage"
    )


def test_prod_settings_refuse_the_insecure_default_secret_key():
    env = {key: value for key, value in os.environ.items() if key != "SECRET_KEY"}
    result = subprocess.run(
        [sys.executable, "-c", "import django; django.setup()"],
        cwd=str(settings.BASE_DIR),
        env={**env, "DJANGO_SETTINGS_MODULE": "config.settings.prod"},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "SECRET_KEY" in result.stderr


@pytest.fixture
def settings_module_loader():
    import importlib

    def _load(module_path: str):
        return importlib.import_module(module_path)

    return _load
