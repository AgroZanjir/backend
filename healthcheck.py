"""Probe the HTTP server, database and shared cache without curl in the image."""

import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main():
    request = Request(
        "http://127.0.0.1:8000/api/v1/health/",
        headers={"Host": "localhost", "X-Forwarded-Proto": "https"},
    )
    try:
        with urlopen(request, timeout=4) as response:
            body = json.load(response)
            healthy = response.status == 200 and body.get("database") == body.get("cache") == "ok"
    except (HTTPError, URLError, OSError, ValueError):
        healthy = False
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
