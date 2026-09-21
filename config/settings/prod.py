import environ
from django.core.exceptions import ImproperlyConfigured

from config.settings.base import *  # noqa: F401,F403

DEBUG = False

# STATICFILES_STORAGE is a no-op setting on Django 5.1+; STORAGES is the only setting that takes
# effect. Manifest storage needs `collectstatic` to have run, which local/e2e/test do not require.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Fail fast rather than silently sign sessions/CSRF with a published default key. Absence is not
# the only way to get one: `base.py` reads a `.env`, and a deployment that copied a developer's
# file carries the published default in a real environment variable. Both are refused here.
INSECURE_DEFAULT_SECRET_KEY = "insecure-dev-key-change-me"  # noqa: S105 - the value to refuse
SECRET_KEY = environ.Env().str("SECRET_KEY")
if not SECRET_KEY.strip() or SECRET_KEY == INSECURE_DEFAULT_SECRET_KEY:
    raise ImproperlyConfigured(
        "SECRET_KEY is empty or still the published development default. Set a unique "
        "SECRET_KEY before running with config.settings.prod."
    )

SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
