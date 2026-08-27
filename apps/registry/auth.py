"""Sessions: OneID at the front, JWT behind it.

The shape of a sign-in, and why it is this shape:

1.  **OneID is the identity source.** A person proves who they are to the
    state's identity provider, and the platform learns a PINFL - never a
    password. `ONEID_ADAPTER=stub` resolves a demo persona instead and says so
    in the response, which is what the sign-in screen's banner reads. Swapping
    in the real adapter changes this module and nothing else, exactly like the
    five ports in `ports/`.

2.  **Access token in memory, refresh token in an httpOnly cookie.** A token
    in localStorage is a finding in any bank's security review, and this
    platform faces banks. The access token is returned in the body for the
    client to hold in memory for the life of the tab; the refresh token is set
    as an httpOnly, SameSite cookie scoped to `/api/v1/auth/` that JavaScript
    cannot read, and `POST /auth/refresh/` is what turns it back into an
    access token after a reload.

3.  **Verification gates the panel, not the sign-in.** Someone whose
    organisation is still in review signs in successfully and is shown the
    waiting screen. Refusing the sign-in would leave them with nothing to look
    at and nothing to do; this way the session exists and the panel does not
    open yet.
"""

from __future__ import annotations

from django.conf import settings
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.registry.models import User


# --- the OneID seam ---------------------------------------------------------


class OneIDError(Exception):
    """The identity provider refused, or does not know this person."""


def resolve_identity(*, persona: str = "", pinfl: str = "", code: str = "") -> User:
    """Return the person behind a OneID sign-in.

    The stub adapter accepts a persona key or a username from the seeded demo
    dataset. The live adapter will exchange `code` for a PINFL at OneID and
    look up - or create - the local shadow row from it; every caller of this
    function is written against that future already.
    """
    adapter = getattr(settings, "ONEID_ADAPTER", "stub")

    if adapter != "stub":  # pragma: no cover - no live adapter is configured yet
        raise OneIDError(
            f"ONEID_ADAPTER={adapter!r} has no implementation; the stub is the only "
            "adapter until the OneID integration lands."
        )

    if pinfl:
        user = User.objects.filter(pinfl=pinfl).first()
        if user:
            return user
        raise OneIDError("No account is linked to that PINFL.")

    key = (persona or code or "").strip()
    if not key:
        raise OneIDError("Give a persona, a PINFL or a OneID authorisation code.")

    user = (
        User.objects.filter(username=key).first()
        or User.objects.filter(memberships__role__code=key).first()
    )
    if not user:
        raise OneIDError(f"The stub adapter knows no persona {key!r}.")
    return user


# --- what a session looks like to the client --------------------------------


def membership_payload(membership) -> dict:
    return {
        "party_id": str(membership.party_id),
        "party_code": membership.party.code,
        "party_name": membership.party.legal_name,
        "party_type": membership.party.type.code,
        "party_verification": membership.party.verification_status,
        "role": membership.role.code,
        "role_label_key": membership.role.label_key,
        "scope": membership.role.scope,
        "capabilities": sorted(c.code for c in membership.role.capabilities.all()),
        "facility_codes": membership.facility_codes,
        "is_primary": membership.is_primary,
    }


def session_payload(user: User, *, access: str | None = None, adapter: str = "") -> dict:
    memberships = user.memberships.select_related(
        "party", "party__type", "role"
    ).prefetch_related("role__capabilities")
    body = {
        "user": {
            "id": str(user.pk),
            "username": user.get_username(),
            "display_name": user.display_name or user.get_username(),
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "initials": user.initials,
            "status": user.status,
            "oneid_verified": user.oneid_verified,
            "eimzo_verified": user.eimzo_verified,
            "is_superuser": user.is_superuser,
            "last_seen_at": user.last_seen_at,
        },
        "memberships": [membership_payload(m) for m in memberships],
    }
    if access is not None:
        body["access"] = access
    if adapter:
        # The sign-in screen prints this: a demo session must never be able to
        # pass for a real one.
        body["adapter"] = adapter
    return body


def _issue(response: Response, user: User) -> str:
    """Mint a token pair, put the refresh half in the cookie, return the access half."""
    refresh = RefreshToken.for_user(user)
    cookie = settings.REFRESH_COOKIE
    response.set_cookie(
        cookie["name"],
        str(refresh),
        max_age=cookie["max_age"],
        httponly=True,
        secure=cookie["secure"],
        samesite=cookie["samesite"],
        path=cookie["path"],
    )
    user.last_seen_at = timezone.now()
    user.save(update_fields=["last_seen_at"])
    return str(refresh.access_token)


