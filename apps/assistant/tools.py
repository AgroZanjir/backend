"""The two things the assistant may look up, and nothing else.

Both call the same composition the two open endpoints call - `_passport(lot,
public=True)` and `trial_detail_payload` - so the ceiling on what the assistant
can say about a lot is exactly the ceiling on what `/api/v1/panels/public/`
already returns to anybody who asks. No owner, no price, no lender, no lien.
That is not a promise made in a prompt; it is the function that builds the
answer.

Two consequences worth keeping:

* Nothing here takes a user, a party or a token. There is no scoped read to get
  wrong, because there is no scoped read.
* A tool result is data from the world, not instruction. Anything inside a lot
  code, a farm name or a certification list is text a stranger typed into a
  form somewhere; the brief tells the model to read it as information, and
  these payloads are trimmed to the fields the panels display anyway.
"""

from __future__ import annotations

from typing import Any

from django.http import Http404

# The tool definitions, in a fixed order. `strict` guarantees the input
# validates exactly, which is what lets `run` index the dict without guarding
# every key. The order is fixed because the tool list is part of the cached
# prefix and reordering it would invalidate the cache for every visitor.
DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "lookup_lot",
        "description": (
            "Look up one lot by its code and return its public passport: the "
            "produce, its grade and status, the farm and region it came from, "
            "the storage zone holding it, the sell-by date, the quality checks "
            "it passed, its public event history and whether its hash chain is "
            "intact. Use this whenever a visitor names or asks about a specific "
            "lot; never answer about a lot from memory. Returns not_found if no "
            "lot carries that code, which usually means it was mistyped off a "
            "sticker."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "The lot code, e.g. AZ-2026-SMQ-0412.",
                }
            },
            "required": ["code"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "lookup_trial",
        "description": (
            "Look up one ZEROCO storage trial by its code and return its arms, "
            "its sampling schedule, the observations actually measured and the "
            "modelled projection. Use this whenever a visitor asks what the "
            "ZEROCO trial has shown, how much longer produce keeps, or about "
            "weight loss, firmness or waste figures. The measured points and "
            "the projection are separate fields and must stay separate in the "
            "answer: never present a projected figure as a measurement."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "The trial code, e.g. TR-MELON-01.",
                }
            },
            "required": ["code"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]

NAMES = [definition["name"] for definition in DEFINITIONS]


def _lot(code: str) -> dict[str, Any]:
    from apps.panels.views import _lot_queryset, _passport

    lot = _lot_queryset().filter(code=code.strip()).first()
    if lot is None:
        raise Http404(code)
    return _passport(lot, public=True)


def _trial(code: str) -> dict[str, Any]:
    from apps.panels import serializers as build
    from apps.quality.models import PilotTrial

    trial = (
        PilotTrial.objects.select_related("product")
        .prefetch_related("arms")
        .filter(code=code.strip())
        .first()
    )
    if trial is None:
        raise Http404(code)
    return build.trial_detail_payload(trial)


RUNNERS = {"lookup_lot": _lot, "lookup_trial": _trial}


def run(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Execute one tool call. Never raises: a failure is an answer too.

    A tool that raises would end the turn with a spinner and no explanation. A
    tool that returns `{"error": "not_found"}` lets the model say "no lot
    carries that code, check the sticker" - which is the true and useful thing
    to say to someone typing a code by hand.
    """
    runner = RUNNERS.get(name)
    if runner is None:  # pragma: no cover - the model can only call what it is given
        return {"error": "unknown_tool", "detail": f"No tool named {name!r}."}

    code = str(payload.get("code", "")).strip()
    if not code:
        return {"error": "no_code", "detail": "A code is required."}
    # Long enough for every code the platform issues; short enough that a
    # paragraph pasted into the argument never reaches a LIKE over the table.
    if len(code) > 64:
        return {"error": "not_found", "detail": "No record carries that code."}

    try:
        return runner(code)
    except Http404:
        return {
            "error": "not_found",
            "detail": (
                f"No record carries the code {code!r}. It may have been mistyped, "
                "or it may belong to nothing."
            ),
        }
