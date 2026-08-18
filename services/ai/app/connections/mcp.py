"""
A client for remote MCP servers, over Streamable HTTP.

Written directly against the specification rather than through the official SDK,
for three reasons that all point the same way. The surface needed here is three
methods - list the tools, call one, and give up quickly if the server will not
answer. The connect path has to check the address it actually resolved to, which
means owning the request. And the SDK carries a ``stdio`` transport this service
must not have a route to at all, which is the scope decision in
``catalog.py``.

Two protocol eras
-----------------

Revision ``2026-07-28`` made Streamable HTTP stateless: no ``initialize``
handshake, no session id, and every request carries its protocol version and
client info in ``_meta`` plus mirrored HTTP headers. Revisions ``2025-03-26``
through ``2025-11-25`` did the opposite - handshake first, then a session id
echoed on every request.

Every remote server in the vetted catalogue today speaks the older shape, and
new ones will speak the newer, so this speaks both. It tries modern first and
falls back on a ``400``/``404``/``405`` whose body is *not* a recognised modern
JSON-RPC error - the distinction the spec draws, and the one that matters:
a modern server also answers ``400`` for an unsupported version, and treating
that as "must be a legacy server" would negotiate down for no reason.

What is deliberately not implemented
------------------------------------

The legacy standalone GET stream, ``Last-Event-ID`` resumption, session
termination by DELETE, server-initiated requests, and ``subscriptions/listen``.
A deck generator lists tools once at run start and calls them; none of that
machinery has a use here, and an unused code path in a network client is a
liability rather than a feature.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import socket
import uuid
from dataclasses import dataclass, field

import httpx

from app.connections.catalog import is_public_address
from app.observability.logging import get_logger

logger = get_logger(__name__)

# The revision this client speaks natively.
PROTOCOL_VERSION = "2026-07-28"

# The revision to fall back to. 2025-06-18 is the oldest that defines the
# MCP-Protocol-Version header, so it is the oldest worth naming; a server older
# still may treat its absence as 2025-03-26, which is its business.
LEGACY_PROTOCOL_VERSION = "2025-06-18"

META_PREFIX = "io.modelcontextprotocol"

CLIENT_INFO = {"name": "ThinkSlides", "version": "1.0.0"}

# Short. A run must not sit waiting on a customer's wiki server: an unreachable
# connection degrades and the deck is built without it (#183).
HANDSHAKE_TIMEOUT_SECONDS = 6.0
CALL_TIMEOUT_SECONDS = 30.0

# A server offering more than this is offering a menu, not a toolset. Every
# schema is spent context in every request of the phase it is offered in, and a
# model shown ninety tools chooses worse than one shown twelve.
MAX_TOOLS_PER_SERVER = 24

ACCEPT = "application/json, text/event-stream"

# The statuses that mean "this endpoint might be an older server" rather than
# "this request was wrong". Anything else is a real failure.
FALLBACK_STATUSES = frozenset({400, 404, 405})

# JSON-RPC error codes a modern server uses, which therefore prove the server is
# modern and must not trigger a fallback.
MODERN_ERROR_CODES = frozenset({
    -32020,  # HeaderMismatch
    -32601,  # Method not found
})


class McpError(RuntimeError):
    """A server that could not be reached or would not answer."""


@dataclass(frozen=True)
class McpTool:
    """One tool a server offers."""

    name: str
    description: str
    input_schema: dict = field(default_factory=dict)


@dataclass
class McpSession:
    """A negotiated connection to one server.

    Holds the era that was agreed and, on the legacy path, the session id the
    server minted. Cheap to keep for the length of a run and cheap to discard.
    """

    url: str
    token: str = ""
    protocol_version: str = PROTOCOL_VERSION
    session_id: str = ""

    @property
    def is_legacy(self) -> bool:
        return self.protocol_version != PROTOCOL_VERSION


# ── The address check ──────────────────────────────────────────────────


async def resolve_and_check(url: str) -> None:
    """Refuse a hostname that resolves somewhere it should not.

    ``check_mcp_url`` in the catalogue sees only what was typed, so a name
    pointing at ``127.0.0.1`` or a metadata address gets past it. This is the
    second gate, and it checks **every** address the name resolves to - a name
    with one public and one private answer is a name that will sometimes reach
    the private one.

    There is a residual window: DNS could change between this check and the
    connection, and closing it properly means connecting to a pinned address
    while still presenting the hostname for TLS, which httpx does not expose.
    Saying so is better than implying cover this does not give. The window is
    small, both gates have to be passed, and the alternative - no check at all -
    is what makes an organisation-supplied URL a way to read this cluster's
    metadata service.
    """
    host = httpx.URL(url).host
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, None, proto=socket.IPPROTO_TCP
        )
    except socket.gaierror as exc:
        raise McpError(f"'{host}' does not resolve.") from exc

    for info in infos:
        raw = info[4][0]
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if not is_public_address(address):
            raise McpError(
                f"'{host}' resolves to {raw}, which is inside the network this "
                "service runs in."
            )


# ── Requests ───────────────────────────────────────────────────────────


def _headers(session: McpSession, method: str, name: str = "") -> dict:
    """The headers one POST carries.

    The mirrored ``Mcp-Method`` / ``Mcp-Name`` headers exist so intermediaries
    can route without parsing the body, and a modern server rejects a request
    whose headers disagree with its body - so they are derived from the same
    values the body is built from, never passed in separately.
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": ACCEPT,
        "MCP-Protocol-Version": session.protocol_version,
        "Mcp-Method": method,
    }
    if name:
        headers["Mcp-Name"] = _header_safe(name)
    if session.token:
        headers["Authorization"] = f"Bearer {session.token}"
    if session.session_id:
        headers["Mcp-Session-Id"] = session.session_id
    return headers


