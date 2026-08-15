"""
Tests for the Simple / Deep Thinking effort levels in the agent loop.

The mode decides two things: whether the agent is offered the web_search tool,
and what the system prompt tells it about researching facts. It also has to
survive the pause/resume round trip, because the resume request carries no mode
of its own.

No database is used - AgentLoop is constructed with db=None and org_id=None,
which is the environment-configuration path and never touches the session.
"""

import json
import uuid

import pytest

from app.agent.loop import (
    MODE_DEEP,
    MODE_SIMPLE,
    AgentLoop,
    normalize_mode,
)


def _loop(mode: str | None = None) -> AgentLoop:
    """A loop past both consultation checkpoints.

    The checkpoints withhold the deck-writing tools until the user has been
    consulted (see test_grounding_checkpoint.py). That is a separate axis from
    the effort level, so these tests hold it open and vary only the mode.
    """
    loop = AgentLoop(db=None, mode=mode)
    loop.working_state.consulted_before_planning = True
    loop.working_state.offered_directions = True
    loop.working_state.reviewed_outline_with_user = True
    return loop


def _tool_names(schemas: list[dict]) -> set[str]:
    return {s["function"]["name"] for s in schemas}


# ── normalize_mode ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("deep", MODE_DEEP),
        ("simple", MODE_SIMPLE),
        ("", MODE_SIMPLE),
        (None, MODE_SIMPLE),
        ("Deep", MODE_SIMPLE),
        ("turbo", MODE_SIMPLE),
    ],
)
def test_normalize_mode_falls_back_to_simple(value, expected):
    """Only an exact "deep" opts in; anything else is the safe default."""
    assert normalize_mode(value) == expected


# ── Tool gating ────────────────────────────────────────────────────────


def test_simple_mode_does_not_offer_web_search():
    """The model cannot call a tool it was never given."""
    loop = _loop(MODE_SIMPLE)

    names = _tool_names(loop._tool_schemas_for_mode())

    assert "web_search" not in names
    # The rest of the toolset is untouched.
    assert "finalize_and_render_pptx" in names
    assert "plan_presentation_structure" in names


def test_deep_mode_offers_web_search():
    loop = _loop(MODE_DEEP)

    assert "web_search" in _tool_names(loop._tool_schemas_for_mode())


def test_gating_removes_only_the_deep_only_tools():
    """Simple mode is the full toolset minus web_search, nothing else."""
    simple = _tool_names(_loop(MODE_SIMPLE)._tool_schemas_for_mode())
    deep = _tool_names(_loop(MODE_DEEP)._tool_schemas_for_mode())

    assert deep - simple == {"web_search"}


# ── System prompt ──────────────────────────────────────────────────────


def test_simple_mode_prompt_tells_the_model_it_has_no_web_access():
    prompt = _loop(MODE_SIMPLE)._build_system_prompt({})

    assert "Deep Thinking is OFF" in prompt
    assert "no web access" in prompt
    # web_search must not appear in the tool list or the workflow.
    assert "web_search" not in prompt


def test_deep_mode_prompt_asks_the_model_to_research_first():
    prompt = _loop(MODE_DEEP)._build_system_prompt({})

    assert "Deep Thinking is ON" in prompt
    assert "call web_search FIRST" in prompt


def test_tool_and_workflow_lists_stay_contiguously_numbered_in_simple_mode():
    """Dropping web_search must renumber the lists, not leave a gap."""
    prompt = _loop(MODE_SIMPLE)._build_system_prompt({})
    lines = prompt.splitlines()

    tools_start = lines.index("## Available Tools") + 2
    tool_numbers = []
    for line in lines[tools_start:]:
        if not line or not line[0].isdigit():
            break
        tool_numbers.append(int(line.split(".", 1)[0]))

    assert tool_numbers == list(range(1, len(tool_numbers) + 1))
    assert len(tool_numbers) == 14  # 15 in deep mode, minus web_search


