"""STORAGE cluster (section 03).

    Facility            a hub, pack-house or ZEROCO chamber
    StorageZone         an addressable space inside a facility, with its own
                        target temperature and humidity band
    GateArrival         a consignment expected at the gate; it becomes a lot
    StoragePlacement    lot, zone, position, placed_at, removed_at. Inventory
                        age and remaining saleable window are computed from
                        this, never stored.
    ConditionReading    the time series, per zone and per shipment
    ConditionExcursion  a detected breach of a band - the evidence an insurance
                        claim is built from

Two shapes here are deliberate:

* `ConditionReading` and `ConditionExcursion` are **scoped by code, not by
  foreign key**: a reading belongs to `storage_zone Z-ZEROCO-01` or to
  `shipment SH-2026-0210`, and a foreign key into `commercial` would make the
  storage cluster depend on the logistics cluster. Clusters meet at the lot.
* `ConditionReading` carries a plain integer primary key and no `updated_at`.
  It will outnumber every other table a thousand to one; it is the one table
  meant to be partitioned or moved to TimescaleDB, and it must stay cheap
  enough to insert in batches of ten thousand.
"""

from django.db import models

from apps.common.models import BaseModel, UUIDModel


class Facility(BaseModel):
    """A hub, pack-house or chamber. `code` is what membership scopes name."""

    class Kind(models.TextChoices):
        HUB = "hub", "Hub"
        PACKHOUSE = "packhouse", "Pack-house"
        CHAMBER = "chamber", "ZEROCO chamber"
        YARD = "yard", "Yard"

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=160)
    kind = models.CharField(max_length=16, choices=Kind, default=Kind.HUB)
    operator_party = models.ForeignKey(
        "registry.Party", on_delete=models.PROTECT, related_name="facilities"
    )
    region = models.CharField(max_length=64, blank=True)
    district = models.CharField(max_length=64, blank=True)
    address = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "facilities"

    def __str__(self) -> str:
        return f"{self.code} {self.name}"


class StorageZone(BaseModel):
    """An addressable room with its own target band.

    Capacity is grams, like every other quantity. `current_*` are caches of the
    latest reading so a dashboard listing twelve zones does not run twelve
    time-series queries; the readings remain the truth.
    """

    class Mode(models.TextChoices):
        ZEROCO = "zeroco", "ZEROCO"
        COLD = "cold", "Conventional cold"
        PRE = "pre", "Pre-cool"
        DRY = "dry", "Dry"

    code = models.CharField(max_length=32, unique=True)
    facility = models.ForeignKey(
        Facility, on_delete=models.CASCADE, related_name="zones"
    )
    mode = models.CharField(max_length=8, choices=Mode)
    capacity_g = models.BigIntegerField()
    target_temp_c = models.DecimalField(max_digits=5, decimal_places=2)
    target_rh_pct = models.DecimalField(max_digits=5, decimal_places=2)
    # The band that turns a reading into an excursion.
    tolerance_temp_c = models.DecimalField(max_digits=4, decimal_places=2, default=2)
    tolerance_rh_pct = models.DecimalField(max_digits=4, decimal_places=2, default=5)
    current_temp_c = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    current_rh_pct = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    reading_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return self.code

    @property
    def used_g(self) -> int:
        """Live fill, from open placements. Never stored - it would drift."""
        return (
            self.placements.filter(removed_at__isnull=True).aggregate(
                total=models.Sum("quantity_g")
            )["total"]
            or 0
        )

    @property
    def off_band(self) -> bool:
        if self.current_temp_c is None:
            return False
        return abs(self.current_temp_c - self.target_temp_c) > self.tolerance_temp_c


