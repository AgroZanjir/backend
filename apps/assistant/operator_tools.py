"""What the assistant may read on behalf of somebody who is signed in.

The public tools in `tools.py` take no user because there is nothing to scope:
they call the passport builder in its public mode, which is open to anybody.
These are the opposite. Every one of them takes the caller and narrows on them
before it builds anything, and it does that through the **same** helpers the
panel views use - `visible_lots`, `memberships_of`, `is_platform`. One place to
get scoping right, not two: an assistant with its own idea of who may see what
is how a bank ends up reading a farm's valuation.

Three rules this module keeps, and each one is a test:

* **Scope before serialise.** The queryset is narrowed first, always.
* **A read of a passport is audited.** `apps/common/api.audit` is called with
  the operator as the actor, exactly as `panel_lot_passport` calls it. "Who
  looked at my lot" is a question this platform promises to answer, and being
  asked through an assistant is not an exemption - if anything it is the case
  the log most needs to carry.
* **Where a panel screen is looser than this, this wins.** The lien register
  view returns every lien to any authenticated caller; the lien tool here
  returns liens over lots the caller can already see. Narrower than the screen
  is a defect worth having; wider than the screen is a breach.
"""

from __future__ import annotations

from typing import Any

MAX_ROWS = 40


def definitions() -> list[dict[str, Any]]:
    """The tool list. A fixed order: it is part of the cached prefix."""
    return [
        {
            "name": "find_lots",
            "description": (
                "Search the lots this person may see and return one row each - "
                "produce, grade, status, weight, storage zone, sell-by date, "
                "whether a lien is over it, and its valuation. Use it for any "
                "question about more than one lot, and for counting, totalling "
                "or ranking them. Returns at most 40 rows, newest first, with "
                "the true total alongside so you can say when there are more."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": (
                            "Comma-separated lot statuses to keep: registered, "
                            "graded, stored, reserved, dispatched, settled, "
                            "rejected, written_off. Empty for any."
                        ),
                    },
                    "product": {
                        "type": "string",
                        "description": "Product code, e.g. melon. Empty for any.",
                    },
                    "zone": {
                        "type": "string",
                        "description": "Storage zone code. Empty for any.",
                    },
                    "pledged": {
                        "type": "string",
                        "description": (
                            "'yes' for lots under an active lien, 'no' for lots "
                            "free of one, empty for both."
                        ),
                    },
                    "expiring_within_days": {
                        "type": "integer",
                        "description": (
                            "Keep only lots whose sell-by date falls within this "
                            "many days. 0 for no date filter."
                        ),
                    },
                },
                "required": [
                    "status",
                    "product",
                    "zone",
                    "pledged",
                    "expiring_within_days",
                ],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "name": "lot_passport",
            "description": (
                "The full passport for one lot: its origin, every event in its "
                "hash-chained log, its quality checks, its documents, any lien "
                "or policy over it, its cold-chain excursions and whether the "
                "chain verifies. Use it for one named lot, never for a survey. "
                "This read is written to the audit log with the operator as the "
                "actor, so do not call it speculatively."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "The lot code."}
                },
                "required": ["code"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "name": "find_zones",
            "description": (
                "The storage zones this person's organisations operate, with "
                "capacity, what is actually in them, live temperature and "
                "humidity, both set points, and whether the room is off band. "
                "Use it for questions about space, conditions or where "
                "something can be put away."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "facility": {
                        "type": "string",
                        "description": "Facility code. Empty for all of them.",
                    }
                },
                "required": ["facility"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "name": "find_liens",
            "description": (
                "Liens over the lots this person may see, with the holder, the "
                "amount and whether the lien is still active. A lien is an "
                "overlay, never a status: a pledged lot is still stored, still "
                "reserved and still shippable, and simply cannot leave without "
                "the lender's release. Use it for anything about what is "
                "blocking a dispatch."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "active_only": {
                        "type": "string",
                        "description": "'yes' for unreleased liens only, 'no' for all.",
                    }
                },
                "required": ["active_only"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    ]


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"yes", "true", "1"}


def _find_lots(request, payload: dict[str, Any]) -> dict[str, Any]:
    from datetime import timedelta

    from django.db.models import Q
    from django.utils import timezone

    from apps.finance.models import Encumbrance
    from apps.panels.views import _lot_rows, visible_lots

    visible = visible_lots(request.user)

    statuses = [s.strip() for s in str(payload.get("status") or "").split(",") if s.strip()]
    if statuses:
        visible = visible.filter(status__in=statuses)

    product = str(payload.get("product") or "").strip()
    if product:
        visible = visible.filter(product__code=product)

    zone = str(payload.get("zone") or "").strip()
    if zone:
        visible = visible.filter(
            placements__zone__code=zone, placements__removed_at__isnull=True
        )

    days = payload.get("expiring_within_days") or 0
    if isinstance(days, int) and days > 0:
        visible = visible.filter(
            sell_by__isnull=False,
            sell_by__lte=timezone.localdate() + timedelta(days=days),
        )

    pledged = str(payload.get("pledged") or "").strip().lower()
    if pledged in {"yes", "no"}:
        under_lien = Q(
            id__in=Encumbrance.objects.filter(released_at__isnull=True).values("lot_id")
        )
        visible = visible.filter(under_lien) if pledged == "yes" else visible.exclude(under_lien)

    visible = visible.distinct().order_by("-harvested_on", "code")
    total = visible.count()
    return {
        "total": total,
        # Said plainly rather than left for the model to infer from a count:
        # "your twelve lots" when there are ninety is the kind of wrong that
        # gets acted on.
        "truncated": total > MAX_ROWS,
        "results": _lot_rows(visible[:MAX_ROWS]),
    }


def _lot_passport(request, payload: dict[str, Any]) -> dict[str, Any]:
    from apps.common.api import audit
    from apps.panels.views import _passport, visible_lots

    code = str(payload.get("code", "")).strip()
    lot = visible_lots(request.user).filter(code=code).first()
    if lot is None:
        return {
            "error": "not_found",
            "detail": (
                f"No lot with the code {code!r} is visible to you. It may not "
                "exist, or it may belong to an organisation you have no "
                "relationship with."
            ),
        }

    # The same line `panel_lot_passport` writes, with the capability it was
    # exercised under. `assistant=True` is what lets an auditor tell a read
    # made through the panel from one made through a question.
    audit(
        request,
        "a_viewed",
        object_ref=lot.code,
        capability="view",
        assistant=True,
    )
    return _passport(lot, public=False)


def _find_zones(request, payload: dict[str, Any]) -> dict[str, Any]:
    from apps.common.api import is_platform, party_ids_of
    from apps.panels import serializers as build
    from apps.panels.views import _zone_queryset

    zones = _zone_queryset()
    if not is_platform(request.user):
        zones = zones.filter(facility__operator_party_id__in=party_ids_of(request.user))

    facility = str(payload.get("facility") or "").strip()
    if facility:
        zones = zones.filter(facility__code=facility)

    rows = list(zones[:MAX_ROWS])
    return {"total": len(rows), "results": [build.zone_payload(z) for z in rows]}


def _find_liens(request, payload: dict[str, Any]) -> dict[str, Any]:
    from apps.finance.models import Encumbrance
    from apps.panels import serializers as build
    from apps.panels.views import visible_lots

    # Deliberately narrower than the lien register screen, which returns every
    # lien to any authenticated caller. `visible_lots` already admits a lender
    # through its own lien, so a bank still sees its whole book.
    liens = Encumbrance.objects.filter(
        lot__in=visible_lots(request.user)
    ).select_related("lot", "application", "holder_party").order_by("-created_on")

    if _truthy(payload.get("active_only")):
        liens = liens.filter(released_at__isnull=True)

    rows = list(liens[:MAX_ROWS])
    return {"total": len(rows), "results": [build.lien_payload(lien) for lien in rows]}


RUNNERS = {
    "find_lots": _find_lots,
    "lot_passport": _lot_passport,
    "find_zones": _find_zones,
    "find_liens": _find_liens,
}


def run(request, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Execute one tool for a signed-in caller. Never raises.

    A tool that raises ends the turn on a spinner. A tool that returns an
    `error` lets the model say what went wrong, which is nearly always more
    useful than silence.
    """
    runner = RUNNERS.get(name)
    if runner is None:  # pragma: no cover - the model can only call what it is given
        return {"error": "unknown_tool", "detail": f"No tool named {name!r}."}
    try:
        return runner(request, dict(payload or {}))
    except Exception as exc:  # noqa: BLE001 - a failed read is an answer, not a crash
        import logging

        logging.getLogger(__name__).exception("Assistant tool %s failed", name)
        return {"error": "failed", "detail": f"That lookup did not complete ({type(exc).__name__})."}