# ── Mode resolution and persistence ────────────────────────────────────


@pytest.mark.asyncio
async def test_configure_defaults_to_simple_when_no_mode_was_given():
    loop = _loop(None)

    await loop._configure()

    assert loop.mode == MODE_SIMPLE


@pytest.mark.asyncio
async def test_deep_mode_is_downgraded_when_no_search_backend_is_usable(monkeypatch):
    """An unconfigured organisation still generates, just without research."""
    from app.config import settings as env_settings
    monkeypatch.setattr(env_settings, "web_search_provider", "tavily")
    monkeypatch.setattr(env_settings, "tavily_api_key", "")
    monkeypatch.setattr(env_settings, "zhipu_api_key", "")

    loop = _loop(MODE_DEEP)
    await loop._configure()

    assert loop.mode == MODE_SIMPLE
    assert loop.search_config is None
    assert "web_search" not in _tool_names(loop._tool_schemas_for_mode())


@pytest.mark.asyncio
async def test_deep_mode_installs_the_search_config_on_the_run_context(monkeypatch):
    """The web_search tool reads its backend off the run context, not a param."""
    from app.agent import run_context
    from app.config import settings as env_settings
    monkeypatch.setattr(env_settings, "web_search_provider", "tavily")
    monkeypatch.setattr(env_settings, "tavily_api_key", "tvly-test")

    loop = _loop(MODE_DEEP)
    try:
        await loop._configure()

        assert loop.mode == MODE_DEEP
        assert run_context.get_search_config().backend == "tavily"
    finally:
        if loop._search_token is not None:
            run_context.reset_search_config(loop._search_token)


class _FakeRedis:
    """Just enough of the Redis client for the working-state round trip."""

    def __init__(self, initial: dict | None = None):
        self.store = dict(initial or {})

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.mark.asyncio
async def test_mode_is_saved_into_the_working_state():
    session_id = uuid.uuid4()
    loop = _loop(MODE_DEEP)
    loop.redis_client = _FakeRedis()

    await loop._save_working_state(session_id)

    saved = json.loads(loop.redis_client.store[f"working_state:{session_id}"])
    assert saved["mode"] == MODE_DEEP


@pytest.mark.asyncio
async def test_a_resumed_run_inherits_the_paused_run_s_mode():
    """The resume request has no composer, so the saved mode is the only source."""
    session_id = uuid.uuid4()
    state = json.dumps({"mode": MODE_DEEP, "completed_tools": {}})

    loop = _loop(None)  # None = "inherit"
    loop.redis_client = _FakeRedis({f"working_state:{session_id}": state})

    assert await loop._load_working_state(session_id) is True
    assert loop.mode == MODE_DEEP


@pytest.mark.asyncio
async def test_an_explicit_mode_wins_over_a_stale_working_state():
    """A fresh Simple message must not be dragged into Deep by leftover state."""
    session_id = uuid.uuid4()
    state = json.dumps({"mode": MODE_DEEP, "completed_tools": {}})

    loop = _loop(MODE_SIMPLE)
    loop.redis_client = _FakeRedis({f"working_state:{session_id}": state})

    await loop._load_working_state(session_id)

    assert loop.mode == MODE_SIMPLE


@pytest.mark.asyncio
async def test_working_state_written_before_modes_existed_resumes_as_simple():
    session_id = uuid.uuid4()
    state = json.dumps({"completed_tools": {}})

    loop = _loop(None)
    loop.redis_client = _FakeRedis({f"working_state:{session_id}": state})

    await loop._load_working_state(session_id)

    assert loop.mode == MODE_SIMPLE


# ── The configured provider reaches the tools ──────────────────────────


class _StubProvider:
    """Stands in for a provider built from an organisation's credentials."""

    def __init__(self):
        self.calls = 0

    async def chat(self, **kwargs):
        self.calls += 1
        return {"message": {"content": "{}"}, "tool_calls": []}


