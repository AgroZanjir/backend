"""Read endpoints for the eight operator panels.

Every view here is a read. Writes belong to the cluster that owns the table -
a grading decision is `quality`'s, a lien is `finance`'s - and are mounted
under their own cluster's URLs. This module exists only so that a screen which
legitimately spans four clusters can be served by one request instead of four.

Two rules the whole module keeps:

* **Scope before serialise.** `party_scope` narrows a queryset to the
  organisations the caller belongs to before anything is built. Platform roles
  see everything; nobody else sees another organisation's rows.
* **Fetch for the page, not for the row.** Placements and liens are fetched
  once per request and passed into the builders, because a lot table renders a
  hundred rows and N+1 here is a hundred queries.
"""

from __future__ import annotations

from django.db.models import Case, Count, IntegerField, Prefetch, Q, Value, When
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.commercial.models import ExportContract, Shipment
from apps.common.api import (
    IsPlatformAdministrator,
    audit,
    is_platform,
    memberships_of,
)
from apps.common.models import Notification
from apps.documents.models import Document
from apps.finance.models import Claim, Encumbrance, FinanceApplication, Policy
from apps.governance.models import AuditEntry, DataGrant
from apps.lots.models import Lot, LotRelation
from apps.panels import serializers as build
from apps.quality.models import PilotTrial, QcRecord
from apps.registry.models import (
    Capability,
    Farm,
    Membership,
    OrganisationType,
    Party,
    Product,
    Role,
    User,
    VerificationCheck,
)
from apps.storage.models import (
    ConditionExcursion,
    ConditionReading,
    Facility,
    GateArrival,
    StoragePlacement,
    StorageZone,
)


#: ZEROCO chambers first, then cold, then the rest. Alphabetical order put the
#: dry store above the chambers, and the chambers are the scarce capacity the
#: whole pilot is about - the room the reader looks for first should be first.
ZONE_ORDER = Case(
    When(mode="zeroco", then=Value(0)),
    When(mode="cold", then=Value(1)),
    When(mode="pre", then=Value(2)),
    default=Value(3),
    output_field=IntegerField(),
)


def _zone_queryset():
    return (
        StorageZone.objects.select_related("facility")
        .annotate(mode_order=ZONE_ORDER)
        .order_by("mode_order", "code")
    )


def _lot_queryset():
    return Lot.objects.select_related("product", "origin_farm", "owner_party")


def visible_lots(user):
    """Every lot this person may see, and why they may see it.

    Four routes, and the panels need all four: a producer owns the lot, a hub
    is holding it, a bank has a lien over it, an insurer covers it. Ownership
    alone would leave the hub's own storage screen empty, which is how this
    rule was found.
    """
    queryset = _lot_queryset()
    if is_platform(user):
        return queryset

    party_ids = [m.party_id for m in memberships_of(user)]
    return queryset.filter(
        Q(owner_party_id__in=party_ids)
        | Q(
            placements__removed_at__isnull=True,
            placements__zone__facility__operator_party_id__in=party_ids,
        )
        | Q(encumbrances__holder_party_id__in=party_ids)
        | Q(policies__insurer_party_id__in=party_ids)
    ).distinct()


def _placements_for(lots) -> dict:
    """One query for every open placement on the page."""
    rows = StoragePlacement.objects.filter(
        lot__in=lots, removed_at__isnull=True
    ).select_related("zone")
    return {row.lot_id: row for row in rows}


def _pledged_for(lots) -> set:
    """One query for every active lien on the page."""
    return set(
        Encumbrance.objects.filter(lot__in=lots, released_at__isnull=True).values_list(
            "lot_id", flat=True
        )
    )


def _lot_rows(lots) -> list[dict]:
    lots = list(lots)
    placements = _placements_for(lots)
    pledged = _pledged_for(lots)
    return [
        build.lot_payload(
            lot, placement=placements.get(lot.id), pledged=lot.id in pledged
        )
        for lot in lots
    ]


# --- reference data ---------------------------------------------------------


