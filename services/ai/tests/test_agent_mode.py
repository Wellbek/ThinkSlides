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


def test_simple_mode_has_no_research_phase():
    """The model cannot call a tool that no phase of its run offers."""
    from app.agent import pipeline

    loop = _loop(MODE_SIMPLE)
    loop.search_config = None

    research = pipeline.PHASES_BY_NAME["research"]
    assert research.applies(loop) is False


def test_deep_mode_researches_with_web_search_and_nothing_else():
    from app.agent import pipeline

    loop = _loop(MODE_DEEP)
    loop.search_config = object()

    research = pipeline.PHASES_BY_NAME["research"]
    assert research.applies(loop) is True
    assert research.tools == {"web_search"}


def test_every_phase_offers_only_its_own_tools():
    """Scoping is the whole mechanism: a phase hands the model the tools for
    the step it is on, so there is nothing to jump to."""
    from app.agent import pipeline

    drafting = pipeline.PHASES_BY_NAME["draft"].tools
    assert "design_slide_content" in drafting
    assert "web_search" not in drafting
    assert "plan_presentation_structure" not in drafting
    # The loop makes its own calls in the loop-driven phases, so they offer
    # the model nothing at all.
    assert pipeline.PHASES_BY_NAME["plan"].tools == frozenset()


# ── System prompt ──────────────────────────────────────────────────────


def test_simple_mode_prompt_tells_the_model_it_has_no_web_access():
    prompt = _loop(MODE_SIMPLE)._build_system_prompt({})

    assert "Deep Thinking is OFF" in prompt
    assert "no web access" in prompt
    # web_search must not appear in the tool list or the workflow.
    assert "web_search" not in prompt


def test_simple_mode_prompt_demands_a_hedge_or_a_refusal_not_a_guess():
    """Simple mode is deliberately just the base LLM - no search, by design.
    With no way to check a fact, the only honest options for something it is
    not certain of are to qualify it or leave it out; stating an unsourced
    specific as plain fact reads exactly like a sourced one to the person
    reading it. Live-observed failure without this: invented company names,
    an invented dollar figure reused as two different totals, and a regional
    funding split impossible for the real organisation being described - all
    stated with full, unqualified confidence."""
    prompt = _loop(MODE_SIMPLE)._build_system_prompt({})

    assert "SolarCo" in prompt  # the concrete counter-example, named
    assert "two options" in prompt
    assert "qualify it" in prompt or "leave it out" in prompt


def test_deep_mode_prompt_asks_the_model_to_research_first():
    prompt = _loop(MODE_DEEP)._build_system_prompt({})

    assert "Deep Thinking is ON" in prompt
    assert "call web_search FIRST" in prompt


def _numbered_tools(prompt: str) -> list[int]:
    lines = prompt.splitlines()
    start = lines.index("## Tools") + 2
    numbers = []
    for line in lines[start:]:
        if not line or not line[0].isdigit():
            break
        numbers.append(int(line.split(".", 1)[0]))
    return numbers


def _tool_prompt(mode, has_documents: bool) -> tuple[str, list[int]]:
    loop = _loop(mode)
    loop._session_has_documents = has_documents
    prompt = loop._build_system_prompt({})
    return prompt, _numbered_tools(prompt)


def test_the_tool_list_stays_contiguously_numbered_in_simple_mode():
    """Dropping a tool must renumber the list, not leave a gap."""
    _prompt, numbers = _tool_prompt(MODE_SIMPLE, has_documents=True)

    assert numbers
    assert numbers == list(range(1, len(numbers) + 1))


def test_simple_mode_describes_exactly_one_tool_fewer_than_deep():
    """
    The one it drops is web_search, and the count is asserted as a
    *relationship* rather than a literal.

    It used to be a literal, and that made every branch adding a tool edit the
    same number - so two branches doing it in parallel merged cleanly into a
    test that was wrong, because a merge takes one side of a changed line and
    neither side was right for the union. The relationship holds however many
    tools exist.
    """
    _deep, deep_numbers = _tool_prompt(MODE_DEEP, has_documents=True)
    _simple, simple_numbers = _tool_prompt(MODE_SIMPLE, has_documents=True)

    assert len(deep_numbers) - len(simple_numbers) == 1


