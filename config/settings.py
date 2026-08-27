"""
Django settings for Agro Zanjir Digital.

Section numbers in comments refer to the Agro Zanjir Architecture blueprint.
Configuration is read from the environment (see .env.example); nothing
deployment-specific is hard-coded here.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CORS_ALLOWED_ORIGINS=(list, ["http://localhost:5173"]),
)
environ.Env.read_env(BASE_DIR / ".env")

# 32+ bytes even in development: PyJWT warns below that, and a warning
# nobody can act on is a warning everybody learns to ignore.
SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="dev-only-not-for-production-do-not-deploy-with-this-key",
)
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# --- applications -----------------------------------------------------------

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "corsheaders",
    "drf_spectacular",
]

# Section 03 - the six clusters plus the lot spine. Each app owns one cluster
# and references `lots` only; clusters never reference each other.
LOCAL_APPS = [
    "apps.common",
    "apps.registry",     # user, party, farm, product, roles, verification
    "apps.lots",         # lot, lot_event, lot_relation  <- the spine
    "apps.quality",      # qc_record, pilot_trial, trial_observation
    "apps.storage",      # facility, storage_zone, storage_placement, condition_reading
    "apps.commercial",   # offtake_contract, export_contract, shipment
    "apps.finance",      # finance_application, encumbrance, policy, claim, settlement
    "apps.documents",    # document vault
    "apps.governance",   # audit log, data-sharing grants
    "apps.panels",       # the cross-cluster read composition the panels are served by
]

# Identity is OneID's; this row is the local shadow of a person. Declared now
# because swapping the user model later means rebuilding every migration that
# ever pointed at it.
AUTH_USER_MODEL = "registry.User"

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
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
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- database ---------------------------------------------------------------

# PostgreSQL is the target (section 05: JSONB for per-product QC specs, RLS for
# party-scoped access, real transactions for money and liens). SQLite is the
# fallback so the shell boots before anyone has provisioned a database.
DATABASES = {
    "default": env.db_url(
        "DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- i18n and time ----------------------------------------------------------

# Section 03: store UTC, render Asia/Tashkent. Harvest and delivery windows are
# dates, not instants - keep them as DateField.
LANGUAGE_CODE = "uz"
LANGUAGES = [("uz", "O'zbekcha"), ("ru", "Russkiy"), ("en", "English")]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "UTC"
DISPLAY_TIME_ZONE = "Asia/Tashkent"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- API --------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "PAGE_SIZE": 50,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Agro Zanjir Digital API",
    "DESCRIPTION": "Lot registry, event log, quality, storage, logistics, finance.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
}

CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")
# The refresh token travels as a cookie, so the browser must be allowed to send
# it: `fetch(..., {credentials: "include"})` is refused without this.
CORS_ALLOW_CREDENTIALS = True

# --- tokens -----------------------------------------------------------------

# Short-lived access token held in memory by the client, long-lived refresh
# token in an httpOnly cookie it cannot read. A token in localStorage is a
# finding in any bank's security review, and this platform faces banks.
from datetime import timedelta  # noqa: E402  (kept beside the setting it configures)

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("ACCESS_TOKEN_MINUTES", default=30)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("REFRESH_TOKEN_DAYS", default=7)),
    "ROTATE_REFRESH_TOKENS": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

REFRESH_COOKIE = {
    "name": "az_refresh",
    "path": "/api/v1/auth/",
    "samesite": "Lax",
    # Set SESSION_COOKIE_SECURE-style behaviour from the environment: over
    # plain http in development the browser drops a Secure cookie silently.
    "secure": env.bool("REFRESH_COOKIE_SECURE", default=not DEBUG),
    "max_age": env.int("REFRESH_TOKEN_DAYS", default=7) * 24 * 3600,
}

# --- OneID (section 04, identity) -------------------------------------------

# OneID is a port like any other. Until the integration exists, `stub` resolves
# a persona to the demo user of the same name and says so in the response; the
# sign-in screen shows that banner. Nothing else in the system knows which
# adapter answered.
ONEID_ADAPTER = env("ONEID_ADAPTER", default="stub")

# --- ports (section 04) -----------------------------------------------------

# Every external relationship is a port with a swappable adapter. "manual"
# means an operator keys the decision in from the admin; swapping in a live
# adapter changes nothing else in the system.
PORT_ADAPTERS = {
    "lender": env("LENDER_ADAPTER", default="manual"),
    "insurer": env("INSURER_ADAPTER", default="manual"),
    "carrier": env("CARRIER_ADAPTER", default="manual"),
    "customs": env("CUSTOMS_ADAPTER", default="manual"),
    "sensor": env("SENSOR_ADAPTER", default="manual"),
}
