"""
Discovered MCP tools: namespaced, run-scoped, and offered only where they belong.

The three properties this file is about are all about *where* a tool appears,
and each of them fails silently if it breaks. A collision shadows a built-in
without saying so. A tool left on the global registry appears in the next
organisation's run. A tool in every phase makes the drafting phase's toolset
unrecognisable, and the pipeline's own docstring says what that does to a model.
"""

import uuid

import pytest

from app.agent.registry import ToolRegistry, get_registry
from app.connections import mcp, mcp_tools
from app.connections.store import ResolvedConnection

pytestmark = pytest.mark.unconfigured_workspace


def _connection(slug="wiki", label="Company wiki", **config) -> ResolvedConnection:
    return ResolvedConnection(
        id=uuid.uuid4(),
        connection_id="mcp_http",
        kind="mcp",
        slug=slug,
        label=label,
        config={"url": "https://mcp.example.com/mcp", **config},
        secrets={"token": "tok-1234"},
    )


def _server(slug="wiki", label="Company wiki", tools=("search",), **kwargs):
    return mcp_tools.DiscoveredServer(
        slug=slug,
        label=label,
        session=mcp.McpSession(url="https://mcp.example.com/mcp"),
        tools=[mcp.McpTool(name=name, description=f"The {name} tool.")
               for name in tools],
        **kwargs,
    )


@pytest.fixture
def listing(monkeypatch):
    """Make ``mcp.list_tools`` answer without a network."""
    def install(answer):
        async def list_tools(url, token=""):
            if isinstance(answer, Exception):
                raise answer
            return mcp.McpSession(url=url, token=token), [
                mcp.McpTool(name=name, description=f"The {name} tool.")
                for name in answer
            ]

        monkeypatch.setattr(mcp, "list_tools", list_tools)
    return install


class TestNamespacing:
    def test_a_tool_is_registered_under_its_server(self):
        assert mcp_tools.tool_name("wiki", "search") == "mcp__wiki__search"

    def test_two_servers_offering_the_same_tool_do_not_collide(self):
        registry = ToolRegistry()

        mcp_tools.register(registry, [
            _server(slug="wiki", tools=("search",)),
            _server(slug="crm", tools=("search",)),
        ])

        names = {tool.name for tool in registry.list_tools()}
        assert names == {"mcp__wiki__search", "mcp__crm__search"}

    def test_a_discovered_tool_cannot_shadow_a_built_in(self):
        """
        The failure that does not announce itself: select_layout arriving from a
        customer's server and replacing the real one.
        """
        registry = get_registry().fork()
        before = registry.get("select_layout")

        mcp_tools.register(registry, [_server(tools=("select_layout",))])

        assert registry.get("select_layout") == before
        assert registry.get("mcp__wiki__select_layout") is not None

    def test_the_prefix_is_recognisable(self):
        assert mcp_tools.is_mcp_tool("mcp__wiki__search")
        assert not mcp_tools.is_mcp_tool("select_layout")


class TestRunScoping:
    def test_a_fork_does_not_change_the_global_registry(self):
        """
        This service is shared. A tool from one organisation's server must not
        still be registered when another organisation's run starts.
        """
        global_registry = get_registry()
        before = len(global_registry.list_tools())

        fork = global_registry.fork()
        mcp_tools.register(fork, [_server(tools=("search", "read"))])

        assert len(global_registry.list_tools()) == before
        assert len(fork.list_tools()) == before + 2

    def test_a_fork_starts_with_every_built_in(self):
        global_registry = get_registry()

        fork = global_registry.fork()

        assert {t.name for t in fork.list_tools()} == {
            t.name for t in global_registry.list_tools()
        }

    def test_two_forks_do_not_see_each_others_tools(self):
        base = get_registry()
        first, second = base.fork(), base.fork()

        mcp_tools.register(first, [_server(slug="a", tools=("search",))])

        assert second.get("mcp__a__search") is None


