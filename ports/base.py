"""Shared vocabulary for the five ports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PortResult:
    """What every adapter returns.

    `pending` is the normal outcome for a manual adapter: the request has been
    recorded and an operator will resolve it from the admin. Callers must treat
    it as success-so-far, never as failure - that is the whole point of the
    seam.
    """

    accepted: bool
    state: str
    reference: str | None = None
    detail: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def pending(cls, detail: str, **payload: Any) -> "PortResult":
        return cls(accepted=True, state="pending_operator", detail=detail, payload=payload)


class Adapter:
    """Base class for adapters. `name` is what settings.PORT_ADAPTERS selects."""

    name: str = "base"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} name={self.name!r}>"


class ManualAdapter(Adapter):
    """Records the request and hands it to a human.

    Subclasses exist per port so the admin can show the right screen; the
    behaviour is identical - nothing is sent anywhere, the state waits for an
    operator.
    """

    name = "manual"

    def _hand_off(self, what: str, **payload: Any) -> PortResult:
        return PortResult.pending(
            f"{what} recorded; an operator resolves it from the admin.", **payload
        )
