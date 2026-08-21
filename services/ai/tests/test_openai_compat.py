"""
Adapting to the parameters a model rejects, without a list of model names.

The bug these cover: ``gpt-4o-search-preview`` was retired and the workspace
was switched to a current model, which answered every request with
``Unsupported parameter: 'max_tokens' ... Use 'max_completion_tokens'
instead.`` - and, once that was renamed, with ``Unsupported value:
'temperature' does not support 0.0 with this model.`` Both come back as plain
400s that name the offending parameter, so neither needs the model to be
recognized in advance.
"""

import pytest

from app.llm.openai_compat import (
    MAX_ADAPTATIONS,
    ParameterCompatibility,
    compatibility_for,
    create_with_compatible_params,
    rejected_parameter,
    reset_cache,
    suggested_replacement,
)


# The two errors that motivated all of this, verbatim from the API.
MAX_TOKENS_ERROR = (
    "Error code: 400 - {'error': {'message': \"Unsupported parameter: "
    "'max_tokens' is not supported with this model. Use "
    "'max_completion_tokens' instead.\", 'type': 'invalid_request_error', "
    "'param': 'max_tokens', 'code': 'unsupported_parameter'}}"
)
TEMPERATURE_ERROR = (
    "Error code: 400 - {'error': {'message': \"Unsupported value: "
    "'temperature' does not support 0.0 with this model. Only the default (1) "
    "value is supported.\", 'type': 'invalid_request_error', 'param': "
    "'temperature', 'code': 'unsupported_value'}}"
)


class _ApiError(Exception):
    """An SDK error, which carries the blamed parameter as an attribute."""

    def __init__(self, message: str, param: str | None = None) -> None:
        super().__init__(message)
        self.param = param


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_cache()
    yield
    reset_cache()


# ── Reading the rejection ──────────────────────────────────────────────


def test_the_blamed_parameter_is_read_off_the_error_attribute():
    exc = _ApiError(MAX_TOKENS_ERROR, param="max_tokens")

    assert rejected_parameter(exc, {"max_tokens": 4096}) == "max_tokens"


def test_the_blamed_parameter_is_read_out_of_the_message_when_untyped():
    """A raw httpx-shaped error has no `param` attribute, only the text."""
    exc = Exception(MAX_TOKENS_ERROR)

    assert rejected_parameter(exc, {"max_tokens": 4096}) == "max_tokens"


def test_the_blamed_parameter_is_read_off_a_response_body():
    exc = Exception("Unsupported parameter")
    exc.body = {"error": {"param": "temperature"}}

    assert rejected_parameter(exc, {"temperature": 0.0}) == "temperature"


def test_an_error_that_is_not_about_a_parameter_is_left_alone():
    """Retrying an auth failure with a shorter request helps nobody."""
    exc = _ApiError("Incorrect API key provided: sk-***.", param=None)

    assert rejected_parameter(exc, {"max_tokens": 4096}) is None


def test_a_rejection_naming_a_parameter_we_did_not_send_is_not_ours():
    exc = _ApiError(
        "Unsupported parameter: 'reasoning_effort' is not supported.",
        param="reasoning_effort",
    )

    assert rejected_parameter(exc, {"max_tokens": 4096}) is None


def test_a_content_complaint_about_messages_is_not_a_parameter_rejection():
    """`param` is set, but removing `messages` would empty the request."""
    exc = _ApiError(
        "Invalid 'messages': the last message must have role 'user'.",
        param="messages",
    )

    assert rejected_parameter(exc, {"messages": []}) is None


def test_the_replacement_name_is_taken_from_the_message():
    assert suggested_replacement(Exception(MAX_TOKENS_ERROR)) == "max_completion_tokens"


def test_there_is_no_replacement_when_none_was_offered():
    assert suggested_replacement(Exception(TEMPERATURE_ERROR)) is None


# ── Rewriting the request ──────────────────────────────────────────────


def test_a_renamed_parameter_travels_in_extra_body():
    """A named argument the installed SDK does not know is a TypeError.

    extra_body is forwarded to the API verbatim on every SDK version, so a
    parameter introduced after the pin still reaches the endpoint.
    """
    compat = ParameterCompatibility()
    compat.learn(_ApiError(MAX_TOKENS_ERROR, "max_tokens"), {"max_tokens": 4096})

    sent = compat.apply({"model": "m", "max_tokens": 4096})

    assert sent == {"model": "m", "extra_body": {"max_completion_tokens": 4096}}


def test_a_dropped_parameter_is_simply_absent():
    compat = ParameterCompatibility()
    compat.learn(_ApiError(TEMPERATURE_ERROR, "temperature"), {"temperature": 0.0})

    assert compat.apply({"model": "m", "temperature": 0.0}) == {"model": "m"}


