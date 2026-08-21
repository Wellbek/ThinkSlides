"""
No image bytes may leave the agent loop.

A generated illustration comes back as base64 inside its tool result, and
the loop serialises tool results into the model conversation, publishes
them over Redis and stores them in Postgres. One run left 3,036,440
characters in ``agent_events`` and asked OpenAI for 767,678 tokens against
a 450,000 ceiling; it died at the draft phase with nothing rendered.

These tests hold the two halves of the fix: the payload is stripped on the
way out, and the 429 it used to cause is no longer mistaken for a
transient rate limit.
"""

import base64

import pytest

from app.agent.redact import MIN_REDACTED_CHARS, redact_binary
from app.llm.provider import _is_rate_limit_error, _is_request_too_large


def _png(size: int = 200_000) -> str:
    return base64.b64encode(b"\x89PNG" + b"\x00" * size).decode("ascii")


def test_a_generated_image_leaves_a_placeholder_not_its_bytes():
    payload = {
        "slideIndex": 4,
        "content": {
            "type": "image",
            "image": {"source": "generated", "data": _png(), "altText": "a vertical farm"},
            "provenance": "generated",
        },
        "summary": "generated illustration, grounded in 3 reference(s)",
    }
    reduced = redact_binary(payload)

    assert "bytes" in reduced["content"]["image"]["data"]
    assert len(reduced["content"]["image"]["data"]) < 60
    # Everything a model actually reasons from survives untouched.
    assert reduced["content"]["image"]["altText"] == "a vertical farm"
    assert reduced["slideIndex"] == 4
    assert reduced["summary"] == payload["summary"]
    # And the original is not mutated - the run still holds the real block.
    assert len(payload["content"]["image"]["data"]) > 100_000


def test_a_provider_rendered_chart_is_stripped_too():
    payload = {"content": {"type": "chart", "chart": {
        "title": "Yield per m2", "renderedPng": _png(), "renderer": "figure"}}}
    reduced = redact_binary(payload)
    assert "bytes" in reduced["content"]["chart"]["renderedPng"]
    assert reduced["content"]["chart"]["title"] == "Yield per m2"


def test_a_whole_deck_is_walked_not_just_the_top_level():
    deck = {"slides": [
        {"slideIndex": 0, "contents": [{"type": "text", "text": "hello"}]},
        {"slideIndex": 1, "contents": [
            {"type": "image", "image": {"data": _png(), "source": "generated"}},
        ]},
    ]}
    reduced = redact_binary(deck)
    assert "bytes" in reduced["slides"][1]["contents"][0]["image"]["data"]
    assert reduced["slides"][0]["contents"][0]["text"] == "hello"


@pytest.mark.parametrize("value", ["", "short", "x" * (MIN_REDACTED_CHARS - 1)])
def test_a_small_value_in_a_binary_field_is_left_alone(value):
    payload = {"image": {"data": value}}
    assert redact_binary(payload)["image"]["data"] == value


def test_a_serialised_argument_is_walked_into():
    """finalize_and_render_pptx is handed the deck already serialised, so the
    payload is a *string* to anything walking the surrounding dict. One run
    left 3,937,391 characters of it in a single tool_call row."""
    import json

    deck = json.dumps({"slides": [
        {"slideIndex": 0, "contents": [
            {"type": "image", "image": {"data": _png(), "altText": "a roastery"}},
        ]},
    ]})
    args = {"slides_json": deck, "session_id": "s1"}
    reduced = redact_binary(args)

    assert len(reduced["slides_json"]) < 300
    assert "bytes" in reduced["slides_json"]
    assert "a roastery" in reduced["slides_json"]
    assert reduced["session_id"] == "s1"
    # The argument the tool itself is handed is untouched.
    assert len(args["slides_json"]) > 100_000


def test_a_long_string_that_is_not_json_is_left_alone():
    prose = "x" * 200_000
    payload = {"brief": prose}
    assert redact_binary(payload)["brief"] == prose


def test_a_serialised_argument_with_no_payload_keeps_its_exact_text():
    import json

    plan = json.dumps({"slides": [{"title": t} for t in ["a"] * 800]})
    payload = {"plan_json": plan}
    assert redact_binary(payload)["plan_json"] == plan


def test_a_payload_with_nothing_to_strip_is_returned_as_is():
    payload = {"slideIndex": 2, "summary": "done", "items": [1, 2, 3]}
    assert redact_binary(payload) is payload


def test_an_oversized_request_is_not_a_rate_limit():
    exc = Exception(
        "Error code: 429 - {'error': {'message': 'Request too large for gpt-4o "
        "in organization org-x on tokens per min (TPM): Limit 450000, Requested "
        "767678. The input or output tokens must be reduced in order to run "
        "successfully.', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}"
    )
    assert _is_request_too_large(exc)
    # Waiting cannot help, so nothing may back off over it.
    assert not _is_rate_limit_error(exc)


def test_a_genuine_rate_limit_still_retries():
    exc = Exception("Error code: 429 - rate limit reached, try again in 12s")
    assert not _is_request_too_large(exc)
    assert _is_rate_limit_error(exc)


def test_a_context_length_error_is_treated_the_same_way():
    exc = Exception(
        "This model's maximum context length is 128000 tokens, however you "
        "requested 190000 tokens."
    )
    assert _is_request_too_large(exc)
    assert not _is_rate_limit_error(exc)
