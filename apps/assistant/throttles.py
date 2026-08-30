"""Two rate limits on the one endpoint that costs money per request.

Every other endpoint in this platform is bounded by a session: to spend the
server's time you first have to have an account. The assistant is not - it is
on the public website, open to anybody, and each question is a paid call to a
model. So it gets its own limits, and it gets two of them:

* a **burst** limit, which stops one person holding the enter key;
* an **hourly** limit, which stops one person spending an afternoon on it.

Keyed on the address in every case, signed in or not. `AnonRateThrottle` lets
an authenticated caller through untouched, and an operator with a session is no
cheaper to answer than a stranger.

The counters live in Django's cache. The default is per-process memory, which
means the limits are per gunicorn worker - three workers, three times the
limit. A deployment that means these numbers sets `CACHES` to something shared;
`backend/README.md` says so beside the setting.
"""

from __future__ import annotations

from rest_framework.throttling import SimpleRateThrottle


class _AddressThrottle(SimpleRateThrottle):
    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class AssistantBurstThrottle(_AddressThrottle):
    scope = "assistant-burst"


class AssistantHourThrottle(_AddressThrottle):
    scope = "assistant-hour"


class AssistantPanelThrottle(SimpleRateThrottle):
    """The panels' assistant, keyed on the person rather than the address.

    An operator behind an office NAT would otherwise share one bucket with
    every colleague on the same line, which is the sort of limit that reads as
    a broken feature rather than as a limit. Keyed on the account, it is a
    limit on one person's afternoon - which is what it is for.
    """

    scope = "assistant-panel"

    def get_cache_key(self, request, view):
        user = request.user
        ident = (
            user.pk
            if user and user.is_authenticated
            else self.get_ident(request)
        )
        return self.cache_format % {"scope": self.scope, "ident": ident}
