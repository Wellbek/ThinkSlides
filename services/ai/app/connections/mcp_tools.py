"""
Discovering an organisation's MCP tools and putting them in front of the model.

Three decisions live here, and all three are about *where* a discovered tool is
allowed to appear rather than about how it is called.

**Namespaced.** A tool is registered as ``mcp__<slug>__<tool>``. Two servers may
both offer ``search``, and a collision with a built-in name would be worse
still - ``select_layout`` arriving from a customer's server and shadowing the
real one is not a bug that announces itself.

**Run-scoped.** The tools go on a fork of the registry, not on the global
singleton. This service is shared: tools discovered from one organisation's
server must not still be registered when another organisation's run starts.

**Phase-scoped.** ``agent/pipeline.py`` already governs which tools exist when,
and its docstring says why it has to: the loop used to enforce sequence by
withholding schemas, and "a model that cannot see the route guesses". Dumping
every discovered tool into every phase would recreate that at a larger scale -
one server with twenty tools would triple the toolset of the slide-drafting
phase, in the phase where the model is composing a page.

Which phase a server's tools belong in is **declared, not guessed**. The server
does not say whether it is a data source or a content source, and inferring it
from tool names is the kind of heuristic that is right until it is silently
wrong. The organisation that configured the connection knows what their wiki
server is for, so they say.
"""

from __future__ import annotations

import asyncio
import re
import json
from dataclasses import dataclass, field

from app.agent.registry import ToolParameter, ToolRegistry, ToolResult
from app.connections import mcp
from app.connections.store import ResolvedConnection
from app.observability.logging import get_logger

logger = get_logger(__name__)

NAMESPACE = "mcp"
SEPARATOR = "__"

PHASE_RESEARCH = "research"
PHASE_DRAFT = "draft"
OFFERABLE_PHASES: tuple[str, ...] = (PHASE_RESEARCH, PHASE_DRAFT)

# A data source is the common case, and it is the conservative one: the drafting
# phase is where the model is composing a page from a budget, and adding tools
# there costs attention on every slide.
DEFAULT_PHASES: tuple[str, ...] = (PHASE_RESEARCH,)

# How much of a tool result is worth carrying. An MCP tool can return a whole
# document, and a run that puts three of those in its message history spends the
# context window on material the model has already summarised.
MAX_RESULT_CHARACTERS = 6000


def tool_name(slug: str, tool: str) -> str:
    """The registered name of one discovered tool."""
    return f"{NAMESPACE}{SEPARATOR}{slug}{SEPARATOR}{tool}"


def is_mcp_tool(name: str) -> bool:
    return name.startswith(f"{NAMESPACE}{SEPARATOR}")


@dataclass
class DiscoveredServer:
    """One server after the handshake, whether or not it worked."""

    slug: str
    label: str
    phases: tuple[str, ...] = DEFAULT_PHASES
    session: mcp.McpSession | None = None
    tools: list[mcp.McpTool] = field(default_factory=list)
    # Set when the server could not be reached. Not fatal - see #183.
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.session is not None and not self.error


def phases_for(connection: ResolvedConnection) -> tuple[str, ...]:
    """Which phases this connection's tools are offered in.

    Read from the connection's own config, because the organisation that set it
    up is the only party that knows what the server is for. An unrecognised or
    empty value falls back to the default rather than to "everywhere".
    """
    raw = connection.config.get("phases")
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",")]
    if not isinstance(raw, list):
        return DEFAULT_PHASES
    chosen = tuple(
        phase for phase in OFFERABLE_PHASES
        if phase in {str(item).strip() for item in raw}
    )
    return chosen or DEFAULT_PHASES


