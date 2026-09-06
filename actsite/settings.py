"""act — Django settings. Config via environment (see .env.example)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("ACT_SECRET_KEY", "dev-only-insecure-key")
DEBUG = os.environ.get("ACT_DEBUG", "1") == "1"
ALLOWED_HOSTS = [h for h in os.environ.get("ACT_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]
CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if h not in ("localhost", "127.0.0.1")]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "campaigns",
    "letters",
]

# LinkedTrust SSO — enabled only when client credentials are configured.
LINKEDTRUST_CLIENT_ID = os.environ.get("LINKEDTRUST_CLIENT_ID", "")
LINKEDTRUST_CLIENT_SECRET = os.environ.get("LINKEDTRUST_CLIENT_SECRET", "")
LINKEDTRUST_SSO_ENABLED = bool(LINKEDTRUST_CLIENT_ID and LINKEDTRUST_CLIENT_SECRET)
if LINKEDTRUST_SSO_ENABLED:
    INSTALLED_APPS.append("linkedtrust_auth")
    LINKEDTRUST_URL = "https://live.linkedtrust.us"  # live issuer only — never dev
    LINKEDTRUST_FRONTEND_URL = os.environ.get("ACT_PUBLIC_URL", "http://localhost:8000")
    LINKEDTRUST_FRONTEND_CALLBACK = "/oauth/callback"
    LINKEDTRUST_USER_HANDLER = "campaigns.auth.get_or_create_user"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "actsite.urls"
WSGI_APPLICATION = "actsite.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]

if os.environ.get("ACT_PG_DB"):
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["ACT_PG_DB"],
        "USER": os.environ.get("ACT_PG_USER", ""),
        "PASSWORD": os.environ.get("ACT_PG_PASSWORD", ""),
        "HOST": os.environ.get("ACT_PG_HOST", "10.0.0.100"),
        "PORT": os.environ.get("ACT_PG_PORT", "5432"),
    }}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/admin/login/"

PUBLIC_URL = os.environ.get("ACT_PUBLIC_URL", "http://localhost:8000")

# Outgoing mail (signature confirmations). Unset EMAIL_HOST means nothing is
# sent; each attempt is still logged for the admin (letters.mail).
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "1") == "1"
EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "0") == "1"
EMAIL_TIMEOUT = 15
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "")