# --- endpoints --------------------------------------------------------------


class OneIDSignInSerializer(serializers.Serializer):
    persona = serializers.CharField(required=False, allow_blank=True)
    pinfl = serializers.CharField(required=False, allow_blank=True)
    code = serializers.CharField(required=False, allow_blank=True)


@extend_schema(
    summary="Sign in through OneID",
    request=OneIDSignInSerializer,
    responses={200: dict, 401: dict},
)
@api_view(["POST"])
@permission_classes([AllowAny])
def oneid_sign_in(request):
    payload = OneIDSignInSerializer(data=request.data)
    payload.is_valid(raise_exception=True)

    try:
        user = resolve_identity(**payload.validated_data)
    except OneIDError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)

    if user.status == User.Status.SUSPENDED:
        return Response(
            {"detail": "This account is suspended."}, status=status.HTTP_403_FORBIDDEN
        )

    response = Response()
    access = _issue(response, user)
    response.data = session_payload(
        user, access=access, adapter=getattr(settings, "ONEID_ADAPTER", "stub")
    )
    return response


class PasswordSignInSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(style={"input_type": "password"})


@extend_schema(
    summary="Sign in with a password (operators and staff only)",
    request=PasswordSignInSerializer,
    responses={200: dict, 401: dict},
)
@api_view(["POST"])
@permission_classes([AllowAny])
def password_sign_in(request):
    """The back door for staff and for anyone OneID cannot reach.

    Kept because the platform must remain operable when the state identity
    provider is down, which it will be.
    """
    from django.contrib.auth import authenticate

    payload = PasswordSignInSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    user = authenticate(request, **payload.validated_data)
    if user is None:
        return Response(
            {"detail": "Those credentials do not match an account."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    response = Response()
    access = _issue(response, user)
    response.data = session_payload(user, access=access, adapter="password")
    return response


@extend_schema(summary="Exchange the refresh cookie for a new access token", responses={200: dict}, request=None)
@api_view(["POST"])
@permission_classes([AllowAny])
def refresh(request):
    """What the client calls on load, before it has any token at all."""
    raw = request.COOKIES.get(settings.REFRESH_COOKIE["name"])
    if not raw:
        return Response(
            {"detail": "No session cookie."}, status=status.HTTP_401_UNAUTHORIZED
        )

    try:
        token = RefreshToken(raw)
        user = User.objects.get(pk=token["user_id"])
    except (TokenError, KeyError, User.DoesNotExist):
        response = Response(
            {"detail": "That session has expired."}, status=status.HTTP_401_UNAUTHORIZED
        )
        response.delete_cookie(
            settings.REFRESH_COOKIE["name"], path=settings.REFRESH_COOKIE["path"]
        )
        return response

    response = Response()
    # Rotation is on, so the old refresh token dies with this exchange.
    access = _issue(response, user)
    response.data = session_payload(user, access=access)
    return response


@extend_schema(summary="End the session", responses={204: None}, request=None)
@api_view(["POST"])
@permission_classes([AllowAny])
def sign_out(request):
    response = Response(status=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        settings.REFRESH_COOKIE["name"], path=settings.REFRESH_COOKIE["path"]
    )
    return response


@extend_schema(summary="The current session", responses={200: dict})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me(request):
    return Response(session_payload(request.user))


@extend_schema(
    summary="The personas the stub identity adapter will accept",
    responses={
        200: inline_serializer(
            name="PersonaList",
            fields={"personas": serializers.ListField(child=serializers.DictField())},
        )
    },
)
@api_view(["GET"])
@permission_classes([AllowAny])
def personas(request):
    """What the demo sign-in screen offers.

    Empty when a live OneID adapter is configured - there are no personas to
    choose from once identity is real, and an endpoint that keeps offering
    them would be an open door.
    """
    if getattr(settings, "ONEID_ADAPTER", "stub") != "stub":  # pragma: no cover
        return Response({"adapter": settings.ONEID_ADAPTER, "personas": []})

    users = (
        User.objects.filter(status=User.Status.ACTIVE)
        .exclude(memberships__isnull=True)
        .prefetch_related("memberships__party", "memberships__party__type", "memberships__role")
        .distinct()
    )
    return Response(
        {
            "adapter": "stub",
            "personas": [
                {
                    "persona": user.get_username(),
                    "name": user.display_name,
                    "initials": user.initials,
                    "org": m.party.legal_name,
                    "party_type": m.party.type.code,
                    "role": m.role.code,
                    "role_label_key": m.role.label_key,
                }
                for user in users
                for m in [user.memberships.first()]
                if m
            ],
        }
    )