def test_an_existing_extra_body_is_preserved():
    """web_search_options and friends already ride there."""
    compat = ParameterCompatibility()
    compat.learn(_ApiError(MAX_TOKENS_ERROR, "max_tokens"), {"max_tokens": 4096})

    sent = compat.apply({"max_tokens": 8, "extra_body": {"web_search_options": {}}})

    assert sent["extra_body"] == {"web_search_options": {}, "max_completion_tokens": 8}


def test_a_rename_of_a_rename_still_resolves():
    compat = ParameterCompatibility(renames={"a": "b", "b": "c"})

    assert compat.apply({"a": 1}) == {"extra_body": {"c": 1}}


def test_a_replacement_that_is_itself_rejected_is_dropped_not_bounced_back():
    """Regression guard: A -> B, "use A instead" must not oscillate forever."""
    compat = ParameterCompatibility()
    compat.learn(_ApiError(MAX_TOKENS_ERROR, "max_tokens"), {"max_tokens": 4096})

    compat.learn(
        _ApiError(
            "Unsupported parameter: 'max_completion_tokens' is not supported "
            "with this model. Use 'max_tokens' instead.",
            "max_completion_tokens",
        ),
        {"extra_body": {"max_completion_tokens": 4096}},
    )

    assert "max_completion_tokens" in compat.dropped
    assert compat.apply({"max_tokens": 4096}) == {}


# ── The retry loop ─────────────────────────────────────────────────────


def _sender(errors: dict[str, str], seen: list[dict]):
    """A stand-in endpoint that rejects each named parameter in turn."""

    async def send(payload):
        seen.append(payload)
        flat = {**payload, **(payload.get("extra_body") or {})}
        for name, message in errors.items():
            if name in flat:
                raise _ApiError(message, name)
        return "accepted"

    return send


@pytest.mark.asyncio
async def test_a_request_is_rewritten_until_the_model_accepts_it():
    """The reported failure end to end: both parameters, one call each."""
    seen: list[dict] = []
    send = _sender(
        {"max_tokens": MAX_TOKENS_ERROR, "temperature": TEMPERATURE_ERROR}, seen
    )

    result = await create_with_compatible_params(
        send,
        {"model": "gpt-5.6-terra", "messages": [], "temperature": 0.0, "max_tokens": 4096},
        endpoint="https://api.openai.com/v1",
        provider="openai",
        model="gpt-5.6-terra",
    )

    assert result == "accepted"
    assert len(seen) == 3
    assert seen[-1] == {
        "model": "gpt-5.6-terra",
        "messages": [],
        "extra_body": {"max_completion_tokens": 4096},
    }


@pytest.mark.asyncio
async def test_what_one_call_learns_the_next_call_starts_from():
    """The wasted round trip is paid once per process, not once per call."""
    seen: list[dict] = []
    send = _sender({"max_tokens": MAX_TOKENS_ERROR}, seen)
    request = {"model": "gpt-5.6-terra", "max_tokens": 4096}
    call = lambda: create_with_compatible_params(  # noqa: E731
        send, dict(request), endpoint="https://api.openai.com/v1",
        provider="openai", model="gpt-5.6-terra",
    )

    await call()
    await call()

    assert len(seen) == 3, "the second call must not repeat the rejected request"


@pytest.mark.asyncio
async def test_what_one_model_learns_is_not_applied_to_another():
    """Two models behind one key do not have to behave the same way."""
    await create_with_compatible_params(
        _sender({"max_tokens": MAX_TOKENS_ERROR}, []),
        {"model": "gpt-5.6-terra", "max_tokens": 4096},
        endpoint="https://api.openai.com/v1", provider="openai", model="gpt-5.6-terra",
    )

    other = compatibility_for("https://api.openai.com/v1", "gpt-4o")

    assert other.renames == {} and other.dropped == set()


@pytest.mark.asyncio
async def test_an_unrelated_failure_is_raised_without_a_second_attempt():
    """A billing error must surface as itself, not as four more billing errors."""
    calls = 0

    async def send(payload):
        nonlocal calls
        calls += 1
        raise _ApiError("You exceeded your current quota.", param=None)

    with pytest.raises(_ApiError, match="quota"):
        await create_with_compatible_params(
            send, {"model": "m", "max_tokens": 4096},
            endpoint="e", provider="openai", model="m",
        )

    assert calls == 1


@pytest.mark.asyncio
async def test_an_endpoint_that_never_stops_complaining_gives_up():
    """The bound exists so a broken endpoint fails fast instead of looping."""
    calls = 0

    async def send(payload):
        nonlocal calls
        calls += 1
        name = f"p{calls}"
        payload[name] = 1  # a fresh parameter to complain about each time
        raise _ApiError(f"Unsupported parameter: '{name}' is not supported.", name)

    with pytest.raises(_ApiError):
        await create_with_compatible_params(
            send, {"model": "m", "max_tokens": 4096},
            endpoint="e", provider="openai", model="m",
        )

    assert calls == MAX_ADAPTATIONS + 1
