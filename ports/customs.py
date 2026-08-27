"""CustomsPort - export declarations and certificate lodgement.

Every document has an issuer and an expiry; this port is where the expiry
matters most, because a certificate that lapses in transit strands a shipment.
"""

from __future__ import annotations

from typing import Any, Protocol

from ports.base import ManualAdapter, PortResult


class CustomsPort(Protocol):
    def lodge_declaration(self, export_contract_id: str, **context: Any) -> PortResult: ...

    def submit_certificate(self, document_id: str, **context: Any) -> PortResult: ...

    def fetch_clearance_status(self, declaration_reference: str) -> PortResult: ...


class ManualCustomsAdapter(ManualAdapter):
    def lodge_declaration(self, export_contract_id: str, **context: Any) -> PortResult:
        return self._hand_off(
            "Declaration", export_contract_id=export_contract_id, **context
        )

    def submit_certificate(self, document_id: str, **context: Any) -> PortResult:
        return self._hand_off("Certificate submission", document_id=document_id, **context)

    def fetch_clearance_status(self, declaration_reference: str) -> PortResult:
        return PortResult(
            accepted=True,
            state="pending_operator",
            detail="Clearance status is recorded by the broker-facing operator.",
            payload={"declaration_reference": declaration_reference},
        )
