"""Resolve a port name to the adapter the environment selected.

    lender = get_port("lender")
    lender.submit_application(application_id)

Callers never name an adapter. Switching a port from `manual` to a live
integration is one environment variable (see settings.PORT_ADAPTERS) and a new
entry in ADAPTERS below - no change to any caller.
"""

from __future__ import annotations

from django.conf import settings

from ports.base import Adapter
from ports.carrier import ManualCarrierAdapter
from ports.customs import ManualCustomsAdapter
from ports.insurer import ManualInsurerAdapter
from ports.lender import ManualLenderAdapter
from ports.sensor import ManualSensorAdapter

ADAPTERS: dict[str, dict[str, type[Adapter]]] = {
    "lender": {"manual": ManualLenderAdapter},
    "insurer": {"manual": ManualInsurerAdapter},
    "carrier": {"manual": ManualCarrierAdapter},
    "customs": {"manual": ManualCustomsAdapter},
    "sensor": {"manual": ManualSensorAdapter},
}


class UnknownPort(KeyError):
    pass


class UnknownAdapter(KeyError):
    pass


def get_port(port: str) -> Adapter:
    try:
        available = ADAPTERS[port]
    except KeyError:
        raise UnknownPort(
            f"{port!r} is not a port; expected one of {sorted(ADAPTERS)}"
        ) from None

    selected = settings.PORT_ADAPTERS.get(port, "manual")
    try:
        return available[selected]()
    except KeyError:
        raise UnknownAdapter(
            f"{port!r} has no {selected!r} adapter; available: {sorted(available)}"
        ) from None
