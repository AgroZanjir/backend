"""Writes for the document vault.

Issuing a document is the moment an export becomes possible, so it writes to
the subject lot's log when the subject is a lot, and it refuses to mark
anything issued without a reference - a certificate with no number is not a
certificate.
"""

from __future__ import annotations

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.api import audit, requires
from apps.documents.models import Document
from apps.lots.models import Lot
from apps.panels.serializers import document_payload
from ports import get_port


class DocumentSerializer(serializers.Serializer):
    code = serializers.CharField(required=False, allow_blank=True)
    subject_type = serializers.ChoiceField(choices=Document.Subject.choices)
    subject_code = serializers.CharField()
    doc_type = serializers.ChoiceField(choices=Document.Type.choices)
    name_key = serializers.CharField(required=False, allow_blank=True)
    reference = serializers.CharField(required=False, allow_blank=True)
    issued_by = serializers.CharField(required=False, allow_blank=True)
    issued_on = serializers.DateField(required=False)
    expires_on = serializers.DateField(required=False)
    file_ref = serializers.CharField(required=False, allow_blank=True)
    status = serializers.ChoiceField(
        choices=Document.Status.choices, default=Document.Status.PENDING
    )


@extend_schema(summary="Record or issue a document", request=DocumentSerializer, responses={200: dict})
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("capture")])
def create_document(request):
    payload = DocumentSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    data = payload.validated_data

    if data["status"] == Document.Status.ISSUED and not data.get("reference"):
        return Response(
            {"detail": "An issued document needs its reference number."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    document = Document.objects.create(
        code=data.get("code")
        or f"{data['doc_type'].upper()}-{data['subject_code']}-{timezone.now():%H%M%S}",
        subject_type=data["subject_type"],
        subject_code=data["subject_code"],
        doc_type=data["doc_type"],
        name_key=data.get("name_key", f"doc_{data['doc_type']}"),
        reference=data.get("reference", ""),
        issued_by=data.get("issued_by", ""),
        issued_on=data.get("issued_on"),
        expires_on=data.get("expires_on"),
        file_ref=data.get("file_ref", ""),
        status=data["status"],
    )

    if document.subject_type == Document.Subject.LOT:
        lot = Lot.objects.filter(code=document.subject_code).first()
        if lot:
            lot.log(
                "document_issued",
                actor_user=request.user,
                actor_label=request.user.display_name,
                payload={
                    "type": document.doc_type,
                    "reference": document.reference,
                    "expires_on": str(document.expires_on or ""),
                },
            )

    # Certificates that the border reads are lodged through the customs port.
    if document.doc_type in {Document.Type.PHYTO, Document.Type.ORIGIN}:
        get_port("customs").submit_certificate(
            str(document.id), doc_type=document.doc_type, reference=document.reference
        )

    audit(request, "a_created", object_ref=document.code, capability="capture")
    return Response(document_payload(document), status=status.HTTP_201_CREATED)

