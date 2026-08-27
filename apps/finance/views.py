"""Writes for finance and risk - and the two ports that make them real.

Every state change that involves a bank or an insurer goes through
`ports.get_port(...)`. Today that resolves to the manual adapter: the request
is recorded, `PortResult.state` comes back `pending_operator`, and a human
finishes it from the admin. When a live adapter is configured the same code
path returns a real reference instead. No caller here changes, which is the
entire point of the seam.

`PortResult.accepted` being true for a pending result matters: pending is
success-so-far. Treating it as failure would make the manual adapter look like
an outage.
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

from apps.common.api import audit, requires
from apps.finance.models import Claim, Encumbrance, FinanceApplication, Policy
from apps.lots.models import Lot
from apps.panels.serializers import application_payload, claim_payload, lien_payload
from apps.registry.models import Party
from ports import get_port


class ApplicationSerializer(serializers.Serializer):
    code = serializers.CharField(required=False, allow_blank=True)
    applicant_party = serializers.CharField(required=False, allow_blank=True)
    lender_party = serializers.CharField()
    kind = serializers.ChoiceField(choices=FinanceApplication.Kind.choices)
    amount_minor = serializers.IntegerField(min_value=1)
    currency = serializers.CharField(max_length=3, default="UZS")
    lots = serializers.ListField(child=serializers.CharField(), required=False)
    ltv_pct = serializers.DecimalField(max_digits=5, decimal_places=2, required=False)
    note = serializers.CharField(required=False, allow_blank=True)


def _next_code(model, prefix: str) -> str:
    year = timezone.localdate().year
    stem = f"{prefix}-{year}-"
    last = (
        model.objects.filter(code__startswith=stem)
        .order_by("-code")
        .values_list("code", flat=True)
        .first()
    )
    return f"{stem}{(int(last.rsplit('-', 1)[1]) + 1) if last else 1:04d}"


@extend_schema(summary="Open a finance application", request=ApplicationSerializer, responses={200: dict})
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("transact")])
@transaction.atomic
def create_application(request):
    payload = ApplicationSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    data = payload.validated_data

    applicant = (
        Party.objects.filter(code=data.get("applicant_party") or "").first()
        or request.user.memberships.first().party
    )
    application = FinanceApplication.objects.create(
        code=data.get("code") or _next_code(FinanceApplication, "FA"),
        applicant_party=applicant,
        lender_party=Party.objects.get(code=data["lender_party"]),
        kind=data["kind"],
        amount_minor=data["amount_minor"],
        currency=data["currency"],
        ltv_pct=data.get("ltv_pct") or 0,
        note=data.get("note", ""),
        status=FinanceApplication.Status.DRAFT,
    )
    if data.get("lots"):
        application.collateral_lots.set(Lot.objects.filter(code__in=data["lots"]))

    audit(request, "a_created", object_ref=application.code, capability="transact")
    return Response(application_payload(application), status=status.HTTP_201_CREATED)



@extend_schema(
    summary="Submit an application to the lender",
    description="Goes through LenderPort. The manual adapter records it as pending.", responses={200: dict}, request=None)
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("transact")])
@transaction.atomic
def submit_application(request, code: str):
    application = FinanceApplication.objects.select_for_update().get(code=code)

    result = get_port("lender").submit_application(
        str(application.id),
        amount_minor=application.amount_minor,
        currency=application.currency,
        lots=[lot.code for lot in application.collateral_lots.all()],
    )
    if not result.accepted:  # pragma: no cover - no adapter refuses yet
        return Response({"detail": result.detail}, status=status.HTTP_502_BAD_GATEWAY)

    application.status = FinanceApplication.Status.SUBMITTED
    application.port_state = result.state
    application.port_reference = result.reference or ""
    application.save(
        update_fields=["status", "port_state", "port_reference", "updated_at"]
    )

    audit(request, "a_submitted", object_ref=application.code, capability="transact")
    return Response(
        {**application_payload(application), "port": {"state": result.state, "detail": result.detail}}
    )



class DecisionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=FinanceApplication.Status.choices)
    note = serializers.CharField(required=False, allow_blank=True)
    ltv_pct = serializers.DecimalField(max_digits=5, decimal_places=2, required=False)


@extend_schema(
    summary="Record the lender's decision",
    description=(
        "The manual adapter's other half: an operator keys in what the bank "
        "said on the phone. An API adapter writes the same fields."
    ),
    request=DecisionSerializer, responses={200: dict})
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("decide")])
@transaction.atomic
def decide_application(request, code: str):
    application = FinanceApplication.objects.select_for_update().get(code=code)
    payload = DecisionSerializer(data=request.data)
    payload.is_valid(raise_exception=True)

    application.status = payload.validated_data["status"]
    application.decided_on = timezone.localdate()
    if payload.validated_data.get("ltv_pct") is not None:
        application.ltv_pct = payload.validated_data["ltv_pct"]
    if payload.validated_data.get("note"):
        application.note = payload.validated_data["note"]
    application.save()

    audit(request, "a_decided", object_ref=application.code, capability="decide")
    return Response(application_payload(application))



class LienSerializer(serializers.Serializer):
    lot = serializers.CharField()
    application = serializers.CharField()
    amount_minor = serializers.IntegerField(min_value=1)
    currency = serializers.CharField(max_length=3, default="UZS")


@extend_schema(
    summary="Register a lien over a lot",
    description=(
        "The lien is an overlay: the lot keeps its status and stays stored, "
        "reserved or shippable. What it loses is the right to leave."
    ),
    request=LienSerializer, responses={200: dict})
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("decide")])
@transaction.atomic
def create_lien(request):
    payload = LienSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    data = payload.validated_data

    lot = Lot.objects.select_for_update().get(code=data["lot"])
    application = FinanceApplication.objects.get(code=data["application"])

    lien = Encumbrance.objects.create(
        lot=lot,
        application=application,
        holder_party=application.lender_party,
        amount_minor=data["amount_minor"],
        currency=data["currency"],
    )

    result = get_port("lender").register_lien(
        str(application.id), str(lot.id), data["amount_minor"]
    )
    lot.log(
        "pledged",
        actor_party=application.lender_party,
        actor_label=application.lender_party.legal_name,
        severity="warn",
        payload={
            "application": application.code,
            "amount_minor": lien.amount_minor,
            "currency": lien.currency,
            "kind": application.kind,
            "port_state": result.state,
        },
    )
    audit(request, "a_pledged", object_ref=lot.code, capability="decide")
    return Response(lien_payload(lien), status=status.HTTP_201_CREATED)



@extend_schema(
    summary="Release a lien",
    description="A release is an event, never a deletion: the row stays, released.", responses={200: dict}, request=None)
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("decide")])
@transaction.atomic
def release_lien(request, lien_id: str):
    lien = Encumbrance.objects.select_for_update().get(pk=lien_id)
    result = get_port("lender").release_lien(str(lien.id))
    lien.release(reference=result.reference or result.state)
    audit(request, "a_released", object_ref=lien.lot.code, capability="decide")
    return Response(lien_payload(lien))



class ClaimSerializer(serializers.Serializer):
    code = serializers.CharField(required=False, allow_blank=True)
    policy = serializers.CharField()
    lot = serializers.CharField(required=False, allow_blank=True)
    excursion_code = serializers.CharField(required=False, allow_blank=True)
    amount_minor = serializers.IntegerField(min_value=1)
    currency = serializers.CharField(max_length=3, default="UZS")
    note = serializers.CharField(required=False, allow_blank=True)


@extend_schema(summary="File an insurance claim", request=ClaimSerializer, responses={200: dict})
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("transact")])
@transaction.atomic
def create_claim(request):
    payload = ClaimSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    data = payload.validated_data

    policy = Policy.objects.get(code=data["policy"])
    lot = Lot.objects.filter(code=data.get("lot") or "").first()

    claim = Claim.objects.create(
        code=data.get("code") or _next_code(Claim, "CL"),
        policy=policy,
        lot=lot,
        excursion_code=data.get("excursion_code", ""),
        amount_minor=data["amount_minor"],
        currency=data["currency"],
        note=data.get("note", ""),
        status=Claim.Status.REVIEW,
    )

    result = get_port("insurer").file_claim(
        str(policy.id),
        {
            "claim": claim.code,
            "lot": lot.code if lot else "",
            "excursion": claim.excursion_code,
            "amount_minor": claim.amount_minor,
            "currency": claim.currency,
        },
    )
    claim.port_state = result.state
    claim.port_reference = result.reference or ""
    claim.save(update_fields=["port_state", "port_reference", "updated_at"])

    if lot:
        lot.log(
            "claim_filed",
            actor_user=request.user,
            actor_label=request.user.display_name,
            severity="warn",
            payload={
                "claim": claim.code,
                "policy": policy.code,
                "excursion": claim.excursion_code,
                "amount_minor": claim.amount_minor,
            },
        )
    audit(request, "a_created", object_ref=claim.code, capability="transact")
    return Response(claim_payload(claim), status=status.HTTP_201_CREATED)



class ClaimDecisionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Claim.Status.choices)
    assessed_minor = serializers.IntegerField(min_value=0, required=False)
    note = serializers.CharField(required=False, allow_blank=True)


@extend_schema(summary="Decide a claim", request=ClaimDecisionSerializer, responses={200: dict})
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("decide")])
@transaction.atomic
def decide_claim(request, code: str):
    claim = Claim.objects.select_for_update().get(code=code)
    payload = ClaimDecisionSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    data = payload.validated_data

    claim.status = data["status"]
    claim.assessed_minor = data.get("assessed_minor", claim.assessed_minor)
    claim.decided_on = timezone.localdate()
    if data.get("note"):
        claim.note = data["note"]
    claim.save()

    audit(request, "a_decided", object_ref=claim.code, capability="decide")
    return Response(claim_payload(claim))

