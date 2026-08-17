"""
The MCP client: two protocol eras, both response shapes, and the address gate.

Driven with ``httpx.MockTransport``, so the requests this client actually sends
are inspected rather than assumed. That matters more than usual here: a modern
server rejects a request whose mirrored headers disagree with its body, so
"the headers are right" is a correctness property and not a style one.
"""

import json

import httpx
import pytest

from app.connections import mcp

pytestmark = pytest.mark.unconfigured_workspace

URL = "https://mcp.example.com/mcp"

TOOLS = {
    "tools": [
        {
            "name": "search_pages",
            "description": "Search the wiki.",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
        {"name": "read_page", "description": "Read one page."},
    ]
}


def _rpc(request: httpx.Request) -> dict:
    return json.loads(request.content)


def _result(request: httpx.Request, result: dict) -> httpx.Response:
    return httpx.Response(200, json={
        "jsonrpc": "2.0", "id": _rpc(request).get("id"), "result": result,
    })


# Held before the autouse fixture stubs it out, so the address gate's own tests
# can exercise the real thing.
REAL_RESOLVE = mcp.resolve_and_check


@pytest.fixture(autouse=True)
def _resolves(monkeypatch):
    """Skip DNS everywhere except the address gate's own tests below."""
    async def ok(url):
        return None

    monkeypatch.setattr(mcp, "resolve_and_check", ok)


def _resolving_to(monkeypatch, *addresses, fails=False):
    """Stand in for DNS, answering with the addresses given."""
    import socket as socket_module

    async def getaddrinfo(host, port, **kwargs):
        if fails:
            raise socket_module.gaierror("nope")
        return [(2, 1, 6, "", (address, 0)) for address in addresses]

    monkeypatch.setattr(
        "asyncio.get_running_loop",
        lambda: type("L", (), {"getaddrinfo": staticmethod(getaddrinfo)})(),
    )


def _mock(handler):
    """Point every client this module builds at one handler."""
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    return patched


@pytest.fixture
def serve(monkeypatch):
    def install(handler):
        monkeypatch.setattr(httpx, "AsyncClient", _mock(handler))
    return install


class TestTheModernEra:
    async def test_it_lists_tools_without_a_handshake(self, serve):
        seen = []

        def handler(request):
            seen.append(_rpc(request)["method"])
            return _result(request, TOOLS)

        serve(handler)
        session, tools = await mcp.list_tools(URL)

        assert seen == ["tools/list"], "a modern server needs no initialize"
        assert [t.name for t in tools] == ["search_pages", "read_page"]
        assert not session.is_legacy

    async def test_the_protocol_version_header_matches_the_body(self, serve):
        """A modern server returns HeaderMismatch when these disagree."""
        captured = {}

        def handler(request):
            captured["header"] = request.headers["mcp-protocol-version"]
            body = _rpc(request)
            captured["body"] = body["params"]["_meta"][
                f"{mcp.META_PREFIX}/protocolVersion"
            ]
            return _result(request, TOOLS)

        serve(handler)
        await mcp.list_tools(URL)

        assert captured["header"] == captured["body"] == mcp.PROTOCOL_VERSION

    async def test_the_method_header_mirrors_the_body(self, serve):
        captured = {}

        def handler(request):
            captured["header"] = request.headers["mcp-method"]
            captured["body"] = _rpc(request)["method"]
            return _result(request, TOOLS)

        serve(handler)
        await mcp.list_tools(URL)

        assert captured["header"] == captured["body"] == "tools/list"

    async def test_it_accepts_both_response_content_types(self, serve):
        def handler(request):
            captured = request.headers["accept"]
            assert "application/json" in captured
            assert "text/event-stream" in captured
            return _result(request, TOOLS)

        serve(handler)
        await mcp.list_tools(URL)

    async def test_a_token_is_sent_as_a_bearer_credential(self, serve):
        captured = {}

        def handler(request):
            captured["auth"] = request.headers.get("authorization")
            return _result(request, TOOLS)

        serve(handler)
        await mcp.list_tools(URL, token="tok-1234")

        assert captured["auth"] == "Bearer tok-1234"

    async def test_no_authorization_header_when_there_is_no_token(self, serve):
        captured = {}

        def handler(request):
            captured["auth"] = request.headers.get("authorization")
            return _result(request, TOOLS)

        serve(handler)
        await mcp.list_tools(URL)

        assert captured["auth"] is None


class TestTheLegacyFallback:
    def _legacy(self, calls):
        def handler(request):
            method = _rpc(request).get("method")
            calls.append(method)
            version = request.headers.get("mcp-protocol-version")
            if version == mcp.PROTOCOL_VERSION:
                # An initialization-based server does not understand a modern
                # request, and answers 400 with no modern JSON-RPC error.
                return httpx.Response(400, text="Bad Request")
            if method == "initialize":
                response = _result(request, {"protocolVersion": "2025-06-18"})
                response.headers["Mcp-Session-Id"] = "sess-42"
                return response
            if method == "notifications/initialized":
                return httpx.Response(202)
            return _result(request, TOOLS)
        return handler

    async def test_a_400_without_a_modern_error_falls_back_to_initialize(self, serve):
        calls = []
        serve(self._legacy(calls))

        session, tools = await mcp.list_tools(URL)

        assert calls[0] == "tools/list", "modern is tried first"
        assert "initialize" in calls
        assert session.is_legacy
        assert [t.name for t in tools] == ["search_pages", "read_page"]

    async def test_the_handshake_is_completed_with_the_notification(self, serve):
        calls = []
        serve(self._legacy(calls))

        await mcp.list_tools(URL)

        assert "notifications/initialized" in calls

    async def test_the_session_id_is_echoed_on_later_requests(self, serve):
        sessions = []

        def handler(request):
            method = _rpc(request).get("method")
            if request.headers.get("mcp-protocol-version") == mcp.PROTOCOL_VERSION:
                return httpx.Response(400, text="Bad Request")
            if method == "initialize":
                response = _result(request, {})
                response.headers["Mcp-Session-Id"] = "sess-42"
                return response
            sessions.append(request.headers.get("mcp-session-id"))
            return httpx.Response(202) if method.startswith("notifications") \
                else _result(request, TOOLS)

        serve(handler)
        session, _ = await mcp.list_tools(URL)

        assert session.session_id == "sess-42"
        assert sessions and all(s == "sess-42" for s in sessions)

    async def test_a_legacy_request_carries_no_modern_meta(self, serve):
        """It would be an unknown field to a server that predates it."""
        bodies = []

        def handler(request):
            if request.headers.get("mcp-protocol-version") == mcp.PROTOCOL_VERSION:
                return httpx.Response(400, text="Bad Request")
            bodies.append(_rpc(request))
            method = _rpc(request).get("method")
            if method == "initialize":
                return _result(request, {})
            if method.startswith("notifications"):
                return httpx.Response(202)
            return _result(request, TOOLS)

        serve(handler)
        await mcp.list_tools(URL)

        for body in bodies:
            assert "_meta" not in (body.get("params") or {})


class TestAModern400IsNotAReasonToFallBack:
    """
    The spec's own rule, and the one that matters: a modern server answers 400
    for an unsupported version and 404 for an unknown method, so falling back on
    the status alone negotiates down against a server that was working.
    """

    async def test_a_header_mismatch_error_is_not_a_legacy_signal(self, serve):
        calls = []

        def handler(request):
            calls.append(_rpc(request).get("method"))
            return httpx.Response(400, json={
                "jsonrpc": "2.0", "id": 1,
                "error": {"code": -32020, "message": "Header mismatch"},
            })

        serve(handler)
        with pytest.raises(mcp.McpError):
            await mcp.list_tools(URL)

        assert "initialize" not in calls

    async def test_a_method_not_found_error_is_not_a_legacy_signal(self, serve):
        calls = []

        def handler(request):
            calls.append(_rpc(request).get("method"))
            return httpx.Response(404, json={
                "jsonrpc": "2.0", "id": 1,
                "error": {"code": -32601, "message": "Method not found"},
            })

        serve(handler)
        with pytest.raises(mcp.McpError):
            await mcp.list_tools(URL)

        assert "initialize" not in calls

    async def test_an_unsupported_version_error_is_not_a_legacy_signal(self, serve):
        calls = []

        def handler(request):
            calls.append(_rpc(request).get("method"))
            return httpx.Response(400, json={
                "jsonrpc": "2.0", "id": 1,
                "error": {
                    "code": -32602,
                    "message": "Unsupported protocol version",
                    "data": {"supported": ["2025-11-25"]},
                },
            })

        serve(handler)
        with pytest.raises(mcp.McpError):
            await mcp.list_tools(URL)

        assert "initialize" not in calls


class TestTheSseResponseShape:
    def _stream(self, frames: list[dict]) -> httpx.Response:
        body = "".join(
            f"event: message\ndata: {json.dumps(frame)}\n\n" for frame in frames
        )
        return httpx.Response(
            200, text=body, headers={"content-type": "text/event-stream"},
        )

    async def test_the_response_is_read_from_the_last_data_frame(self, serve):
        serve(lambda request: self._stream([
            {"jsonrpc": "2.0", "method": "notifications/progress",
             "params": {"progress": 1}},
            {"jsonrpc": "2.0", "id": 1, "result": TOOLS},
        ]))

        _session, tools = await mcp.list_tools(URL)

        assert [t.name for t in tools] == ["search_pages", "read_page"]

    async def test_keep_alive_comments_are_ignored(self, serve):
        """
        A colon line is a comment the SSE spec requires clients to ignore, and a
        naive line reader treats it as malformed input.
        """
        body = (
            ":\n\n"
            "event: message\n"
            f"data: {json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': TOOLS})}\n\n"
            ":\n\n"
        )
        serve(lambda request: httpx.Response(
            200, text=body, headers={"content-type": "text/event-stream"},
        ))

        _session, tools = await mcp.list_tools(URL)

        assert len(tools) == 2

    async def test_an_error_on_the_stream_is_raised(self, serve):
        serve(lambda request: self._stream([
            {"jsonrpc": "2.0", "id": 1,
             "error": {"code": -1, "message": "the index is rebuilding"}},
        ]))

        with pytest.raises(mcp.McpError) as exc:
            await mcp.list_tools(URL)

        assert "rebuilding" in str(exc.value)

    async def test_crlf_framing_is_handled(self, serve):
        """Servers send \\r\\n; a splitter that only knows \\n reads nothing."""
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "result": TOOLS})
        serve(lambda request: httpx.Response(
            200, text=f"event: message\r\ndata: {payload}\r\n\r\n",
            headers={"content-type": "text/event-stream"},
        ))

        _session, tools = await mcp.list_tools(URL)

        assert len(tools) == 2


