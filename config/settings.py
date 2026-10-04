import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("SECRET_KEY", "wallet-interview-demo-only")
DEBUG = os.environ.get("DEBUG", "0") == "1"
ALLOWED_HOSTS = ["*"]
SERVICE_ROLE = os.environ.get("SERVICE_ROLE", "wallet")
ROOT_URLCONF = "provider.urls" if SERVICE_ROLE == "provider" else "wallets.urls"
WSGI_APPLICATION = "config.wsgi.application"
INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    "wallets",
    "provider",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.common.CommonMiddleware",
]
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {},
    }
]
USE_TZ = True
TIME_ZONE = "UTC"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
APPEND_SLASH = False


def database(name):
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": name,
        "USER": os.environ.get("PGUSER", "wallet"),
        "PASSWORD": os.environ.get("PGPASSWORD", "wallet"),
        "HOST": os.environ.get("PGHOST", "127.0.0.1"),
        "PORT": os.environ.get("PGPORT", "55432"),
        "CONN_MAX_AGE": 0,
        "OPTIONS": {"connect_timeout": 5},
    }


DATABASES = {
    "default": database(os.environ.get("PGDATABASE", "wallet")),
    "provider": database(os.environ.get("PROVIDER_DATABASE", "provider")),
}
DATABASE_ROUTERS = ["config.routers.ServiceRouter"]
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "EXCEPTION_HANDLER": "common.errors.exception_handler",
}
SPECTACULAR_SETTINGS = {
    "TITLE": "Provider simulator" if SERVICE_ROLE == "provider" else "Auditable Wallet",
    "DESCRIPTION": "Interview demo. Whole IRR amounts are decimal strings. Demo user/account IDs; no authentication.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "COMPONENT_SPLIT_REQUEST": True,
}
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
PROVIDER_URL = os.environ.get("PROVIDER_URL", "http://127.0.0.1:8001")
PLATFORM_ACCOUNT = os.environ.get("PLATFORM_ACCOUNT", "platform-account")
PROVIDER_TIMEOUT = float(os.environ.get("PROVIDER_TIMEOUT", "5"))
PROVIDER_DELAY = float(os.environ.get("PROVIDER_DELAY", "2"))
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://127.0.0.1:56379/0")
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_DEFAULT_QUEUE = os.environ.get("CELERY_QUEUE", "celery")
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_CONNECTION_TIMEOUT = 3
CELERY_BEAT_SCHEDULE = {
    "scan-pending-top-ups": {
        "task": "wallets.dispatch_pending_top_ups",
        "schedule": float(os.environ.get("POLL_INTERVAL", "10")),
        "options": {"expires": 30},
    },
}
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
