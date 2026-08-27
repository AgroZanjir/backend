"""GOVERNANCE - the audit log and the data-sharing grants.

Two platform-wide concerns that belong to no cluster and must not be pushed
into one:

    AuditEntry  who did what, to which object, under which capability. Written
                by the API layer, never edited, and readable by an auditor who
                holds no other access at all.
    DataGrant   which organisation may see which fields of whose data, until
                when, and on what basis - consent or law. The platform's answer
                to "why can a bank see my storage temperatures?".

The audit log is append-only for the same reason `lots.LotEvent` is: a log
that can be edited is not a log. It is deliberately *not* the lot event chain,
which records what happened to the goods; this records what people did in the
software, including reads.
"""

from django.core.exceptions import ValidationError
from django.db import models

from apps.common.models import BaseModel


class AuditEntry(BaseModel):
    """One line of the audit log. Append-only."""

    occurred_at = models.DateTimeField(db_index=True)
    actor_user = models.ForeignKey(
        "registry.User", null=True, blank=True, on_delete=models.PROTECT,
        related_name="audit_entries",
    )
    # A sensor or an external system has no user row; the label carries it.
    actor_label = models.CharField(max_length=120, blank=True)
    actor_party = models.ForeignKey(
        "registry.Party", null=True, blank=True, on_delete=models.PROTECT,
        related_name="audit_entries",
    )
    # i18n keys, resolved in the reader's language: a_viewed, a_pledged, ...
    action_key = models.CharField(max_length=32)
    # What was acted on - a lot code, an organisation id, a person's name.
    object_ref = models.CharField(max_length=120, blank=True)
    capability = models.CharField(max_length=32, blank=True)
    context = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-occurred_at"]
        verbose_name_plural = "audit entries"

    def __str__(self) -> str:
        return f"{self.occurred_at:%Y-%m-%d %H:%M} {self.action_key} {self.object_ref}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("audit entries are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("audit entries cannot be deleted.")


class DataGrant(BaseModel):
    """Permission for one organisation to see a defined slice of data.

    `basis` is the difference between a grant a farmer can revoke and one they
    cannot: consent is theirs to withdraw, a statutory reporting duty is not,
    and showing both in one list without that distinction would be misleading.
    """

    class Basis(models.TextChoices):
        OWNER = "owner", "Owner consent"
        LAW = "law", "Statutory"
        CONTRACT = "contract", "Contractual"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        REVOKED = "revoked", "Revoked"
        EXPIRED = "expired", "Expired"

    grantee_party = models.ForeignKey(
        "registry.Party", null=True, blank=True, on_delete=models.PROTECT,
        related_name="grants_received",
    )
    # Not every grantee is on the platform: a ministry or a statistics agency
    # may hold a grant without ever having an account.
    grantee_label = models.CharField(max_length=160, blank=True)
    # i18n keys for the scope and the field set, as the sharing screen shows
    # them: g_pledged / g_f_coll, g_region / g_f_agg, ...
    scope_key = models.CharField(max_length=32)
    fields_key = models.CharField(max_length=32)
    basis = models.CharField(max_length=12, choices=Basis, default=Basis.OWNER)
    status = models.CharField(max_length=12, choices=Status, default=Status.ACTIVE)
    granted_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["grantee_label", "scope_key"]

    def __str__(self) -> str:
        name = self.grantee_party.legal_name if self.grantee_party else self.grantee_label
        return f"{name}: {self.scope_key}"
