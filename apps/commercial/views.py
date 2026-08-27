"""Writes for the commercial and logistics clusters.

Booking a carrier and lodging a customs declaration both go through their
ports, so the same code runs whether a coordinator phones the haulier or an
API answers. Departure is the interesting one: it dispatches every lot on the
shipment, which means the lot spine gets to refuse - a pledged lot stops the
whole truck, and the response says which one and whose lien.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.commercial.models import ExportContract, Shipment, ShipmentLine
from apps.common.api import audit, requires
from apps.documents.models import Document
from apps.lots.models import Lot, dispatch_blockers
from apps.panels.serializers import shipment_payload
from apps.registry.models import Party
from ports import get_port


class ShipmentSerializer(serializers.Serializer):
    code = serializers.CharField(required=False, allow_blank=True)
    export_contract = serializers.CharField(required=False, allow_blank=True)
    carrier_party = serializers.CharField(required=False, allow_blank=True)
    mode = serializers.ChoiceField(choices=Shipment.Mode.choices, default="reefer")
    vehicle = serializers.CharField(required=False, allow_blank=True)
    set_point_c = serializers.DecimalField(max_digits=5, decimal_places=2, required=False)
    origin = serializers.CharField(required=False, allow_blank=True)
    destination = serializers.CharField(required=False, allow_blank=True)
    distance_km = serializers.IntegerField(min_value=0, default=0)
    departs_at = serializers.DateTimeField(required=False)
    eta = serializers.DateTimeField(required=False)
    lines = serializers.ListField(child=serializers.DictField(), required=False)


@extend_schema(summary="Plan a shipment", request=ShipmentSerializer, responses={200: dict})
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("transact")])
@transaction.atomic
def create_shipment(request):
    payload = ShipmentSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    data = payload.validated_data

    year = timezone.localdate().year
    shipment = Shipment.objects.create(
        code=data.get("code") or f"SH-{year}-{Shipment.objects.count() + 1:04d}",
        export_contract=ExportContract.objects.filter(
            code=data.get("export_contract") or ""
        ).first(),
        carrier_party=Party.objects.filter(code=data.get("carrier_party") or "").first(),
        mode=data["mode"],
        vehicle=data.get("vehicle", ""),
        set_point_c=data.get("set_point_c"),
        origin=data.get("origin", ""),
        destination=data.get("destination", ""),
        distance_km=data["distance_km"],
        departs_at=data.get("departs_at"),
        eta=data.get("eta"),
        status=Shipment.Status.PLANNED,
    )
    for line in data.get("lines", []):
        ShipmentLine.objects.create(
            shipment=shipment,
            lot=Lot.objects.get(code=line["lot"]),
            quantity_g=int(line["quantity_g"]),
        )

    result = get_port("carrier").request_booking(
        str(shipment.id), route=shipment.route, mode=shipment.mode
    )
    audit(request, "a_created", object_ref=shipment.code, capability="transact")
    return Response(
        {**shipment_payload(shipment), "port": {"state": result.state}},
        status=status.HTTP_201_CREATED,
    )



@extend_schema(
    summary="Depart a shipment",
    description=(
        "Dispatches every lot on the shipment. Refused with 409 if any lot is "
        "under an active lien - one pledged pallet stops the truck, and the "
        "response names it."
    ), responses={200: dict}, request=None)
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("approve")])
@transaction.atomic
def depart(request, code: str):
    shipment = Shipment.objects.select_for_update().get(code=code)
    lines = list(shipment.lines.select_related("lot"))

    blocked = {
        line.lot.code: dispatch_blockers(line.lot)
        for line in lines
        if dispatch_blockers(line.lot)
    }
    if blocked:
        return Response(
            {"detail": "This shipment cannot leave.", "blockers": blocked},
            status=status.HTTP_409_CONFLICT,
        )

    for line in lines:
        try:
            line.lot.transition(Lot.Status.DISPATCHED)
        except ValidationError as exc:
            return Response(
                {"detail": f"{line.lot.code}: {exc.messages[0]}"},
                status=status.HTTP_409_CONFLICT,
            )
        line.lot.log(
            "dispatched",
            actor_user=request.user,
            actor_label=request.user.display_name,
            severity="accept",
            payload={
                "shipment": shipment.code,
                "vehicle": shipment.vehicle,
                "destination": shipment.destination,
                "quantity_g": line.quantity_g,
            },
        )

    shipment.status = Shipment.Status.IN_TRANSIT
    shipment.departs_at = shipment.departs_at or timezone.now()
    shipment.save(update_fields=["status", "departs_at", "updated_at"])

    if shipment.export_contract:
        shipment.export_contract.status = ExportContract.Status.SHIPPED
        shipment.export_contract.save(update_fields=["status", "updated_at"])

    audit(request, "a_dispatched", object_ref=shipment.code, capability="approve")
    return Response(shipment_payload(shipment))



@extend_schema(summary="Confirm delivery", responses={200: dict}, request=None)
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("transact")])
@transaction.atomic
def deliver(request, code: str):
    shipment = Shipment.objects.select_for_update().get(code=code)
    result = get_port("carrier").confirm_delivery(str(shipment.id))

    shipment.status = Shipment.Status.DELIVERED
    shipment.arrived_at = timezone.now()
    shipment.save(update_fields=["status", "arrived_at", "updated_at"])

    for line in shipment.lines.select_related("lot"):
        line.lot.log(
            "delivered",
            actor_user=request.user,
            actor_label=request.user.display_name,
            severity="accept",
            payload={"shipment": shipment.code, "port_state": result.state},
        )
    return Response(shipment_payload(shipment))



@extend_schema(
    summary="Lodge the customs declaration for an export contract",
    description="Goes through CustomsPort; expired documents are refused before it does.", responses={200: dict}, request=None)
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("sign")])
def lodge_declaration(request, code: str):
    contract = ExportContract.objects.get(code=code)
    documents = Document.objects.filter(
        subject_type=Document.Subject.EXPORT_CONTRACT, subject_code=contract.code
    )

    missing = [d.doc_type for d in documents if d.status != Document.Status.ISSUED]
    expired = [d.doc_type for d in documents if d.is_expired]
    if missing or expired:
        return Response(
            {
                "detail": "The document set is not ready for the border.",
                "awaited": missing,
                "expired": expired,
            },
            status=status.HTTP_409_CONFLICT,
        )

    result = get_port("customs").lodge_declaration(
        str(contract.id), incoterm=contract.incoterm, country=contract.buyer_country
    )
    audit(request, "a_submitted", object_ref=contract.code, capability="sign")
    return Response({"contract": contract.code, "port": {"state": result.state, "detail": result.detail}})