def test_the_document_tools_are_described_only_when_there_are_documents():
    """The prompt describing a tool the run withholds is the two disagreeing,
    and the model resolves that by trying it anyway."""
    with_docs, with_numbers = _tool_prompt(MODE_SIMPLE, has_documents=True)
    without, without_numbers = _tool_prompt(MODE_SIMPLE, has_documents=False)

    assert without_numbers == list(range(1, len(without_numbers) + 1))
    # analyze_document and extract_key_data, and nothing else.
    assert len(with_numbers) - len(without_numbers) == 2
    assert "extract_key_data" not in without
    assert "analyze_document" not in without
    assert "extract_key_data" in with_docs


def test_every_described_tool_is_one_the_run_can_actually_call():
    """
    The guard the literal counts were really standing in for.

    A prompt naming a tool the registry does not have is the prompt and the
    loop disagreeing, and a model resolves that by trying it anyway - which is
    the failure the pipeline's own docstring describes. Checking against the
    registry catches a tool vanishing *and* a tool being described but never
    registered, neither of which a count can see.
    """
    from app.agent.registry import ToolRegistry, _register_default_tools

    registry = ToolRegistry()
    _register_default_tools(registry)
    registered = {tool.name for tool in registry.list_tools()}

    prompt, _numbers = _tool_prompt(MODE_DEEP, has_documents=True)
    lines = prompt.splitlines()
    start = lines.index("## Tools") + 2
    described = []
    for line in lines[start:]:
        if not line or not line[0].isdigit():
            break
        described.append(line.split(". ", 1)[1].split(" —")[0].strip())

    assert described
    assert set(described) <= registered, set(described) - registered


def _described_tools(prompt: str) -> set[str]:
    lines = prompt.splitlines()
    start = lines.index("## Tools") + 2
    described = set()
    for line in lines[start:]:
        if not line or not line[0].isdigit():
            break
        described.add(line.split(". ", 1)[1].split(" \u2014")[0].strip())
    return described


def _drafting_loop(pack_id: str | None) -> AgentLoop:
    """A loop sitting on the step that writes a slide.

    That is the step the withheld tools were withheld from, so it is the one
    whose prompt has to agree with its toolset.
    """
    # Simple mode: deep would put the research phase in front of drafting, and
    # the step this is about is the drafting one.
    loop = _loop(MODE_SIMPLE)
    loop._session_has_documents = True
    loop.working_state.pack_id = pack_id
    loop.working_state.brief = {"brief": "a deck", "slideCount": 1}
    loop.working_state.presentation_structure = {
        "totalSlides": 1, "slides": [{"title": "One"}]
    }
    return loop


@pytest.mark.parametrize("pack_id", [None, "authored-abc"])
def test_no_part_of_the_prompt_names_a_tool_the_run_withholds(pack_id):
    """
    The invariant the registry check was one level too high to see.

    `select_layout` is registered on every run - it is withheld from the
    *phase*, which is a layer the previous test does not reach. A run on a
    design language composes its pages in code and is not offered the geometry
    tools, and for a while the prompt went on naming them anyway: the tool
    list, the `## Building Visuals` advice, and worst of all the `## Next
    Step` directive, which is re-issued every turn the model fails to comply.
    A model told each turn to call a tool it cannot see calls something else
    until its turns run out, which is exactly what every template-less run
    did.

    So this checks the whole prompt, not just the numbered list - the
    directive and the prose are where the damage actually was.
    """
    loop = _drafting_loop(pack_id)
    prompt = loop._build_system_prompt({})

    # The directive is the site that mattered most, so this fails loudly if the
    # loop ever stops putting it in the prompt and the check goes vacuous.
    assert "Draft slide 1" in prompt

    withheld = loop._withheld_tools()
    # Both sit behind CODE_AMENDMENT_ENABLED, and a drafting loop with
    # nothing rendered yet withholds edit_deck_with_code on that ground too
    # - it edits a file that does not exist yet at this point in a run.
    # revise_slides sends a finished deck's slides back, so a run that is not
    # amending one has nothing to send.
    expected = {"compute_visual_with_code", "edit_deck_with_code", "revise_slides"}
    if pack_id is not None:
        expected |= {"select_layout", "compose_slide_regions"}
    assert withheld == expected
    for name in withheld:
        assert name not in prompt, f"{name} is described but withheld"


