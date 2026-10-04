"""Isolated test settings; CI supplies disposable PostgreSQL and Redis URLs."""

import os

os.environ["DJANGO_READ_DOT_ENV"] = "false"

from .settings import *  # noqa: F403, E402

SECRET_KEY = "ci-only-secret-configuration-never-used-for-production-requests"
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0
ONEID_ADAPTER = "stub"
ASSISTANT_API_KEY = ""
ASSISTANT_ADAPTER = "auto"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
if not os.environ.get("DATABASE_URL"):
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
if not os.environ.get("CACHE_URL"):
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
