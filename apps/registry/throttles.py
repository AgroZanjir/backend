"""Limits on the doors.

Every other endpoint is bounded by a session. These are the endpoints that
issue one, so they are bounded by nothing else, and an unbounded sign-in is a
password guesser's whole afternoon.

Two keys, because the two attacks are different shapes:

* **by address** stops one machine working through a dictionary;
* **by username** stops a spread-out attempt on one account from a botnet,
  where every request comes from a different address and the address limit
  never fires.

The counters live in Django's cache. The default is per-process memory, which
means the limits are per gunicorn worker - three workers, three times the
limit. A deployment that means these numbers sets `CACHES` to something
shared; backend/README.md says so beside the setting.
"""

from __future__ import annotations

import hashlib

from rest_framework.throttling import SimpleRateThrottle


class SignInAddressThrottle(SimpleRateThrottle):
    scope = "signin-address"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class SignInAccountThrottle(SimpleRateThrottle):
    """Per account named, not per account that exists.

    A name nobody holds is counted too: answering a guess at a real username
    more slowly than a guess at an invented one is how an attacker learns
    which names are real.
    """

    scope = "signin-account"

    def get_cache_key(self, request, view):
        data = request.data if isinstance(request.data, dict) else {}
        named = str(data.get("username") or data.get("persona") or "").strip().lower()
        if not named:
            return None
        # Hashed so the cache - which is shared infrastructure, and in Redis is
        # readable by anything holding the connection - never holds a list of
        # who has been trying to sign in.
        digest = hashlib.sha256(named.encode("utf-8")).hexdigest()[:32]
        return self.cache_format % {"scope": self.scope, "ident": digest}
