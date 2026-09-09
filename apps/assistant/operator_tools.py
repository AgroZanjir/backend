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
    """The tool list. A fixed order: it is part of the cached prefix.

    **The filters are optional, and that is load-bearing.** They were all
    `required` under `strict: true` at first, on the reading that strict mode
    wants every property listed. It does not want it enough to be worth this:
    forced to supply a value for filters it did not want, the model wrote its
    own tool-call syntax into them - `status` came through as
    `'</…parameter>\n<parameter name="product">'` - and a junk status filtered
    every row out. A producer with two lots was told he had none, in a
    confident paragraph, with no error anywhere. An optional filter is one the
    model can leave out, and a filter left out is one that cannot be malformed.

    `lot_passport` keeps `strict`, because its one field is genuinely required
    and there is nothing to leave out.
    """
    return [
        {
            "name": "find_lots",
            "description": (
                "Search the lots this person may see and return one row each - "
                "produce, grade, status, weight, storage zone, sell-by date, "
                "whether a lien is over it, and its valuation. Use it for any "
                "question about more than one lot, and for counting, totalling "
                "or ranking them. Every filter is optional: omit the ones you "
                "do not want rather than passing an empty value. Returns at "
                "most 40 rows, newest first, with the true total alongside so "
                "you can say when there are more."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": (
                            "Comma-separated lot statuses to keep: registered, "
                            "graded, stored, reserved, dispatched, settled, "
                            "rejected, written_off. Note that 'pledged' is not "
                            "one - a lien is an overlay, and `pledged` below is "
                            "how you filter on it. Omit for any status."
                        ),
                    },
                    "product": {
                        "type": "string",
                        "description": "Product code, e.g. melon. Omit for any.",
                    },
                    "zone": {
                        "type": "string",
                        "description": "Storage zone code. Omit for any.",
                    },
                    "pledged": {
                        "type": "string",
                        "enum": ["yes", "no"],
                        "description": (
                            "'yes' for lots under an active lien, 'no' for lots "
                            "free of one. Omit for both."
                        ),
                    },
                    "expiring_within_days": {
                        "type": "integer",
                        "minimum": 1,
                        "description": (
                            "Keep only lots whose sell-by date falls within this "
                            "many days. Omit for no date filter."
                        ),
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
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
                        "description": "Facility code. Omit for all of them.",
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
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
                        "enum": ["yes", "no"],
                        "description": (
                            "'yes' for unreleased liens only. Omit for all."
                        ),
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "find_arrivals",
            "description": (
                "The gate queue: what is expected or being weighed at the "
                "facilities this person's organisations operate, with the farm "
                "it is coming from, the produce, the vehicle and the expected "
                "weight. This is the ONLY thing that answers 'what is at the "
                "gate' - a lot with status 'registered' has already been "
                "through the gate and is not the queue."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "open_only": {
                        "type": "string",
                        "enum": ["yes", "no"],
                        "description": (
                            "'yes' for arrivals still queued or on the "
                            "weighbridge. Omit for all of them."
                        ),
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "find_excursions",
            "description": (
                "Cold-chain excursions - a zone or a shipment going outside its "
                "temperature or humidity band - with the peak, the threshold, "
                "how long it lasted, the sensor, the severity, whether it has "
                "been resolved, and which lots were in scope. Use it for "
                "anything about a breach of conditions, and as the evidence "
                "behind a write-off or an insurance claim."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "scope_code": {
                        "type": "string",
                        "description": "A zone or shipment code. Omit for all.",
                    },
                    "unresolved_only": {
                        "type": "string",
                        "enum": ["yes", "no"],
                        "description": "'yes' for open excursions. Omit for all.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "find_qc",
            "description": (
                "Quality-control records over the lots this person may see: the "
                "stage, who inspected, the measurements taken (Brix, firmness, "
                "calibre and whatever else the produce's spec asks for), the "
                "defect percentage, the grade assigned, whether it passed, and "
                "any laboratory document behind it."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "lot": {"type": "string", "description": "Lot code. Omit for all."},
                    "failed_only": {
                        "type": "string",
                        "enum": ["yes", "no"],
                        "description": "'yes' for checks that did not pass.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "find_trials",
            "description": (
                "The ZEROCO storage trials, with each arm, the lots on it, the "
                "sampling schedule, every observation actually recorded, and "
                "the modelled projection. **`observed_points` is how many "
                "sampling days have really been measured**; everything past "
                "that on any chart is projection and must be described as one. "
                "Trial results are public - this is the same record the website "
                "publishes - so it is not scoped to the caller."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Trial code for the full detail. Omit for a list of all.",
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "find_applications",
            "description": (
                "Finance applications this person's organisations are party to, "
                "as applicant or as lender: the amount, the kind, the status, "
                "the loan-to-value, the collateral lots and where the lender "
                "port has got to. Every port runs its manual adapter today, so "
                "a decision waits for an operator - nothing has been sent to a "
                "bank."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": (
                            "One of draft, submitted, review, approved, "
                            "disbursed, repaid, rejected. Omit for any."
                        ),
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "find_policies",
            "description": (
                "Insurance policies this person's organisations hold or "
                "underwrite: the kind, the period, the sum insured, the "
                "deductible and the lots or shipment covered."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "active_only": {
                        "type": "string",
                        "enum": ["yes", "no"],
                        "description": "'yes' for policies in force. Omit for all.",
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "find_claims",
            "description": (
                "Insurance claims on the policies this person's organisations "
                "hold or underwrite, or over lots they can see: the lot, the "
                "excursion cited as its cause, the amount assessed, the status "
                "and the decision date. Use it for anything about a loss."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": (
                            "One of draft, filed, assessing, approved, paid, "
                            "rejected. Omit for any."
                        ),
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "find_shipments",
            "description": (
                "Shipments this person's organisations are party to - as "
                "carrier, as the exporter behind the contract, or through a lot "
                "on board: origin, destination, vehicle, mode, set point, "
                "departure, ETA, arrival and status. This is the ONLY thing "
                "that answers what is in transit; a lot's status does not carry "
                "a destination and 'dispatched' is not a transit record."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": (
                            "One of planned, loading, in_transit, delivered, "
                            "cancelled. Omit for any."
                        ),
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "find_exports",
            "description": (
                "Export contracts this person's organisations sell under: the "
                "buyer and their country, the produce and quantity, the "
                "incoterm, the payment terms, the ship-by date and the status."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": (
                            "One of draft, signed, shipped, closed, cancelled. "
                            "Omit for any."
                        ),
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "find_organisations",
            "description": (
                "The organisation register: every party, its type, its "
                "verification state and how many people belong to it. "
                "**Platform roles only** - the operator's own staff. Anyone "
                "else gets a refusal, which is correct and not an error to work "
                "around."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "pending_only": {
                        "type": "string",
                        "enum": ["yes", "no"],
                        "description": (
                            "'yes' for organisations still awaiting a decision - "
                            "pending or in review. A rejected organisation has "
                            "been decided and is not included."
                        ),
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    ]


class BadFilter(Exception):
    """A filter value that names nothing.

    Raised rather than quietly ignored, and rather than quietly matching
    nothing. Those are the two tempting options and both are worse:

    * Ignoring it answers a different question from the one asked - "all your
      lots" when the model asked for the stored ones.
    * Matching nothing returns `total: 0`, which is **indistinguishable from a
      true empty result**. That is not theoretical. A malformed `status` did
      exactly this on the first live run and a producer with two lots was told,
      in a confident paragraph, that he had none.

    An error the model can read is the only option that cannot become a
    falsehood. It says what was wrong and what the valid values are, and the
    model asks again.
    """


def _one_of(value: Any, field: str, allowed: set[str]) -> str:
    """A filter value, checked against what exists. Empty means no filter."""
    text = str(value or "").strip()
    if not text:
        return ""
    if text not in allowed:
        raise BadFilter(
            f"{field}={text!r} is not a value this platform uses. "
            f"Valid: {', '.join(sorted(allowed))}. Omit {field} for no filter."
        )
    return text


def _find_lots(request, payload: dict[str, Any]) -> dict[str, Any]:
    from datetime import timedelta

    from django.db.models import Q
    from django.utils import timezone

    from apps.finance.models import Encumbrance
    from apps.lots.models import Lot
    from apps.panels.views import _lot_rows, visible_lots
    from apps.registry.models import Product
    from apps.storage.models import StorageZone

    visible = visible_lots(request.user)
    applied: dict[str, Any] = {}

    statuses = [s.strip() for s in str(payload.get("status") or "").split(",") if s.strip()]
    if statuses:
        allowed = set(Lot.Status.values)
        for status in statuses:
            _one_of(status, "status", allowed)
        visible = visible.filter(status__in=statuses)
        applied["status"] = statuses

    product = _one_of(
        payload.get("product"),
        "product",
        set(Product.objects.values_list("code", flat=True)),
    )
    if product:
        visible = visible.filter(product__code=product)
        applied["product"] = product

    zone = _one_of(
        payload.get("zone"), "zone", set(StorageZone.objects.values_list("code", flat=True))
    )
    if zone:
        visible = visible.filter(
            placements__zone__code=zone, placements__removed_at__isnull=True
        )
        applied["zone"] = zone

    days = payload.get("expiring_within_days") or 0
    if isinstance(days, int) and days > 0:
        visible = visible.filter(
            sell_by__isnull=False,
            sell_by__lte=timezone.localdate() + timedelta(days=days),
        )
        applied["expiring_within_days"] = days

    pledged = _one_of(payload.get("pledged"), "pledged", {"yes", "no"})
    if pledged:
        under_lien = Q(
            id__in=Encumbrance.objects.filter(released_at__isnull=True).values("lot_id")
        )
        visible = visible.filter(under_lien) if pledged == "yes" else visible.exclude(under_lien)
        applied["pledged"] = pledged

    visible = visible.distinct().order_by("-harvested_on", "code")
    total = visible.count()
    return {
        "total": total,
        # Echoed back so the model can see what it actually asked for. An empty
        # result reads very differently beside the filters that produced it,
        # and this is what lets it say "none stored" rather than "none".
        "filters_applied": applied,
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
    from apps.storage.models import Facility

    zones = _zone_queryset()
    if not is_platform(request.user):
        zones = zones.filter(facility__operator_party_id__in=party_ids_of(request.user))

    facility = _one_of(
        payload.get("facility"),
        "facility",
        set(Facility.objects.values_list("code", flat=True)),
    )
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

    if _one_of(payload.get("active_only"), "active_only", {"yes", "no"}) == "yes":
        liens = liens.filter(released_at__isnull=True)

    rows = list(liens[:MAX_ROWS])
    return {"total": len(rows), "results": [build.lien_payload(lien) for lien in rows]}



# --- the rest of the clusters ---------------------------------------------
#
# Each of these was added because the assistant answered a question about it
# from the nearest tool it *did* have, and stated the result as though it fit.
# Asked what was at the gate it listed lots with status `registered`; asked what
# was in transit it said "nothing", having looked at lot statuses, while two
# shipments sat in the shipment table. A missing tool does not read as missing -
# it reads as an answer.


def _party_scope_or_platform(request):
    """`(is_platform, party_ids)` - the two things every scope below needs."""
    from apps.common.api import is_platform, party_ids_of

    return is_platform(request.user), party_ids_of(request.user)


def _rows(queryset, build_row):
    rows = list(queryset[:MAX_ROWS])
    return {"total": len(rows), "results": [build_row(r) for r in rows]}


def _find_arrivals(request, payload):
    from apps.panels import serializers as build
    from apps.storage.models import GateArrival

    platform, parties = _party_scope_or_platform(request)
    arrivals = GateArrival.objects.select_related("facility", "farm", "product")
    if not platform:
        from django.db.models import Q

        # Either you run the yard or it is your produce arriving at it.
        arrivals = arrivals.filter(
            Q(facility__operator_party_id__in=parties) | Q(farm__party_id__in=parties)
        )

    if _one_of(payload.get("open_only"), "open_only", {"yes", "no"}) == "yes":
        arrivals = arrivals.filter(
            status__in=[GateArrival.Status.QUEUED, GateArrival.Status.WEIGHING]
        )
    return _rows(arrivals.distinct(), build.arrival_payload)


def _find_excursions(request, payload):
    from apps.panels import serializers as build
    from apps.panels.views import visible_lots
    from apps.storage.models import ConditionExcursion

    platform, parties = _party_scope_or_platform(request)
    excursions = ConditionExcursion.objects.all()

    scope = str(payload.get("scope_code") or "").strip()
    if scope:
        excursions = excursions.filter(scope_code=scope)
    if _one_of(payload.get("unresolved_only"), "unresolved_only", {"yes", "no"}) == "yes":
        excursions = excursions.filter(resolved=False)

    if not platform:
        # Two routes in, and an insurer needs the second: the zone is at a
        # facility you operate, or a lot you can see was in it. Matched in
        # Python for the same reason `_excursions_for` does it - a JSON
        # `contains` lookup is one query on PostgreSQL and an error on the
        # SQLite fallback, which is a documented part of this project.
        from apps.storage.models import StorageZone

        mine = set(
            StorageZone.objects.filter(
                facility__operator_party_id__in=parties
            ).values_list("code", flat=True)
        )
        seen = set(visible_lots(request.user).values_list("code", flat=True))
        excursions = [
            e
            for e in excursions
            if e.scope_code in mine or (set(e.affected_lot_codes or []) & seen)
        ]
        rows = excursions[:MAX_ROWS]
        return {"total": len(rows), "results": [build.excursion_payload(e) for e in rows]}

    return _rows(excursions, build.excursion_payload)


def _find_qc(request, payload):
    from apps.panels import serializers as build
    from apps.panels.views import visible_lots
    from apps.quality.models import QcRecord

    records = QcRecord.objects.filter(
        lot__in=visible_lots(request.user)
    ).select_related("lot", "inspector")

    lot = str(payload.get("lot") or "").strip()
    if lot:
        records = records.filter(lot__code=lot)
    if _one_of(payload.get("failed_only"), "failed_only", {"yes", "no"}) == "yes":
        records = records.filter(passed=False)

    return _rows(records, lambda r: {"lot": r.lot.code, **build.qc_payload(r)})


def _find_trials(request, payload):
    """Not scoped, and deliberately so.

    The trial is the evidence the whole ZEROCO case rests on, and the public
    website already publishes it through an open endpoint. Scoping it here
    would mean the operator running the trial could see less of it than an
    anonymous visitor.
    """
    from apps.panels import serializers as build
    from apps.quality.models import PilotTrial

    trials = PilotTrial.objects.select_related("product").prefetch_related(
        "arms__observations"
    )
    code = str(payload.get("code") or "").strip()
    if code:
        trial = trials.filter(code=code).first()
        if trial is None:
            return {"error": "not_found", "detail": f"No trial with the code {code!r}."}
        return build.trial_detail_payload(trial)

    return _rows(trials, build.trial_summary_payload)


def _find_applications(request, payload):
    from django.db.models import Q

    from apps.finance.models import FinanceApplication
    from apps.panels import serializers as build

    platform, parties = _party_scope_or_platform(request)
    apps_qs = FinanceApplication.objects.select_related(
        "applicant_party", "lender_party"
    ).prefetch_related("collateral_lots")
    if not platform:
        apps_qs = apps_qs.filter(
            Q(applicant_party_id__in=parties) | Q(lender_party_id__in=parties)
        )

    status = _one_of(
        payload.get("status"), "status", set(FinanceApplication.Status.values)
    )
    if status:
        apps_qs = apps_qs.filter(status=status)
    return _rows(apps_qs.distinct(), build.application_payload)


def _find_policies(request, payload):
    from django.db.models import Q

    from apps.finance.models import Policy
    from apps.panels import serializers as build

    platform, parties = _party_scope_or_platform(request)
    policies = Policy.objects.select_related("insurer_party", "holder_party")
    if not platform:
        policies = policies.filter(
            Q(insurer_party_id__in=parties) | Q(holder_party_id__in=parties)
        )
    if _one_of(payload.get("active_only"), "active_only", {"yes", "no"}) == "yes":
        policies = policies.filter(status=Policy.Status.ACTIVE)
    return _rows(policies.distinct(), build.policy_payload)


def _find_claims(request, payload):
    from django.db.models import Q

    from apps.finance.models import Claim
    from apps.panels import serializers as build
    from apps.panels.views import visible_lots

    platform, parties = _party_scope_or_platform(request)
    claims = Claim.objects.select_related(
        "policy", "policy__holder_party", "policy__insurer_party", "lot"
    )
    if not platform:
        claims = claims.filter(
            Q(policy__insurer_party_id__in=parties)
            | Q(policy__holder_party_id__in=parties)
            | Q(lot__in=visible_lots(request.user))
        )
    status = _one_of(payload.get("status"), "status", set(Claim.Status.values))
    if status:
        claims = claims.filter(status=status)
    return _rows(claims.distinct(), build.claim_payload)


def _find_shipments(request, payload):
    from django.db.models import Q

    from apps.commercial.models import Shipment
    from apps.panels import serializers as build
    from apps.panels.views import visible_lots

    platform, parties = _party_scope_or_platform(request)
    shipments = Shipment.objects.select_related("carrier_party", "export_contract")
    if not platform:
        # Three routes: you drive it, you sold it, or something of yours is on
        # board. An exporter reaches its own shipments through the second.
        shipments = shipments.filter(
            Q(carrier_party_id__in=parties)
            | Q(export_contract__seller_party_id__in=parties)
            | Q(lines__lot__in=visible_lots(request.user))
        )
    status = _one_of(payload.get("status"), "status", set(Shipment.Status.values))
    if status:
        shipments = shipments.filter(status=status)
    return _rows(shipments.distinct(), build.shipment_payload)


def _find_exports(request, payload):
    from apps.commercial.models import ExportContract
    from apps.panels import serializers as build

    platform, parties = _party_scope_or_platform(request)
    contracts = ExportContract.objects.select_related("seller_party", "product")
    if not platform:
        contracts = contracts.filter(seller_party_id__in=parties)
    status = _one_of(payload.get("status"), "status", set(ExportContract.Status.values))
    if status:
        contracts = contracts.filter(status=status)
    return _rows(contracts.distinct(), build.export_payload)


def _find_organisations(request, payload):
    from django.db.models import Count

    from apps.panels import serializers as build
    from apps.registry.models import Party

    platform, _ = _party_scope_or_platform(request)
    if not platform:
        # A refusal, not an empty list. An empty register would read as "there
        # are no organisations", which is a different and false statement.
        return {
            "error": "forbidden",
            "detail": (
                "The organisation register is open to platform roles only - the "
                "operator's own staff. Say so plainly; it is not a fault."
            ),
        }

    parties = Party.objects.select_related("type").annotate(n=Count("memberships"))
    if _one_of(payload.get("pending_only"), "pending_only", {"yes", "no"}) == "yes":
        # Awaiting a decision, not merely "not verified". A rejected
        # organisation has been decided; sweeping it in here makes the count
        # of things needing attention wrong, which is the only reason anyone
        # asks for this filter.
        parties = parties.filter(
            verification_status__in=[
                Party.Verification.PENDING,
                Party.Verification.REVIEW,
            ]
        )
    return _rows(parties, lambda p: build.organisation_payload(p, user_count=p.n))



# --- quantities, rendered rather than left as arithmetic --------------------


def _rendered(value: Any) -> Any:
    """Walk a payload and put a readable form beside every quantity.

    Rules 4 and 5 of this project are that quantities are integer grams and
    money is integer minor units plus a currency. Both are right for a
    database and both are traps for a reader, so every panel screen converts
    them through `panel-format.ts` before anybody sees one.

    The assistant had no such formatter and was doing the arithmetic in its
    head. It mostly got it right, which is worse than always getting it wrong:
    asked in Russian what an insurer covered, it reported a lot valued at
    168,000,000 UZS as **16,800,000** - a tenfold understatement of insured
    value, in a fluent paragraph, beside three other figures it had divided
    correctly. Nothing about the answer looked wrong.

    So the conversion happens here, once, in Python. `valuation_minor` keeps
    its raw value and gains `valuation: "168000000.00 UZS"` beside it;
    `net_weight_g` gains `net_weight: "4200.0 kg"`. The model reads a string
    and copies it. There is no sum left for it to get wrong.

    A money field whose currency cannot be found is left alone rather than
    rendered with a guessed one. This project spans UZS, USD and JPY, and a
    number with the wrong currency on it is worse than a number the reader has
    to go and look up.
    """
    if isinstance(value, list):
        return [_rendered(item) for item in value]
    if not isinstance(value, dict):
        return value

    out = {key: _rendered(item) for key, item in value.items()}

    for key, raw in list(value.items()):
        if not isinstance(raw, int) or isinstance(raw, bool):
            continue

        if key.endswith("_minor"):
            stem = key[: -len("_minor")]
            currency = value.get(f"{stem}_currency") or value.get("currency")
            if currency and stem not in out:
                out[stem] = f"{raw / 100:,.2f} {currency}"
        elif key.endswith("_g"):
            stem = key[: -len("_g")]
            if stem not in out:
                out[stem] = f"{raw / 1000:,.1f} kg"

    return out


RUNNERS = {
    "find_lots": _find_lots,
    "lot_passport": _lot_passport,
    "find_zones": _find_zones,
    "find_liens": _find_liens,
    "find_arrivals": _find_arrivals,
    "find_excursions": _find_excursions,
    "find_qc": _find_qc,
    "find_trials": _find_trials,
    "find_applications": _find_applications,
    "find_policies": _find_policies,
    "find_claims": _find_claims,
    "find_shipments": _find_shipments,
    "find_exports": _find_exports,
    "find_organisations": _find_organisations,
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
        # Rendered on the way out, so every tool gets it - including the next
        # one somebody adds.
        return _rendered(runner(request, dict(payload or {})))
    except BadFilter as bad:
        # The one failure the model can do something about, so it is worded for
        # the model rather than logged and swallowed.
        return {"error": "bad_filter", "detail": str(bad)}
    except Exception as exc:  # noqa: BLE001 - a failed read is an answer, not a crash
        import logging

        logging.getLogger(__name__).exception("Assistant tool %s failed", name)
        return {"error": "failed", "detail": f"That lookup did not complete ({type(exc).__name__})."}
