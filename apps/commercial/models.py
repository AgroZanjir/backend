"""COMMERCIAL and LOGISTICS clusters (section 03).

    OfftakeContract     a domestic purchase commitment against future lots
    ExportContract      the export sale a settlement waterfall pays out from
    PriceTerm           pricing attached to a contract
    Shipment            a movement, with its own condition-reading scope
    ShipmentLine        lot quantities on a shipment

Money is integer minor units plus a currency, with the FX rate snapshotted on
the row that used it - this project spans UZS, USD and JPY, and an export
contract signed in dollars settles in sum at a rate that must not be looked up
again later.

Delivery windows are dates, not instants.

Nothing here references `finance` or `storage`. A shipment does not know about
a loan, and its temperature trace is found by scope code (`shipment`,
`SH-2026-0210`) in the storage cluster's time series rather than by a foreign
key in either direction.
"""

from django.db import models

from apps.common.models import BaseModel, MoneyModel


class OfftakeContract(BaseModel):
    """A domestic commitment to buy future production."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SIGNED = "signed", "Signed"
        FULFILLING = "fulfilling", "Being fulfilled"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"

    code = models.CharField(max_length=32, unique=True)
    seller_party = models.ForeignKey(
        "registry.Party", on_delete=models.PROTECT, related_name="offtake_sales"
    )
    buyer_party = models.ForeignKey(
        "registry.Party", on_delete=models.PROTECT, related_name="offtake_purchases"
    )
    product = models.ForeignKey(
        "registry.Product", on_delete=models.PROTECT, related_name="offtake_contracts"
    )
    quantity_g = models.BigIntegerField(default=0)
    status = models.CharField(max_length=12, choices=Status, default=Status.DRAFT)
    delivery_from = models.DateField(null=True, blank=True)
    delivery_to = models.DateField(null=True, blank=True)
    signed_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-signed_on", "code"]

    def __str__(self) -> str:
        return self.code


class ExportContract(MoneyModel, BaseModel):
    """An export sale. The settlement waterfall pays out from this row."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SIGNED = "signed", "Signed"
        IN_PROGRESS = "in_progress", "In progress"
        SHIPPED = "shipped", "Shipped"
        SETTLED = "settled", "Settled"
        CANCELLED = "cancelled", "Cancelled"

    class Payment(models.TextChoices):
        LC = "lc", "Letter of credit"
        CAD = "cad", "Cash against documents"
        ADVANCE = "advance", "Advance payment"
        OPEN = "open", "Open account"

    code = models.CharField(max_length=32, unique=True)
    seller_party = models.ForeignKey(
        "registry.Party", on_delete=models.PROTECT, related_name="export_contracts"
    )
    buyer_name = models.CharField(max_length=160)
    buyer_country = models.CharField(max_length=2)
    product = models.ForeignKey(
        "registry.Product", on_delete=models.PROTECT, related_name="export_contracts"
    )
    quantity_g = models.BigIntegerField(default=0)
    incoterm = models.CharField(max_length=8, blank=True)
    payment_terms = models.CharField(max_length=8, choices=Payment, default=Payment.LC)
    status = models.CharField(max_length=12, choices=Status, default=Status.DRAFT)
    signed_on = models.DateField(null=True, blank=True)
    ship_by = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-signed_on", "code"]

    def __str__(self) -> str:
        return f"{self.code} {self.buyer_name}"


class PriceTerm(MoneyModel, BaseModel):
    """Pricing attached to a contract.

    `amount_minor` here is a unit price - per tonne, in the contract currency -
    which is why the basis is explicit rather than assumed.
    """

    class Basis(models.TextChoices):
        PER_TONNE = "per_tonne", "Per tonne"
        PER_KG = "per_kg", "Per kilogram"
        LOT_SUM = "lot_sum", "Whole lot"

    export_contract = models.ForeignKey(
        ExportContract, null=True, blank=True, on_delete=models.CASCADE,
        related_name="price_terms",
    )
    offtake_contract = models.ForeignKey(
        OfftakeContract, null=True, blank=True, on_delete=models.CASCADE,
        related_name="price_terms",
    )
    basis = models.CharField(max_length=12, choices=Basis, default=Basis.PER_TONNE)
    grade = models.CharField(max_length=8, blank=True)
    note = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["grade"]

    def __str__(self) -> str:
        return f"{self.amount_minor} {self.currency}/{self.basis}"


class Shipment(BaseModel):
    """A movement of lots, and its own condition scope.

    `code` is what `storage.ConditionReading.scope_code` carries for a reefer:
    the trace lives in the time-series table, this row stays small.
    """

    class Mode(models.TextChoices):
        REEFER = "reefer", "Reefer truck"
        DRY_TRUCK = "dry_truck", "Dry truck"
        RAIL = "rail", "Rail"
        AIR = "air", "Air"

    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        LOADING = "loading", "Loading"
        IN_TRANSIT = "in_transit", "In transit"
        AT_BORDER = "at_border", "At the border"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"

    code = models.CharField(max_length=32, unique=True)
    export_contract = models.ForeignKey(
        ExportContract, null=True, blank=True, on_delete=models.PROTECT,
        related_name="shipments",
    )
    carrier_party = models.ForeignKey(
        "registry.Party", null=True, blank=True, on_delete=models.PROTECT,
        related_name="shipments",
    )
    mode = models.CharField(max_length=12, choices=Mode, default=Mode.REEFER)
    vehicle = models.CharField(max_length=64, blank=True)
    set_point_c = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    origin = models.CharField(max_length=120, blank=True)
    destination = models.CharField(max_length=120, blank=True)
    distance_km = models.PositiveIntegerField(default=0)
    departs_at = models.DateTimeField(null=True, blank=True)
    eta = models.DateTimeField(null=True, blank=True)
    arrived_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status, default=Status.PLANNED)

    class Meta:
        ordering = ["-departs_at", "code"]

    def __str__(self) -> str:
        return self.code

    @property
    def route(self) -> str:
        return f"{self.origin} → {self.destination}".strip(" →")


class ShipmentLine(BaseModel):
    """One lot's quantity on one shipment."""

    shipment = models.ForeignKey(
        Shipment, on_delete=models.CASCADE, related_name="lines"
    )
    lot = models.ForeignKey(
        "lots.Lot", on_delete=models.PROTECT, related_name="shipment_lines"
    )
    quantity_g = models.BigIntegerField()

    class Meta:
        ordering = ["shipment__code", "lot__code"]
        constraints = [
            models.UniqueConstraint(
                fields=["shipment", "lot"], name="uniq_lot_per_shipment"
            )
        ]

    def __str__(self) -> str:
        return f"{self.shipment.code}: {self.lot.code}"

