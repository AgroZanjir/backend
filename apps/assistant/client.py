"""The seam between the website and Claude.

Same shape as everything else that leaves this system: the caller names what it
wants, an adapter decides how - or whether - it happens. `assistant_state()` is
the honest half of it. If no key is configured, or the SDK is not installed, or
an operator has switched the assistant off, the endpoint says so and the widget
prints it. A chat box that opens onto an error is worse than one that never
opens, and this project has a rule about demonstrations that can pass for the
real thing.

What crosses the wire is bounded on purpose:

* the brief from `brief.py`, which is the same account of the programme the
  website's pages make;
* the visitor's question and the transcript of this one conversation, both
  capped;
* whatever the two tools in `tools.py` returned, which is public data.

Nothing else. No session, no token, no party, no lot the visitor could not have
opened themselves.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from django.conf import settings

from apps.assistant import brief, operator_tools, tools

logger = logging.getLogger(__name__)

# One question, and the transcript it belongs to. A chat panel on a marketing
# site is not a research tool: these caps are what keeps one visitor with a
# clipboard from spending the month's budget in an afternoon.
MAX_QUESTION_CHARS = 2000
MAX_MESSAGE_CHARS = 4000
MAX_HISTORY_MESSAGES = 12
# Two lookups and a follow-up is a generous ceiling for "what is in lot X, and
# what did the trial show?". Past that the model is looping, not working.
MAX_TOOL_ROUNDS = 4
# An operator's question is often a survey rather than a lookup, and a survey
# is several reads before a sentence: lots, then the liens over them, then the
# zone they are in. It gets more room, and the same hard stop.
MAX_OPERATOR_TOOL_ROUNDS = 7

# The server-side fallback beta: on a policy decline the API re-runs the same
# request on another model inside the same call, so a visitor gets an answer
# rather than a dead panel. It is switched off for the process the first time
# an account turns out not to carry the beta - see `_create`.
_fallbacks_available = True
_client = None


def _sdk():
    """Import the SDK only when it is actually needed.

    The backend has to boot, migrate, run its tests and serve every other
    endpoint on a machine where `anthropic` was never installed - which is what
    a deployment that has not switched the assistant on looks like.
    """
    import anthropic

    return anthropic


def _api_key() -> str:
    return (getattr(settings, "ASSISTANT_API_KEY", "") or "").strip()


def assistant_state() -> dict[str, Any]:
    """What the widget is told before it offers anybody a text box.

    `available` is the only field it acts on; `reason` is what it prints when
    that is false, and it names the thing an operator has to fix.
    """
    adapter = getattr(settings, "ASSISTANT_ADAPTER", "auto")
    state = {
        "adapter": "off",
        "model": getattr(settings, "ASSISTANT_MODEL", ""),
        "available": False,
        "reason": "",
    }

    if adapter == "off":
        state["reason"] = "The assistant is switched off in this deployment."
        return state
    if not _api_key():
        state["reason"] = "No ANTHROPIC_API_KEY is configured on the server."
        return state
    try:
        _sdk()
    except ImportError:
        state["reason"] = "The anthropic SDK is not installed on the server."
        return state

    state["adapter"] = "claude"
    state["available"] = True
    return state


def _anthropic():
    global _client
    if _client is None:
        anthropic = _sdk()
        _client = anthropic.Anthropic(
            api_key=_api_key(),
            # A visitor is waiting. The SDK's ten-minute default belongs to a
            # batch job; one retry is enough to ride out a blip and not enough
            # to leave a chat panel spinning for a minute.
            timeout=60.0,
            max_retries=1,
        )
    return _client


def _open(*, fallbacks: bool, **params):
    """The stream manager for one turn. The request fires on `__enter__`."""
    client = _anthropic()
    if fallbacks:
        return client.beta.messages.stream(
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            **params,
        )
    return client.messages.stream(**params)


def _turn(**params) -> Iterator[tuple[str, dict[str, Any]]]:
    """Stream one turn: yields `delta` events, returns the final message.

    The retry exists because the fallback beta is an account-level entitlement
    and a deployment cannot know from here whether it has it. The 400 that says
    so arrives on the opening request, before a single token - which is why the
    retry is safe, and why it refuses to run once any text has been emitted.
    """
    global _fallbacks_available
    fallbacks = _fallbacks_available and getattr(settings, "ASSISTANT_FALLBACKS", True)

    while True:
        started = False
        try:
            with _open(fallbacks=fallbacks, **params) as stream:
                for event in stream:
                    if (
                        event.type == "content_block_delta"
                        and event.delta.type == "text_delta"
                    ):
                        started = True
                        yield "delta", {"text": event.delta.text}
                return stream.get_final_message()
        except _sdk().BadRequestError as exc:
            if not fallbacks or started or "fallback" not in str(exc).lower():
                raise
            # The account does not carry the beta. Say so once, stop asking for
            # it, and answer the visitor who is still waiting.
            logger.warning(
                "Assistant: server-side fallbacks unavailable on this account "
                "(%s); continuing without them.",
                exc,
            )
            _fallbacks_available = fallbacks = False


def _clean(text: Any, limit: int) -> str:
    return str(text or "").strip()[:limit]


def _turns(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The transcript the browser sent, reduced to what may be replayed.

    Text only, two roles only, and the tail rather than the head. The client is
    not trusted with the shape of the conversation: a `role` of `system` posted
    from a browser would be an operator instruction written by a stranger, and
    an assistant turn carrying tool blocks it invented would be worse.
    """
    turns: list[dict[str, Any]] = []
    for entry in history[-MAX_HISTORY_MESSAGES:]:
        if not isinstance(entry, dict):
            continue
        role = entry.get("role")
        content = _clean(entry.get("content"), MAX_MESSAGE_CHARS)
        if role not in ("user", "assistant") or not content:
            continue
        # The API needs the first turn to be the visitor's, and needs no two
        # turns of the same role in a row to have been dropped into one.
        if not turns and role != "user":
            continue
        turns.append({"role": role, "content": content})

    while turns and turns[-1]["role"] == "assistant":
        turns.pop()
    return turns


