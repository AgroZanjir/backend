"""FINANCE & RISK cluster (section 03).

    FinanceApplication      what the lender port reads and writes
    Encumbrance             a lien over a lot; blocks dispatch, released by
                            event and never by deletion
    Policy                  insurance cover over lots or shipments
    Claim                   built from condition-excursion evidence
    SettlementAllocation    the waterfall that splits an incoming export
                            payment across lender, insurer, logistics,
                            operator and farmer

All amounts are integer minor units with an explicit currency.

Nothing here is reachable from `commercial` or `storage`: a shipment does not
know about a loan. This cluster reaches *out* to the lot spine, and to the
storage cluster's excursions by code rather than by foreign key, so the
dependency arrow only ever points one way.

Rule 3 - encumbrance is an overlay, not a status - is enforced in `apps.py`,
which registers `dispatch_guard` with the lot spine at app load. `lots` never
imports this module; it simply asks every registered guard whether a lot may
leave.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel, Currency, MoneyModel


class FinanceApplication(MoneyModel, BaseModel):
    """A credit request secured against lots.

    The state machine is identical whether a human keys the bank's decision in
    from the admin or an ISO 20022 adapter posts it: that is the entire point
    of the port. `port_reference` is whatever the far side calls this request.
    """

    class Kind(models.TextChoices):
        INVENTORY = "inventory", "Inventory finance"
        PRE_EXPORT = "pre_export", "Pre-export finance"
        WORKING = "working", "Working capital"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        REVIEW = "review", "In review"
        APPROVED = "approved", "Approved"
        DISBURSED = "disbursed", "Disbursed"
        REPAID = "repaid", "Repaid"
        REJECTED = "rejected", "Rejected"

    #: Which statuses may follow which, as the lot has. An application only
    #: moves forwards: a decision that was wrong is a new application, not a
    #: rewritten one, because a lien and a disbursement were hung on the old
    #: answer. `review` is optional - a small application can be approved
    #: straight off the queue, and the lender is the one who says which.
    TRANSITIONS: dict[str, tuple[str, ...]] = {
        Status.DRAFT: (Status.SUBMITTED,),
        Status.SUBMITTED: (Status.REVIEW, Status.APPROVED, Status.REJECTED),
        Status.REVIEW: (Status.APPROVED, Status.REJECTED),
        Status.APPROVED: (Status.DISBURSED,),
        Status.DISBURSED: (Status.REPAID,),
        Status.REPAID: (),
        Status.REJECTED: (),
    }

    code = models.CharField(max_length=32, unique=True)
    applicant_party = models.ForeignKey(
        "registry.Party", on_delete=models.PROTECT, related_name="finance_applications"
    )
    lender_party = models.ForeignKey(
        "registry.Party", on_delete=models.PROTECT, related_name="finance_underwritten"
    )
    kind = models.CharField(max_length=12, choices=Kind, default=Kind.INVENTORY)
    status = models.CharField(max_length=12, choices=Status, default=Status.DRAFT)
    collateral_lots = models.ManyToManyField(
        "lots.Lot", blank=True, related_name="finance_applications"
    )
    ltv_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    applied_on = models.DateField(default=timezone.localdate)
    decided_on = models.DateField(null=True, blank=True)
    port_reference = models.CharField(max_length=64, blank=True)
    port_state = models.CharField(max_length=32, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-applied_on", "code"]

    def __str__(self) -> str:
        return self.code

    # -- transitions ------------------------------------------------------

    def can_transition_to(self, status: str) -> bool:
        return status in self.TRANSITIONS.get(self.status, ())

    def transition(self, status: str, *, force: bool = False) -> None:
        """Move the application, refusing anything the lifecycle disallows.

        `force` is for the admin, which is the manual-adapter surface: the
        reason it exists is to correct a state the product cannot reach, and
        a bank that settles by telephone will need it.
        """
        if not force and not self.can_transition_to(status):
            raise ValidationError(
                f"{self.code}: {self.status} -> {status} is not a permitted "
                "transition"
            )
        self.status = status
        # The day the answer was given. A disbursement is not a decision, so
        # it does not overwrite the day the decision was made.
        if status in {self.Status.APPROVED, self.Status.REJECTED}:
            self.decided_on = timezone.localdate()
        self.save(update_fields=["status", "decided_on", "updated_at"])

    @property
    def collateral_value_minor(self) -> int:
        """What the pledged lots are worth, in the application's currency.

        Valuations are held on the lot in UZS; a foreign-currency application
        must snapshot an FX rate before this number means anything, which is
        why the currency mismatch is not silently converted here.
        """
        return sum(lot.valuation_minor for lot in self.collateral_lots.all())


class Encumbrance(MoneyModel, BaseModel):
    """A lien over one lot.

    Its presence blocks dispatch; its release is an event, not a deletion -
    `released_at` is set and the row stays, because the question a bank asks
    six months later is "was this ever pledged?", not "is it now?".
    """

    lot = models.ForeignKey(
        "lots.Lot", on_delete=models.PROTECT, related_name="encumbrances"
    )
    application = models.ForeignKey(
        FinanceApplication, on_delete=models.PROTECT, related_name="encumbrances"
    )
    holder_party = models.ForeignKey(
        "registry.Party", on_delete=models.PROTECT, related_name="liens_held"
    )
    created_on = models.DateField(default=timezone.localdate)
    released_at = models.DateTimeField(null=True, blank=True)
    release_reference = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-created_on"]
        indexes = [models.Index(fields=["lot", "released_at"])]

    def __str__(self) -> str:
        state = "released" if self.released_at else "active"
        return f"{self.lot.code} <- {self.application.code} ({state})"

    @property
    def is_active(self) -> bool:
        return self.released_at is None

    def release(self, *, reference: str = "") -> None:
        """Release the lien and write it into the lot's history."""
        if self.released_at:
            return
        self.released_at = timezone.now()
        self.release_reference = reference
        self.save(update_fields=["released_at", "release_reference", "updated_at"])
        self.lot.log(
            "lien_released",
            actor_party=self.holder_party,
            actor_label=self.holder_party.legal_name,
            severity="accept",
            payload={
                "application": self.application.code,
                "amount_minor": self.amount_minor,
                "currency": self.currency,
                "reference": reference,
            },
        )


