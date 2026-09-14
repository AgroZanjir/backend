"""DOCUMENT VAULT (section 03).

    Document    subject_type, subject_code, doc_type, file_ref, issued_by,
                expires_on

Polymorphic over lots, shipments, parties and contracts: phytosanitary
certificates, lab reports, invoices, packing lists, letters of credit. The
subject is held as (type, code) rather than as five nullable foreign keys -
the vault is the one place in the system that legitimately spans clusters, and
five columns of which exactly one is non-null is how that becomes a mess.

Every document has an issuer and most have an expiry, and the expiry is what
the customs and export workflows key off: a phytosanitary certificate that
expires before the border is worth nothing.

Files live in S3-compatible object storage (MinIO if self-hosted); `file_ref`
is the key, never the bytes. Check data-residency requirements before choosing
a provider (section 08, decision 01).
"""

from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel


class Document(BaseModel):
    class Subject(models.TextChoices):
        LOT = "lot", "Lot"
        SHIPMENT = "shipment", "Shipment"
        PARTY = "party", "Organisation"
        EXPORT_CONTRACT = "export_contract", "Export contract"
        QC_RECORD = "qc_record", "QC record"
        CLAIM = "claim", "Claim"

    class Type(models.TextChoices):
        PHYTO = "phyto", "Phytosanitary certificate"
        ORIGIN = "origin", "Certificate of origin"
        INVOICE = "invoice", "Commercial invoice"
        PACKING = "packing", "Packing list"
        LAB = "lab", "Laboratory report"
        CMR = "cmr", "CMR consignment note"
        LC = "lc", "Letter of credit"
        CONTRACT = "contract", "Contract"
        LICENCE = "licence", "Licence"
        CHARTER = "charter", "Charter"
        # Evidence rather than paperwork: what an inspector photographs at the
        # bench, and what an adjuster asks for when a claim turns on condition.
        PHOTO = "photo", "Photograph"
        REGISTRATION = "registration", "Registration certificate"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Awaited"
        ISSUED = "issued", "Issued"
        EXPIRED = "expired", "Expired"
        REVOKED = "revoked", "Revoked"

    code = models.CharField(max_length=48, unique=True)
    subject_type = models.CharField(max_length=20, choices=Subject)
    subject_code = models.CharField(max_length=48, db_index=True)
    doc_type = models.CharField(max_length=16, choices=Type)
    # The i18n key for the document's name. Stored as a key, not a sentence:
    # this vault is read by an Uzbek farmer, a Russian-speaking broker and a
    # Latvian buyer, and only one of them reads Uzbek.
    name_key = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=10, choices=Status, default=Status.PENDING)
    reference = models.CharField(max_length=64, blank=True)
    issued_by = models.CharField(max_length=160, blank=True)
    issued_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    # The object-storage key. Never the bytes: the row is small, replicated
    # and dumped nightly, and a photograph is none of those things. Which
    # storage answers the key is `STORAGES["default"]` - a filesystem in the
    # pilot, S3 later, and nothing in this module changes when it moves.
    file_ref = models.CharField(max_length=512, blank=True)
    # Over the bytes as stored. Evidence that has to be shown unchanged later
    # needs something to be shown against, and the platform already argues this
    # way about its event log.
    checksum_sha256 = models.CharField(max_length=64, blank=True)
    byte_size = models.PositiveBigIntegerField(default=0)
    content_type = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["subject_code", "doc_type"]
        indexes = [models.Index(fields=["subject_type", "subject_code"])]

    def __str__(self) -> str:
        return f"{self.code} ({self.doc_type})"

    @property
    def url(self) -> str:
        """Where the bytes are, or "" for a row that records a paper original."""
        if not self.file_ref:
            return ""
        from django.core.files.storage import default_storage

        return default_storage.url(self.file_ref)

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_on and self.expires_on < timezone.localdate())