def _question(question: str, *, lang: str, context: list[str]) -> dict[str, Any]:
    """The reader's turn, with the things that vary about it attached.

    Language and whereabouts live here rather than in the system prompt on
    purpose: everything above the cache breakpoint has to be byte-identical
    between requests or nobody gets a cache hit, and these two are exactly what
    differs between an Uzbek farmer on /showroom and a Russian banker on /bank.
    """
    said = [f"Reader's language: {lang}. Answer in it.", *context]
    return {
        "role": "user",
        "content": (
            f"<context>{' '.join(said)}</context>\n"
            f"<question>{question}</question>"
        ),
    }


def answer(
    *,
    question: str,
    history: list[dict[str, Any]] | None = None,
    lang: str = "en",
    page: str = "",
) -> Iterator[tuple[str, dict[str, Any]]]:
    """The public website's assistant: no session, no scope, no audit.

    Its tools take no caller because there is nothing to scope - they build the
    public passport, which is open to anybody.
    """
    context = [f"They are reading the page {page} on the website."] if page else []
    return _converse(
        question=question,
        history=history,
        lang=lang,
        context=context,
        # A thunk, not a value. `_converse` is a generator, so its body does
        # not run until the first `next()`; these two do not have that
        # protection, and building the brief here would put a database read in
        # front of the check that says whether there is a model to send it to.
        system=brief.system_blocks,
        definitions=lambda: tools.DEFINITIONS,
        effort=settings.ASSISTANT_EFFORT,
        rounds=MAX_TOOL_ROUNDS,
        run_tool=lambda name, payload: tools.run(name, payload),
    )


def answer_for_operator(
    request,
    *,
    question: str,
    history: list[dict[str, Any]] | None = None,
    lang: str = "en",
    panel: str = "",
) -> Iterator[tuple[str, dict[str, Any]]]:
    """The panels' assistant: scoped to the caller, and audited.

    The request travels all the way to the tool rather than a user id, because
    `audit` needs it: an audit line carries the actor, their primary
    organisation and the capability the read was made under, and reconstructing
    that from a bare user is how the two paths would drift apart.
    """
    context = [f"They are on the {panel} screen."] if panel else []
    return _converse(
        question=question,
        history=history,
        lang=lang,
        context=context,
        system=lambda: brief.operator_system_blocks(request.user, panel),
        definitions=operator_tools.definitions,
        effort=settings.ASSISTANT_PANEL_EFFORT,
        # An operator's question is usually a survey - "which of mine are
        # pledged and go off this month" is three reads before a sentence -
        # where a visitor's is one lookup at most.
        rounds=MAX_OPERATOR_TOOL_ROUNDS,
        run_tool=lambda name, payload: operator_tools.run(request, name, payload),
    )