@pytest.mark.asyncio
async def test_tools_use_the_run_s_configured_provider_not_the_environment():
    """Regression: choosing a provider in Settings only changed the agent loop.

    design_slide_content, select_source_image, extract_key_data and
    plan_presentation_structure each call get_llm_provider() directly, which
    returns the process-wide provider built from environment variables. An
    organisation that configured OpenAI still had its slides written by the
    env-configured Zhipu model - and an organisation with no env key at all
    could not generate anything.
    """
    from app.agent import run_context
    from app.agent.tools import (
        design_slide_content,
        extract_key_data,
        select_source_image,
        plan_presentation_structure,
    )

    modules = [
        design_slide_content,
        select_source_image,
        extract_key_data,
        plan_presentation_structure,
    ]

    # Every one of them must prefer the run context over its module-level
    # getter, so a run's provider is what actually makes the call.
    for module in modules:
        assert hasattr(module, "get_chat_provider"), (
            f"{module.__name__} does not consult the run context"
        )

    stub = _StubProvider()
    token = run_context.set_chat_provider(stub)
    try:
        assert run_context.get_chat_provider() is stub
        for module in modules:
            assert module.get_chat_provider() is stub
    finally:
        run_context.reset_chat_provider(token)

    # Outside a run the context is empty, so the env provider still applies.
    assert run_context.get_chat_provider() is None


@pytest.mark.asyncio
async def test_configure_publishes_the_chat_provider_on_the_run_context(monkeypatch):
    """The loop is what installs it, so a tool called mid-run can find it."""
    from app.agent import run_context

    loop = _loop(MODE_SIMPLE)
    await loop._configure()
    try:
        assert run_context.get_chat_provider() is loop.llm
    finally:
        if loop._provider_token is not None:
            run_context.reset_chat_provider(loop._provider_token)


@pytest.mark.asyncio
async def test_run_clears_the_provider_it_installed():
    """A finished run must not leak its provider into the next one."""
    from app.agent import run_context

    loop = _loop(MODE_SIMPLE)
    loop.redis_client = _FakeRedis()

    async def _boom(*args, **kwargs):
        raise RuntimeError("context load failed")

    monkey = loop._load_context if hasattr(loop, "_load_context") else None
    assert monkey is not None
    loop._load_context = _boom

    with pytest.raises(RuntimeError):
        async for _ in loop.run(uuid.uuid4()):
            pass

    assert run_context.get_chat_provider() is None


# ── Search-only models and rate limits ─────────────────────────────────


def test_search_only_models_are_not_valid_chat_models():
    """A model that rejects `tools` cannot drive a tool-calling agent."""
    from app.llm.catalog import supports_tool_calling

    assert supports_tool_calling("openai", "gpt-4o") is True
    assert supports_tool_calling("openai", "gpt-4o-search-preview") is False
    assert supports_tool_calling("openai", "gpt-4o-mini-search-preview") is False
    assert supports_tool_calling("anthropic", "claude-opus-5") is True
    # An unknown provider is assumed capable rather than blocked.
    assert supports_tool_calling("someone-else", "their-model") is True


def test_saving_a_search_only_model_as_the_chat_model_is_rejected():
    """Caught at save time, naming the field it does belong in."""
    from fastapi import HTTPException
    from app.api.settings import ModelRoleInput, _validate_chat_model

    with pytest.raises(HTTPException) as exc:
        _validate_chat_model(
            ModelRoleInput(provider="openai", model="gpt-4o-search-preview")
        )

    assert exc.value.status_code == 400
    assert "Deep Thinking model" in exc.value.detail
    _validate_chat_model(ModelRoleInput(provider="openai", model="gpt-4o"))


