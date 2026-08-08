"""
Tests for extract_json_object, the shared LLM-response JSON extractor.

The concrete bug this exists for: design_slide_content's old fallback
silently produced {"raw_content": ..., "slideIndex": N} whenever the model's
response wasn't strictly parseable JSON (a stray sentence before/after it, or
an unfenced response) - which passed straight through as a "successful"
slide with zero content items. Rendered decks then had the right slide
count but nothing on them.
"""

from app.agent.tools._json_extract import coerce_json_arg, extract_json_object


def test_plain_json_object():
    assert extract_json_object('{"a": 1}') == {"a": 1}


def test_json_with_surrounding_whitespace():
    assert extract_json_object('\n\n  {"a": 1}  \n') == {"a": 1}


def test_fenced_json_block():
    text = 'Here is the slide:\n```json\n{"a": 1}\n```\n'
    assert extract_json_object(text) == {"a": 1}


def test_unfenced_prose_before_and_after():
    """The exact shape that used to fall through to the empty-object
    fallback: no fence, just a sentence wrapped around the JSON."""
    text = 'Sure, here is the slide content:\n\n{"a": 1}\n\nLet me know if you want changes.'
    assert extract_json_object(text) == {"a": 1}


def test_nested_braces_and_strings_containing_braces():
    text = 'Notes: {"a": {"b": 2}, "c": "text with a { brace } inside"}'
    assert extract_json_object(text) == {"a": {"b": 2}, "c": "text with a { brace } inside"}


def test_escaped_quote_inside_string_does_not_break_brace_matching():
    text = '{"a": "she said \\"hi\\""}'
    assert extract_json_object(text) == {"a": 'she said "hi"'}


def test_no_json_anywhere_returns_none():
    assert extract_json_object("I could not generate this slide, sorry.") is None


def test_empty_string_returns_none():
    assert extract_json_object("") is None
    assert extract_json_object("   ") is None


def test_json_array_at_top_level_returns_none():
    """Callers need a dict; a bare array isn't a valid slide/structure object."""
    assert extract_json_object('[{"a": 1}]') is None


# ── coerce_json_arg ─────────────────────────────────────────────────────
# Regression: ask_user_question crashed in production with "the JSON object
# must be str, bytes or bytearray, not list" - the model sent a native JSON
# array for "options" despite its declared "string" type, and a bare
# json.loads(options) raises TypeError (not JSONDecodeError) on a list.


def test_coerce_json_arg_passes_through_a_native_list():
    assert coerce_json_arg(["a", "b"], []) == ["a", "b"]


def test_coerce_json_arg_passes_through_a_native_dict():
    assert coerce_json_arg({"a": 1}, {}) == {"a": 1}


def test_coerce_json_arg_parses_a_json_string():
    assert coerce_json_arg('["a", "b"]', []) == ["a", "b"]
    assert coerce_json_arg('{"a": 1}', {}) == {"a": 1}


def test_coerce_json_arg_falls_back_on_unparseable_string():
    assert coerce_json_arg("not json", "fallback") == "fallback"


def test_coerce_json_arg_falls_back_on_empty_or_none():
    assert coerce_json_arg("", "fallback") == "fallback"
    assert coerce_json_arg(None, "fallback") == "fallback"