class TestPhaseScoping:
    def test_research_is_the_default(self):
        """
        The conservative choice: the drafting phase is where the model composes
        a page, and every extra tool there costs attention on every slide.
        """
        assert mcp_tools.phases_for(_connection()) == ("research",)

    def test_a_connection_can_ask_for_the_drafting_phase(self):
        assert mcp_tools.phases_for(
            _connection(phases=["draft"])
        ) == ("draft",)

    def test_a_connection_can_ask_for_both(self):
        assert mcp_tools.phases_for(
            _connection(phases=["research", "draft"])
        ) == ("research", "draft")

    def test_the_order_is_the_pipeline_order_not_the_input_order(self):
        assert mcp_tools.phases_for(
            _connection(phases=["draft", "research"])
        ) == ("research", "draft")

    def test_a_comma_separated_string_is_accepted(self):
        assert mcp_tools.phases_for(
            _connection(phases="research, draft")
        ) == ("research", "draft")

    def test_an_unknown_phase_falls_back_rather_than_meaning_everywhere(self):
        assert mcp_tools.phases_for(
            _connection(phases=["rendering"])
        ) == mcp_tools.DEFAULT_PHASES

    def test_registration_reports_which_phase_each_tool_belongs_to(self):
        registry = ToolRegistry()

        by_phase = mcp_tools.register(registry, [
            _server(slug="wiki", tools=("search",), phases=("research",)),
            _server(slug="brand", tools=("logo",), phases=("draft",)),
        ])

        assert by_phase["research"] == {"mcp__wiki__search"}
        assert by_phase["draft"] == {"mcp__brand__logo"}

    def test_a_server_in_both_phases_appears_in_both(self):
        registry = ToolRegistry()

        by_phase = mcp_tools.register(registry, [
            _server(tools=("search",), phases=("research", "draft")),
        ])

        assert "mcp__wiki__search" in by_phase["research"]
        assert "mcp__wiki__search" in by_phase["draft"]

    def test_every_offerable_phase_is_a_real_pipeline_phase(self):
        """
        A phase name nobody walks is a scoping choice that silently means
        "nowhere".
        """
        from app.agent.pipeline import PHASES_BY_NAME

        for phase in mcp_tools.OFFERABLE_PHASES:
            assert phase in PHASES_BY_NAME, phase

    def test_the_offerable_phases_are_the_model_driven_ones(self):
        """
        A loop-driven phase makes its own calls, so offering it a tool means
        nothing - the loop would never pick it up.
        """
        from app.agent.pipeline import MODEL_DRIVEN, PHASES_BY_NAME

        for phase in mcp_tools.OFFERABLE_PHASES:
            assert PHASES_BY_NAME[phase].driver == MODEL_DRIVEN, phase


class TestDiscovery:
    async def test_it_returns_one_server_per_connection(self, listing):
        listing(("search", "read"))

        servers = await mcp_tools.discover([
            _connection(slug="wiki"), _connection(slug="crm"),
        ])

        assert [s.slug for s in servers] == ["wiki", "crm"]
        assert all(s.ok for s in servers)

    async def test_no_connections_means_no_work(self, listing):
        assert await mcp_tools.discover([]) == []

    async def test_the_handshakes_run_concurrently(self, monkeypatch):
        """
        N servers should cost roughly one round trip. Serial handshakes mean a
        workspace with ten connections waits a minute for its first slide.
        """
        import asyncio

        in_flight = 0
        peak = 0

        async def list_tools(url, token=""):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1
            return mcp.McpSession(url=url), []

        monkeypatch.setattr(mcp, "list_tools", list_tools)
        await mcp_tools.discover([_connection(slug=f"s{n}") for n in range(4)])

        assert peak == 4

    async def test_an_unreachable_server_reports_rather_than_raises(self, listing):
        listing(mcp.McpError("connection refused"))

        servers = await mcp_tools.discover([_connection()])

        assert len(servers) == 1
        assert not servers[0].ok
        assert "refused" in servers[0].error

    async def test_one_bad_server_does_not_stop_the_others(self, monkeypatch):
        async def list_tools(url, token=""):
            if "broken" in url:
                raise mcp.McpError("down")
            return mcp.McpSession(url=url), [mcp.McpTool("search", "")]

        monkeypatch.setattr(mcp, "list_tools", list_tools)

        servers = await mcp_tools.discover([
            _connection(slug="broken", url="https://broken.example.com/mcp"),
            _connection(slug="fine"),
        ])

        assert [s.ok for s in servers] == [False, True]

    async def test_an_unexpected_exception_is_still_not_fatal(self, monkeypatch):
        """
        A bug in this path must not take a run down with it. Logged as a bug,
        recorded as a degraded server, and the deck is still built.
        """
        async def list_tools(url, token=""):
            raise ValueError("something nobody predicted")

        monkeypatch.setattr(mcp, "list_tools", list_tools)

        servers = await mcp_tools.discover([_connection()])

        assert not servers[0].ok
        assert "nobody predicted" in servers[0].error

    async def test_a_connection_with_no_url_is_degraded_not_attempted(self, listing):
        listing(("search",))
        connection = ResolvedConnection(
            id=uuid.uuid4(), connection_id="mcp_http", kind="mcp",
            slug="empty", label="Empty", config={}, secrets={},
        )

        servers = await mcp_tools.discover([connection])

        assert not servers[0].ok
        assert "URL" in servers[0].error


