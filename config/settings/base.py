"""Settings shared by every environment. Import from local/prod/e2e, never run directly."""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(str(env_file))

SECRET_KEY = env.str("SECRET_KEY", default="insecure-dev-key-change-me")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

DATA_DIR = Path(env.str("DATA_DIR", default=str(BASE_DIR / "data")))
for _subdir in ("logs", "cache", "exports", "repos", "staticfiles"):
    (DATA_DIR / _subdir).mkdir(parents=True, exist_ok=True)

REPORT_TIMEZONE = env.str("REPORT_TIMEZONE", default="Europe/Kyiv")
FIELD_ENCRYPTION_KEYS = env.list("FIELD_ENCRYPTION_KEYS", default=[])
STORE_RAW_PAYLOADS = env.bool("STORE_RAW_PAYLOADS", default=False)

GITHUB_API_BASE_URL = env.str("GITHUB_API_BASE_URL", default="https://api.github.com")
GITHUB_GRAPHQL_URL = f"{GITHUB_API_BASE_URL}/graphql"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "huey.contrib.djhuey",
    "django_tables2",
    "django_filters",
    "apps.accounts",
    "apps.connections",
    "apps.catalog",
    "apps.github_sync",
    "apps.activity",
    "apps.ai_detection",
    "apps.policy",
    "apps.metrics",
    "apps.churn",
    "apps.dashboards",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.accounts.middleware.UserLanguageMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.accounts.context_processors.theme_preference",
                "apps.connections.context_processors.connection_alerts",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": env.db_url(
        "DATABASE_URL",
        default=f"sqlite:///{DATA_DIR / 'db.sqlite3'}",
    )
}
from config.db import configure_sqlite_transaction_mode  # noqa: E402

configure_sqlite_transaction_mode(DATABASES["default"])

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboards:overview"

LANGUAGE_CODE = "en"
LANGUAGES = [
    ("en", "English"),
    ("uk", "Українська"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
USE_I18N = True
USE_THOUSAND_SEPARATOR = True
TIME_ZONE = "UTC"
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = DATA_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": str(DATA_DIR / "cache" / "metrics"),
        "OPTIONS": {"MAX_ENTRIES": 5000},
    }
}

HUEY = {
    "huey_class": "huey.SqliteHuey",
    "name": "pr-radar",
    "filename": str(DATA_DIR / "huey.sqlite3"),
    "immediate": False,
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "secret_mask": {"()": "config.logging_filters.SecretMaskingFilter"},
    },
    "formatters": {
        "default": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["secret_mask"],
            "formatter": "default",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(DATA_DIR / "logs" / "pr-radar.log"),
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "filters": ["secret_mask"],
            "formatter": "default",
        },
    },
    "root": {
        "handlers": ["console", "file"],
        "level": "INFO",
    },
}
