"""The contact form.

The form used to say, under itself, that it sent nothing. That was honest and
it was still a website collecting nothing, so it now writes an enquiry and the
line under it says what happens next instead.

What this endpoint is not: an identity. Nothing here is verified, so an enquiry
names no party and creates no account. It is a message in a box that somebody
at the operator reads.
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import (
    api_view,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.website.models import Enquiry
from apps.website.throttles import EnquiryBurstThrottle, EnquiryDayThrottle


class EnquirySerializer(serializers.ModelSerializer):
    class Meta:
        model = Enquiry
        fields = ("name", "organisation", "email", "phone", "topic", "message")

    def validate_message(self, value: str) -> str:
        text = value.strip()
        if len(text) < 10:
            raise serializers.ValidationError(
                "Tell us a little more than that."
            )
        return text


def _address(request) -> str | None:
    # Behind nginx the peer is the proxy; the real address is the first hop in
    # the forwarded list. Only trusted because the deployment sets it - see
    # infra/nginx/agrozanjir.conf.
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (forwarded.split(",")[0].strip() or None) if forwarded else request.META.get("REMOTE_ADDR")


@extend_schema(
    summary="Send an enquiry from the public website",
    request=EnquirySerializer,
    responses={201: dict},
)
@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([EnquiryBurstThrottle, EnquiryDayThrottle])
def enquire(request):
    payload = EnquirySerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    enquiry = payload.save(source_address=_address(request))
    # Nothing about the record goes back: what a stranger gets is that it
    # arrived. An id here would be an id to guess at.
    return Response(
        {"received": True, "topic": enquiry.topic},
        status=status.HTTP_201_CREATED,
    )