class TestRegistrationOfDegradedServers:
    def test_a_failed_server_contributes_no_tools(self):
        registry = ToolRegistry()

        by_phase = mcp_tools.register(registry, [
            mcp_tools.DiscoveredServer(slug="down", label="Down",
                                       error="unreachable"),
        ])

        assert registry.list_tools() == []
        assert all(not names for names in by_phase.values())

    def test_the_model_is_never_shown_a_tool_that_cannot_run(self):
        registry = ToolRegistry()

        by_phase = mcp_tools.register(registry, [
            _server(slug="up", tools=("search",)),
            mcp_tools.DiscoveredServer(slug="down", label="Down", error="nope"),
        ])

        assert by_phase["research"] == {"mcp__up__search"}


class TestTheRegisteredSchema:
    def test_the_description_names_the_server_it_came_from(self):
        """
        The model is choosing between this and a built-in, and where it came
        from is what makes the choice sensible. It also marks the text after it
        as written by a third party.
        """
        registry = ToolRegistry()
        mcp_tools.register(registry, [_server(label="Company wiki")])

        definition, _fn = registry.get("mcp__wiki__search")

        assert definition.description.startswith("[Company wiki]")

    def test_a_tool_with_no_description_still_gets_one(self):
        registry = ToolRegistry()
        server = _server()
        server.tools = [mcp.McpTool(name="search", description="")]

        mcp_tools.register(registry, [server])
        definition, _fn = registry.get("mcp__wiki__search")

        assert "search" in definition.description

    def test_the_input_schema_becomes_parameters(self):
        registry = ToolRegistry()
        server = _server()
        server.tools = [mcp.McpTool(
            name="search", description="",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look for"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
        )]

        mcp_tools.register(registry, [server])
        definition, _fn = registry.get("mcp__wiki__search")

        by_name = {p.name: p for p in definition.parameters}
        assert by_name["query"].required is True
        assert by_name["query"].description == "What to look for"
        assert by_name["limit"].type == "integer"
        assert by_name["limit"].required is False

    def test_an_array_parameter_always_gets_an_items_schema(self):
        """The providers reject an array parameter without one."""
        registry = ToolRegistry()
        server = _server()
        server.tools = [mcp.McpTool(
            name="search", description="",
            input_schema={"properties": {"tags": {"type": "array"}}},
        )]

        mcp_tools.register(registry, [server])
        definition, _fn = registry.get("mcp__wiki__search")

        assert definition.parameters[0].items == {"type": "string"}

    def test_an_unsupported_type_becomes_a_string_rather_than_being_dropped(self):
        """
        A dropped parameter makes the tool uncallable; a string one makes it
        awkward. Awkward is better.
        """
        registry = ToolRegistry()
        server = _server()
        server.tools = [mcp.McpTool(
            name="search", description="",
            input_schema={"properties": {"filter": {"type": "object"}}},
        )]

        mcp_tools.register(registry, [server])
        definition, _fn = registry.get("mcp__wiki__search")

        assert definition.parameters[0].type == "string"

    def test_an_enum_reaches_the_schema(self):
        registry = ToolRegistry()
        server = _server()
        server.tools = [mcp.McpTool(
            name="search", description="",
            input_schema={"properties": {
                "scope": {"type": "string", "enum": ["all", "recent"]},
            }},
        )]

        mcp_tools.register(registry, [server])
        definition, _fn = registry.get("mcp__wiki__search")

        assert definition.parameters[0].enum == ["all", "recent"]

    def test_the_schema_survives_the_function_calling_conversion(self):
        registry = ToolRegistry()
        server = _server()
        server.tools = [mcp.McpTool(
            name="search", description="",
            input_schema={
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )]

        mcp_tools.register(registry, [server])
        schema = next(
            s for s in registry.get_function_schemas()
            if s["function"]["name"] == "mcp__wiki__search"
        )

        assert schema["function"]["parameters"]["required"] == ["query"]


class TestCallingADiscoveredTool:
    async def test_a_result_comes_back_as_text(self, monkeypatch):
        async def call_tool(session, name, arguments, schema=None):
            return {"content": [{"type": "text", "text": "the answer"}]}

        monkeypatch.setattr(mcp, "call_tool", call_tool)
        registry = ToolRegistry()
        mcp_tools.register(registry, [_server()])
        _definition, fn = registry.get("mcp__wiki__search")

        result = await fn(query="revenue")

        assert result.success
        assert result.data["text"] == "the answer"
        assert result.data["server"] == "Company wiki"

    async def test_structured_content_is_preferred_over_prose(self, monkeypatch):
        async def call_tool(session, name, arguments, schema=None):
            return {
                "content": [{"type": "text", "text": "Revenue was 4,152"}],
                "structuredContent": {"revenue": 4152},
            }

        monkeypatch.setattr(mcp, "call_tool", call_tool)
        registry = ToolRegistry()
        mcp_tools.register(registry, [_server()])
        _definition, fn = registry.get("mcp__wiki__search")

        result = await fn()

        assert "4152" in result.data["text"]

    async def test_an_error_result_is_a_failed_tool_result(self, monkeypatch):
        async def call_tool(session, name, arguments, schema=None):
            return {"isError": True,
                    "content": [{"type": "text", "text": "no such page"}]}

        monkeypatch.setattr(mcp, "call_tool", call_tool)
        registry = ToolRegistry()
        mcp_tools.register(registry, [_server()])
        _definition, fn = registry.get("mcp__wiki__search")

        result = await fn()

        assert not result.success
        assert "no such page" in result.error

    async def test_an_unreachable_server_reports_rather_than_raising(self, monkeypatch):
        """
        A tool that throws ends the turn; a tool that reports lets the phase
        continue and the model try something else.
        """
        async def call_tool(session, name, arguments, schema=None):
            raise mcp.McpError("the server went away")

        monkeypatch.setattr(mcp, "call_tool", call_tool)
        registry = ToolRegistry()
        mcp_tools.register(registry, [_server()])
        _definition, fn = registry.get("mcp__wiki__search")

        result = await fn()

        assert not result.success
        assert "went away" in result.error

    async def test_a_long_result_is_truncated(self, monkeypatch):
        async def call_tool(session, name, arguments, schema=None):
            return {"content": [{"type": "text", "text": "x" * 50_000}]}

        monkeypatch.setattr(mcp, "call_tool", call_tool)
        registry = ToolRegistry()
        mcp_tools.register(registry, [_server()])
        _definition, fn = registry.get("mcp__wiki__search")

        result = await fn()

        assert len(result.data["text"]) < 50_000
        assert "truncated" in result.data["text"]

    async def test_a_resource_link_is_rendered_as_one(self, monkeypatch):
        async def call_tool(session, name, arguments, schema=None):
            return {"content": [{
                "type": "resource_link", "name": "Q3 board deck",
                "uri": "https://wiki.example.com/q3",
            }]}

        monkeypatch.setattr(mcp, "call_tool", call_tool)
        registry = ToolRegistry()
        mcp_tools.register(registry, [_server()])
        _definition, fn = registry.get("mcp__wiki__search")

        result = await fn()

        assert "Q3 board deck" in result.data["text"]
        assert "https://wiki.example.com/q3" in result.data["text"]

    async def test_the_tools_own_schema_is_passed_to_the_call(self, monkeypatch):
        """The x-mcp-header annotations live on it, and a server validates them."""
        captured = {}

        async def call_tool(session, name, arguments, schema=None):
            captured["schema"] = schema
            return {"content": []}

        monkeypatch.setattr(mcp, "call_tool", call_tool)
        registry = ToolRegistry()
        server = _server()
        server.tools = [mcp.McpTool(
            name="search", description="",
            input_schema={"properties": {"q": {"x-mcp-header": "Q"}}},
        )]
        mcp_tools.register(registry, [server])
        _definition, fn = registry.get("mcp__wiki__search")

        await fn(q="x")

        assert captured["schema"]["properties"]["q"]["x-mcp-header"] == "Q"
