"""Writes for the document vault.

Issuing a document is the moment an export becomes possible, so it writes to
the subject lot's log when the subject is a lot, and it refuses to mark
anything issued without a reference - a certificate with no number is not a
certificate.
"""

from __future__ import annotations

import hashlib
import uuid

from django.core.files.storage import default_storage
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
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
    return Response(
        document_payload(document, request), status=status.HTTP_201_CREATED
    )



# What an operator may put in the vault from a panel. Deliberately short: this
# is evidence and paperwork, not a file share.
ALLOWED_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "application/pdf": ".pdf",
}

#: Eight megabytes. A phone photograph of a pallet is one to three; a scan of a
#: certificate is under one. Anything larger is a mistake, and the place to
#: refuse it is before it is written, not after the disk fills.
MAX_BYTES = 8 * 1024 * 1024


class UploadSerializer(serializers.Serializer):
    subject_type = serializers.ChoiceField(choices=Document.Subject.choices)
    subject_code = serializers.CharField(max_length=48)
    doc_type = serializers.ChoiceField(
        choices=Document.Type.choices, default=Document.Type.PHOTO
    )
    reference = serializers.CharField(required=False, allow_blank=True)


def _refuse(detail: str, blocker: str):
    return Response(
        {"detail": detail, "blockers": [blocker]},
        status=status.HTTP_400_BAD_REQUEST,
    )


@extend_schema(
    summary="Put a file in the vault",
    description=(
        "Multipart. The bytes go to the configured storage and the row keeps "
        "the key and a SHA-256 of what was written, so a photograph offered as "
        "evidence months later can be shown to be the one that was taken."
    ),
    request={"multipart/form-data": UploadSerializer},
    responses={201: dict},
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("capture")])
@parser_classes([MultiPartParser, FormParser])
def upload_document(request):
    payload = UploadSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    data = payload.validated_data

    # Only against a lot this person can see. `capture` says they may record
    # things; it does not say whose. The five routes are the same ones their
    # screens use, so a hub can attach to a lot it holds without owning it.
    if data["subject_type"] == Document.Subject.LOT:
        from apps.panels.views import visible_lots

        if not visible_lots(request.user).filter(code=data["subject_code"]).exists():
            return _refuse(
                "That lot is not one of yours.",
                "This lot is not visible to your organisation.",
            )

    upload = request.FILES.get("file")
    if upload is None:
        return _refuse("Attach a file.", "No file was attached.")
    if upload.size > MAX_BYTES:
        return _refuse(
            "That file is too large.",
            f"Files are limited to {MAX_BYTES // (1024 * 1024)} MB; "
            f"this one is {upload.size / (1024 * 1024):.1f} MB.",
        )

    # The browser's word for what it sent, checked against a short list. Not a
    # guarantee of content - it is a client's claim - which is why the stored
    # name never takes an extension from the client either.
    suffix = ALLOWED_TYPES.get((upload.content_type or "").split(";")[0].strip())
    if suffix is None:
        return _refuse(
            "That kind of file cannot go in the vault.",
            "Photographs (JPEG, PNG, WebP, HEIC) and PDFs only.",
        )

    digest = hashlib.sha256()
    for chunk in upload.chunks():
        digest.update(chunk)
    upload.seek(0)

    # The path is built here and never from anything the caller typed: a
    # subject code arrives from a form, and a name from a form is a way into
    # somebody else's directory.
    #
    # The name is also the only thing guarding the bytes. nginx serves /media/
    # without asking who is asking, so the key is a random 128 bits rather than
    # anything a reader could walk. That is the usual trade for a pilot on a
    # filesystem; the moment this storage is S3 it should be a signed URL, and
    # `Document.url` is the one place that changes.
    key = f"documents/{data['subject_type']}/{uuid.uuid4().hex}{suffix}"
    stored = default_storage.save(key, upload)

    document = Document.objects.create(
        code=f"{data['doc_type'].upper()}-{uuid.uuid4().hex[:10].upper()}",
        subject_type=data["subject_type"],
        subject_code=data["subject_code"],
        doc_type=data["doc_type"],
        name_key=f"doc_{data['doc_type']}",
        # The file is the document, so it exists as soon as it is stored.
        # `reference` carries what the operator called it; the certificate rule
        # in `create_document` is about numbers issued by somebody else.
        status=Document.Status.ISSUED,
        reference=(data.get("reference") or upload.name or "")[:64],
        issued_by=request.user.display_name or request.user.get_username(),
        issued_on=timezone.localdate(),
        file_ref=stored,
        checksum_sha256=digest.hexdigest(),
        byte_size=upload.size,
        content_type=upload.content_type or "",
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
                    "checksum_sha256": document.checksum_sha256,
                },
            )

    audit(request, "a_created", object_ref=document.code, capability="capture")
    return Response(
        document_payload(document, request), status=status.HTTP_201_CREATED
    )
