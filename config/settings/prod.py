import environ

from config.settings.base import *  # noqa: F401,F403

DEBUG = False

# STATICFILES_STORAGE is a no-op setting on Django 5.1+; STORAGES is the only setting that takes
# effect. Manifest storage needs `collectstatic` to have run, which local/e2e/test do not require.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Fail fast rather than silently sign sessions/CSRF with a published default key.
SECRET_KEY = environ.Env().str("SECRET_KEY")

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