class TestWhatComesBackFromToolsList:
    async def test_a_tool_with_no_name_is_dropped(self, serve):
        serve(lambda request: _result(request, {"tools": [
            {"description": "nameless"}, {"name": "real"},
        ]}))

        _session, tools = await mcp.list_tools(URL)

        assert [t.name for t in tools] == ["real"]

    async def test_a_missing_schema_becomes_an_empty_one(self, serve):
        serve(lambda request: _result(request, {"tools": [{"name": "read_page"}]}))

        _session, tools = await mcp.list_tools(URL)

        assert tools[0].input_schema == {}

    async def test_an_empty_tool_list_is_not_an_error(self, serve):
        serve(lambda request: _result(request, {"tools": []}))

        _session, tools = await mcp.list_tools(URL)

        assert tools == []

    async def test_a_result_with_no_tools_key_is_not_an_error(self, serve):
        serve(lambda request: _result(request, {}))

        _session, tools = await mcp.list_tools(URL)

        assert tools == []

    async def test_an_over_long_list_is_truncated(self, serve):
        offered = [{"name": f"tool_{n}"} for n in range(mcp.MAX_TOOLS_PER_SERVER + 5)]
        serve(lambda request: _result(request, {"tools": offered}))

        _session, tools = await mcp.list_tools(URL)

        assert len(tools) == mcp.MAX_TOOLS_PER_SERVER

    async def test_a_tool_with_an_unsafe_header_annotation_is_dropped(self, serve):
        """
        The spec requires rejecting the tool, not the server: one malformed
        definition must not stop the others being used.
        """
        serve(lambda request: _result(request, {"tools": [
            {"name": "bad", "inputSchema": {"properties": {
                "region": {"type": "string", "x-mcp-header": "Bad\r\nInjected"},
            }}},
            {"name": "good"},
        ]}))

        _session, tools = await mcp.list_tools(URL)

        assert [t.name for t in tools] == ["good"]

    async def test_a_tool_with_an_empty_header_annotation_is_dropped(self, serve):
        serve(lambda request: _result(request, {"tools": [
            {"name": "bad", "inputSchema": {"properties": {
                "region": {"type": "string", "x-mcp-header": "  "},
            }}},
            {"name": "good"},
        ]}))

        _session, tools = await mcp.list_tools(URL)

        assert [t.name for t in tools] == ["good"]


