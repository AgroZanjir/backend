"""Settings used only for collecting static assets into the Docker image."""

import os

os.environ["DJANGO_READ_DOT_ENV"] = "false"

from .settings import *  # noqa: F403, E402

SECRET_KEY = "static-build-only-configuration-never-used-for-serving-requests"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
ASSISTANT_ADAPTER = "off"
ASSISTANT_API_KEY = ""
ONEID_ADAPTER = "disabled"