class GateArrival(BaseModel):
    """A consignment expected at, or standing on, the weighbridge.

    It becomes a lot at registration; `lot` is filled in then, which is also
    how the gate screen knows what it has already processed.
    """

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        WEIGHING = "weighing", "On the weighbridge"
        REGISTERED = "registered", "Registered"
        REJECTED = "rejected", "Turned away"

    facility = models.ForeignKey(
        Facility, on_delete=models.CASCADE, related_name="arrivals"
    )
    farm = models.ForeignKey(
        "registry.Farm", null=True, blank=True, on_delete=models.PROTECT,
        related_name="arrivals",
    )
    product = models.ForeignKey(
        "registry.Product", on_delete=models.PROTECT, related_name="arrivals"
    )
    vehicle = models.CharField(max_length=32)
    expected_at = models.DateTimeField()
    estimated_weight_g = models.BigIntegerField(default=0)
    status = models.CharField(max_length=12, choices=Status, default=Status.QUEUED)
    # Set by the gate screen at registration. A code, not a foreign key into
    # the spine, so an arrival can be recorded before the lot exists.
    lot_code = models.CharField(max_length=32, blank=True)

    class Meta:
        ordering = ["expected_at"]

    def __str__(self) -> str:
        return f"{self.vehicle} @ {self.expected_at:%H:%M}"


class StoragePlacement(BaseModel):
    """Where a lot physically is, and for how long it has been there.

    Storage age and remaining window are derived from `placed_at`; storing
    either would mean recomputing rows nightly and getting it wrong once.
    """

    lot = models.ForeignKey(
        "lots.Lot", on_delete=models.PROTECT, related_name="placements"
    )
    zone = models.ForeignKey(
        StorageZone, on_delete=models.PROTECT, related_name="placements"
    )
    position = models.CharField(max_length=16, blank=True)
    quantity_g = models.BigIntegerField()
    placed_at = models.DateTimeField()
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-placed_at"]
        indexes = [models.Index(fields=["zone", "removed_at"])]

    def __str__(self) -> str:
        return f"{self.lot.code} @ {self.zone.code}/{self.position}"

    @property
    def is_open(self) -> bool:
        return self.removed_at is None


class ConditionReading(models.Model):
    """One sensor sample.

    Scoped by (`scope_type`, `scope_code`) rather than a foreign key so the
    same table serves a storage zone and a reefer container without the
    storage cluster learning what a shipment is.
    """

    class Scope(models.TextChoices):
        ZONE = "storage_zone", "Storage zone"
        SHIPMENT = "shipment", "Shipment"

    id = models.BigAutoField(primary_key=True)
    scope_type = models.CharField(max_length=16, choices=Scope)
    scope_code = models.CharField(max_length=32)
    sensor_id = models.CharField(max_length=32)
    recorded_at = models.DateTimeField()
    temp_c = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    rh_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["-recorded_at"]
        indexes = [
            models.Index(fields=["scope_type", "scope_code", "-recorded_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["scope_type", "scope_code", "sensor_id", "recorded_at"],
                name="uniq_reading_per_sensor_instant",
            )
        ]

    def __str__(self) -> str:
        return f"{self.scope_code} {self.recorded_at:%Y-%m-%d %H:%M} {self.temp_c}"


class ConditionExcursion(UUIDModel):
    """A breach of a band, with the trace that proves it.

    This row is the evidence an insurance claim is built from, so it holds the
    sampled trace rather than pointing at readings that a retention policy may
    later thin out. `affected_lot_codes` is a list of codes for the same reason
    the scope is: the storage cluster must not depend on anything downstream.
    """

    class Metric(models.TextChoices):
        TEMP = "temp", "Temperature"
        RH = "rh", "Humidity"

    class Severity(models.TextChoices):
        MINOR = "minor", "Minor"
        MAJOR = "major", "Major"
        CRITICAL = "critical", "Critical"

    code = models.CharField(max_length=32, unique=True)
    scope_type = models.CharField(max_length=16, choices=ConditionReading.Scope)
    scope_code = models.CharField(max_length=32)
    metric = models.CharField(max_length=8, choices=Metric, default=Metric.TEMP)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    peak_value = models.DecimalField(max_digits=6, decimal_places=2)
    threshold = models.DecimalField(max_digits=6, decimal_places=2)
    severity = models.CharField(
        max_length=8, choices=Severity, default=Severity.MINOR
    )
    sensor_id = models.CharField(max_length=32, blank=True)
    trace = models.JSONField(default=list, blank=True)
    affected_lot_codes = models.JSONField(default=list, blank=True)
    resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return self.code

    @property
    def duration_minutes(self) -> int:
        if not self.ended_at:
            return 0
        return int((self.ended_at - self.started_at).total_seconds() // 60)
