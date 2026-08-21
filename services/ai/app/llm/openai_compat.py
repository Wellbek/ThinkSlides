"""
Adapting a request to the parameters an OpenAI-shaped model actually accepts.

OpenAI retires request parameters as its model line moves on, and which ones a
given model still takes is not knowable from the model name. ``gpt-5.6-terra``
rejects ``max_tokens`` (it wants ``max_completion_tokens``) and rejects
``temperature`` outright; ``gpt-4o`` accepts both; an OpenAI-compatible
endpoint behind a custom ``base_url`` may accept only the old spelling. Any
list of model names encoding that goes stale the moment a provider ships a new
id - which is exactly how this service ended up sending ``max_tokens`` to a
model that had stopped accepting it.

So nothing here is keyed on a model name. A request goes out as written; if the
API rejects one parameter, the rejection itself says which one and often names
its replacement, and that is what gets recorded. The next attempt applies what
was learned, and the process repeats until the request is accepted or there is
nothing left to learn.

What is learned is cached per ``(endpoint, model)`` for the life of the
process, so the wasted round trip is paid once rather than on every call.

Renamed parameters ride in ``extra_body`` rather than as named arguments: the
API takes them verbatim there, whereas a named argument the installed SDK does
not know raises ``TypeError`` before any request is made. That keeps the
adaptation working for a parameter introduced after the pinned SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.observability.logging import get_logger

import re

logger = get_logger(__name__)

# How many times one request may be rewritten before giving up. A model that
# rejects both `max_tokens` and `temperature` needs two; the bound only exists
# so a provider that answers every request with a fresh complaint fails fast
# instead of looping.
MAX_ADAPTATIONS = 4

# Phrases OpenAI (and the endpoints that emulate it) use when the request
# itself is the problem. Without one of these an error is something else -
# an auth failure, a content filter, a 500 - and must be raised untouched.
_REJECTION_MARKERS = (
    "unsupported parameter",
    "unsupported value",
    "unrecognized request argument",
    "unknown parameter",
    "is not supported with this model",
    "does not support",
)

# Removing one of these leaves a request that cannot mean anything, so a
# complaint naming one is a complaint about its contents, not its presence.
ESSENTIAL_PARAMS = frozenset({"model", "messages", "input"})

_QUOTED_NAME_RE = re.compile(r"['\"`]([A-Za-z_][A-Za-z0-9_.]*)['\"`]")
_REPLACEMENT_RE = re.compile(
    r"\buse\s+['\"`]([A-Za-z_][A-Za-z0-9_.]*)['\"`]\s+instead", re.IGNORECASE
)


def _error_param(exc: Exception) -> str | None:
    """The parameter the API blamed, from wherever this SDK version put it."""
    param = getattr(exc, "param", None)
    if isinstance(param, str) and param:
        return param
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("param"), str):
            return error["param"] or None
    return None


def rejected_parameter(exc: Exception, sent: dict[str, Any]) -> str | None:
    """Which of the parameters we sent this error is complaining about.

    Returns None when the error is not a parameter rejection, or names a
    parameter this request did not send - in which case there is nothing to
    adapt and the caller must let the error through.
    """
    # The wording is checked first even though the SDK often reports the
    # parameter as a field: a 400 can blame `messages` for a content policy
    # violation, and acting on that would strip the conversation out of the
    # request and ask again with nothing in it.
    text = str(exc)
    if not any(marker in text.lower() for marker in _REJECTION_MARKERS):
        return None

    named = _error_param(exc)
    if named in sent:
        return named
    # Otherwise read it out of the message: the first quoted name that is
    # actually one of ours. Quoted names we did not send are noise - the
    # suggested replacement, an enum value, a model id.
    for candidate in _QUOTED_NAME_RE.findall(text):
        if candidate in sent:
            return candidate
    return None


def suggested_replacement(exc: Exception) -> str | None:
    """The parameter the API told us to use instead, when it named one."""
    match = _REPLACEMENT_RE.search(str(exc))
    return match.group(1) if match else None


@dataclass
class ParameterCompatibility:
    """What one endpoint and model have taught us about their parameters."""

    # Wire name -> the name to send instead. Chains are followed, so a
    # parameter renamed twice still resolves.
    renames: dict[str, str] = field(default_factory=dict)
    # Parameters this model refuses outright, with no replacement offered.
    dropped: set[str] = field(default_factory=set)
    # Every name the API has rejected, including replacement names that
    # turned out to be wrong too. Guards against renaming A to B, being told
    # to use A, and oscillating forever.
    rejected: set[str] = field(default_factory=set)

    def apply(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Rewrite a request with everything learned so far.

        Renamed parameters move into ``extra_body`` so the request does not
        depend on the installed SDK knowing the new name.
        """
        out: dict[str, Any] = {}
        extra_body: dict[str, Any] = dict(kwargs.get("extra_body") or {})

        for name, value in kwargs.items():
            if name == "extra_body":
                continue
            wire = self._resolve(name)
            if wire in self.dropped:
                continue
            if wire == name:
                out[name] = value
            else:
                extra_body[wire] = value

        if extra_body:
            out["extra_body"] = extra_body
        return out

    def _resolve(self, name: str) -> str:
        seen = {name}
        wire = name
        while wire in self.renames:
            wire = self.renames[wire]
            if wire in seen:
                break
            seen.add(wire)
        return wire

    def learn(self, exc: Exception, sent: dict[str, Any]) -> bool:
        """Record what this error says. False when there is nothing to learn.

        ``sent`` is the request as it actually went over the wire, including
        anything ``apply`` moved into ``extra_body`` - the API blames the name
        it received, not the one the caller wrote.
        """
        name = rejected_parameter(exc, _wire_names(sent))
        if name is None or name in self.dropped or name in ESSENTIAL_PARAMS:
            return False

        self.rejected.add(name)
        replacement = suggested_replacement(exc)
        if replacement and replacement != name and replacement not in self.rejected:
            self.renames[name] = replacement
        else:
            self.dropped.add(name)
        return True


def _wire_names(sent: dict[str, Any]) -> dict[str, Any]:
    """Flatten ``extra_body`` back out, so every name sent is at top level."""
    flat = {k: v for k, v in sent.items() if k != "extra_body"}
    flat.update(sent.get("extra_body") or {})
    return flat


_CACHE: dict[tuple[str, str], ParameterCompatibility] = {}


def compatibility_for(endpoint: str, model: str) -> ParameterCompatibility:
    """The learned state for one endpoint and model, created on first use."""
    return _CACHE.setdefault((endpoint, model), ParameterCompatibility())


def reset_cache() -> None:
    """Forget everything learned. For tests."""
    _CACHE.clear()


async def create_with_compatible_params(
    send: Callable[[dict[str, Any]], Awaitable[Any]],
    kwargs: dict[str, Any],
    *,
    endpoint: str,
    provider: str,
    model: str,
) -> Any:
    """Send a request, rewriting it until the API stops rejecting parameters.

    ``send`` receives the (possibly rewritten) request and performs the call,
    including whatever retrying the caller wants around it.
    """
    compat = compatibility_for(endpoint, model)

    for attempt in range(MAX_ADAPTATIONS + 1):
        sent = compat.apply(kwargs)
        try:
            return await send(sent)
        except Exception as exc:
            if attempt == MAX_ADAPTATIONS or not compat.learn(exc, sent):
                raise
            logger.info(
                "llm_parameter_adapted",
                provider=provider,
                model=model,
                renames=dict(compat.renames),
                dropped=sorted(compat.dropped),
                error=str(exc)[:200],
            )

    raise AssertionError("unreachable: the loop either returns or raises")
