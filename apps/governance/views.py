"""Writes for the governance cluster: the data-sharing grants.

A grant is the platform's answer to "why can a bank see my storage
temperatures?", so two things about these endpoints matter more than the code:

* **Revoking is a state change, not a deletion.** The question a farmer asks
  six months later is "who *could* see this in August?", and a deleted row
  cannot answer it.
* **A statutory grant cannot be revoked here.** Consent is the owner's to
  withdraw; a reporting duty to a ministry is not, and a screen that offered
  the button anyway would be lying about what the button does.
"""

from __future__ import annotations

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.common.api import IsPlatformAdministrator, audit
from apps.governance.models import DataGrant
from apps.panels.serializers import grant_payload
from apps.registry.models import Party


class GrantSerializer(serializers.Serializer):
    grantee_party = serializers.CharField(required=False, allow_blank=True)
    grantee_label = serializers.CharField(required=False, allow_blank=True)
    scope_key = serializers.CharField()
    fields_key = serializers.CharField()
    basis = serializers.ChoiceField(choices=DataGrant.Basis.choices)
    expires_on = serializers.DateField(required=False, allow_null=True)


@extend_schema(summary="Grant an organisation a slice of data", request=GrantSerializer, responses={201: dict})
@api_view(["POST"])
@permission_classes([IsPlatformAdministrator])
def create_grant(request):
    payload = GrantSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    data = payload.validated_data

    party = Party.objects.filter(code=data.get("grantee_party") or "").first()
    if not party and not data.get("grantee_label"):
        return Response(
            {"detail": "A grant needs a grantee: an organisation on the platform, or a name."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    grant = DataGrant.objects.create(
        grantee_party=party,
        grantee_label="" if party else data["grantee_label"],
        scope_key=data["scope_key"],
        fields_key=data["fields_key"],
        basis=data["basis"],
        status=DataGrant.Status.ACTIVE,
        granted_on=timezone.localdate(),
        expires_on=data.get("expires_on"),
    )
    audit(
        request,
        "a_granted",
        object_ref=party.code if party else grant.grantee_label,
        capability="administer",
    )
    return Response(grant_payload(grant), status=status.HTTP_201_CREATED)


@extend_schema(
    summary="Revoke a grant",
    description="Sets the state and keeps the row: who could see what, when, is a question that outlives the grant.",
    request=None,
    responses={200: dict, 409: dict},
)
@api_view(["POST"])
@permission_classes([IsPlatformAdministrator])
def revoke_grant(request, grant_id: str):
    grant = DataGrant.objects.get(pk=grant_id)

    if grant.basis == DataGrant.Basis.LAW:
        return Response(
            {
                "detail": (
                    "This grant rests on a statutory duty, not on consent. "
                    "It cannot be revoked from here."
                )
            },
            status=status.HTTP_409_CONFLICT,
        )

    grant.status = DataGrant.Status.REVOKED
    grant.save(update_fields=["status", "updated_at"])
    audit(
        request,
        "a_revoked",
        object_ref=grant.grantee_party.code if grant.grantee_party else grant.grantee_label,
        capability="administer",
    )
    return Response(grant_payload(grant))
