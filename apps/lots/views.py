"""Writes against the spine.

Every one of these is a lifecycle transition, and every one appends to the
event log in the same transaction as the change it describes. That is the
whole contract of this cluster: the `lot` row is a cache, and it may never
move without the log recording why.

The dispatch endpoint is where rule 3 becomes visible. It does not check for
liens itself - it asks the spine, which asks every guard any cluster
registered. A lot under an active lien is refused with the lender named.
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

from apps.common.api import audit, has_capability, requires
from apps.lots.models import Lot, LotRelation, dispatch_blockers
from apps.panels.serializers import lot_payload
from apps.registry.models import Farm, Party, Product


class RegisterLotSerializer(serializers.Serializer):
    """What the gate screen sends when a vehicle finishes weighing."""

    code = serializers.CharField(max_length=32, required=False, allow_blank=True)
    product = serializers.SlugField()
    farm = serializers.CharField(required=False, allow_blank=True)
    owner_party = serializers.CharField(required=False, allow_blank=True)
    gross_weight_g = serializers.IntegerField(min_value=0, required=False)
    net_weight_g = serializers.IntegerField(min_value=1)
    harvested_on = serializers.DateField(required=False)
    facility = serializers.CharField(required=False, allow_blank=True)
    # The gate is the one screen that runs where connectivity fails. The client
    # sends a key it generated; a repeat of the same key returns the same lot
    # rather than creating a second one.
    idempotency_key = serializers.CharField(required=False, allow_blank=True)


def _next_lot_code(region: str = "SMQ") -> str:
    year = timezone.localdate().year
    prefix = f"AZ-{year}-{region}-"
    last = (
        Lot.objects.filter(code__startswith=prefix)
        .order_by("-code")
        .values_list("code", flat=True)
        .first()
    )
    serial = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{serial:04d}"


@extend_schema(summary="Register a lot at the gate", request=RegisterLotSerializer, responses={200: dict})
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("capture")])
@transaction.atomic
def register(request):
    payload = RegisterLotSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    data = payload.validated_data

    key = data.get("idempotency_key") or ""
    if key:
        existing = Lot.objects.filter(events__payload__idempotency_key=key).first()
        if existing:
            return Response(lot_payload(existing), status=status.HTTP_200_OK)

    farm = Farm.objects.filter(code=data.get("farm") or "").first()
    owner = (
        Party.objects.filter(code=data.get("owner_party") or "").first()
        or (farm.party if farm else None)
        or request.user.memberships.first().party
    )

    # The farm owns it; whoever is standing at the gate has custody of it.
    # Those are different questions and the hub needs the second one answered
    # to see its own intake before anything is on a shelf.
    membership = request.user.memberships.select_related("party").first()

    lot = Lot.objects.create(
        code=data.get("code") or _next_lot_code(),
        product=Product.objects.get(code=data["product"]),
        origin_farm=farm,
        owner_party=owner,
        custody_party=membership.party if membership else None,
        net_weight_g=data["net_weight_g"],
        gross_weight_g=data.get("gross_weight_g"),
        harvested_on=data.get("harvested_on"),
        status=Lot.Status.REGISTERED,
    )
    lot.log(
        "registered",
        actor_user=request.user,
        actor_label=request.user.display_name,
        facility_code=data.get("facility", ""),
        payload={
            "gross_weight_g": data.get("gross_weight_g"),
            "net_weight_g": data["net_weight_g"],
            "idempotency_key": key,
        },
    )
    audit(request, "a_created", object_ref=lot.code, capability="capture")
    return Response(lot_payload(lot), status=status.HTTP_201_CREATED)



class GradeSerializer(serializers.Serializer):
    grade = serializers.CharField(max_length=8)
    net_weight_g = serializers.IntegerField(min_value=1, required=False)
    note = serializers.CharField(required=False, allow_blank=True)
    photo_refs = serializers.ListField(child=serializers.CharField(), required=False)


@extend_schema(summary="Grade a registered lot", request=GradeSerializer, responses={200: dict})
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("capture")])
@transaction.atomic
def grade(request, code: str):
    lot = Lot.objects.select_for_update().get(code=code)
    payload = GradeSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    data = payload.validated_data

    try:
        lot.grade = data["grade"]
        if data.get("net_weight_g"):
            lot.net_weight_g = data["net_weight_g"]
        lot.save(update_fields=["grade", "net_weight_g", "updated_at"])
        lot.transition(Lot.Status.GRADED)
    except ValidationError as exc:
        return Response({"detail": exc.messages}, status=status.HTTP_409_CONFLICT)

    lot.log(
        "graded",
        actor_user=request.user,
        actor_label=request.user.display_name,
        severity="accept",
        payload={
            "grade": data["grade"],
            "net_weight_g": lot.net_weight_g,
            "photos": len(data.get("photo_refs", [])),
            "note": data.get("note", ""),
        },
    )
    audit(request, "a_created", object_ref=lot.code, capability="approve")
    return Response(lot_payload(lot))



class SplitSerializer(serializers.Serializer):
    """Grading usually divides an intake; this is how the children are made."""

    children = serializers.ListField(
        child=serializers.DictField(), allow_empty=False
    )


@extend_schema(summary="Split a lot into graded children", request=SplitSerializer, responses={200: dict})
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("capture")])
@transaction.atomic
def split(request, code: str):
    parent = Lot.objects.select_for_update().get(code=code)
    payload = SplitSerializer(data=request.data)
    payload.is_valid(raise_exception=True)

    children = []
    total = 0
    for spec in payload.validated_data["children"]:
        quantity = int(spec["net_weight_g"])
        total += quantity
        child = Lot.objects.create(
            code=spec.get("code") or _next_lot_code(),
            product=parent.product,
            origin_farm=parent.origin_farm,
            owner_party=parent.owner_party,
            custody_party=parent.custody_party,
            net_weight_g=quantity,
            grade=spec.get("grade", ""),
            status=Lot.Status.GRADED,
            harvested_on=parent.harvested_on,
        )
        LotRelation.objects.create(
            parent=parent, child=child, kind=LotRelation.Kind.SPLIT, quantity_g=quantity
        )
        child.log(
            "split_from",
            actor_user=request.user,
            actor_label=request.user.display_name,
            payload={"parent": parent.code, "grade": child.grade},
        )
        children.append(child)

    if total > parent.net_weight_g:
        raise ValidationError("A split cannot produce more than went in.")

    parent.log(
        "split",
        actor_user=request.user,
        actor_label=request.user.display_name,
        payload={"children": [c.code for c in children], "quantity_g": total},
    )
    return Response(
        {"parent": lot_payload(parent), "children": [lot_payload(c) for c in children]},
        status=status.HTTP_201_CREATED,
    )



class TransitionSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True)


@extend_schema(summary="Reserve a lot against a contract", responses={200: dict}, request=None)
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("transact")])
def reserve(request, code: str):
    lot = Lot.objects.get(code=code)
    try:
        lot.transition(Lot.Status.RESERVED)
    except ValidationError as exc:
        return Response({"detail": exc.messages}, status=status.HTTP_409_CONFLICT)
    lot.log(
        "reserved",
        actor_user=request.user,
        actor_label=request.user.display_name,
        payload={"reason": request.data.get("reason", "")},
    )
    return Response(lot_payload(lot))



@extend_schema(
    summary="Dispatch a lot",
    description=(
        "Refused with 409 and the lender named while an active lien exists. "
        "The lien is an overlay, not a status: the lot is still stored, still "
        "reserved, still shippable in every other sense."
    ), responses={200: dict}, request=None)
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("approve")])
@transaction.atomic
def dispatch(request, code: str):
    lot = Lot.objects.select_for_update().get(code=code)
    blockers = dispatch_blockers(lot)
    if blockers:
        return Response(
            {"detail": "This lot cannot leave.", "blockers": blockers},
            status=status.HTTP_409_CONFLICT,
        )
    try:
        lot.transition(Lot.Status.DISPATCHED)
    except ValidationError as exc:
        return Response({"detail": exc.messages}, status=status.HTTP_409_CONFLICT)

    # It has left. Custody passes to whoever is carrying it, and the platform
    # does not know that yet - better empty than wrong.
    lot.custody_party = None
    lot.save(update_fields=["custody_party", "updated_at"])

    lot.log(
        "dispatched",
        actor_user=request.user,
        actor_label=request.user.display_name,
        severity="accept",
        payload={"shipment": request.data.get("shipment", "")},
    )
    audit(request, "a_dispatched", object_ref=lot.code, capability="approve")
    return Response(lot_payload(lot))



@extend_schema(summary="Write a lot off", responses={200: dict}, request=None)
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("approve")])
def write_off(request, code: str):
    lot = Lot.objects.get(code=code)
    try:
        lot.transition(Lot.Status.WRITTEN_OFF)
    except ValidationError as exc:
        return Response({"detail": exc.messages}, status=status.HTTP_409_CONFLICT)
    lot.valuation_minor = 0
    lot.save(update_fields=["valuation_minor", "updated_at"])
    lot.log(
        "written_off",
        actor_user=request.user,
        actor_label=request.user.display_name,
        severity="warn",
        payload={"reason": request.data.get("reason", "")},
    )
    audit(request, "a_written_off", object_ref=lot.code, capability="approve")
    return Response(lot_payload(lot))



@extend_schema(
    summary="Verify a lot's event chain",
    description="Recomputes every hash in order. This is the tamper evidence.", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def verify_chain(request, code: str):
    lot = Lot.objects.get(code=code)
    intact = lot.chain_intact
    if has_capability(request.user, "audit"):
        audit(request, "a_verified", object_ref=lot.code, capability="audit")
    return Response(
        {
            "lot": lot.code,
            "events": lot.events.count(),
            "chain_intact": intact,
        }
    )