@pytest.mark.parametrize("pack_id", [None, "authored-abc"])
def test_every_described_slide_tool_is_offered_while_drafting(pack_id):
    """
    Described \u2286 offered, one layer below described \u2286 registered.

    Restricted to the tools the *model* chooses. Most of this pipeline is
    loop-driven: the loop calls plan_presentation_structure and
    finalize_and_render_pptx itself, and the tool list describes them so the
    model understands the run it is inside rather than so it can call them.
    The drafting set is different - those are offered, the model picks among
    them, and a name in the prompt that is not in the offered set is a
    instruction the model cannot carry out.
    """
    from app.agent import pipeline

    loop = _drafting_loop(pack_id)
    described = _described_tools(loop._build_system_prompt({}))
    offered = pipeline.SLIDE_TOOLS - loop._withheld_tools()

    assert described & pipeline.SLIDE_TOOLS
    assert described & pipeline.SLIDE_TOOLS <= offered, (
        (described & pipeline.SLIDE_TOOLS) - offered
    )


def test_the_pack_path_still_describes_the_tools_it_does_have():
    """A filter that dropped everything would pass the two tests above."""
    loop = _drafting_loop("authored-abc")
    described = _described_tools(loop._build_system_prompt({}))

    assert "design_slide_content" in described
    assert "build_chart" in described
    assert "select_layout" not in described


def test_the_template_path_is_unchanged():
    """The geometry tools are withheld from the design-language path only."""
    described = _described_tools(_drafting_loop(None)._build_system_prompt({}))

    assert {"select_layout", "compose_slide_regions"} <= described


# ── Mode resolution and persistence ────────────────────────────────────


@pytest.mark.asyncio
async def test_configure_defaults_to_simple_when_no_mode_was_given():
    loop = _loop(None)

    await loop._configure()

    assert loop.mode == MODE_SIMPLE


@pytest.mark.asyncio
async def test_deep_mode_is_downgraded_when_no_search_backend_is_usable(monkeypatch):
    """An unconfigured organisation still generates, just without research."""
    monkeypatch.setattr(
        "app.ai_settings.resolve_search_config",
        lambda row: (None, "No deep-search backend is configured."),
    )

    loop = _loop(MODE_DEEP)
    await loop._configure()

    assert loop.mode == MODE_SIMPLE
    assert loop.search_config is None


@pytest.mark.asyncio
async def test_deep_mode_installs_the_search_config_on_the_run_context(monkeypatch):
    """The web_search tool reads its backend off the run context, not a param."""
    from app.agent import run_context
    from app.ai_settings import SearchConfig

    monkeypatch.setattr(
        "app.ai_settings.resolve_search_config",
        lambda row: (SearchConfig(backend="tavily", api_key="tvly-test"), ""),
    )

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
        assert hasattr(module, "require_chat_provider"), (
            f"{module.__name__} does not consult the run context"
        )

    stub = _StubProvider()
    token = run_context.set_chat_provider(stub)
    try:
        assert run_context.get_chat_provider() is stub
        for module in modules:
            assert module.require_chat_provider() is stub
    finally:
        run_context.reset_chat_provider(token)

    # Outside a run the context is empty - and that is now an error rather
    # than a cue to fall back to a provider built from the environment.
    from app.llm.provider import AINotConfiguredError

    assert run_context.get_chat_provider() is None
    for module in modules:
        with pytest.raises(AINotConfiguredError):
            module.require_chat_provider()


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


