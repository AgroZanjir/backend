"""LenderPort - inventory finance against a lot (sections 04, 03/FINANCE).

Phase 1 runs the manual adapter: the operator keys the bank's decision in after
a phone call. Phase 3 swaps in a REST / ISO 20022 adapter. `finance_application`
and the domain logic around it are identical in both.
"""

from __future__ import annotations

from typing import Any, Protocol

from ports.base import ManualAdapter, PortResult


class LenderPort(Protocol):
    def submit_application(self, application_id: str, **context: Any) -> PortResult:
        """Send a credit request secured against one or more lots."""

    def fetch_decision(self, application_id: str) -> PortResult:
        """Poll for approval, rejection or a request for more evidence."""

    def register_lien(self, application_id: str, lot_id: str, amount_minor: int) -> PortResult:
        """Tell the lender a lien now exists. Blocks dispatch until released."""

    def release_lien(self, encumbrance_id: str) -> PortResult:
        """Release is an event, never a deletion of the encumbrance row."""


class ManualLenderAdapter(ManualAdapter):
    def submit_application(self, application_id: str, **context: Any) -> PortResult:
        return self._hand_off(
            "Finance application", application_id=application_id, **context
        )

    def fetch_decision(self, application_id: str) -> PortResult:
        return PortResult(
            accepted=True,
            state="pending_operator",
            detail="No automated decision source; read the current state from the record.",
            payload={"application_id": application_id},
        )

    def register_lien(self, application_id: str, lot_id: str, amount_minor: int) -> PortResult:
        return self._hand_off(
            "Lien registration",
            application_id=application_id,
            lot_id=lot_id,
            amount_minor=amount_minor,
        )

    def release_lien(self, encumbrance_id: str) -> PortResult:
        return self._hand_off("Lien release", encumbrance_id=encumbrance_id)
