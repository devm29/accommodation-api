"""
Django settings for bnbu_backend_api.

Everything optional is optional on purpose: the API boots, migrates, serves
and runs its test suite with no OpenAI key, no Cloudinary account, no AirDNA
key and no ClickUp board. Each integration reports itself unavailable at the
one endpoint that needs it rather than failing at import.
"""

import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import cloudinary
import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
IS_DEPLOYED = ENVIRONMENT in {"staging", "production"}
#: True while `manage.py test` is running. Used to keep the suite free of
#: Redis and of a live broker: tests exercise the code, not the infrastructure.
TESTING = "test" in sys.argv or os.getenv("DJANGO_TESTING") == "1"

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    if IS_DEPLOYED:
        raise ImproperlyConfigured(
            "SECRET_KEY must be set when ENVIRONMENT is staging or production."
        )
    SECRET_KEY = "django-insecure-local-development-key-do-not-use-in-production"

DEBUG = _flag("DEBUG")

if IS_DEPLOYED:
    ALLOWED_HOSTS = [host.strip() for host in os.getenv("ALLOWED_HOSTS", "localhost").split(",")]
else:
    ALLOWED_HOSTS = [
        host.strip()
        for host in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,0.0.0.0").split(",")
    ]


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "django_filters",
    "corsheaders",
    "drf_yasg",
    "bnbu_core",
    "accounts",
    "lease",
    "regulations",
    "rental",
]

AUTH_USER_MODEL = "accounts.CustomUser"

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "bnbu_backend_api.urls"
WSGI_APPLICATION = "bnbu_backend_api.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DATABASES = {
    "default": dj_database_url.config(
        default=os.getenv(
            "DATABASE_URL", "postgres://dev_user:dev_password@localhost:5432/dev_database"
        ),
        conn_max_age=int(os.getenv("DB_CONN_MAX_AGE", "60")),
        conn_health_checks=True,
    )
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTHENTICATION_BACKENDS = [
    "accounts.backends.EmailBackend",
    "django.contrib.auth.backends.ModelBackend",
]


# ---------------------------------------------------------------------------
# Internationalisation and static files
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# The repository ships no `static/` directory; listing a missing path here
# makes every `manage.py` invocation emit staticfiles.W004.
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").is_dir() else []
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

FILE_UPLOAD_MAX_MEMORY_SIZE = 52_428_800


# ---------------------------------------------------------------------------
# REST framework
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PAGINATION_CLASS": "bnbu_core.pagination.DefaultPagination",
    "PAGE_SIZE": 10,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(days=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

SWAGGER_SETTINGS = {
    "USE_SESSION_AUTH": False,
    "SECURITY_DEFINITIONS": {"Bearer": {"type": "apiKey", "name": "Authorization", "in": "header"}},
}

CORS_ALLOWED_ORIGINS = json.loads(os.getenv("CORS_ALLOWED_ORIGINS", "[]"))
CORS_ALLOW_HEADERS = ["content-type", "authorization", "x-csrftoken", "x-requested-with"]


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "no-reply@localhost")
# Base URL of the frontend that serves the password-reset form. The reset link
# was once hardcoded to example.com, which pointed nowhere in a real deploy.
PASSWORD_RESET_URL_BASE = os.getenv("PASSWORD_RESET_URL_BASE", "http://localhost:5173")


# ---------------------------------------------------------------------------
# Cache, broker and Celery
# ---------------------------------------------------------------------------

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

if TESTING:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            # redis-py rejects ssl_cert_reqs on a plain redis:// connection.
            "OPTIONS": {"ssl_cert_reqs": None} if REDIS_URL.startswith("rediss://") else {},
        }
    }

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
# Under test, tasks run inline: the suite asserts on what a task does, never
# on whether a broker delivered it.
CELERY_TASK_ALWAYS_EAGER = TESTING or _flag("CELERY_TASK_ALWAYS_EAGER")
CELERY_TASK_EAGER_PROPAGATES = CELERY_TASK_ALWAYS_EAGER


# ---------------------------------------------------------------------------
# Language model seam
# ---------------------------------------------------------------------------

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
#: "openai", "demo" (canned answers, no network) or "null". Empty means: use
#: OpenAI when a key is present, otherwise the null provider.
# Under test the default is the null provider, so a forgotten mock fails
# loudly instead of reaching for a real, paid endpoint.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "null" if TESTING else "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4")
#: Per-purpose model overrides, e.g. {"lease_chat": "gpt-4o-mini"}.
LLM_MODEL_OVERRIDES = json.loads(os.getenv("LLM_MODEL_OVERRIDES", "{}"))
#: Cap on how many five-page chunks of one document are sent for review, so a
#: single upload cannot fan out into an unbounded number of paid calls.
LEASE_MAX_CHUNKS = int(os.getenv("LEASE_MAX_CHUNKS", "12"))

#: Regulation sources, consulted in order. "curated" answers locally from a
#: reviewed table; "llm" asks the configured provider.
REGULATION_SOURCES = [
    name.strip()
    for name in os.getenv("REGULATION_SOURCES", "curated,llm").split(",")
    if name.strip()
]
#: How long an answer about a location is reused. Two users asking about the
#: same city should not cost two model calls.
REGULATION_CACHE_TTL = int(os.getenv("REGULATION_CACHE_TTL", "86400"))


# ---------------------------------------------------------------------------
# Third-party integrations - every one of them optional
# ---------------------------------------------------------------------------

AIRDNA_URL = os.getenv("AIRDNA_URL")
AIRDNA_API_KEY = os.getenv("AIRDNA_API_KEY")

CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")
CLOUDINARY_CONFIGURED = all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET])
cloudinary.config(
    cloud_name=CLOUDINARY_CLOUD_NAME,
    api_key=CLOUDINARY_API_KEY,
    api_secret=CLOUDINARY_API_SECRET,
)

# ClickUp: approved properties are pushed to a board. Calling .format() on the
# raw getenv result meant that without CLICKUP_URL set, settings raised
# AttributeError at import and no management command would run at all.
CLICKUP_BASE_URL = os.getenv("CLICKUP_URL")
LIST_ID = os.getenv("LIST_ID")
CLICKUP_URL = CLICKUP_BASE_URL.format(list_id=LIST_ID) if CLICKUP_BASE_URL else None
TEAM_ID = os.getenv("TEAM_ID")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
ZILLOW_CUSTOM_ID = os.getenv("ZILLOW_CUSTOM_ID")
PROPERTY_STATUS_CUSTOM_ID = os.getenv("PROPERTY_STATUS_CUSTOM_ID")
# ClickUp dropdown option ids. These differ per board and were hardcoded UUIDs
# in the task, with only the "Approved" one configurable.
APPROVED = os.getenv("APPROVED")
REJECTED = os.getenv("CLICKUP_OPTION_REJECTED")
CALL_BACK = os.getenv("CLICKUP_OPTION_CALL_BACK")
OWNER_APPROVAL = os.getenv("CLICKUP_OPTION_OWNER_APPROVAL")
ON_HOLD = os.getenv("CLICKUP_OPTION_ON_HOLD")
SEE_NOTES = os.getenv("CLICKUP_OPTION_SEE_NOTES")
FOR_CHI_ONLY = os.getenv("CLICKUP_OPTION_FOR_CHI_ONLY")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

# This was logging.basicConfig() at import time, writing lease_analysis.log
# relative to whatever the working directory happened to be, and hijacking the
# root logger for anything that imported settings.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.db.backends": {"level": "INFO", "handlers": ["console"], "propagate": False},
    },
}