class Policy(MoneyModel, BaseModel):
    """Insurance cover over lots or shipments."""

    class Kind(models.TextChoices):
        STORAGE = "storage", "Storage cover"
        CARGO = "cargo", "Cargo cover"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    code = models.CharField(max_length=32, unique=True)
    insurer_party = models.ForeignKey(
        "registry.Party", on_delete=models.PROTECT, related_name="policies_written"
    )
    holder_party = models.ForeignKey(
        "registry.Party", on_delete=models.PROTECT, related_name="policies_held"
    )
    kind = models.CharField(max_length=8, choices=Kind, default=Kind.STORAGE)
    status = models.CharField(max_length=12, choices=Status, default=Status.ACTIVE)
    covered_lots = models.ManyToManyField(
        "lots.Lot", blank=True, related_name="policies"
    )
    # Cargo cover follows a shipment; by code, because this cluster must not
    # depend on `commercial`.
    covered_shipment_code = models.CharField(max_length=32, blank=True)
    starts_on = models.DateField()
    ends_on = models.DateField()
    deductible_minor = models.BigIntegerField(default=0)

    class Meta:
        ordering = ["-starts_on", "code"]
        verbose_name_plural = "policies"

    def __str__(self) -> str:
        return self.code


class Claim(MoneyModel, BaseModel):
    """A claim against a policy, built from excursion evidence."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        REVIEW = "review", "In review"
        APPROVED = "approved", "Approved"
        PAID = "paid", "Paid"
        DECLINED = "declined", "Declined"

    code = models.CharField(max_length=32, unique=True)
    policy = models.ForeignKey(Policy, on_delete=models.PROTECT, related_name="claims")
    lot = models.ForeignKey(
        "lots.Lot", null=True, blank=True, on_delete=models.PROTECT,
        related_name="claims",
    )
    # The excursion this is built on, by code. See the module docstring.
    excursion_code = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=12, choices=Status, default=Status.DRAFT)
    filed_on = models.DateField(default=timezone.localdate)
    decided_on = models.DateField(null=True, blank=True)
    assessed_minor = models.BigIntegerField(default=0)
    port_reference = models.CharField(max_length=64, blank=True)
    port_state = models.CharField(max_length=32, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-filed_on", "code"]

    def __str__(self) -> str:
        return self.code


class SettlementAllocation(MoneyModel, BaseModel):
    """One line of the waterfall an export payment is split by.

    Priority is ascending: the lender is paid before the farmer, and that
    ordering is data rather than code so a different contract can carry a
    different waterfall without a deployment.
    """

    class Category(models.TextChoices):
        LENDER = "lender", "Loan repayment"
        INSURER = "insurer", "Premium"
        LOGISTICS = "logistics", "Logistics"
        OPERATOR = "operator", "Operator fee"
        FARMER = "farmer", "Producer"
        TAX = "tax", "Duties and tax"

    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        RELEASED = "released", "Released"
        PAID = "paid", "Paid"

    # The contract being settled, by code: `commercial` owns that row and this
    # cluster does not import it.
    export_contract_code = models.CharField(max_length=32, db_index=True)
    priority = models.PositiveSmallIntegerField()
    party = models.ForeignKey(
        "registry.Party", on_delete=models.PROTECT, related_name="settlement_lines"
    )
    category = models.CharField(max_length=12, choices=Category)
    status = models.CharField(max_length=10, choices=Status, default=Status.PLANNED)
    paid_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["export_contract_code", "priority"]
        constraints = [
            models.UniqueConstraint(
                fields=["export_contract_code", "priority"],
                name="uniq_waterfall_priority",
            )
        ]

    def __str__(self) -> str:
        return f"{self.export_contract_code}#{self.priority} {self.category}"


def dispatch_guard(lot) -> list[str]:
    """Rule 3, enforced: an active lien stops a lot leaving.

    Registered with the lot spine in `FinanceConfig.ready()`. The spine calls
    it without knowing this cluster exists.
    """
    liens = Encumbrance.objects.filter(lot=lot, released_at__isnull=True).select_related(
        "holder_party", "application"
    )
    return [
        f"lien {lien.application.code} held by {lien.holder_party.legal_name} "
        f"({lien.amount_minor} {Currency(lien.currency).label})"
        for lien in liens
    ]
