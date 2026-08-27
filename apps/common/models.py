"""Shared base models.

Two conventions from section 03 that are expensive to retrofit:

* quantities are integers in one base unit (grams) - boxes, crates and pallets
  are presentation, never storage;
* money is an integer count of minor units plus an explicit currency, with the
  FX rate snapshotted on the transaction that used it.

This module must not import from any cluster. Everything else depends on it,
so a dependency in this direction would make the whole graph circular.
"""

import uuid

from django.db import models


class UUIDModel(models.Model):
    """Primary keys are UUIDs so lot codes can be minted offline in the field."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class BaseModel(UUIDModel, TimestampedModel):
    class Meta:
        abstract = True


class Currency(models.TextChoices):
    """The three this project spans. Minor units differ; see `MINOR_UNITS`."""

    UZS = "UZS", "Uzbek sum"
    USD = "USD", "US dollar"
    JPY = "JPY", "Japanese yen"


#: Digits after the decimal point per currency. JPY has none, which is exactly
#: why the amount is stored as minor units and never as a float.
MINOR_UNITS = {"UZS": 2, "USD": 2, "JPY": 0}


class MoneyModel(models.Model):
    """An amount of money, stored the only way that survives an audit.

    `amount_minor` is an integer count of the currency's smallest unit.
    `fx_rate_to_uzs` is snapshotted on the row that used it - a rate looked up
    later is a different number, and a settlement that changes value when you
    re-read it is not a settlement.
    """

    amount_minor = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=3, choices=Currency, default=Currency.UZS)
    fx_rate_to_uzs = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True
    )

    class Meta:
        abstract = True

    @property
    def amount_major(self) -> float:
        """For display only. Never do arithmetic on this."""
        return self.amount_minor / (10 ** MINOR_UNITS.get(self.currency, 2))


class Notification(BaseModel):
    """A line in the bell menu.

    Deliberately holds no foreign key into any cluster: it carries the subject
    as a code string. A notification that pins a row open forever is how
    retention policies get broken.
    """

    class Level(models.TextChoices):
        CRITICAL = "crit", "Critical"
        WARNING = "warn", "Warning"
        INFO = "info", "Info"
        GOOD = "good", "Good"

    user = models.ForeignKey(
        "registry.User",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    level = models.CharField(max_length=8, choices=Level, default=Level.INFO)
    # An i18n key, not a sentence: the reader's language is chosen in the
    # browser, and a message stored in Uzbek would reach a Russian broker in
    # Uzbek.
    message_key = models.CharField(max_length=48)
    subject = models.CharField(max_length=64, blank=True)
    occurred_at = models.DateTimeField()
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-occurred_at"]

    def __str__(self) -> str:
        return f"{self.level}:{self.message_key} {self.subject}"
