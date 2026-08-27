"""ORIGIN cluster - who and what (section 03).

    User                a person. OneID is the identity source; this row is the
                        local shadow of it, which is why there is no password
                        requirement and why `pinfl` is the join key.
    Party               one table for farmers, aggregators, exporters, banks,
                        insurers, carriers and the operator - roles differ, the
                        shape does not.
    OrganisationType    the thirteen kinds of party, and which verification
                        checks each kind must clear. Configuration, not code:
                        adding a kind is a row.
    Capability / Role   the ten primitives, and the thirty-seven named bundles
                        the product speaks in. A role is a bundle; the
                        capability is what the API actually checks.
    Membership          a person's scoped role at a party, optionally narrowed
                        to named facilities.
    VerificationCheck   the six checks, five of them automatable.
    PartyVerification   one check's result for one party. The organisation's
                        `verification_status` is a cache of these rows.
    Farm                a party's production site.
    ProductionPlan      what a farm intends to grow, per season.
    Product             the catalogue entry a lot points at; `qc_spec` is the
                        per-product schema `quality.QcRecord.measurements` is
                        keyed by.

No model here references an outer cluster. Facility scope is held as a list of
facility codes rather than a foreign key into `storage` for exactly that
reason - identity must not depend on the warehouse.
"""

from django.contrib.auth.models import AbstractUser
from django.db import models

from apps.common.models import BaseModel


class User(AbstractUser):
    """A person.

    `AbstractUser` is subclassed rather than extended by a profile table: the
    fields below are read on every request that resolves a session, and a join
    for them would be paid forever. OneID owns identity, so `pinfl` is unique
    where present and `password` is unused for anyone who signs in through it.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INVITED = "invited", "Invited"
        PENDING = "pending", "Pending"
        SUSPENDED = "suspended", "Suspended"

    # OneID's personal identification number. Null until the person has been
    # through the gate at least once; unique when set.
    pinfl = models.CharField(max_length=14, unique=True, null=True, blank=True)
    display_name = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=16, choices=Status, default=Status.PENDING)
    oneid_verified = models.BooleanField(default=False)
    eimzo_verified = models.BooleanField(default=False)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["display_name", "username"]

    def __str__(self) -> str:
        return self.display_name or self.get_username()

    @property
    def initials(self) -> str:
        """`D. Yusupov` -> `DY`; the avatar the panels draw."""
        parts = [p for p in self.display_name.replace(".", " ").split() if p]
        return "".join(p[0] for p in parts[:2]).upper()


class OrganisationType(BaseModel):
    """One of the thirteen kinds of organisation.

    `required_checks` is what makes the verification screen honest: a carrier
    has no land rights to verify and a farmer needs no sector licence, so the
    checks that do not apply are never shown as passed.
    """

    code = models.SlugField(max_length=32, unique=True)
    label_key = models.CharField(max_length=32)
    icon = models.CharField(max_length=32)
    required_checks = models.JSONField(default=list)
    licence_register = models.CharField(max_length=120, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "code"]

    def __str__(self) -> str:
        return self.code


class Capability(BaseModel):
    """A primitive permission. Roles are bundles of these; the API checks these."""

    code = models.SlugField(max_length=32, unique=True)
    label_key = models.CharField(max_length=32)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "code"]
        verbose_name_plural = "capabilities"

    def __str__(self) -> str:
        return self.code


class Role(BaseModel):
    """A named bundle of capabilities, and where it applies."""

    class Scope(models.TextChoices):
        PLATFORM = "platform", "Platform"
        ORG = "org", "Organisation"
        FACILITY = "facility", "Facility"
        REGION = "region", "Region"
        NATIONAL = "national", "National"

    code = models.SlugField(max_length=48, unique=True)
    label_key = models.CharField(max_length=32)
    group_key = models.CharField(max_length=32)
    scope = models.CharField(max_length=16, choices=Scope)
    capabilities = models.ManyToManyField(Capability, related_name="roles")
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "code"]

    def __str__(self) -> str:
        return self.code


class Party(BaseModel):
    """An organisation on the platform, of any kind."""

    class Verification(models.TextChoices):
        VERIFIED = "verified", "Verified"
        REVIEW = "review", "In review"
        PENDING = "pending", "Pending"
        REJECTED = "rejected", "Rejected"

    code = models.CharField(max_length=24, unique=True)
    legal_name = models.CharField(max_length=200)
    type = models.ForeignKey(
        OrganisationType, on_delete=models.PROTECT, related_name="parties"
    )
    tin = models.CharField(max_length=24, blank=True)
    region = models.CharField(max_length=64, blank=True)
    district = models.CharField(max_length=64, blank=True)
    # A cache of PartyVerification rows, kept because every panel header reads
    # it and recomputing six checks per request would be silly.
    verification_status = models.CharField(
        max_length=16, choices=Verification, default=Verification.PENDING
    )
    verified_on = models.DateField(null=True, blank=True)
    verified_by = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "parties"

    def __str__(self) -> str:
        return f"{self.code} {self.legal_name}"


class Membership(BaseModel):
    """A person's role at an organisation.

    `facility_codes` holds storage facility codes as plain strings. That is
    deliberate: a foreign key would make identity depend on the storage
    cluster, and the rule is that clusters meet at the lot, not at each other.
    An empty list means the role applies party-wide.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    party = models.ForeignKey(Party, on_delete=models.CASCADE, related_name="memberships")
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="memberships")
    facility_codes = models.JSONField(default=list, blank=True)
    is_primary = models.BooleanField(default=True)

    class Meta:
        ordering = ["party__code", "user__display_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "party", "role"], name="uniq_membership"
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} @ {self.party.code} as {self.role.code}"

    def has_capability(self, code: str) -> bool:
        return self.role.capabilities.filter(code=code).exists()


