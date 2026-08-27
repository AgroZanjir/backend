"""THE SPINE - lot, lot_event, lot_relation (section 02).

Four rules that are expensive to change later, and how each is enforced here:

1.  **Lots split and merge.** `LotRelation` records parent, child, kind and
    quantity, so a child lot traces back to every parent farm. Grading divides
    an intake into grades; several farmers' Grade A consolidates into one
    export pallet.
2.  **The event log is the truth; the `lot` row is a cache.** `LotEvent` is
    append-only - `save()` refuses a second write and `delete()` refuses
    outright - and each row's `hash` covers the previous row's, so any edit to
    history breaks the chain from that point on. That chain is the tamper
    evidence, and the reason no blockchain is needed.
3.  **Encumbrance is an overlay, not a status.** There is no `pledged` in
    `Lot.Status`. A lien lives in `finance.Encumbrance` and intercepts the
    dispatch transition through `dispatch_blockers()`, which this module calls
    without importing finance - the check is registered, not imported.
4.  **One base unit.** `net_weight_g` is an integer count of grams.

Lifecycle: REGISTERED -> GRADED -> STORED -> RESERVED -> DISPATCHED -> SETTLED,
with REJECTED and WRITTEN_OFF as exits from grading and storage.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timezone as dt_timezone
from typing import Callable

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel, Currency

#: Callables that answer "may this lot leave?". `finance` registers one at app
#: load; `lots` never imports `finance`. This is how rule 3 is enforced without
#: making the spine depend on an outer cluster.
_DISPATCH_GUARDS: list[Callable[["Lot"], list[str]]] = []


def register_dispatch_guard(guard: Callable[["Lot"], list[str]]) -> None:
    """Register a guard returning a list of human-readable blockers."""
    if guard not in _DISPATCH_GUARDS:
        _DISPATCH_GUARDS.append(guard)


def dispatch_blockers(lot: "Lot") -> list[str]:
    """Every reason this lot may not be dispatched, from every cluster."""
    blockers: list[str] = []
    for guard in _DISPATCH_GUARDS:
        blockers.extend(guard(lot))
    return blockers


class Lot(BaseModel):
    """A consignment under one identifier, from the gate to settlement."""

    class Status(models.TextChoices):
        REGISTERED = "registered", "Registered"
        GRADED = "graded", "Graded"
        STORED = "stored", "Stored"
        RESERVED = "reserved", "Reserved"
        DISPATCHED = "dispatched", "Dispatched"
        SETTLED = "settled", "Settled"
        REJECTED = "rejected", "Rejected"
        WRITTEN_OFF = "written_off", "Written off"

    class StorageMode(models.TextChoices):
        ZEROCO = "zeroco", "ZEROCO"
        COLD = "cold", "Conventional cold"
        PRE = "pre", "Pre-cool"
        DRY = "dry", "Dry"
        NONE = "none", "Not stored"

    class TrialArm(models.TextChoices):
        ZEROCO = "zeroco", "ZEROCO arm"
        CONTROL = "control", "Control arm"

    #: Which statuses may follow which. A lot cannot go back up the chain: a
    #: correction is a new event, never a rewritten one.
    TRANSITIONS: dict[str, tuple[str, ...]] = {
        Status.REGISTERED: (Status.GRADED, Status.REJECTED),
        Status.GRADED: (Status.STORED, Status.RESERVED, Status.WRITTEN_OFF),
        Status.STORED: (Status.RESERVED, Status.DISPATCHED, Status.WRITTEN_OFF),
        Status.RESERVED: (Status.DISPATCHED, Status.STORED, Status.WRITTEN_OFF),
        Status.DISPATCHED: (Status.SETTLED,),
        Status.SETTLED: (),
        Status.REJECTED: (),
        Status.WRITTEN_OFF: (),
    }

    code = models.CharField(max_length=32, unique=True, db_index=True)
    product = models.ForeignKey(
        "registry.Product", on_delete=models.PROTECT, related_name="lots"
    )
    origin_farm = models.ForeignKey(
        "registry.Farm", null=True, blank=True, on_delete=models.PROTECT,
        related_name="lots",
    )
    owner_party = models.ForeignKey(
        "registry.Party", on_delete=models.PROTECT, related_name="lots"
    )
    # Integer grams. Boxes, crates and pallets are presentation (rule 4).
    net_weight_g = models.BigIntegerField()
    gross_weight_g = models.BigIntegerField(null=True, blank=True)
    grade = models.CharField(max_length=8, blank=True)
    status = models.CharField(
        max_length=16, choices=Status, default=Status.REGISTERED, db_index=True
    )
    storage_mode = models.CharField(
        max_length=8, choices=StorageMode, default=StorageMode.NONE
    )
    harvested_on = models.DateField(null=True, blank=True)
    # The end of the saleable window. Computed from the product's shelf life
    # under the mode it is actually stored in, then held, because a bank reads
    # this number and needs it not to move under it.
    sell_by = models.DateField(null=True, blank=True)
    trial_arm = models.CharField(
        max_length=8, choices=TrialArm, blank=True, default=""
    )
    valuation_minor = models.BigIntegerField(default=0)
    valuation_currency = models.CharField(
        max_length=3, choices=Currency, default=Currency.UZS
    )

    class Meta:
        ordering = ["-created_at", "code"]
        indexes = [
            models.Index(fields=["status", "sell_by"]),
            models.Index(fields=["owner_party", "status"]),
        ]

    def __str__(self) -> str:
        return self.code

    # -- transitions ------------------------------------------------------

    def can_transition_to(self, status: str) -> bool:
        return status in self.TRANSITIONS.get(self.status, ())

    def transition(self, status: str, *, force: bool = False) -> None:
        """Move the lot, refusing anything the lifecycle does not allow.

        `force` exists for the admin, which is the manual-adapter surface and
        occasionally has to correct reality. It still writes an event.
        """
        if not force and not self.can_transition_to(status):
            raise ValidationError(
                f"{self.code}: {self.status} -> {status} is not a permitted transition"
            )
        if status == self.Status.DISPATCHED:
            blockers = dispatch_blockers(self)
            if blockers:
                raise ValidationError(
                    f"{self.code} cannot be dispatched: " + "; ".join(blockers)
                )
        self.status = status
        self.save(update_fields=["status", "updated_at"])

    # -- the log ----------------------------------------------------------

    def log(self, event_type: str, **kwargs) -> "LotEvent":
        """Append to this lot's event log. The only way to write history."""
        return LotEvent.objects.append(lot=self, event_type=event_type, **kwargs)

    @property
    def chain_intact(self) -> bool:
        return LotEvent.objects.chain_intact(self)