@extend_schema(summary="Everything the panels treat as a lookup table", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def reference(request):
    """Products, facilities, zones, farms, and the whole role model.

    One request because these change monthly at most and every panel needs
    several of them; the client caches the response for the session.
    """
    farms = Farm.objects.select_related("party").annotate(n=Count("lots"))
    zones = _zone_queryset()
    roles = Role.objects.prefetch_related("capabilities")

    groups: dict[str, list] = {}
    for role in roles:
        groups.setdefault(role.group_key, []).append(
            {
                "code": role.code,
                "label_key": role.label_key,
                "scope": role.scope,
                "capabilities": sorted(c.code for c in role.capabilities.all()),
            }
        )

    return Response(
        {
            "products": [build.product_payload(p) for p in Product.objects.all()],
            "facilities": [build.facility_payload(f) for f in Facility.objects.all()],
            "zones": [build.zone_payload(z) for z in zones],
            "farms": [build.farm_payload(f, lot_count=f.n) for f in farms],
            "org_types": [
                {
                    "code": t.code,
                    "label_key": t.label_key,
                    "icon": t.icon,
                    "required_checks": t.required_checks,
                    "licence_register": t.licence_register,
                }
                for t in OrganisationType.objects.all()
            ],
            "capabilities": [
                {"code": c.code, "label_key": c.label_key}
                for c in Capability.objects.all()
            ],
            "role_groups": [
                {"group_key": key, "roles": items} for key, items in groups.items()
            ],
            "verification_checks": [
                {
                    "code": c.code,
                    "label_key": c.label_key,
                    "register": c.register,
                    "mode": c.mode,
                }
                for c in VerificationCheck.objects.all()
            ],
        }
    )


# --- the spine --------------------------------------------------------------


@extend_schema(summary="Lots visible to the caller", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def lots(request):
    visible = visible_lots(request.user)

    status_filter = request.query_params.get("status")
    if status_filter:
        visible = visible.filter(status__in=status_filter.split(","))

    return Response({"results": _lot_rows(visible)})


def _excursions_for(lot) -> list:
    """Excursions that touched this lot.

    Narrowed by the zones the lot has actually been in, then matched in
    Python. A `JSONField__contains` lookup would be one query on PostgreSQL and
    an error on SQLite, and the fallback database is a documented part of this
    project - so the portable version wins until the JSON index is worth
    having.
    """
    zone_codes = list(
        lot.placements.values_list("zone__code", flat=True).distinct()
    )
    candidates = ConditionExcursion.objects.filter(scope_code__in=zone_codes)
    return [e for e in candidates if lot.code in (e.affected_lot_codes or [])]


def _passport(lot, *, public: bool) -> dict:
    """The lot passport, in two depths.

    The public version answers "is this real and how was it handled?" without
    naming an owner, a price or a lender. That is the whole design of the
    public panel: provenance is public, commerce is not.
    """
    placement = (
        StoragePlacement.objects.filter(lot=lot, removed_at__isnull=True)
        .select_related("zone")
        .first()
    )
    lien = (
        Encumbrance.objects.filter(lot=lot, released_at__isnull=True)
        .select_related("application", "holder_party")
        .first()
    )
    events = lot.events.select_related("actor_user", "actor_party").order_by("sequence")
    qc = lot.qc_records.select_related("inspector").order_by("inspected_on")

    body = {
        "lot": build.lot_payload(lot, placement=placement, pledged=bool(lien)),
        "events": [build.event_payload(e) for e in events],
        "qc": [build.qc_payload(record) for record in qc],
        "chain_intact": lot.chain_intact,
        "zone": build.zone_payload(placement.zone) if placement else None,
        "relations": [
            {
                "parent": relation.parent.code,
                "child": relation.child.code,
                "kind": relation.kind,
                "quantity_g": relation.quantity_g,
            }
            for relation in LotRelation.objects.filter(
                Q(parent=lot) | Q(child=lot)
            ).select_related("parent", "child")
        ],
        "documents": [
            build.document_payload(d)
            for d in Document.objects.filter(subject_type="lot", subject_code=lot.code)
        ],
    }

    if public:
        # Strip everything commercial. The passport still proves the chain.
        body["lot"] = {
            key: value
            for key, value in body["lot"].items()
            if key
            not in {
                "valuation_minor",
                "valuation_currency",
                "pledged",
                "owner_party",
                "owner_name",
            }
        }
        body.pop("relations", None)
        # Provenance is the point of this page, so the farm comes with it -
        # its name, where it is and what it is certified for. Who owns the lot
        # and what it is worth do not.
        # The product's name in all three languages: the catalogue is public,
        # and a page that answers "what am I holding?" with a slug does not
        # answer it.
        body["product"] = {
            "code": lot.product.code,
            "name_uz": lot.product.name_uz,
            "name_ru": lot.product.name_ru,
            "name_en": lot.product.name_en,
            "variety": lot.product.variety,
        }
        farm = lot.origin_farm
        body["origin"] = (
            {
                "farm": farm.code,
                "name": farm.name,
                "region": farm.region,
                "district": farm.district,
                "certifications": farm.certifications,
            }
            if farm
            else None
        )
        # Public events only: a pledge and an excursion are the farmer's
        # business with their bank and their insurer, not the shopper's.
        public_types = {"registered", "graded", "placed", "inspected", "delivered"}
        body["events"] = [e for e in body["events"] if e["type"] in public_types]
        body["qc"] = [
            {"stage": q["stage"], "inspected_on": q["inspected_on"], "passed": q["passed"]}
            for q in body["qc"]
        ]
        return body

    policy = (
        Policy.objects.filter(covered_lots=lot, status=Policy.Status.ACTIVE)
        .select_related("insurer_party", "holder_party")
        .first()
    )
    body["lien"] = build.lien_payload(lien) if lien else None
    body["policy"] = build.policy_payload(policy) if policy else None
    body["excursions"] = [build.excursion_payload(e) for e in _excursions_for(lot)]
    return body


@extend_schema(summary="The lot passport", operation_id="panel_lot_passport", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def lot_passport(request, code: str):
    # Scoped, not merely authenticated: the full passport carries valuation,
    # owner and lender. Someone with no relationship to the lot gets the
    # public one, which is open to everybody anyway.
    lot = get_object_or_404(visible_lots(request.user), code=code)
    audit(request, "a_viewed", object_ref=lot.code, capability="view")
    return Response(_passport(lot, public=False))


@extend_schema(
    summary="The public passport for a lot",
    description="Open by design: provenance is public, commerce is not.", responses={200: dict})
@api_view(["GET"])
@permission_classes([AllowAny])
def public_passport(request, code: str):
    lot = get_object_or_404(_lot_queryset(), code=code)
    return Response(_passport(lot, public=True))


# --- hub --------------------------------------------------------------------


@extend_schema(summary="Zones, with live fill and set points", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def zones(request):
    queryset = _zone_queryset()
    facility = request.query_params.get("facility")
    if facility:
        queryset = queryset.filter(facility__code=facility)
    return Response({"results": [build.zone_payload(z) for z in queryset]})


@extend_schema(summary="What is expected at the gate", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def arrivals(request):
    queryset = GateArrival.objects.select_related("facility", "farm", "product")
    if request.query_params.get("open") == "1":
        queryset = queryset.filter(
            status__in=[GateArrival.Status.QUEUED, GateArrival.Status.WEIGHING]
        )
    return Response({"results": [build.arrival_payload(a) for a in queryset]})


@extend_schema(summary="Condition excursions", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def excursions(request):
    queryset = ConditionExcursion.objects.all()
    scope = request.query_params.get("scope_code")
    if scope:
        queryset = queryset.filter(scope_code=scope)
    return Response({"results": [build.excursion_payload(e) for e in queryset]})


@extend_schema(summary="One excursion, with its trace", operation_id="panel_excursion", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def excursion(request, code: str):
    row = get_object_or_404(ConditionExcursion, code=code)
    audit(request, "a_downloaded", object_ref=row.code, capability="view")
    return Response(build.excursion_payload(row))


@extend_schema(summary="Condition readings for a zone or a shipment", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def readings(request):
    scope_type = request.query_params.get("scope_type", "storage_zone")
    scope_code = request.query_params.get("scope_code", "")
    limit = min(int(request.query_params.get("limit", 96)), 1000)
    queryset = ConditionReading.objects.filter(
        scope_type=scope_type, scope_code=scope_code
    )[:limit]
    return Response(
        {
            "results": [
                {
                    "recorded_at": r.recorded_at,
                    "temp_c": r.temp_c,
                    "rh_pct": r.rh_pct,
                    "sensor_id": r.sensor_id,
                }
                for r in reversed(list(queryset))
            ]
        }
    )


# --- quality ----------------------------------------------------------------


@extend_schema(summary="Pilot trials", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def trials(request):
    # Running trials first, then planned, then finished. Newest-first would put
    # a trial that has not started above the one being measured this week, and
    # the reader of this table is looking after the ones that are running.
    queryset = (
        PilotTrial.objects.select_related("product")
        .prefetch_related("arms__observations")
        .annotate(
            status_order=Case(
                When(status="running", then=Value(0)),
                When(status="planned", then=Value(1)),
                When(status="completed", then=Value(2)),
                default=Value(3),
                output_field=IntegerField(),
            )
        )
        .order_by("status_order", "-started_on")
    )
    return Response({"results": [build.trial_summary_payload(t) for t in queryset]})


@extend_schema(
    summary="One trial: both arms, measured points and the projection",
    operation_id="panel_trial",
    responses={200: dict},
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def trial(request, code: str):
    row = get_object_or_404(
        PilotTrial.objects.select_related("product").prefetch_related("arms"), code=code
    )
    return Response(build.trial_detail_payload(row))


@extend_schema(
    summary="The ZEROCO trial, publicly",
    description=(
        "Open by design: the trial is the evidence the whole ZEROCO case "
        "rests on, and the public website draws its chart from it. Measured "
        "points and the modelled projection stay separate here too."
    ),
    operation_id="panel_public_trial",
    responses={200: dict},
)
@api_view(["GET"])
@permission_classes([AllowAny])
def public_trial(request, code: str):
    row = get_object_or_404(
        PilotTrial.objects.select_related("product").prefetch_related("arms"), code=code
    )
    return Response(build.trial_detail_payload(row))


@extend_schema(summary="QC records", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def qc_records(request):
    queryset = QcRecord.objects.select_related("lot", "inspector")
    lot_code = request.query_params.get("lot")
    if lot_code:
        queryset = queryset.filter(lot__code=lot_code)
    return Response(
        {
            "results": [
                {"lot": record.lot.code, **build.qc_payload(record)}
                for record in queryset
            ]
        }
    )


# --- finance and insurance --------------------------------------------------


@extend_schema(summary="Finance applications", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def applications(request):
    queryset = FinanceApplication.objects.select_related(
        "applicant_party", "lender_party"
    ).prefetch_related("collateral_lots")
    party_ids = [m.party_id for m in memberships_of(request.user)]
    if not is_platform(request.user):
        queryset = queryset.filter(
            Q(applicant_party_id__in=party_ids) | Q(lender_party_id__in=party_ids)
        )
    return Response({"results": [build.application_payload(a) for a in queryset]})


@extend_schema(summary="The lien register", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def liens(request):
    queryset = Encumbrance.objects.select_related(
        "lot", "application", "holder_party"
    ).order_by("-created_on")
    return Response({"results": [build.lien_payload(lien) for lien in queryset]})


@extend_schema(summary="Insurance claims", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def claims(request):
    queryset = Claim.objects.select_related(
        "policy", "policy__holder_party", "policy__insurer_party", "lot"
    )
    return Response({"results": [build.claim_payload(c) for c in queryset]})


@extend_schema(summary="Policies", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def policies(request):
    queryset = Policy.objects.select_related("insurer_party", "holder_party")
    return Response({"results": [build.policy_payload(p) for p in queryset]})


# --- export -----------------------------------------------------------------


@extend_schema(summary="Export contracts", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def exports(request):
    queryset = ExportContract.objects.select_related("product", "seller_party").prefetch_related(
        "shipments"
    )
    return Response({"results": [build.export_payload(c) for c in queryset]})


@extend_schema(summary="Shipments", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def shipments(request):
    queryset = Shipment.objects.select_related("carrier_party", "export_contract")
    return Response({"results": [build.shipment_payload(s) for s in queryset]})


@extend_schema(summary="One shipment, with its temperature trace", operation_id="panel_shipment", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def shipment(request, code: str):
    row = get_object_or_404(
        Shipment.objects.select_related("carrier_party", "export_contract"), code=code
    )
    trace = list(
        ConditionReading.objects.filter(scope_type="shipment", scope_code=row.code)
        .order_by("recorded_at")
        .values_list("temp_c", flat=True)
    )
    return Response(build.shipment_payload(row, temps=trace))


@extend_schema(summary="Documents for a subject", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def documents(request):
    queryset = Document.objects.all()
    subject_type = request.query_params.get("subject_type")
    subject_code = request.query_params.get("subject_code")
    if subject_type:
        queryset = queryset.filter(subject_type=subject_type)
    if subject_code:
        queryset = queryset.filter(subject_code=subject_code)
    return Response({"results": [build.document_payload(d) for d in queryset]})


# --- the bell ---------------------------------------------------------------


@extend_schema(summary="Notifications for the signed-in person", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def notifications(request):
    # A notification with no user is a platform-wide one: everybody sees it.
    queryset = Notification.objects.filter(
        Q(user=request.user) | Q(user__isnull=True)
    )[:50]
    return Response({"results": [build.notification_payload(n) for n in queryset]})


# --- administration ---------------------------------------------------------


@extend_schema(summary="Organisations", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsPlatformAdministrator])
def organisations(request):
    queryset = (
        Party.objects.select_related("type")
        .annotate(n=Count("memberships"))
        .order_by("code")
    )
    return Response(
        {"results": [build.organisation_payload(p, user_count=p.n) for p in queryset]}
    )


@extend_schema(summary="One organisation, with its verification checks", operation_id="panel_organisation", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsPlatformAdministrator])
def organisation(request, code: str):
    party = get_object_or_404(
        Party.objects.select_related("type").prefetch_related(
            "verifications__verification_check"
        ),
        code=code,
    )
    audit(request, "a_viewed", object_ref=party.code, capability="verify")
    return Response(
        {
            **build.organisation_payload(party),
            "required_checks": party.type.required_checks,
            "licence_register": party.type.licence_register,
            "checks": {
                v.verification_check.code: {
                    "result": v.result,
                    "decided_at": v.decided_at,
                    "decided_by": v.decided_by.display_name if v.decided_by else "",
                    "note": v.note,
                    "evidence": v.evidence,
                }
                for v in party.verifications.all()
            },
            "members": [
                build.platform_user_payload(m.user)
                for m in party.memberships.select_related("user", "role", "party")
            ],
        }
    )


@extend_schema(summary="People on the platform", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsPlatformAdministrator])
def platform_users(request):
    queryset = User.objects.prefetch_related(
        Prefetch("memberships", queryset=Membership.objects.select_related("party", "role"))
    )
    return Response({"results": [build.platform_user_payload(u) for u in queryset]})


@extend_schema(summary="The audit log", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsPlatformAdministrator])
def audit_log(request):
    queryset = AuditEntry.objects.select_related("actor_user", "actor_party")[:200]
    return Response({"results": [build.audit_payload(e) for e in queryset]})


@extend_schema(summary="Data-sharing grants", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsPlatformAdministrator])
def grants(request):
    queryset = DataGrant.objects.select_related("grantee_party")
    return Response({"results": [build.grant_payload(g) for g in queryset]})
