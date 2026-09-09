"""Writes for the quality cluster.

A QC record is evidence: it attaches to the lot, it goes into the lot's log,
and when it assigns a grade it moves the lot with it. Recording quality
without moving the lot would leave two versions of the truth.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.api import audit, requires
from apps.lots.models import Lot
from apps.panels.serializers import qc_payload, trial_detail_payload
from apps.quality.models import PilotTrial, QcRecord, TrialArm, TrialObservation
from apps.registry.models import Product


class QcRecordSerializer(serializers.Serializer):
    lot = serializers.CharField()
    stage = serializers.ChoiceField(choices=QcRecord.Stage.choices)
    inspected_on = serializers.DateField()
    measurements = serializers.DictField(required=False)
    grade_assigned = serializers.CharField(required=False, allow_blank=True)
    defect_pct = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False
    )
    passed = serializers.BooleanField(default=True)
    note = serializers.CharField(required=False, allow_blank=True)


@extend_schema(summary="Record a QC inspection", request=QcRecordSerializer, responses={200: dict})
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("capture")])
@transaction.atomic
def create_qc_record(request):
    payload = QcRecordSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    data = payload.validated_data

    lot = Lot.objects.select_for_update().get(code=data["lot"])
    record = QcRecord.objects.create(
        lot=lot,
        stage=data["stage"],
        inspected_on=data["inspected_on"],
        inspector=request.user,
        inspector_label=request.user.display_name,
        measurements=data.get("measurements", {}),
        grade_assigned=data.get("grade_assigned", ""),
        defect_pct=data.get("defect_pct"),
        passed=data["passed"],
        note=data.get("note", ""),
    )

    lot.log(
        "sampled" if not record.grade_assigned else "graded",
        actor_user=request.user,
        actor_label=request.user.display_name,
        severity="accept" if record.passed else "warn",
        payload={
            "stage": record.stage,
            "measurements": record.measurements,
            "grade": record.grade_assigned,
            "defect_pct": str(record.defect_pct or ""),
        },
    )

    # A grade at intake is also a lifecycle decision, not just a note.
    if record.grade_assigned and lot.can_transition_to(Lot.Status.GRADED):
        lot.grade = record.grade_assigned
        lot.save(update_fields=["grade", "updated_at"])
        try:
            lot.transition(Lot.Status.GRADED)
        except ValidationError:  # pragma: no cover - guarded by can_transition_to
            pass

    audit(request, "a_created", object_ref=lot.code, capability="capture")
    return Response(qc_payload(record), status=status.HTTP_201_CREATED)



class ObservationSerializer(serializers.Serializer):
    """What a tablet sends from a cold store.

    Every measurement but the day is optional *and nullable*: a cleared field
    serialises to null, and refusing that turned a partly-taken observation
    into an error instead of a partly-taken observation.
    """

    arm = serializers.ChoiceField(choices=TrialArm.Kind.choices)
    day_index = serializers.IntegerField(min_value=0)
    observed_on = serializers.DateField()
    weight_loss_pct = serializers.DecimalField(
        max_digits=6, decimal_places=2, required=False, allow_null=True
    )
    waste_pct = serializers.DecimalField(
        max_digits=6, decimal_places=2, required=False, allow_null=True
    )
    firmness_n = serializers.DecimalField(
        max_digits=6, decimal_places=2, required=False, allow_null=True
    )
    colour_score = serializers.DecimalField(
        max_digits=4, decimal_places=1, required=False, allow_null=True
    )
    markdown_pct = serializers.DecimalField(
        max_digits=6, decimal_places=2, required=False, allow_null=True
    )
    note = serializers.CharField(required=False, allow_blank=True)


@extend_schema(
    summary="Record a trial observation",
    description=(
        "One sampling day on one arm. Recorded observations are what the "
        "comparison chart draws solid; the modelled curve stays dashed."
    ),
    request=ObservationSerializer, responses={200: dict})
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("capture")])
@transaction.atomic
def create_observation(request, code: str):
    trial = PilotTrial.objects.get(code=code)
    payload = ObservationSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    data = payload.validated_data

    arm = trial.arms.select_related("lot").get(kind=data.pop("arm"))
    observation, created = TrialObservation.objects.update_or_create(
        arm=arm,
        day_index=data.pop("day_index"),
        defaults={**data, "observer": request.user},
    )

    arm.lot.log(
        "trial_observed",
        actor_user=request.user,
        actor_label=request.user.display_name,
        payload={
            "trial": trial.code,
            "arm": arm.kind,
            "day": observation.day_index,
            "weight_loss_pct": str(observation.weight_loss_pct or ""),
            "firmness_n": str(observation.firmness_n or ""),
        },
    )
    if trial.status == PilotTrial.Status.PLANNED:
        trial.status = PilotTrial.Status.RUNNING
        trial.save(update_fields=["status", "updated_at"])

    audit(request, "a_created", object_ref=trial.code, capability="capture")
    return Response(
        trial_detail_payload(trial),
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )



class TrialSerializer(serializers.Serializer):
    product = serializers.CharField()
    facility_code = serializers.CharField(required=False, allow_blank=True)
    # Days from day zero. The default is the schedule the pilot runs on; a
    # trial that samples on other days is still a trial.
    schedule_days = serializers.ListField(
        child=serializers.IntegerField(min_value=0), required=False
    )


def _next_trial_code(product_code: str) -> str:
    """`TR-MELON-02`, after `TR-MELON-01`.

    Numbered per product because that is how the pilot refers to them out
    loud - "the second melon trial" - and the product is the thing being
    argued about.
    """
    stem = f"TR-{product_code.upper()}-"
    last = (
        PilotTrial.objects.filter(code__startswith=stem)
        .order_by("-code")
        .values_list("code", flat=True)
        .first()
    )
    nth = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{stem}{nth:02d}"


@extend_schema(
    summary="Open a pilot trial",
    description=(
        "A trial starts planned and with no arms: which consignment is "
        "divided between ZEROCO and the control is decided at the moment "
        "there is one to divide, which is usually days later. The screens "
        "already show that state - the list carries a planned trial with no "
        "arms - and this is how one gets there."
    ),
    request=TrialSerializer,
    responses={201: dict},
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("capture")])
@transaction.atomic
def create_trial(request):
    payload = TrialSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    data = payload.validated_data

    product = Product.objects.filter(code=data["product"]).first()
    if product is None:
        return Response(
            {"detail": "No such product.", "blockers": ["No such product."]},
            status=status.HTTP_400_BAD_REQUEST,
        )

    trial = PilotTrial.objects.create(
        code=_next_trial_code(product.code),
        product=product,
        facility_code=data.get("facility_code", ""),
        schedule_days=data.get("schedule_days") or [0, 7, 14, 21, 28],
        status=PilotTrial.Status.PLANNED,
    )
    audit(request, "a_created", object_ref=trial.code, capability="capture")
    return Response(
        {"code": trial.code, "status": trial.status},
        status=status.HTTP_201_CREATED,
    )
