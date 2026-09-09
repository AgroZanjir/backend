"""Three endpoints: what the assistant is, and one answer from each of the two.

`GET /api/v1/assistant/` is what the widget asks before it renders a text box.
It is cheap, unthrottled and honest - if no key is configured it says so, and
the panel prints that instead of offering a conversation it cannot have.

`POST /api/v1/assistant/ask/` streams the answer back as server-sent events.
Streaming rather than one JSON body for the ordinary reason - a paragraph that
arrives a word at a time reads as an answer, and the same paragraph after four
silent seconds reads as a hang - and for one particular to this endpoint: a
lookup happens mid-answer, and the panel can only say "reading lot
AZ-2026-SMQ-0412" while it is happening if it hears about it while it is
happening.

`POST /api/v1/assistant/panel/ask/` is the same stream for somebody signed in,
and it is a different assistant behind the same shape: a brief that knows which
organisation they belong to, and tools scoped to what their own screens show.
It needs a session and the `view` capability, and the passports it opens are
written to the audit log under the operator's name.

The first two are open. That is not an oversight: the website they serve is
open, and that assistant can reach nothing a visitor could not open for
themselves. The third is not open, for the mirror-image reason.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from django.http import StreamingHttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import (
    api_view,
    permission_classes,
    renderer_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.response import Response

from apps.assistant import client
from apps.assistant.throttles import (
    AssistantBurstThrottle,
    AssistantHourThrottle,
    AssistantPanelThrottle,
)
from apps.common.api import requires


@extend_schema(
    summary="Whether the assistant is connected",
    description=(
        "The website's assistant panel asks this before it offers a text box. "
        "`available` is false whenever no model is reachable, and `reason` "
        "names what an operator has to fix."
    ),
    responses={200: dict},
)
@api_view(["GET"])
@permission_classes([AllowAny])
def state(request):
    return Response(client.assistant_state())


def _frame(event: str, payload: dict) -> bytes:
    """One server-sent event.

    `json.dumps` is what keeps a newline inside a token from ending the frame -
    an answer containing a blank line would otherwise arrive as two events, the
    second of them malformed.
    """
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


class EventStreamRenderer(BaseRenderer):
    """Lets DRF negotiate `text/event-stream`, which is what the widget asks for.

    Content negotiation runs **before** the view body. A client that honestly
    declares `Accept: text/event-stream` - which both widgets do, because that
    is what they are about to read - was refused with 406 and never reached the
    code below, no matter that the view was about to stream exactly that.

    This went unnoticed through every end-to-end check because `curl` sends
    `Accept: */*` unless told otherwise, and `*/*` is satisfied by the JSON
    renderer. The transport was tested with a header the real client does not
    send. `test_views.py` now asks with the browser's header, which is the test
    that was missing rather than the one that failed.

    `render` is only ever reached for an error DRF raises before the view
    returns its stream - a throttle, a permission, a malformed body. Those are
    emitted as one SSE frame in the shape the client's parser already knows, so
    a refusal arrives as a readable error rather than as a stream that opens
    and closes saying nothing.
    """

    media_type = "text/event-stream"
    format = "event-stream"
    charset = "utf-8"

    def render(self, data, accepted_media_type=None, renderer_context=None) -> str:
        detail = ""
        if isinstance(data, dict):
            detail = str(data.get("detail", ""))
        status = getattr(renderer_context.get("response"), "status_code", 0) if renderer_context else 0
        code = {401: "signed_out", 403: "signed_out", 429: "busy"}.get(status, "failed")
        return _frame(code and "error", {"code": code, "detail": detail}).decode()


def _events(**kwargs) -> Iterator[bytes]:
    # A comment frame first. It costs two bytes and it forces the response
    # headers out through every proxy between here and the reader, so the
    # browser's fetch resolves now rather than when the first token lands.
    yield b": open\n\n"
    try:
        for event, payload in client.answer(**kwargs):
            yield _frame(event, payload)
    except Exception:  # noqa: BLE001 - the stream is already open; end it cleanly
        # Headers are long gone, so there is no status code left to return.
        # An `error` frame is the only way left to tell the panel anything.
        yield _frame("error", {"code": "interrupted"})


def _operator_events(request, **kwargs) -> Iterator[bytes]:
    yield b": open\n\n"
    try:
        for event, payload in client.answer_for_operator(request, **kwargs):
            yield _frame(event, payload)
    except Exception:  # noqa: BLE001 - the stream is already open; end it cleanly
        yield _frame("error", {"code": "interrupted"})


@extend_schema(
    summary="Ask the website's assistant",
    description=(
        "Streams one answer as server-sent events: `delta` carries a fragment "
        "of text, `tool` says a public lookup started or finished, `done` "
        "closes the turn, `blocked` reports a refusal and `error` replaces the "
        "turn with a `code` the client words in the reader's language. The "
        "transcript is sent by the client and stored nowhere."
    ),
    request=dict,
    responses={200: str},
)
@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([AssistantBurstThrottle, AssistantHourThrottle])
@renderer_classes([JSONRenderer, EventStreamRenderer])
def ask(request):
    body = request.data if isinstance(request.data, dict) else {}
    history = body.get("history")

    response = StreamingHttpResponse(
        _events(
            question=body.get("question", ""),
            history=history if isinstance(history, list) else [],
            lang=str(body.get("lang") or request.LANGUAGE_CODE or "en")[:8],
            page=str(body.get("page") or "")[:120],
        ),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache, no-transform"
    # nginx buffers a proxied response by default, which holds every token back
    # until the answer is finished - the exact thing streaming is for.
    response["X-Accel-Buffering"] = "no"
    return response


@extend_schema(
    summary="Ask the panels' assistant",
    description=(
        "The same event stream as the public endpoint, for somebody signed in. "
        "The brief carries the caller's own organisations, roles and "
        "capabilities; the tools are scoped by the same rules their screens "
        "are, and a passport opened this way is written to the audit log."
    ),
    request=dict,
    responses={200: str},
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, requires("view")])
@throttle_classes([AssistantPanelThrottle])
@renderer_classes([JSONRenderer, EventStreamRenderer])
def panel_ask(request):
    body = request.data if isinstance(request.data, dict) else {}
    history = body.get("history")

    response = StreamingHttpResponse(
        _operator_events(
            request,
            question=body.get("question", ""),
            history=history if isinstance(history, list) else [],
            lang=str(body.get("lang") or request.LANGUAGE_CODE or "en")[:8],
            panel=str(body.get("panel") or "")[:120],
        ),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache, no-transform"
    response["X-Accel-Buffering"] = "no"
    return response