def _header_safe(value: str) -> str:
    """A header value, base64-sentinel-encoded when it cannot be plain ASCII.

    Tool names are only *recommended* to be header-safe, so a server offering
    one that is not must not make every call to it unsendable.
    """
    plain = value.isascii() and value == value.strip() and all(
        0x20 <= ord(c) <= 0x7E for c in value
    )
    if plain and not (value.startswith("=?base64?") and value.endswith("?=")):
        return value
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return f"=?base64?{encoded}?="


def _body(session: McpSession, method: str, params: dict | None = None) -> dict:
    """One JSON-RPC request, with the per-request metadata a modern server needs."""
    payload: dict = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": method,
        "params": dict(params or {}),
    }
    if not session.is_legacy:
        payload["params"]["_meta"] = {
            f"{META_PREFIX}/protocolVersion": session.protocol_version,
            f"{META_PREFIX}/clientInfo": dict(CLIENT_INFO),
            f"{META_PREFIX}/clientCapabilities": {},
        }
    return payload


def _parse(response: httpx.Response) -> dict:
    """The JSON-RPC result, from either response shape.

    A server answers a request with a JSON object or with an SSE stream whose
    last data frame is the response. Both are mandatory for a client to handle,
    and a client that only reads JSON works against half the servers.
    """
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        message = _last_sse_message(response.text)
    else:
        message = _json_or_none(response.text)

    if message is None:
        raise McpError("The server's reply was not JSON-RPC.")
    if "error" in message:
        error = message["error"] or {}
        raise McpError(
            f"{error.get('message') or 'The server returned an error.'} "
            f"(code {error.get('code')})"
        )
    return message.get("result") or {}


def _json_or_none(text: str) -> dict | None:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _last_sse_message(text: str) -> dict | None:
    """The final JSON-RPC message on an SSE stream.

    A response stream may carry progress notifications before the response, and
    the response terminates it - so the last parsable data frame is the answer.
    Comment lines beginning with a colon are keep-alives and are ignored, which
    the SSE specification requires and a naive line reader gets wrong.
    """
    last = None
    for block in text.replace("\r\n", "\n").split("\n\n"):
        data = "".join(
            line[len("data:"):].strip()
            for line in block.split("\n")
            if line.startswith("data:")
        )
        if not data:
            continue
        message = _json_or_none(data)
        if message is not None and ("result" in message or "error" in message):
            last = message
    return last