async def discover(
    connections: list[ResolvedConnection],
) -> list[DiscoveredServer]:
    """Handshake every server at once and return what each offered.

    Concurrent because N servers should cost roughly one round trip rather than
    N: a run that waits six seconds per server before it starts is a run whose
    first slide arrives a minute late on a workspace with ten connections.

    A server that fails comes back with its reason rather than raising. Deciding
    what an unreachable connection means is the caller's job, and the answer is
    never "fail the run".
    """
    if not connections:
        return []

    results = await asyncio.gather(
        *(_discover_one(connection) for connection in connections),
        return_exceptions=True,
    )

    servers = []
    for connection, result in zip(connections, results, strict=True):
        if isinstance(result, BaseException):
            # gather with return_exceptions catches what _discover_one did not.
            # Reaching here is a bug rather than a network failure, so it is
            # logged as one - and still not raised.
            logger.error("mcp_discovery_crashed", slug=connection.slug,
                         error=str(result))
            servers.append(DiscoveredServer(
                slug=connection.slug, label=connection.label,
                phases=phases_for(connection), error=str(result),
            ))
        else:
            servers.append(result)
    return servers


async def _discover_one(connection: ResolvedConnection) -> DiscoveredServer:
    url = str(connection.config.get("url") or "")
    token = str(connection.secrets.get("token") or "")
    server = DiscoveredServer(
        slug=connection.slug,
        label=connection.label,
        phases=phases_for(connection),
    )
    if not url:
        server.error = "No endpoint URL is configured."
        return server

    try:
        session, tools = await mcp.list_tools(url, token)
    except mcp.McpError as exc:
        server.error = str(exc)
        logger.warning("mcp_unreachable", slug=connection.slug, error=str(exc))
        return server
    except Exception as exc:  # noqa: BLE001 - a connection must never fail a run
        server.error = f"Unexpected failure reaching the server: {exc}"
        logger.error("mcp_discovery_failed", slug=connection.slug, error=str(exc))
        return server

    server.session = session
    server.tools = tools
    logger.info("mcp_discovered", slug=connection.slug, tools=len(tools))
    return server


def register(
    registry: ToolRegistry, servers: list[DiscoveredServer]
) -> dict[str, frozenset[str]]:
    """Register every reachable server's tools and say where they may appear.

    Returns the phase-to-tool-name mapping the loop adds to each phase's own
    toolset. A server that failed contributes nothing and no name - the model is
    never shown a tool that cannot run.
    """
    by_phase: dict[str, set[str]] = {phase: set() for phase in OFFERABLE_PHASES}

    for server in servers:
        if not server.ok:
            continue
        for tool in server.tools:
            name = tool_name(server.slug, tool.name)
            registry.register_tool(
                name=name,
                description=_description(server, tool),
                fn=_caller(server, tool),
                parameters=_parameters(tool),
            )
            for phase in server.phases:
                by_phase.setdefault(phase, set()).add(name)

    return {phase: frozenset(names) for phase, names in by_phase.items()}


def _description(server: DiscoveredServer, tool: mcp.McpTool) -> str:
    """The tool's own description, with the server it came from named.

    Naming the source is not decoration: the model is choosing between this and
    a built-in, and "from the Company wiki server" is the fact that makes the
    choice sensible. It is also a standing reminder that the text after it was
    written by a third party.
    """
    own = tool.description or f"The {tool.name} tool."
    return f"[{server.label}] {own}"


def _parameters(tool: mcp.McpTool) -> list[ToolParameter]:
    """The tool's JSON Schema, flattened into the registry's parameter shape.

    Only the top level, and only the types the function-calling schema builder
    emits. A nested object is passed through as a string the model fills with
    JSON, which is what the built-in tools already do for structured arguments -
    and is better than dropping the parameter, which would make the tool
    uncallable rather than awkward.
    """
    schema = tool.input_schema or {}
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    required = schema.get("required")
    required = set(required) if isinstance(required, list) else set()

    parameters = []
    for name, spec in properties.items():
        spec = spec if isinstance(spec, dict) else {}
        kind = spec.get("type")
        kind = kind if kind in {"string", "number", "integer", "boolean", "array"} \
            else "string"
        parameter = ToolParameter(
            name=str(name),
            type=kind,
            description=str(spec.get("description") or ""),
            required=name in required,
        )
        if kind == "array":
            items = spec.get("items")
            # The schema builder emits "items" only when it is a dict, and an
            # array parameter without one is rejected by the providers.
            parameter.items = items if isinstance(items, dict) else {"type": "string"}
        if isinstance(spec.get("enum"), list) and kind == "string":
            parameter.enum = [str(v) for v in spec["enum"]]
        parameters.append(parameter)
    return parameters


