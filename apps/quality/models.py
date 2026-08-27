"""QUALITY cluster (section 03).

    QcRecord            lot, stage, inspector, measurements, grade assigned
    PilotTrial          one controlled comparison: one source lot, two arms
    TrialArm            the ZEROCO arm and the control arm
    TrialObservation    the repeated measurements on a schedule

`QcRecord.measurements` is structured JSON keyed by the product's `qc_spec`,
not fixed columns - brix matters for melons, drip loss for fish, and a column
per metric would mean a migration per product.

The pilot trial is the controlled comparison the whole ZEROCO business case
rests on. Two things here exist to keep that case defensible:

* `TrialArm.projection` holds the modelled curve and is stored separately from
  `TrialObservation` rows, which hold what was actually measured. The panels
  draw the measured part solid and the projection dashed. A projection that
  can be mistaken for a measurement is how a business case dies in review.
* Observations are scheduled by `day_index` against `PilotTrial.schedule_days`,
  so a missed sampling day is visible as a gap rather than silently absent.
"""

from django.db import models

from apps.common.models import BaseModel


class QcRecord(BaseModel):
    """One inspection of one lot at one stage."""

    class Stage(models.TextChoices):
        INTAKE = "intake", "At intake"
        PRE_STORAGE = "pre_storage", "Before storage"
        IN_STORAGE = "in_storage", "In storage"
        PRE_DISPATCH = "pre_dispatch", "Before dispatch"
        ARRIVAL = "arrival", "On arrival"

    lot = models.ForeignKey(
        "lots.Lot", on_delete=models.PROTECT, related_name="qc_records"
    )
    stage = models.CharField(max_length=16, choices=Stage)
    inspected_on = models.DateField()
    inspector = models.ForeignKey(
        "registry.User", null=True, blank=True, on_delete=models.PROTECT,
        related_name="qc_records",
    )
    inspector_label = models.CharField(max_length=120, blank=True)
    # Keyed by the product's qc_spec: {"brix": 12.4, "firmness_n": 8.4, ...}
    measurements = models.JSONField(default=dict, blank=True)
    grade_assigned = models.CharField(max_length=8, blank=True)
    defect_pct = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    passed = models.BooleanField(default=True)
    note = models.TextField(blank=True)
    # The lab report in the vault, by code. The documents cluster is polymorphic
    # over everything; pointing at it by key keeps the direction one-way.
    lab_document_code = models.CharField(max_length=48, blank=True)

    class Meta:
        ordering = ["lot__code", "inspected_on"]
        indexes = [models.Index(fields=["lot", "stage"])]

    def __str__(self) -> str:
        return f"{self.lot.code} {self.stage}"


class PilotTrial(BaseModel):
    """One controlled storage comparison."""

    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        ABANDONED = "abandoned", "Abandoned"

    code = models.CharField(max_length=32, unique=True)
    product = models.ForeignKey(
        "registry.Product", on_delete=models.PROTECT, related_name="trials"
    )
    facility_code = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=12, choices=Status, default=Status.PLANNED)
    started_on = models.DateField(null=True, blank=True)
    completed_on = models.DateField(null=True, blank=True)
    # The sampling schedule in days from day zero: [0, 7, 14, ...].
    schedule_days = models.JSONField(default=list)
    protocol = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-started_on", "code"]

    def __str__(self) -> str:
        return self.code

    @property
    def observed_points(self) -> int:
        """How many sampling days have actually been recorded on every arm."""
        arms = self.arms.count()
        if not arms:
            return 0
        return (
            TrialObservation.objects.filter(arm__trial=self)
            .values("day_index")
            .annotate(n=models.Count("id"))
            .filter(n__gte=arms)
            .count()
        )


class TrialArm(BaseModel):
    """One side of the comparison."""

    class Kind(models.TextChoices):
        ZEROCO = "zeroco", "ZEROCO"
        CONTROL = "control", "Control"

    trial = models.ForeignKey(PilotTrial, on_delete=models.CASCADE, related_name="arms")
    kind = models.CharField(max_length=8, choices=Kind)
    lot = models.ForeignKey(
        "lots.Lot", on_delete=models.PROTECT, related_name="trial_arms"
    )
    zone_code = models.CharField(max_length=32, blank=True)
    quantity_g = models.BigIntegerField(default=0)
    # The modelled curve, per metric: {"loss": [0, 1.8, ...], ...}. Explicitly
    # not a measurement; the panels draw it dashed and say so.
    projection = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["trial__code", "kind"]
        constraints = [
            models.UniqueConstraint(fields=["trial", "kind"], name="uniq_arm_per_trial")
        ]

    def __str__(self) -> str:
        return f"{self.trial.code}/{self.kind}"


class TrialObservation(BaseModel):
    """A measurement taken on one arm on one scheduled day."""

    arm = models.ForeignKey(
        TrialArm, on_delete=models.CASCADE, related_name="observations"
    )
    day_index = models.PositiveSmallIntegerField()
    observed_on = models.DateField()
    observer = models.ForeignKey(
        "registry.User", null=True, blank=True, on_delete=models.PROTECT,
        related_name="trial_observations",
    )
    weight_loss_pct = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True
    )
    waste_pct = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True
    )
    firmness_n = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True
    )
    colour_score = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True
    )
    markdown_pct = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True
    )
    photo_refs = models.JSONField(default=list, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["arm__trial__code", "day_index", "arm__kind"]
        constraints = [
            models.UniqueConstraint(
                fields=["arm", "day_index"], name="uniq_observation_per_day"
            )
        ]

    def __str__(self) -> str:
        return f"{self.arm} d{self.day_index}"