def _is_modern_error(response: httpx.Response) -> bool:
    """Whether a 4xx body proves the server speaks a modern revision.

    The spec's own fallback rule. A modern server answers 400 for an
    unsupported version and 404 for an unknown method, so falling back on the
    status alone negotiates down against a server that was working fine.
    """
    message = _json_or_none(response.text)
    if message is None:
        return False
    error = message.get("error")
    if not isinstance(error, dict):
        return False
    if error.get("code") in MODERN_ERROR_CODES:
        return True
    return bool(error.get("data", {}).get("supported")) if isinstance(
        error.get("data"), dict
    ) else False


async def _post(
    client: httpx.AsyncClient,
    session: McpSession,
    method: str,
    params: dict | None = None,
    *,
    name: str = "",
    timeout: float,
) -> httpx.Response:
    body = _body(session, method, params)
    return await client.post(
        session.url,
        json=body,
        headers=_headers(session, method, name),
        timeout=timeout,
    )


# ── Discovery ──────────────────────────────────────────────────────────


async def list_tools(url: str, token: str = "") -> tuple[McpSession, list[McpTool]]:
    """Negotiate with one server and return its tools.

    Raises ``McpError`` for anything that goes wrong, including a timeout. The
    caller decides what an unreachable server means; this only decides what
    "reached" means.
    """
    await resolve_and_check(url)
    session = McpSession(url=url, token=token)

    async with httpx.AsyncClient(follow_redirects=False) as client:
        try:
            response = await _post(
                client, session, "tools/list",
                timeout=HANDSHAKE_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise McpError(f"Could not reach the server: {exc}") from exc

        if response.status_code in FALLBACK_STATUSES and not _is_modern_error(response):
            logger.info("mcp_falling_back_to_handshake", url=url,
                        status=response.status_code)
            session = await _handshake(client, session)
            try:
                response = await _post(
                    client, session, "tools/list",
                    timeout=HANDSHAKE_TIMEOUT_SECONDS,
                )
            except httpx.HTTPError as exc:
                raise McpError(f"Could not reach the server: {exc}") from exc

        if response.status_code >= 400:
            raise McpError(_status_reason(response))

        return session, _tools_from(_parse(response), url)


async def _handshake(
    client: httpx.AsyncClient, session: McpSession
) -> McpSession:
    """The pre-2026 initialize exchange, and the session id it mints."""
    legacy = McpSession(
        url=session.url, token=session.token,
        protocol_version=LEGACY_PROTOCOL_VERSION,
    )
    try:
        response = await _post(
            client, legacy, "initialize",
            {
                "protocolVersion": LEGACY_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": dict(CLIENT_INFO),
            },
            timeout=HANDSHAKE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise McpError(f"Could not reach the server: {exc}") from exc

    if response.status_code >= 400:
        raise McpError(_status_reason(response))

    _parse(response)
    legacy.session_id = response.headers.get("mcp-session-id", "")

    # The notification that completes the handshake. A server that will not take
    # it is a server whose handshake did not complete, but there is nothing to
    # read back from a notification, so a failure here is logged rather than
    # raised - tools/list is the real test and it comes next.
    try:
        await client.post(
            legacy.url,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=_headers(legacy, "notifications/initialized"),
            timeout=HANDSHAKE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning("mcp_initialized_notification_failed",
                       url=legacy.url, error=str(exc))

    return legacy


def _status_reason(response: httpx.Response) -> str:
    """Why a non-2xx reply failed, in words worth showing a user."""
    if response.status_code in (401, 403):
        return (
            "The server refused the credential. Check the token and the access "
            "it was given."
        )
    if response.status_code == 404:
        return "No MCP endpoint at that URL."
    if response.status_code >= 500:
        return f"The server returned an error ({response.status_code})."
    message = _json_or_none(response.text) or {}
    detail = (message.get("error") or {}).get("message") if isinstance(
        message.get("error"), dict
    ) else None
    return detail or f"The server rejected the request ({response.status_code})."


def _tools_from(result: dict, url: str) -> list[McpTool]:
    """The tools in a ``tools/list`` result, filtered to the usable ones."""
    raw = result.get("tools")
    if not isinstance(raw, list):
        return []

    tools = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        schema = entry.get("inputSchema")
        schema = schema if isinstance(schema, dict) else {}
        if _rejects_schema(name, schema, url):
            continue
        tools.append(McpTool(
            name=name,
            description=str(entry.get("description") or "").strip(),
            input_schema=schema,
        ))

    if len(tools) > MAX_TOOLS_PER_SERVER:
        logger.warning("mcp_tool_list_truncated", url=url, offered=len(tools),
                       kept=MAX_TOOLS_PER_SERVER)
        tools = tools[:MAX_TOOLS_PER_SERVER]
    return tools


def _rejects_schema(name: str, schema: dict, url: str) -> bool:
    """Whether one tool definition is malformed enough to drop.

    Dropping the tool rather than the server: the spec is explicit that one bad
    definition must not stop the others being used, and a server with twelve
    good tools and one broken one is far more useful than no server.
    """
    for prop, spec in (schema.get("properties") or {}).items():
        if not isinstance(spec, dict):
            continue
        header = spec.get("x-mcp-header")
        if header is None:
            continue
        if not isinstance(header, str) or not header.strip():
            logger.warning("mcp_tool_rejected", url=url, tool=name,
                           reason=f"empty x-mcp-header on '{prop}'")
            return True
        if any(c in header for c in "\r\n") or not header.isascii():
            logger.warning("mcp_tool_rejected", url=url, tool=name,
                           reason=f"unsafe x-mcp-header on '{prop}'")
            return True
    return False


# ── Calling ────────────────────────────────────────────────────────────


async def call_tool(
    session: McpSession, name: str, arguments: dict, schema: dict | None = None
) -> dict:
    """Invoke one tool and return its result.

    The mirrored ``Mcp-Param-*`` headers a server may ask for are sent, because
    a conforming client must: a server that annotated a parameter will reject
    the call without them, and the annotation lives on the tool's own schema -
    which is why the schema is passed back in rather than looked up.
    """
    await resolve_and_check(session.url)
    headers = _headers(session, "tools/call", name)
    headers.update(mirrored_param_headers(schema or {}, arguments or {}))

    async with httpx.AsyncClient(follow_redirects=False) as client:
        try:
            response = await client.post(
                session.url,
                json=_body(session, "tools/call",
                           {"name": name, "arguments": dict(arguments or {})}),
                headers=headers,
                timeout=CALL_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise McpError(f"Could not reach the server: {exc}") from exc

    if response.status_code >= 400:
        raise McpError(_status_reason(response))
    return _parse(response)


def mirrored_param_headers(schema: dict, arguments: dict) -> dict:
    """The ``Mcp-Param-*`` headers this call's arguments imply.

    Read at the exact property path the annotation sits on, and omitted where
    the call did not supply a value - both of which the spec requires, and both
    of which a server validates against the body.
    """
    headers = {}
    for prop, spec in (schema.get("properties") or {}).items():
        if not isinstance(spec, dict):
            continue
        header = spec.get("x-mcp-header")
        if not isinstance(header, str) or not header.strip():
            continue
        if prop not in arguments or arguments[prop] is None:
            continue
        headers[f"Mcp-Param-{header}"] = _header_safe(_as_header_value(arguments[prop]))
    return headers


def _as_header_value(value: object) -> str:
    """One argument as its header string, per the spec's type conversion."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return str(value)
