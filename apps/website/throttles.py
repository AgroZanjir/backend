"""Limits on the one thing a stranger can write to.

Keyed on the address, signed in or not - the form is on the open internet and
an authenticated caller is no more entitled to fill a table with it. The
counters live in the cache, so a deployment that means these numbers gives
Django a shared one; see the note in apps/assistant/throttles.py.
"""

from __future__ import annotations

from rest_framework.throttling import SimpleRateThrottle


class _AddressThrottle(SimpleRateThrottle):
    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class EnquiryBurstThrottle(_AddressThrottle):
    scope = "enquiry-burst"


class EnquiryDayThrottle(_AddressThrottle):
    scope = "enquiry-day"