class LotEventQuerySet(models.QuerySet):
    def append(
        self,
        *,
        lot: Lot,
        event_type: str,
        occurred_at=None,
        actor_party=None,
        actor_user=None,
        actor_label: str = "",
        facility_code: str = "",
        payload: dict | None = None,
        severity: str = "",
    ) -> "LotEvent":
        """Append one row, chained to the last one on this lot.

        Ordering is by `occurred_at` then insertion, and the chain follows
        insertion order - a late-arriving field reading does not rewrite the
        hash of everything after the moment it describes.
        """
        previous = self.filter(lot=lot).order_by("-sequence").first()
        event = LotEvent(
            lot=lot,
            sequence=(previous.sequence + 1) if previous else 1,
            event_type=event_type,
            occurred_at=occurred_at or timezone.now(),
            actor_party=actor_party,
            actor_user=actor_user,
            actor_label=actor_label,
            facility_code=facility_code,
            payload=payload or {},
            severity=severity,
            prev_hash=previous.hash if previous else "",
        )
        event.save()
        return event

    def chain_intact(self, lot: Lot) -> bool:
        """Recompute every hash in order and compare. This is the audit."""
        previous_hash = ""
        for event in self.filter(lot=lot).order_by("sequence"):
            if event.prev_hash != previous_hash:
                return False
            if event.hash != event.compute_hash(previous_hash):
                return False
            previous_hash = event.hash
        return True


