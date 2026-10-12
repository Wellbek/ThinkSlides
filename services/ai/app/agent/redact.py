"""
Strip heavy binary payloads out of anything that leaves the agent loop.

A generated illustration and a provider-rendered chart both come back as
base64 inside their tool result. The loop serialises tool results straight
into the model conversation, publishes them as ``tool_result`` events over
Redis and stores them in Postgres - so a single 2MB PNG became three
megabytes of base64 in the next request's messages, in the browser's event
stream and in the database row.

Measured on one run: two ``generate_image`` calls left 3,036,440 characters
in ``agent_events``, and the next call asked OpenAI for 767,678 tokens
against a 450,000 TPM ceiling. The run died at the draft phase with nothing
rendered.

The bytes are not needed anywhere downstream of the loop. ``working_state``
already holds the real block - it is stashed there before the result is
serialised - and the renderer reads from that. What the model needs to know
is that an image exists, what it shows and how big it is, which is what the
placeholder says.

This is the same shape ``select_source_image`` already uses deliberately:
the model sees an id and a caption, never the bytes, and the loop resolves
the id server-side.
"""

from __future__ import annotations

import json
from typing import Any

# Fields whose value is a base64 blob rather than something to read.
BINARY_FIELDS = ("data", "renderedPng", "imageData", "pngBase64")

# Fields the loop keeps and the model never sees. `sourceText` is a whole
# uploaded document, carried so a figure can be traced to an offset inside it;
# putting it in the conversation would spend the context window on text the
# model already has a summary of.
LOOP_ONLY_FIELDS = ("sourceText",)

# Below this a value is small enough to be a marker or a data URI stub
# rather than a real payload, and rewriting it would lose information for
# no benefit.
MIN_REDACTED_CHARS = 512

# Several tool arguments are handed over already serialised - slides_json,
# prepared_visuals, selected_image_context - so the payload is a *string*
# to anything walking the surrounding dict, and a walk that only looks at
# dicts and lists sails straight past it. finalize_and_render_pptx is the
# case that matters: it is given the deck's real JSON, images included,
# and needs it, but the event announcing the call does not.
MIN_NESTED_JSON_CHARS = 4096


def _placeholder(value: str) -> str:
    """What stands in for the bytes: enough to reason about, not to carry."""
    # base64 is 4 characters per 3 bytes.
    return f"<{len(value) * 3 // 4} bytes, held by the run>"


def _redact_serialised(value: str) -> str:
    """The same JSON string with its binary fields replaced.

    Parsed and re-serialised rather than pattern-matched: a regex over
    base64 would have to guess where a payload starts and stops, and would
    happily mangle a long ordinary string that happened to look like one.
    """
    if value[:1] not in ("{", "["):
        return value
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return value
    reduced = redact_binary(parsed)
    if reduced is parsed:
        return value
    return json.dumps(reduced)


def redact_binary(payload: Any) -> Any:
    """A copy of ``payload`` with every large binary field replaced.

    Returns the argument itself when nothing needed replacing, so the common
    case costs one walk and no allocation.
    """
    if isinstance(payload, str):
        if len(payload) >= MIN_NESTED_JSON_CHARS:
            reduced = _redact_serialised(payload)
            if reduced is not payload:
                return reduced
        return payload
    if isinstance(payload, dict):
        changed = False
        out: dict[str, Any] = {}
        for key, value in payload.items():
            if key in LOOP_ONLY_FIELDS:
                # Dropped outright rather than replaced with a placeholder:
                # the model has a summary of this already, and a note saying
                # a document was here would only invite it to ask for one.
                changed = True
                continue
            if (
                key in BINARY_FIELDS
                and isinstance(value, str)
                and len(value) >= MIN_REDACTED_CHARS
            ):
                out[key] = _placeholder(value)
                changed = True
                continue
            reduced = redact_binary(value)
            if reduced is not value:
                changed = True
            out[key] = reduced
        return out if changed else payload
    if isinstance(payload, list):
        reduced_items = [redact_binary(item) for item in payload]
        if any(a is not b for a, b in zip(reduced_items, payload)):
            return reduced_items
        return payload
    return payload
