"""
An unreachable connection degrades loudly and never fails a run.

Both failure modes here are real, and the quiet one is the worse of the two.
A run that dies because a customer's wiki server is down is unacceptable. A run
that quietly produces a thinner deck because a source vanished, with nothing
said, is worse - the user cannot tell "no relevant results" from "the tool was
never there".

The precedent is the validation bookkeeping in ``loop.py``: checks that could not
run at all are tracked separately from real attempts, "so infrastructure trouble
cannot exhaust the revision budget, but still bounded so it cannot spin forever".
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.agent.event_types import CLIENT_EVENT_TYPES
from app.connections import mcp, mcp_tools, store
from app.db.models import OrganisationConnection

pytestmark = pytest.mark.unconfigured_workspace

ORG_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _row(**kwargs) -> OrganisationConnection:
    fields = {
        "id": uuid.uuid4(),
        "organisation_id": ORG_ID,
        "connection_id": "mcp_http",
        "slug": "wiki",
        "label": "Company wiki",
        "enabled": True,
        "config": {"url": "https://mcp.example.com/mcp"},
        "secrets_encrypted": {},
        "secret_hints": {},
        "last_checked_at": None,
        "last_error": None,
        "tool_count": None,
    }
    fields.update(kwargs)
    return OrganisationConnection(**fields)


class FakeSession:
    def __init__(self, rows):
        self.rows = list(rows)
        self.commits = 0

    async def commit(self):
        self.commits += 1


@pytest.fixture
def db(monkeypatch):
    def install(rows):
        session = FakeSession(rows)

        async def load_rows(_db, org_id):
            return [r for r in session.rows if r.organisation_id == org_id]

        monkeypatch.setattr(store, "load_rows", load_rows)
        return session
    return install


class TestTheThreeStatesAreDistinguishable:
    """
    Never tried, working, and reachable-an-hour-ago-but-not-now. A single
    boolean collapses the first two, and a user with a connection they have just
    added would be told it was broken.
    """

    def test_a_connection_that_has_never_run_is_unknown(self):
        payload = store.redacted_connection(_row())

        assert payload["health"]["status"] == "unknown"
        assert payload["health"]["lastCheckedAt"] is None

    def test_a_connection_that_worked_is_healthy(self):
        payload = store.redacted_connection(_row(
            last_checked_at=datetime.now(timezone.utc), tool_count=7,
        ))

        assert payload["health"]["status"] == "healthy"
        assert payload["health"]["toolCount"] == 7
        assert payload["health"]["lastError"] is None

    def test_a_connection_that_failed_is_degraded_and_says_why(self):
        payload = store.redacted_connection(_row(
            last_checked_at=datetime.now(timezone.utc),
            last_error="The server refused the credential.",
        ))

        assert payload["health"]["status"] == "degraded"
        assert "credential" in payload["health"]["lastError"]

    def test_a_disabled_connection_is_neither_healthy_nor_degraded(self):
        """Turned off is a choice, not a fault, and must not read as one."""
        payload = store.redacted_connection(_row(
            enabled=False, last_checked_at=datetime.now(timezone.utc),
            last_error="was down last time",
        ))

        assert payload["health"]["status"] == "disabled"

    def test_the_last_checked_time_is_carried(self):
        checked = datetime(2026, 8, 17, 9, 30, tzinfo=timezone.utc)

        payload = store.redacted_connection(_row(last_checked_at=checked))

        assert payload["health"]["lastCheckedAt"].startswith("2026-08-17T09:30")


class TestRecordingHealth:
    async def test_a_successful_run_clears_a_previous_error(self, db):
        row = _row(last_error="was down", last_checked_at=datetime(2020, 1, 1))
        session = db([row])

        await store.record_health(session, ORG_ID, {row.id: ("", 5)})

        assert row.last_error is None
        assert row.tool_count == 5
        assert row.last_checked_at.year >= 2026

    async def test_a_failed_run_records_the_reason(self, db):
        row = _row()
        session = db([row])

        await store.record_health(session, ORG_ID, {row.id: ("unreachable", 0)})

        assert row.last_error == "unreachable"
        assert row.tool_count == 0
        assert row.last_checked_at is not None

    async def test_a_row_not_in_the_results_is_left_alone(self, db):
        touched = _row(slug="a")
        untouched = _row(slug="b", last_error="old news")
        session = db([touched, untouched])

        await store.record_health(session, ORG_ID, {touched.id: ("", 1)})

        assert untouched.last_error == "old news"
        assert untouched.last_checked_at is None

    async def test_nothing_to_record_writes_nothing(self, db):
        session = db([_row()])

        await store.record_health(session, ORG_ID, {})

        assert session.commits == 0

    async def test_a_write_failure_does_not_propagate(self, db, monkeypatch):
        """
        Failing a run over a failed write about a failure would be absurd. This
        is bookkeeping.
        """
        row = _row()
        session = db([row])

        async def explode():
            raise RuntimeError("the database went away")

        monkeypatch.setattr(session, "commit", explode)

        await store.record_health(session, ORG_ID, {row.id: ("down", 0)})


class TestADegradedServerContributesNothing:
    def test_it_registers_no_tools(self):
        from app.agent.registry import ToolRegistry

        registry = ToolRegistry()
        mcp_tools.register(registry, [
            mcp_tools.DiscoveredServer(slug="down", label="Down", error="timeout"),
        ])

        assert registry.list_tools() == []

    def test_it_is_not_offered_in_any_phase(self):
        from app.agent.registry import ToolRegistry

        by_phase = mcp_tools.register(ToolRegistry(), [
            mcp_tools.DiscoveredServer(slug="down", label="Down", error="timeout"),
        ])

        assert all(not names for names in by_phase.values())

    def test_a_working_server_beside_a_broken_one_still_works(self):
        from app.agent.registry import ToolRegistry

        registry = ToolRegistry()
        by_phase = mcp_tools.register(registry, [
            mcp_tools.DiscoveredServer(slug="down", label="Down", error="timeout"),
            mcp_tools.DiscoveredServer(
                slug="up", label="Up",
                session=mcp.McpSession(url="https://mcp.example.com/mcp"),
                tools=[mcp.McpTool("search", "")],
            ),
        ])

        assert by_phase["research"] == {"mcp__up__search"}


class TestTheWarningEvent:
    def test_the_browser_is_subscribed_to_it(self):
        """
        EventSource dispatches by name, so an event the browser was never told
        about is delivered nowhere - no error, no warning. That is how
        validation_unresolved was lost for its entire life.
        """
        assert "connection_degraded" in CLIENT_EVENT_TYPES

    def test_the_typescript_contract_lists_it_too(self):
        import pathlib

        schema = (
            pathlib.Path(__file__).resolve().parents[3]
            / "packages/schemas/src/agent-events.ts"
        ).read_text()

        assert '"connection_degraded"' in schema
        assert "ConnectionDegradedEvent" in schema

    def test_the_store_has_a_handler_for_it(self):
        """
        The handler map is typed ``Record<AgentEventType, ...>``, so a missing
        one is a compile error - but this suite does not compile TypeScript, and
        a subscribed event with no handler is the failure that hides.
        """
        import pathlib

        store_source = (
            pathlib.Path(__file__).resolve().parents[3]
            / "apps/web/src/stores/chat-store.ts"
        ).read_text()

        assert "connection_degraded:" in store_source


class TestTheDegradedListTheWarningCarries:
    def _loop(self, servers):
        from app.agent.loop import AgentLoop

        loop = AgentLoop.__new__(AgentLoop)
        loop.mcp_servers = servers
        return loop

    def test_it_names_each_connection_and_its_reason(self):
        """
        "One connection is degraded" tells a user nothing they can act on.
        "Company wiki: the server refused the credential" names the credential
        to go and fix.
        """
        degraded = self._loop([
            mcp_tools.DiscoveredServer(
                slug="wiki", label="Company wiki",
                error="The server refused the credential.",
            ),
        ])._degraded_connections()

        assert degraded == [{
            "label": "Company wiki",
            "reason": "The server refused the credential.",
        }]

    def test_a_working_server_is_not_in_it(self):
        degraded = self._loop([
            mcp_tools.DiscoveredServer(
                slug="up", label="Up",
                session=mcp.McpSession(url="https://mcp.example.com/mcp"),
            ),
        ])._degraded_connections()

        assert degraded == []

    def test_no_connections_means_no_warning(self):
        assert self._loop([])._degraded_connections() == []

    def test_several_failures_are_one_list_not_several_warnings(self):
        degraded = self._loop([
            mcp_tools.DiscoveredServer(slug="a", label="A", error="down"),
            mcp_tools.DiscoveredServer(slug="b", label="B", error="refused"),
        ])._degraded_connections()

        assert len(degraded) == 2


class TestItIsNeverRetriedMidRun:
    def test_discovery_is_the_only_place_that_handshakes(self):
        """
        Retrying is how a six-second timeout becomes a minute of them. The
        structural guarantee is that a failed server has no registered tool, so
        there is nothing to call and therefore nothing to retry.
        """
        from app.agent.registry import ToolRegistry

        registry = ToolRegistry()
        mcp_tools.register(registry, [
            mcp_tools.DiscoveredServer(slug="down", label="Down", error="timeout"),
        ])

        assert registry.get("mcp__down__search") is None

    def test_the_handshake_timeout_is_short_enough_to_not_stall_a_run(self):
        assert mcp.HANDSHAKE_TIMEOUT_SECONDS <= 10