class TestCallingATool:
    async def test_the_name_and_arguments_reach_the_server(self, serve):
        captured = {}

        def handler(request):
            captured["body"] = _rpc(request)
            captured["name_header"] = request.headers.get("mcp-name")
            return _result(request, {"content": [{"type": "text", "text": "found"}]})

        serve(handler)
        session = mcp.McpSession(url=URL)
        result = await mcp.call_tool(session, "search_pages", {"query": "revenue"})

        assert captured["body"]["params"]["name"] == "search_pages"
        assert captured["body"]["params"]["arguments"] == {"query": "revenue"}
        assert captured["name_header"] == "search_pages"
        assert result["content"][0]["text"] == "found"

    async def test_an_annotated_parameter_is_mirrored_into_a_header(self, serve):
        captured = {}

        def handler(request):
            captured["region"] = request.headers.get("mcp-param-Region")
            return _result(request, {"content": []})

        serve(handler)
        schema = {"properties": {
            "region": {"type": "string", "x-mcp-header": "Region"},
            "query": {"type": "string"},
        }}
        await mcp.call_tool(
            mcp.McpSession(url=URL), "execute_sql",
            {"region": "us-west1", "query": "select 1"}, schema,
        )

        assert captured["region"] == "us-west1"

    async def test_an_annotated_parameter_that_was_not_supplied_is_omitted(self, serve):
        captured = {}

        def handler(request):
            captured["names"] = [k.lower() for k in request.headers]
            return _result(request, {"content": []})

        serve(handler)
        schema = {"properties": {
            "region": {"type": "string", "x-mcp-header": "Region"},
        }}
        await mcp.call_tool(mcp.McpSession(url=URL), "t", {}, schema)

        assert "mcp-param-region" not in captured["names"]

    async def test_a_401_says_the_credential_was_refused(self, serve):
        serve(lambda request: httpx.Response(401, text="nope"))

        with pytest.raises(mcp.McpError) as exc:
            await mcp.call_tool(mcp.McpSession(url=URL), "t", {})

        assert "credential" in str(exc.value).lower()

    async def test_a_network_failure_is_an_mcp_error_not_an_httpx_one(self, serve):
        def handler(request):
            raise httpx.ConnectError("no route to host")

        serve(handler)
        with pytest.raises(mcp.McpError) as exc:
            await mcp.call_tool(mcp.McpSession(url=URL), "t", {})

        assert "reach" in str(exc.value).lower()