# How much of a result has to look like a document before it is read as one.
# Detected on content, never on a tool's name: a server called "diagrams" may
# return prose, and one called "lookup" may return a figure.
_SVG_OPEN = re.compile(r"<svg\b", re.IGNORECASE)


def _svg_figure(text: str) -> str | None:
    """The SVG document in this result, if the result is one.

    Only a whole document, and only one that the importer will actually
    accept. A result that merely mentions SVG, or contains a fragment, is
    text - and treating it as a figure would put a broken picture on a page
    in place of an answer.
    """
    candidate = (text or "").strip()
    if not candidate or not _SVG_OPEN.search(candidate):
        return None
    start = candidate.lower().find("<svg")
    end = candidate.lower().rfind("</svg>")
    if start < 0 or end < 0:
        return None
    document = candidate[start:end + len("</svg>")]
    try:
        from app.renderer.layout import PositionConfig
        from app.renderer.svg.importer import to_primitives

        if not to_primitives(document, PositionConfig(left=0, top=0,
                                                      width=6, height=4)):
            return None
    except Exception:
        return None
    return document


def _caller(server: DiscoveredServer, tool: mcp.McpTool):
    """The async function the registry invokes for one discovered tool."""

    async def run(**arguments) -> ToolResult:
        if server.session is None:
            return ToolResult(success=False, error="That server is not connected.")
        try:
            result = await mcp.call_tool(
                server.session, tool.name, arguments, tool.input_schema
            )
        except mcp.McpError as exc:
            # Returned as a failed result rather than raised, so the model sees
            # what went wrong and can try something else. A tool that throws
            # ends the turn; a tool that reports lets the phase continue.
            return ToolResult(success=False, error=str(exc))

        text = _flatten(result)
        if result.get("isError"):
            return ToolResult(success=False, error=text or "The tool reported an error.")

        figure = _svg_figure(text)
        if figure is not None:
            # A server that returns a drawn figure is a figure source like any
            # other, so it goes through the same importer and arrives as
            # native shapes in the deck's palette. Pasting it as a flat
            # picture instead would be a silent downgrade - the kind nobody
            # notices until a user tries to recolour it.
            logger.info("mcp_svg_figure", server=server.slug, tool=tool.name,
                        chars=len(figure))
            return ToolResult(success=True, data={
                "server": server.label,
                "blocks": [{"type": "diagram",
                            "diagram": {"format": "svg", "svg": figure}}],
                "text": "The server returned a figure, which has been "
                        "attached to this slide as editable shapes.",
            })
        return ToolResult(success=True, data={"text": text, "server": server.label})

    return run


def _flatten(result: dict) -> str:
    """One MCP tool result as the text to hand the model.

    ``structuredContent`` is preferred when a server sends it, because a shape
    the model can read is better than the prose rendering of the same thing.
    Truncated, because a tool can return a whole document and a run that carries
    three of those spends its context window on material it has already used.
    """
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and structured:
        text = json.dumps(structured, ensure_ascii=False)
    else:
        parts = []
        for item in result.get("content") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and item.get("text"):
                parts.append(str(item["text"]))
            elif item.get("type") == "resource_link" and item.get("uri"):
                parts.append(f"[{item.get('name') or 'resource'}] {item['uri']}")
        text = "\n\n".join(parts)

    if len(text) > MAX_RESULT_CHARACTERS:
        return (
            text[:MAX_RESULT_CHARACTERS]
            + f"\n\n[truncated at {MAX_RESULT_CHARACTERS} characters]"
        )
    return text