def test_saving_a_search_only_model_as_the_reasoning_model_is_rejected():
    """Caught at save time, naming the field it does belong in."""
    from fastapi import HTTPException
    from app.api.settings import ModelRoleInput, _validate_chat_model

    with pytest.raises(HTTPException) as exc:
        _validate_chat_model(
            ModelRoleInput(provider="openai", model="gpt-4o-search-preview")
        )

    assert exc.value.status_code == 400
    assert "reasoning model" in exc.value.detail
    assert "Deep Thinking search model" in exc.value.detail
    _validate_chat_model(ModelRoleInput(provider="openai", model="gpt-4o"))


def test_native_search_is_not_gated_on_a_list_of_model_names():
    """Regression: the list named the retired '-search-preview' models.

    Anything a user could actually select was therefore refused, and the
    refusal told them to pick a model that no longer exists.
    """
    from app.ai_settings import _native_search_supported
    from app.llm.provider import ProviderCredentials

    for model in ("gpt-5.6-terra", "gpt-4o", "some-model-shipped-next-year"):
        ok, reason = _native_search_supported(
            ProviderCredentials("openai", model, "sk-test")
        )
        assert (ok, reason) == (True, ""), model


def test_native_search_reason_names_the_field_to_change(monkeypatch):
    """A provider that does gate search on models still says which field.

    Regression: "requires one of ..." read as "change your chat model", which
    puts a search-only model in the role that drives the agent.
    """
    from dataclasses import replace

    from app.ai_settings import _native_search_supported
    from app.llm.catalog import PROVIDERS_BY_ID
    from app.llm.provider import ProviderCredentials

    gated = replace(
        PROVIDERS_BY_ID["openai"], native_web_search_models=("only-this-one",)
    )
    monkeypatch.setitem(PROVIDERS_BY_ID, "openai", gated)

    ok, reason = _native_search_supported(
        ProviderCredentials("openai", "gpt-4o", "sk-test")
    )

    assert ok is False
    assert "Deep Thinking model" in reason
    assert "only-this-one" in reason


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


@pytest.mark.asyncio
async def test_a_simple_run_still_learns_whether_a_backend_exists(monkeypatch):
    """
    The offer to research has to be reachable from the mode it upgrades.

    ``_can_offer_research`` requires a search config, and this used to be
    resolved only when the mode was already Deep - so a Simple run never had
    one, the offer was never made, and a brief that plainly stood on facts
    ("how big the market is, who the players are") was written from memory
    without the user ever being asked. The existing tests missed it because
    they set ``search_config`` by hand; the defect was in the wiring between
    this method and the gate, not in either one alone.
    """
    from app.ai_settings import SearchConfig

    monkeypatch.setattr(
        "app.ai_settings.resolve_search_config",
        lambda row: (SearchConfig(backend="tavily", api_key="tvly-test"), ""),
    )

    loop = _loop(MODE_SIMPLE)
    await loop._configure()

    assert loop.mode == MODE_SIMPLE
    assert loop.search_config is not None
    loop.working_state.brief = {"needsResearch": True}
    assert loop._can_offer_research() is True


@pytest.mark.asyncio
async def test_a_simple_run_knowing_of_a_backend_still_cannot_use_it(monkeypatch):
    """Knowing a backend exists is not the same as being able to search with it.

    web_search reads its backend off the run context, so a Simple run must
    leave that unset however much it knows - otherwise resolving the config
    early would hand every Simple run the tool it was told it does not have.
    """
    from app.agent import run_context
    from app.ai_settings import SearchConfig

    monkeypatch.setattr(
        "app.ai_settings.resolve_search_config",
        lambda row: (SearchConfig(backend="tavily", api_key="tvly-test"), ""),
    )

    loop = _loop(MODE_SIMPLE)
    try:
        await loop._configure()

        assert loop.search_config is not None
        assert run_context.get_search_config() is None
        assert loop._search_token is None
    finally:
        if loop._search_token is not None:
            run_context.reset_search_config(loop._search_token)