class TestHeaderValueEncoding:
    @pytest.mark.parametrize("value", ["plain", "us-west1", "a_b.c"])
    def test_a_safe_ascii_value_is_sent_as_itself(self, value):
        assert mcp._header_safe(value) == value

    @pytest.mark.parametrize("value", [
        "Hello, 世界", " padded ", "line1\nline2",
    ])
    def test_an_unsafe_value_is_base64_sentinel_encoded(self, value):
        encoded = mcp._header_safe(value)

        assert encoded.startswith("=?base64?")
        assert encoded.endswith("?=")

    def test_a_value_that_looks_like_the_sentinel_is_encoded_too(self):
        """Otherwise the server cannot tell an encoded value from a literal one."""
        encoded = mcp._header_safe("=?base64?literal?=")

        assert encoded != "=?base64?literal?="
        assert encoded.startswith("=?base64?")

    def test_a_header_value_is_always_latin_1_encodable(self):
        """httpx raises on a header value it cannot put on the wire."""
        for value in ("世界", "naïve", "tab\there"):
            mcp._header_safe(value).encode("latin-1")

    @pytest.mark.parametrize("value,expected", [
        (True, "true"), (False, "false"), (42, "42"), (-7, "-7"), ("x", "x"),
    ])
    def test_types_convert_the_way_the_spec_says(self, value, expected):
        assert mcp._as_header_value(value) == expected


class TestTheAddressGate:
    """
    The second of the two gates. ``check_mcp_url`` sees only what was typed, so
    a *name* pointing at a private address is what this catches.
    """

    async def test_a_name_resolving_to_loopback_is_refused(self, monkeypatch):
        _resolving_to(monkeypatch, "127.0.0.1")

        with pytest.raises(mcp.McpError) as exc:
            await REAL_RESOLVE("https://sneaky.example.com/mcp")

        assert "127.0.0.1" in str(exc.value)

    async def test_every_answer_is_checked_not_just_the_first(self, monkeypatch):
        """
        A name with one public and one private answer is a name that will
        sometimes reach the private one.
        """
        _resolving_to(monkeypatch, "93.184.216.34", "169.254.169.254")

        with pytest.raises(mcp.McpError):
            await REAL_RESOLVE("https://split.example.com/mcp")

    async def test_a_public_name_passes(self, monkeypatch):
        _resolving_to(monkeypatch, "93.184.216.34")

        await REAL_RESOLVE("https://ordinary.example.com/mcp")

    async def test_a_name_that_does_not_resolve_says_so(self, monkeypatch):
        _resolving_to(monkeypatch, fails=True)

        with pytest.raises(mcp.McpError) as exc:
            await REAL_RESOLVE("https://missing.example.com/mcp")

        assert "resolve" in str(exc.value)


class TestRedirectsAreNotFollowed:
    async def test_the_redirect_target_is_never_requested(self, serve):
        """
        A followed redirect is a way past both address gates: the checked URL is
        public and the one actually fetched is whatever the server named. So the
        assertion is that the target was never reached, not merely that the call
        failed - it would fail either way.
        """
        requested = []

        def handler(request):
            requested.append(str(request.url))
            return httpx.Response(
                302, headers={"location": "https://169.254.169.254/mcp"},
            )

        serve(handler)
        with pytest.raises(mcp.McpError):
            await mcp.list_tools(URL)

        assert requested == [URL]