def test_native_search_reason_names_the_field_to_change():
    """Regression: "requires one of ..." read as "change your chat model"."""
    from app.ai_settings import _native_search_supported
    from app.llm.provider import ProviderCredentials

    ok, reason = _native_search_supported(
        ProviderCredentials("openai", "gpt-4o", "sk-test")
    )

    assert ok is False
    assert "Deep Thinking model" in reason
    assert "gpt-4o-search-preview" in reason


@pytest.mark.parametrize(
    "exc,retryable",
    [
        (type("E", (Exception,), {"status_code": 429})(), True),
        (type("E", (Exception,), {"status_code": 503})(), True),
        (type("E", (Exception,), {"status_code": 401})(), False),
        (type("E", (Exception,), {"status_code": 404})(), False),
        (Exception("Rate limit reached for gpt-4o"), True),
        (Exception("invalid x-api-key"), False),
    ],
)
def test_only_transient_failures_are_retried(exc, retryable):
    from app.agent.loop import _is_retryable

    assert _is_retryable(exc) is retryable


def test_the_provider_s_own_retry_advice_is_honoured():
    """OpenAI puts the wait in the message; obeying it beats guessing."""
    from app.agent.loop import _retry_after_seconds

    exc = Exception(
        "Rate limit reached for gpt-4o ... Please try again in 7.696s. Visit ..."
    )

    assert _retry_after_seconds(exc) == pytest.approx(8.196)
    assert _retry_after_seconds(Exception("no advice here")) is None


@pytest.mark.asyncio
async def test_a_transient_server_error_is_retried_rather_than_abandoning_the_run(monkeypatch):
    """Regression: one hiccup threw away every slide designed so far."""
    import app.agent.loop as loop_module

    sleeps: list[float] = []

    async def _no_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(loop_module.asyncio, "sleep", _no_sleep)

    attempts = {"n": 0}

    class _Flaky:
        async def chat(self, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                err = Exception("Bad gateway")
                err.status_code = 502
                raise err
            return {"message": {"content": "done"}, "tool_calls": []}

    loop = _loop(MODE_SIMPLE)
    loop.llm = _Flaky()

    result = await loop._chat_with_retry(messages=[], tools=None)

    assert result["message"]["content"] == "done"
    assert attempts["n"] == 2
    assert sleeps == [pytest.approx(loop.RETRY_BASE_SECONDS)]


@pytest.mark.asyncio
async def test_a_rate_limit_is_left_to_the_provider_layer(monkeypatch):
    """The provider retries 429s, honouring the provider's own retry-after.
    Retrying them here as well multiplied the two budgets and made the wait
    reported to the user a fraction of the one actually being served."""
    import app.agent.loop as loop_module

    async def _no_sleep(seconds):
        raise AssertionError("the loop must not sit on a rate limit itself")

    monkeypatch.setattr(loop_module.asyncio, "sleep", _no_sleep)

    attempts = {"n": 0}

    class _RateLimited:
        async def chat(self, **kwargs):
            attempts["n"] += 1
            err = Exception("Rate limit reached. Please try again in 2s.")
            err.status_code = 429
            raise err

    loop = _loop(MODE_SIMPLE)
    loop.llm = _RateLimited()

    with pytest.raises(Exception, match="Rate limit"):
        await loop._chat_with_retry(messages=[], tools=None)

    assert attempts["n"] == 1


@pytest.mark.asyncio
async def test_a_bad_key_is_not_retried(monkeypatch):
    """Retrying an auth failure just wastes the user's time."""
    import app.agent.loop as loop_module

    async def _no_sleep(seconds):
        raise AssertionError("should not have slept")

    monkeypatch.setattr(loop_module.asyncio, "sleep", _no_sleep)

    class _BadKey:
        async def chat(self, **kwargs):
            err = Exception("invalid x-api-key")
            err.status_code = 401
            raise err

    loop = _loop(MODE_SIMPLE)
    loop.llm = _BadKey()

    with pytest.raises(Exception, match="invalid x-api-key"):
        await loop._chat_with_retry(messages=[], tools=None)
