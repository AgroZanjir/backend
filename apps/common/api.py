"""Shared API machinery: who may do what, and what gets written down.

Three things live here because every cluster needs them and none should own
them:

* `HasCapability` - the permission class. Roles are how the product speaks;
  capabilities are what the API checks. A view names the capability it needs
  and never a role, so adding the thirty-eighth role changes no view.
* `party_scope` - the queryset narrowing that keeps one organisation out of
  another's data. Row-level security in PostgreSQL will eventually enforce the
  same rule underneath; this is the application-level half of it.
* `audit` - one call that appends to the governance log. Reads are logged too:
  "who looked at my lot" is a question this platform has to answer.
"""

from __future__ import annotations

from django.utils import timezone
from rest_framework import permissions

from apps.governance.models import AuditEntry


def memberships_of(user) -> list:
    """Every membership the user holds, with role and party prefetched."""
    if not user or not user.is_authenticated:
        return []
    return list(
        user.memberships.select_related("party", "party__type", "role").prefetch_related(
            "role__capabilities"
        )
    )


def capabilities_of(user) -> set[str]:
    """The union of every capability the user's roles carry."""
    if getattr(user, "is_superuser", False):
        return {"*"}
    return {
        capability.code
        for membership in memberships_of(user)
        for capability in membership.role.capabilities.all()
    }


def has_capability(user, code: str) -> bool:
    caps = capabilities_of(user)
    return "*" in caps or code in caps


def party_ids_of(user) -> list:
    return [m.party_id for m in memberships_of(user)]


class HasCapability(permissions.BasePermission):
    """Require a capability. Read-only requests need `view`.

    Use `requires("capture")` to name a different one; the capability has to
    live on the permission class rather than on the view because DRF builds
    the permission from the class and never sees attributes set on a
    function-based view.
    """

    message = "Your role does not carry the capability this action requires."
    capability = ""

    def has_permission(self, request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False
        needed = self.capability or (
            "view" if request.method in permissions.SAFE_METHODS else ""
        )
        if not needed:
            return True
        return has_capability(request.user, needed)


def requires(code: str) -> type[HasCapability]:
    """`@permission_classes([IsAuthenticated, requires("capture")])`.

    A view names the capability it needs and never a role, so adding the
    thirty-eighth role changes no view.
    """
    return type(
        f"Requires{code.title()}",
        (HasCapability,),
        {
            "capability": code,
            "message": f"This action needs the {code!r} capability, which your role does not carry.",
        },
    )


#: The three capabilities the administration panel exists to exercise.
#: `administer` runs it, `verify` admits organisations to it, `audit` reads
#: what everyone did in it. All three are platform-scope work.
PLATFORM_CAPABILITIES = ("administer", "verify", "audit")


class IsPlatformAdministrator(permissions.BasePermission):
    """The administration panel, and only from a platform role.

    It used to demand `administer` alone, which locked out the two roles the
    panel was built for: a verification officer holds `verify` and an auditor
    holds `audit`, and neither could load the organisations they are there to
    decide on or read. The panel's own screens then crashed on the empty
    collections, which is how this was found.
    """

    message = "Only the platform's own staff may use this endpoint."

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        return any(
            m.role.scope == "platform"
            and any(m.has_capability(c) for c in PLATFORM_CAPABILITIES)
            for m in memberships_of(user)
        )


def is_platform(user) -> bool:
    """True for a superuser or anyone holding a platform-scoped role.

    Platform roles are the operator's own staff: verification officers,
    auditors, administrators. Everyone else is scoped to their organisations.
    """
    if getattr(user, "is_superuser", False):
        return True
    return any(m.role.scope == "platform" for m in memberships_of(user))


def party_scope(queryset, user, field: str = "owner_party_id"):
    """Narrow a queryset to the parties the user belongs to.

    A superuser and anyone holding a platform-scoped role sees everything -
    that is what a platform role is for. Everyone else sees their own
    organisations' rows and nothing else.
    """
    if is_platform(user):
        return queryset
    return queryset.filter(**{f"{field}__in": party_ids_of(user)})


def audit(
    request,
    action_key: str,
    *,
    object_ref: str = "",
    capability: str = "view",
    **context,
) -> AuditEntry | None:
    """Append one line to the audit log.

    Returns None for an anonymous request rather than writing a headless row:
    the public passport is deliberately open, and logging every anonymous
    lookup as if it were an actor would fill the table with noise.
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return None
    primary = next((m for m in memberships_of(user) if m.is_primary), None)
    return AuditEntry.objects.create(
        occurred_at=timezone.now(),
        actor_user=user,
        actor_label=user.display_name or user.get_username(),
        actor_party=primary.party if primary else None,
        action_key=action_key,
        object_ref=object_ref,
        capability=capability,
        context=context,
    )