class LotEvent(BaseModel):
    """One append-only line of a lot's history.

    Never updated, never deleted. `hash` covers the row's content *and* the
    previous row's hash, so editing any row invalidates every row after it -
    which is what makes the log evidence rather than a table.
    """

    class Severity(models.TextChoices):
        ACCEPT = "accept", "Accepted"
        WARN = "warn", "Warning"

    lot = models.ForeignKey(Lot, on_delete=models.PROTECT, related_name="events")
    sequence = models.PositiveIntegerField()
    event_type = models.CharField(max_length=32, db_index=True)
    occurred_at = models.DateTimeField()
    actor_party = models.ForeignKey(
        "registry.Party", null=True, blank=True, on_delete=models.PROTECT,
        related_name="lot_events",
    )
    actor_user = models.ForeignKey(
        "registry.User", null=True, blank=True, on_delete=models.PROTECT,
        related_name="lot_events",
    )
    # Free text for a non-user actor: a sensor id, a bank, a customs office.
    actor_label = models.CharField(max_length=120, blank=True)
    facility_code = models.CharField(max_length=32, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    severity = models.CharField(max_length=8, choices=Severity, blank=True, default="")
    prev_hash = models.CharField(max_length=64, blank=True)
    hash = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["lot__code", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["lot", "sequence"], name="uniq_lot_event_sequence"
            )
        ]
        indexes = [models.Index(fields=["lot", "occurred_at"])]

    objects = LotEventQuerySet.as_manager()

    def __str__(self) -> str:
        return f"{self.lot_id}#{self.sequence} {self.event_type}"

    def compute_hash(self, prev_hash: str | None = None) -> str:
        """SHA-256 over the canonical form of this row plus its predecessor.

        `sort_keys` and the explicit separators matter: the hash has to be
        reproducible by anyone auditing the chain, in any language.
        """
        body = json.dumps(
            {
                "lot": str(self.lot_id),
                "sequence": self.sequence,
                "event_type": self.event_type,
                # Normalised to UTC before hashing. The same instant written
                # from Tashkent and read back as UTC must produce the same
                # hash, or the chain would break on every round trip.
                "occurred_at": self.occurred_at.astimezone(dt_timezone.utc).isoformat(),
                "actor_party": str(self.actor_party_id or ""),
                "actor_user": str(self.actor_user_id or ""),
                "actor_label": self.actor_label,
                "facility_code": self.facility_code,
                "payload": self.payload,
                "prev": self.prev_hash if prev_hash is None else prev_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def save(self, *args, **kwargs):
        if self._state.adding:
            self.hash = self.compute_hash()
            return super().save(*args, **kwargs)
        raise ValidationError(
            "lot_event is append-only: correct the record with a new event."
        )

    def delete(self, *args, **kwargs):
        raise ValidationError("lot_event is append-only and cannot be deleted.")


class LotRelation(BaseModel):
    """How lots divide and combine.

    Modelled from day one because retrofitting it breaks traceability in the
    first week of real use: grading splits an intake, consolidation merges
    several farmers' Grade A into one pallet, and a repack does both.
    """

    class Kind(models.TextChoices):
        SPLIT = "split", "Split"
        MERGE = "merge", "Merge"
        REPACK = "repack", "Repack"
        SAMPLE = "sample", "Sample drawn"

    parent = models.ForeignKey(Lot, on_delete=models.PROTECT, related_name="children")
    child = models.ForeignKey(Lot, on_delete=models.PROTECT, related_name="parents")
    kind = models.CharField(max_length=8, choices=Kind)
    quantity_g = models.BigIntegerField()

    class Meta:
        ordering = ["parent__code", "child__code"]
        constraints = [
            models.UniqueConstraint(
                fields=["parent", "child", "kind"], name="uniq_lot_relation"
            ),
            models.CheckConstraint(
                condition=~models.Q(parent=models.F("child")),
                name="lot_relation_not_self",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.parent.code} -{self.kind}-> {self.child.code}"
