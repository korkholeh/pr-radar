"""prod minus TLS: DEBUG=False so a real 500 is a real error page, plain http on 127.0.0.1,
and a database file separate from the developer's own."""

import os
from pathlib import Path

# prod.py requires SECRET_KEY from the environment; e2e is a disposable localhost surface, not a
# deployment, so it gets a fixed non-sensitive default instead of failing to boot.
os.environ.setdefault("SECRET_KEY", "e2e-not-a-production-secret")

from config.db import configure_sqlite_transaction_mode  # noqa: E402
from config.settings.prod import *  # noqa: E402,F401,F403
from config.settings.prod import BASE_DIR  # noqa: E402

DEBUG = False
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

# e2e is a disposable localhost surface, not a deployment: same rationale as the SECRET_KEY
# default above — seed_e2e needs a working encryption key to store a connection token.
if not FIELD_ENCRYPTION_KEYS:  # noqa: F405
    FIELD_ENCRYPTION_KEYS = ["e2e-not-a-production-encryption-key"]

SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

DATA_DIR = Path(BASE_DIR) / ".e2e" / "data"
for _subdir in ("logs", "cache", "exports", "repos", "staticfiles"):
    (DATA_DIR / _subdir).mkdir(parents=True, exist_ok=True)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(DATA_DIR / "db.sqlite3"),
    }
}
configure_sqlite_transaction_mode(DATABASES["default"])

STATIC_ROOT = DATA_DIR / "staticfiles"

LOGGING["handlers"]["file"]["filename"] = str(DATA_DIR / "logs" / "pr-radar.log")  # noqa: F405

HUEY = {
    "huey_class": "huey.SqliteHuey",
    "name": "pr-radar-e2e",
    "filename": str(DATA_DIR / "huey.sqlite3"),
    "immediate": False,
}
