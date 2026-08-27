"""InsurerPort - cover over stored lots and shipments, and claims against it.

A claim is only as good as its evidence: the excursion record, the condition
readings around it and the lot event log. The port carries the bundle; the
platform builds it.
"""

from __future__ import annotations

from typing import Any, Protocol

from ports.base import ManualAdapter, PortResult


class InsurerPort(Protocol):
    def quote(self, subject_type: str, subject_id: str, **context: Any) -> PortResult: ...

    def bind_policy(self, quote_reference: str, **context: Any) -> PortResult: ...

    def file_claim(self, policy_id: str, evidence_bundle: dict[str, Any]) -> PortResult:
        """`evidence_bundle` is excursions plus the readings and events proving them."""

    def fetch_claim_status(self, claim_id: str) -> PortResult: ...


class ManualInsurerAdapter(ManualAdapter):
    def quote(self, subject_type: str, subject_id: str, **context: Any) -> PortResult:
        return self._hand_off(
            "Quote request", subject_type=subject_type, subject_id=subject_id, **context
        )

    def bind_policy(self, quote_reference: str, **context: Any) -> PortResult:
        return self._hand_off("Policy binding", quote_reference=quote_reference, **context)

    def file_claim(self, policy_id: str, evidence_bundle: dict[str, Any]) -> PortResult:
        return self._hand_off(
            "Claim", policy_id=policy_id, evidence_items=len(evidence_bundle)
        )

    def fetch_claim_status(self, claim_id: str) -> PortResult:
        return PortResult(
            accepted=True,
            state="pending_operator",
            detail="Status is whatever the operator last recorded.",
            payload={"claim_id": claim_id},
        )
