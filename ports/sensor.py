"""SensorPort - temperature and humidity readings into `condition_reading`.

Phase 1 imports CSV exports from handheld loggers; a live adapter streams from
gateways later. Readings arrive with a scope (a storage zone or a shipment) and
are idempotent on (sensor_id, ts) so a re-imported file cannot double-count.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from ports.base import ManualAdapter, PortResult


class SensorPort(Protocol):
    def ingest(
        self, scope_type: str, scope_id: str, readings: Iterable[dict[str, Any]]
    ) -> PortResult:
        """Each reading: sensor_id, ts, temp_c, rh_pct."""

    def list_sensors(self, scope_type: str, scope_id: str) -> PortResult: ...


class ManualSensorAdapter(ManualAdapter):
    """CSV / spreadsheet import, driven from the admin."""

    def ingest(
        self, scope_type: str, scope_id: str, readings: Iterable[dict[str, Any]]
    ) -> PortResult:
        rows = list(readings)
        return self._hand_off(
            "Reading batch",
            scope_type=scope_type,
            scope_id=scope_id,
            row_count=len(rows),
        )

    def list_sensors(self, scope_type: str, scope_id: str) -> PortResult:
        return PortResult(
            accepted=True,
            state="pending_operator",
            detail="Sensors are whatever the operator registered against the scope.",
            payload={"scope_type": scope_type, "scope_id": scope_id},
        )