class VerificationCheck(BaseModel):
    """One of the six checks an organisation clears to be let in."""

    class Mode(models.TextChoices):
        AUTO = "auto", "Automatic"
        MANUAL = "manual", "Manual"

    code = models.SlugField(max_length=32, unique=True)
    label_key = models.CharField(max_length=32)
    register = models.CharField(max_length=64, blank=True)
    mode = models.CharField(max_length=8, choices=Mode)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "code"]

    def __str__(self) -> str:
        return self.code


class PartyVerification(BaseModel):
    """The result of one check for one organisation."""

    class Result(models.TextChoices):
        PASS = "pass", "Pass"
        REVIEW = "review", "In review"
        FAIL = "fail", "Fail"
        PENDING = "pending", "Not started"

    party = models.ForeignKey(
        Party, on_delete=models.CASCADE, related_name="verifications"
    )
    # Named `verification_check`, not `check`: `Model.check()` is Django's own
    # system-check hook and a field of that name silently shadows it.
    verification_check = models.ForeignKey(
        VerificationCheck, on_delete=models.PROTECT, related_name="results"
    )
    result = models.CharField(max_length=8, choices=Result, default=Result.PENDING)
    evidence = models.JSONField(default=dict, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["party__code", "verification_check__sort_order"]
        constraints = [
            models.UniqueConstraint(
                fields=["party", "verification_check"], name="uniq_party_check"
            )
        ]

    def __str__(self) -> str:
        return f"{self.party.code}/{self.verification_check.code}={self.result}"


class Product(BaseModel):
    """A catalogue entry. Lots point at this; QC specs hang off it."""

    code = models.SlugField(max_length=32, unique=True)
    name_uz = models.CharField(max_length=64)
    name_ru = models.CharField(max_length=64)
    name_en = models.CharField(max_length=64)
    variety = models.CharField(max_length=64, blank=True)
    hs_code = models.CharField(max_length=16, blank=True)
    # Per-product QC schema: brix matters for melons, drip loss for fish.
    qc_spec = models.JSONField(default=dict, blank=True)
    # Shelf life under each storage mode, in days. What the sell-by date and
    # every "days left" figure on the panels is computed from.
    shelf_life_days = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return self.code

    def name(self, lang: str = "uz") -> str:
        return getattr(self, f"name_{lang}", self.name_uz)


class Farm(BaseModel):
    """A production site belonging to a party."""

    code = models.CharField(max_length=24, unique=True)
    party = models.ForeignKey(Party, on_delete=models.CASCADE, related_name="farms")
    name = models.CharField(max_length=160)
    owner_name = models.CharField(max_length=160, blank=True)
    region = models.CharField(max_length=64, blank=True)
    district = models.CharField(max_length=64, blank=True)
    hectares = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    certifications = models.JSONField(default=list, blank=True)
    # PostGIS boundary goes here when the GIS dependency lands; the shape of
    # everything above is unaffected by it.
    boundary_geojson = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} {self.name}"


class ProductionPlan(BaseModel):
    """What a farm intends to grow this season.

    Windows are dates, not instants - a harvest window is a fortnight, not a
    timestamp.
    """

    farm = models.ForeignKey(Farm, on_delete=models.CASCADE, related_name="plans")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="plans")
    season_year = models.PositiveSmallIntegerField()
    planted_hectares = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    expected_yield_g = models.BigIntegerField(default=0)
    harvest_from = models.DateField(null=True, blank=True)
    harvest_to = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-season_year", "farm__code"]
        constraints = [
            models.UniqueConstraint(
                fields=["farm", "product", "season_year"], name="uniq_plan_per_season"
            )
        ]

    def __str__(self) -> str:
        return f"{self.farm.code} {self.product.code} {self.season_year}"
