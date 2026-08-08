"""
Shared helper for pulling a JSON object out of an LLM completion.

Models reliably wrap their JSON in prose ("Here's the slide:\n\n{...}") or
markdown fences, especially on longer completions (e.g. comprehensive-detail
slides with long speaker notes give the model more room to add commentary).
A bare ``json.loads`` fails on all of these, and previously each tool's
fallback for that case silently produced a near-empty object - which then
sailed through as a "successful" tool call with no content, the exact "right
slide count, no content" failure mode.
"""

from __future__ import annotations
import json
import re


def coerce_json_arg(value, default=None):
    """Accept a tool-call argument that's declared as a JSON string but may
    arrive as an already-native Python list/dict instead.

    Tool-calling models frequently ignore a parameter's declared "string"
    type and emit a real JSON array/object as the argument value directly -
    especially for arrays (see ask_user_question's "options", which
    reliably arrives as a native list). Since the loop's own
    ``json.loads(fn_args_str)`` has already turned the *outer* tool-call
    arguments into a Python dict by the time a tool sees them, a nested
    value that was sent natively is already a list/dict - calling
    ``json.loads`` on that raises ``TypeError: ... not list``, not the
    ``JSONDecodeError`` callers usually guard against. This normalizes
    both shapes to the same result.
    """
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return default


def extract_json_object(text: str) -> dict | None:
    """Best-effort extraction of a single top-level JSON object from ``text``.

    Tries, in order: the whole string as-is, a fenced ```json ...``` block,
    then the first balanced {...} span found anywhere in the text. Returns
    None if nothing parses, so the caller can fail loudly instead of
    proceeding with an empty/garbage object.
    """
    text = text.strip()
    if not text:
        return None

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        try:
            parsed = json.loads(fence_match.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # First balanced {...} span - catches a stray sentence before/after the
    # JSON that wasn't fenced at all.
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        break
                    break
        start = text.find("{", start + 1)

    return None
