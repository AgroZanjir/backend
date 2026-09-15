"""What the API says when it will not answer.

The write endpoints fetch their subject from a queryset that is already
narrowed to what the caller's organisation may touch, so "you may not" and
"there is no such thing" arrive here as the same exception - a `DoesNotExist`.
That is deliberate on both counts:

* it is one code path, so a view cannot forget the authorisation half;
* the answer is the same either way. Telling a stranger that
  `AZ-2026-SMQ-0412` exists but is not theirs, while `AZ-2026-SMQ-9999` does
  not exist at all, is a way to enumerate every lot on the platform by asking.

Without this the same situation was an unhandled exception: a 500, a stack
trace in the log, and in `DEBUG` a full traceback in the response body.
"""

from __future__ import annotations

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


def exception_handler(exc, context):
    handled = drf_exception_handler(exc, context)
    if handled is not None:
        return handled

    if isinstance(exc, ObjectDoesNotExist):
        return Response(
            {"detail": "No such record, or not one of yours."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # A domain rule refusing the write - the lifecycle, the dispatch guard.
    # These carry the reason the panels print, so they must not become a 500
    # for having been raised outside a view that thought to catch them.
    if isinstance(exc, ValidationError):
        messages = getattr(exc, "messages", [str(exc)])
        return Response(
            {"detail": messages, "blockers": messages},
            status=status.HTTP_409_CONFLICT,
        )

    return None
