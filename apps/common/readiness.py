"""Small readiness check shared by HTTP health and container startup."""

import logging
import uuid

from django.core.cache import cache
from django.db import connection

logger = logging.getLogger(__name__)


def dependency_status():
    result = {"database": "ok", "cache": "ok"}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        # Connection exceptions can contain hosts/usernames; never return them.
        logger.warning("Database readiness check failed")
        result["database"] = "unavailable"
    try:
        key = f"readiness:{uuid.uuid4().hex}"
        cache.set(key, "ok", timeout=10)
        if cache.get(key) != "ok":
            raise RuntimeError("Cache round-trip failed")
        cache.delete(key)
    except Exception:
        logger.warning("Cache readiness check failed")
        result["cache"] = "unavailable"
    return result
