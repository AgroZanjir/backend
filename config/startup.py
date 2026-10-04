"""Bounded dependency wait before checks and migrations. Never print connection URLs."""

import os
import sys
import time

import django


def main():
    django.setup()
    from django.db import connection

    from apps.common.checks import production_configuration
    from apps.common.readiness import dependency_status

    errors = production_configuration(None)
    if errors:
        for error in errors:
            print(f"{error.id}: {error.msg}", file=sys.stderr)
        return 1

    deadline = time.monotonic() + int(os.environ.get("STARTUP_TIMEOUT", "60"))
    while True:
        status = dependency_status()
        if all(value == "ok" for value in status.values()):
            return 0
        connection.close()
        if time.monotonic() >= deadline:
            print("Database/cache readiness timed out; inspect service health.", file=sys.stderr)
            return 1
        print("Waiting for database and cache...", flush=True)
        time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())
