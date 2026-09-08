"""act — Django settings. Config via environment (see .env.example)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("ACT_SECRET_KEY", "dev-only-insecure-key")
DEBUG = os.environ.get("ACT_DEBUG", "1") == "1"
ALLOWED_HOSTS = [h for h in os.environ.get("ACT_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]
CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if h not in ("localhost", "127.0.0.1")]

# Which public apps this deployment serves (routes + admin). Models of every app
# are always installed so one database can back several deployments.
ALL_ACT_APPS = ["campaigns", "letters"]
ENABLED_APPS = [a.strip() for a in os.environ.get("ACT_APPS", ",".join(ALL_ACT_APPS)).split(",") if a.strip()]
_unknown = set(ENABLED_APPS) - set(ALL_ACT_APPS)
if _unknown or not ENABLED_APPS:
    raise RuntimeError(f"ACT_APPS must be a comma list from {ALL_ACT_APPS}, got {ENABLED_APPS!r}")

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

# Served under a path prefix (e.g. demos.linkedtrust.us/act/): ACT_BASE_PATH=/act
BASE_PATH = os.environ.get("ACT_BASE_PATH", "").rstrip("/")
FORCE_SCRIPT_NAME = BASE_PATH or None
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
# Behind Caddy/nginx that terminate TLS: cookies over https only when not in dev.
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

STATIC_URL = f"{BASE_PATH}/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Hashed static filenames in production so nginx can cache /static/ for a long
# time and a changed CSS or font still reaches every browser at once.
# (Run tests with ACT_DEBUG=1: the manifest only exists after collectstatic.)
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage" if DEBUG
                    else "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"},
}
MEDIA_URL = f"{BASE_PATH}/media/"
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
