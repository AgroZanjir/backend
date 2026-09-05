"""Writes for the registry: verification decisions and memberships.

Verification is the gate the whole platform hangs on - an organisation that
has not cleared it cannot open a panel - so two things here are deliberate:

* A check that does not apply to this kind of organisation cannot be recorded
  at all. A carrier has no land rights; showing or storing a "pass" for one
  would be a compliance officer's nightmare.
* The organisation's `verification_status` is recomputed from its check rows
  rather than set by hand. The cache follows the evidence, never the other way
  round.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.api import IsPlatformAdministrator, audit
from apps.panels.serializers import organisation_payload, platform_user_payload
from apps.registry.models import (
    Membership,
    Party,
    PartyVerification,
    Role,
    User,
    VerificationCheck,
)


def recompute_status(party: Party) -> str:
    """Derive the organisation's status from its checks.

    Verified only when every required check has passed; rejected the moment one
    fails; in review while any is being looked at; pending otherwise.
    """
    required = set(party.type.required_checks or [])
    results = {
        v.verification_check.code: v.result
        for v in party.verifications.select_related("verification_check")
    }
    considered = {code: results.get(code, "pending") for code in required}

    if any(result == PartyVerification.Result.FAIL for result in considered.values()):
        return Party.Verification.REJECTED
    if considered and all(
        result == PartyVerification.Result.PASS for result in considered.values()
    ):
        return Party.Verification.VERIFIED
    if any(result == PartyVerification.Result.REVIEW for result in considered.values()):
        return Party.Verification.REVIEW
    return Party.Verification.PENDING


class CheckDecisionSerializer(serializers.Serializer):
    check = serializers.SlugField()
    result = serializers.ChoiceField(choices=PartyVerification.Result.choices)
    note = serializers.CharField(required=False, allow_blank=True)
    evidence = serializers.DictField(required=False)


@extend_schema(summary="Record one verification check", request=CheckDecisionSerializer, responses={200: dict})
@api_view(["POST"])
@permission_classes([IsPlatformAdministrator])
@transaction.atomic
def decide_check(request, code: str):
    party = Party.objects.select_related("type").get(code=code)
    payload = CheckDecisionSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    data = payload.validated_data

    if data["check"] not in (party.type.required_checks or []):
        return Response(
            {
                "detail": (
                    f"The {data['check']!r} check does not apply to a "
                    f"{party.type.code}; recording a result for it would be false."
                )
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    check = VerificationCheck.objects.get(code=data["check"])
    PartyVerification.objects.update_or_create(
        party=party,
        verification_check=check,
        defaults={
            "result": data["result"],
            "note": data.get("note", ""),
            "evidence": data.get("evidence", {}),
            "decided_at": timezone.now(),
            "decided_by": request.user,
        },
    )

    party.verification_status = recompute_status(party)
    if party.verification_status == Party.Verification.VERIFIED:
        party.verified_on = timezone.localdate()
        party.verified_by = request.user.display_name or request.user.get_username()
    party.save(
        update_fields=["verification_status", "verified_on", "verified_by", "updated_at"]
    )

    audit(request, "a_verified", object_ref=party.code, capability="verify")
    return Response(organisation_payload(party))


class InviteSerializer(serializers.Serializer):
    display_name = serializers.CharField()
    username = serializers.CharField()
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    party = serializers.CharField()
    role = serializers.SlugField()
    facility_codes = serializers.ListField(child=serializers.CharField(), required=False)


@extend_schema(
    summary="Invite a person into an organisation",
    description=(
        "The person exists as `invited` until they come through OneID; the "
        "membership is real from the moment it is created, which is what the "
        "administration screens list."
    ),
    request=InviteSerializer, responses={200: dict})
@api_view(["POST"])
@permission_classes([IsPlatformAdministrator])
@transaction.atomic
def invite(request):
    payload = InviteSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    data = payload.validated_data

    user, created = User.objects.get_or_create(
        username=data["username"],
        defaults={
            "display_name": data["display_name"],
            "email": data.get("email", ""),
            "phone": data.get("phone", ""),
            "status": User.Status.INVITED,
        },
    )
    if created:
        # No password is set: OneID is the way in, and an unusable password is
        # the correct state for an account that has never been claimed.
        user.set_unusable_password()
        user.save(update_fields=["password"])

    Membership.objects.get_or_create(
        user=user,
        party=Party.objects.get(code=data["party"]),
        role=Role.objects.get(code=data["role"]),
        defaults={"facility_codes": data.get("facility_codes", [])},
    )
    audit(request, "a_role", object_ref=user.display_name, capability="administer")
    return Response(
        platform_user_payload(user),
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


class MembershipStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=User.Status.choices)


@extend_schema(summary="Suspend or restore a person", request=MembershipStatusSerializer, responses={200: dict})
@api_view(["POST"])
@permission_classes([IsPlatformAdministrator])
def set_user_status(request, user_id: str):
    user = User.objects.get(pk=user_id)
    refusal = _not_yourself(request, user)
    if refusal is not None:
        return refusal

    payload = MembershipStatusSerializer(data=request.data)
    payload.is_valid(raise_exception=True)

    user.status = payload.validated_data["status"]
    user.is_active = user.status != User.Status.SUSPENDED
    user.save(update_fields=["status", "is_active"])

    audit(request, "a_role", object_ref=user.display_name, capability="administer")
    return Response(platform_user_payload(user))


def _not_yourself(request, user) -> Response | None:
    """Refuse an administrator acting on their own account.

    Suspending yourself, or moving yourself off the role that let you in,
    ends the session that is doing it - and the way back is a database
    shell. Someone else with the capability can always do it instead.
    """
    if user.pk == request.user.pk:
        return Response(
            {
                "detail": "This is your own account.",
                # `blockers` reach the panel's toast verbatim, so they are
                # sentences rather than codes - see dispatch_blockers().
                "blockers": [
                    "Ask another administrator to do this to your own account."
                ],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


class MembershipRoleSerializer(serializers.Serializer):
    role = serializers.SlugField(max_length=48)


@extend_schema(
    summary="Change what a person is at their organisation",
    description=(
        "Moves the person's membership to another role. The membership is the "
        "thing that changes, not the account: their history keeps its actor "
        "and their old decisions keep the role they were made under."
    ),
    request=MembershipRoleSerializer,
    responses={200: dict},
)
@api_view(["POST"])
@permission_classes([IsPlatformAdministrator])
@transaction.atomic
def set_user_role(request, user_id: str):
    user = User.objects.get(pk=user_id)
    refusal = _not_yourself(request, user)
    if refusal is not None:
        return refusal

    payload = MembershipRoleSerializer(data=request.data)
    payload.is_valid(raise_exception=True)

    role = Role.objects.filter(code=payload.validated_data["role"]).first()
    if role is None:
        return Response(
            {"detail": "No such role.", "blockers": ["There is no role with that code."]},
            status=status.HTTP_400_BAD_REQUEST,
        )

    membership = user.memberships.order_by("-is_primary").first()
    if membership is None:
        return Response(
            {
                "detail": "This person belongs to no organisation yet.",
                "blockers": [
                    "This person belongs to no organisation yet, so there is no"
                    " role to change. Invite them into one first."
                ],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # A platform-scope role is power over everyone's data, so it can only be
    # held at the organisation that runs the platform. Without this an
    # administrator could hand `platform_owner` to a gate operator at a farm
    # and the scope column on the role screen would be a decoration.
    if (
        role.scope == Role.Scope.PLATFORM
        and membership.party.type.code != "operator"
    ):
        return Response(
            {
                "detail": "A platform role can only be held at the operator.",
                "blockers": [
                    "A platform-wide role can only be held at the organisation"
                    " that runs the platform."
                ],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if membership.role_id != role.pk:
        # (user, party, role) is unique, so if they already hold the target
        # role at this organisation the change is the removal of the old row
        # rather than an edit that would collide with it.
        existing = user.memberships.filter(party=membership.party, role=role).first()
        if existing is not None:
            membership.delete()
            membership = existing
            membership.is_primary = True
            membership.save(update_fields=["is_primary"])
        else:
            membership.role = role
            membership.save(update_fields=["role", "updated_at"])

    audit(request, "a_role", object_ref=user.display_name, capability="administer")
    return Response(platform_user_payload(user))


@extend_schema(summary="Whoever the caller is, as the panels need them", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_organisations(request):
    parties = Party.objects.filter(
        memberships__user=request.user
    ).select_related("type").distinct()
    return Response({"results": [organisation_payload(p) for p in parties]})
