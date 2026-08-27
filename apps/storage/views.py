"""Writes for the storage cluster: the gate, the shelf, and the sensors.

Placement is the one that matters most. Inventory age, remaining saleable
window and zone fill are all derived from `StoragePlacement.placed_at`, so a
placement that is recorded late or not at all is a lot the platform cannot
price. It writes to the lot's log for the same reason.

Sensor readings arrive through `SensorPort`. The manual adapter accepts a
batch and reports how many rows it took; a live adapter will do the same from
an MQTT bridge. Whichever answers, the rows land in the same table and the
zone's cached reading is refreshed from the newest one.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.api import audit, requires
from apps.lots.models import Lot
from apps.panels.serializers import arrival_payload, excursion_payload, lot_payload, zone_payload
from apps.storage.models import (
    ConditionExcursion,
    ConditionReading,
    GateArrival,
    StoragePlacement,
    StorageZone,
)
from ports import get_port


class PlacementSerializer(serializers.Serializer):
    lot = serializers.CharField()
    zone = serializers.CharField()
    position = serializers.CharField(required=False, allow_blank=True)
    quantity_g = serializers.IntegerField(min_value=1, required=False)


@extend_schema(summary="Put a lot away", request=PlacementSerializer, responses={200: dict})
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("capture")])
@transaction.atomic
def place(request):
    payload = PlacementSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    data = payload.validated_data

    lot = Lot.objects.select_for_update().get(code=data["lot"])
    zone = StorageZone.objects.select_related("facility").get(code=data["zone"])
    quantity = data.get("quantity_g") or lot.net_weight_g

    # The lifecycle is REGISTERED -> GRADED -> STORED. Putting an ungraded lot
    # on a shelf loses the one moment where its grade, and therefore its
    # value, is decided.
    if lot.status == Lot.Status.REGISTERED:
        return Response(
            {"detail": "Grade this lot before placing it."},
            status=status.HTTP_409_CONFLICT,
        )

    if zone.used_g + quantity > zone.capacity_g:
        return Response(
            {
                "detail": "That zone does not have room for this lot.",
                "capacity_g": zone.capacity_g,
                "used_g": zone.used_g,
            },
            status=status.HTTP_409_CONFLICT,
        )

    placement = StoragePlacement.objects.create(
        lot=lot,
        zone=zone,
        position=data.get("position", ""),
        quantity_g=quantity,
        placed_at=timezone.now(),
    )

    # The storage mode is the lot's, not the zone's, but it is decided by where
    # the lot actually went - which is here.
    lot.storage_mode = zone.mode
    lot.sell_by = _sell_by(lot, zone)
    lot.save(update_fields=["storage_mode", "sell_by", "updated_at"])
    if lot.can_transition_to(Lot.Status.STORED):
        lot.transition(Lot.Status.STORED)

    lot.log(
        "placed",
        actor_user=request.user,
        actor_label=request.user.display_name,
        facility_code=zone.facility.code,
        severity="accept",
        payload={
            "zone": zone.code,
            "position": placement.position,
            "temp_c": str(zone.current_temp_c or ""),
            "rh_pct": str(zone.current_rh_pct or ""),
        },
    )
    audit(request, "a_placed", object_ref=lot.code, capability="capture")
    return Response(
        {"lot": lot_payload(lot), "zone": zone_payload(zone)},
        status=status.HTTP_201_CREATED,
    )



def _sell_by(lot, zone):
    """The end of the saleable window, from the product's shelf life.

    Held on the lot once it is known rather than recomputed on read: a bank
    lends against this date, and a number that moves when the catalogue is
    edited is not a number anyone can lend against.
    """
    days = (lot.product.shelf_life_days or {}).get(zone.mode)
    if not days:
        return lot.sell_by
    start = lot.harvested_on or timezone.localdate()
    return start + timedelta(days=int(days))


@extend_schema(summary="Take a lot off the shelf", responses={200: dict}, request=None)
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("capture")])
@transaction.atomic
def remove(request):
    lot = Lot.objects.get(code=request.data["lot"])
    placement = (
        StoragePlacement.objects.select_for_update()
        .filter(lot=lot, removed_at__isnull=True)
        .select_related("zone")
        .first()
    )
    if not placement:
        return Response(
            {"detail": "That lot is not currently placed."},
            status=status.HTTP_409_CONFLICT,
        )
    placement.removed_at = timezone.now()
    placement.save(update_fields=["removed_at", "updated_at"])
    lot.log(
        "removed",
        actor_user=request.user,
        actor_label=request.user.display_name,
        facility_code=placement.zone.facility.code,
        payload={"zone": placement.zone.code, "position": placement.position},
    )
    return Response({"lot": lot_payload(lot)})



class WeighSerializer(serializers.Serializer):
    gross_weight_g = serializers.IntegerField(min_value=0)
    tare_weight_g = serializers.IntegerField(min_value=0, default=0)


@extend_schema(summary="Record a weighbridge reading for an arrival", request=WeighSerializer, responses={200: dict})
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("capture")])
def weigh(request, arrival_id: str):
    arrival = GateArrival.objects.get(pk=arrival_id)
    payload = WeighSerializer(data=request.data)
    payload.is_valid(raise_exception=True)

    arrival.status = GateArrival.Status.WEIGHING
    arrival.estimated_weight_g = (
        payload.validated_data["gross_weight_g"] - payload.validated_data["tare_weight_g"]
    )
    arrival.save(update_fields=["status", "estimated_weight_g", "updated_at"])
    return Response(arrival_payload(arrival))



class ReadingSerializer(serializers.Serializer):
    scope_type = serializers.ChoiceField(choices=ConditionReading.Scope.choices)
    scope_code = serializers.CharField()
    readings = serializers.ListField(child=serializers.DictField(), allow_empty=False)


@extend_schema(
    summary="Ingest a batch of sensor readings",
    description="Goes through SensorPort. Duplicate instants are ignored, not rejected.",
    request=ReadingSerializer, responses={200: dict})
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("capture")])
@transaction.atomic
def ingest_readings(request):
    payload = ReadingSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    data = payload.validated_data

    result = get_port("sensor").ingest(
        data["scope_type"], data["scope_code"], iter(data["readings"])
    )

    rows = [
        ConditionReading(
            scope_type=data["scope_type"],
            scope_code=data["scope_code"],
            sensor_id=row.get("sensor_id", ""),
            recorded_at=row["recorded_at"],
            temp_c=row.get("temp_c"),
            rh_pct=row.get("rh_pct"),
        )
        for row in data["readings"]
    ]
    # A field gateway that retries will resend what it already sent; the unique
    # constraint makes that harmless instead of fatal.
    ConditionReading.objects.bulk_create(rows, ignore_conflicts=True)

    if data["scope_type"] == ConditionReading.Scope.ZONE:
        zone = StorageZone.objects.filter(code=data["scope_code"]).first()
        newest = max(rows, key=lambda r: r.recorded_at, default=None)
        if zone and newest:
            zone.current_temp_c = newest.temp_c
            zone.current_rh_pct = newest.rh_pct
            zone.reading_at = newest.recorded_at
            zone.save(
                update_fields=[
                    "current_temp_c",
                    "current_rh_pct",
                    "reading_at",
                    "updated_at",
                ]
            )

    return Response(
        {"accepted": len(rows), "port": {"state": result.state, "detail": result.detail}},
        status=status.HTTP_202_ACCEPTED,
    )



@extend_schema(summary="Mark an excursion resolved", responses={200: dict}, request=None)
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("approve")])
def resolve_excursion(request, code: str):
    excursion = ConditionExcursion.objects.get(code=code)
    excursion.resolved = True
    excursion.save(update_fields=["resolved"])

    for lot_code in excursion.affected_lot_codes:
        lot = Lot.objects.filter(code=lot_code).first()
        if lot:
            lot.log(
                "excursion_resolved",
                actor_user=request.user,
                actor_label=request.user.display_name,
                severity="accept",
                payload={"excursion": excursion.code},
            )
    audit(request, "a_resolved", object_ref=excursion.code, capability="approve")
    return Response(excursion_payload(excursion))