def _converse(
    *,
    question: str,
    history: list[dict[str, Any]] | None,
    lang: str,
    context: list[str],
    system,
    definitions,
    effort: str,
    rounds: int,
    run_tool,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """One turn of either assistant, as `(event, payload)` pairs.

    The two differ in exactly four things - the brief, the tools, the effort
    and how many rounds of lookups they get - so they share this. The view
    turns the pairs into server-sent events; keeping the loop free of HTTP is
    what makes it testable without one.

    Five events. `delta` carries a fragment of the answer, `tool` says a lookup
    started or finished so the panel can say which, `done` closes the turn,
    `error` replaces it, and `blocked` is a refusal - separated from `error`
    because "I will not answer that" is not a failure of the software.
    """
    question = _clean(question, MAX_QUESTION_CHARS)
    if not question:
        yield "error", {"code": "empty"}
        return

    state = assistant_state()
    if not state["available"]:
        # `reason` names the setting an operator has to change, in English,
        # because an operator is who it is for. The reader is shown the
        # sentence the widget composes from `code`, in their own language.
        yield "error", {"code": "unavailable", "reason": state["reason"]}
        return

    params: dict[str, Any] = {
        "model": settings.ASSISTANT_MODEL,
        "max_tokens": settings.ASSISTANT_MAX_TOKENS,
        "system": system(),
        "tools": definitions(),
        "output_config": {"effort": effort},
    }
    messages = _turns(history or []) + [
        _question(question, lang=lang, context=context)
    ]
    looked_up: list[dict[str, str]] = []

    try:
        for _ in range(rounds):
            message = yield from _turn(messages=messages, **params)

            # A safety decline. `stop_details` is populated for this stop
            # reason and for no other, so it is only ever read here.
            if message.stop_reason == "refusal":
                category = getattr(message.stop_details, "category", None)
                yield "blocked", {"category": category or ""}
                return

            if message.stop_reason != "tool_use":
                yield "done", {"lookups": looked_up}
                return

            messages.append({"role": "assistant", "content": message.content})
            results = []
            for block in message.content:
                if block.type != "tool_use":
                    continue
                # `strict` on the definitions guarantees the shape; the input
                # is still parsed rather than string-matched, per the SDK's own
                # warning about how these are escaped.
                payload = dict(block.input or {})
                code = str(payload.get("code", ""))
                yield "tool", {"name": block.name, "code": code, "state": "running"}

                result = run_tool(block.name, payload)
                found = "error" not in result
                if found:
                    looked_up.append({"name": block.name, "code": code})
                yield "tool", {
                    "name": block.name,
                    "code": code,
                    "state": "done",
                    "found": found,
                }
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": _as_text(result),
                        "is_error": not found,
                    }
                )
            messages.append({"role": "user", "content": results})

        # Out of rounds with the model still calling tools. Say something true
        # rather than closing the turn on a half-sentence.
        yield "error", {"code": "too_many_lookups"}

    except Exception as exc:  # noqa: BLE001 - every failure ends the same way
        yield "error", {"code": _failure(exc)}


def _as_text(result: dict[str, Any]) -> str:
    import json

    return json.dumps(result, default=str, ensure_ascii=False)


def _failure(exc: Exception) -> str:
    """Classify a failure. The sentence is the widget's to write.

    Same rule the panels keep for lot events: the backend reports what
    happened, and the reader sees it worded in their own language. A visitor
    reading the site in Uzbek should not be handed an English apology.

    The exception's own message is never returned either way - it can carry a
    request id, a partial key or an account name, and none of that belongs in a
    chat panel on a public website. It goes to the log, where it is useful.
    """
    anthropic = _sdk()
    logger.exception("Assistant turn failed", exc_info=exc)

    if isinstance(exc, anthropic.RateLimitError):
        return "busy"
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return "config"
    if isinstance(exc, anthropic.APIConnectionError):
        return "network"
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code >= 500:
        return "upstream"
    return "failed"
