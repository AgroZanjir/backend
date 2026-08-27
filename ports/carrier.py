"""CarrierPort - reefer transport booking and tracking."""

from __future__ import annotations

from typing import Any, Protocol

from ports.base import ManualAdapter, PortResult


class CarrierPort(Protocol):
    def request_booking(self, shipment_id: str, **context: Any) -> PortResult: ...

    def fetch_tracking(self, shipment_id: str) -> PortResult: ...

    def confirm_delivery(self, shipment_id: str, **context: Any) -> PortResult: ...


class ManualCarrierAdapter(ManualAdapter):
    def request_booking(self, shipment_id: str, **context: Any) -> PortResult:
        return self._hand_off("Booking request", shipment_id=shipment_id, **context)

    def fetch_tracking(self, shipment_id: str) -> PortResult:
        return PortResult(
            accepted=True,
            state="pending_operator",
            detail="Tracking is keyed in from the carrier's messages.",
            payload={"shipment_id": shipment_id},
        )

    def confirm_delivery(self, shipment_id: str, **context: Any) -> PortResult:
        return self._hand_off("Delivery confirmation", shipment_id=shipment_id, **context)
