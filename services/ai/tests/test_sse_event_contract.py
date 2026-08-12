"""
The SSE event names are a contract between three codebases.

They are written out in the AI service (what it emits), in the TypeScript
schema (the typed union), and in the web client (what it subscribes to). If
those drift, the failure is silent: EventSource dispatches by event name, so
one the browser was never told about is delivered nowhere - no error, no
warning, no type error. The orchestrator persists the stream regardless, so it
reappears after a reload, which looks like a rendering bug rather than a
missing subscription.

The web half is held together by types (the store's handler map is
``Record<AgentEventType, SSEEventHandler>``, so a missing handler will not
compile). This is the half that reaches across the language boundary.
"""

import re
from pathlib import Path

import pytest

from app.agent.event_types import (
    ALL_EVENT_TYPES,
    CLIENT_EVENT_TYPES,
    INTERNAL_EVENT_TYPES,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_FILE = REPO_ROOT / "packages" / "schemas" / "src" / "agent-events.ts"
AI_APP = Path(__file__).resolve().parents[1] / "app"


def _parse_const_array(source: str, name: str) -> set[str]:
    """Pull the string members out of `export const NAME = [...] as const;`."""
    match = re.search(
        rf"export const {name} = \[(.*?)\] as const;", source, re.DOTALL
    )
    assert match, f"{name} not found in {SCHEMA_FILE}"
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


@pytest.fixture(scope="module")
def schema_source() -> str:
    assert SCHEMA_FILE.exists(), f"missing {SCHEMA_FILE}"
    return SCHEMA_FILE.read_text()


def test_client_event_names_match_the_typescript_schema(schema_source):
    ts_names = _parse_const_array(schema_source, "AGENT_EVENT_TYPES")

    missing_in_ts = CLIENT_EVENT_TYPES - ts_names
    missing_in_py = ts_names - CLIENT_EVENT_TYPES

    assert not missing_in_ts, (
        f"emitted but never subscribed to - these would be dropped silently: "
        f"{sorted(missing_in_ts)}. Add them to AGENT_EVENT_TYPES in {SCHEMA_FILE}."
    )
    assert not missing_in_py, (
        f"subscribed to but not declared in the AI service: "
        f"{sorted(missing_in_py)}. Add them to app/agent/event_types.py."
    )


def test_internal_event_names_match_the_typescript_schema(schema_source):
    ts_names = _parse_const_array(schema_source, "INTERNAL_AGENT_EVENT_TYPES")
    assert ts_names == INTERNAL_EVENT_TYPES, (
        "the internal (deliberately unsubscribed) event lists disagree: "
        f"TypeScript has {sorted(ts_names)}, Python has {sorted(INTERNAL_EVENT_TYPES)}"
    )


def test_client_and_internal_names_do_not_overlap():
    overlap = CLIENT_EVENT_TYPES & INTERNAL_EVENT_TYPES
    assert not overlap, f"an event cannot be both client-facing and internal: {sorted(overlap)}"


def test_every_emitted_event_name_is_declared():
    """
    Every ``_event("name", ...)`` in the service must be a known name.

    ``_event`` enforces this at runtime; this catches it without having to
    execute the branch that emits it.
    """
    undeclared: dict[str, set[str]] = {}
    for path in AI_APP.rglob("*.py"):
        for name in re.findall(r'_event\(\s*"([a-z_]+)"', path.read_text()):
            if name not in ALL_EVENT_TYPES:
                undeclared.setdefault(str(path.relative_to(AI_APP)), set()).add(name)

    assert not undeclared, (
        f"event names emitted but not declared in app/agent/event_types.py: "
        f"{ {k: sorted(v) for k, v in undeclared.items()} }"
    )


def test_event_rejects_an_undeclared_name():
    from app.agent.loop import _event

    with pytest.raises(ValueError, match="unknown agent event type"):
        _event("deck_rethemed", "validation", {})


def test_event_accepts_a_declared_name():
    from app.agent.loop import _event

    event = _event("validation_unresolved", "validation", {"issueCount": 2})
    assert event["type"] == "validation_unresolved"
    assert event["phase"] == "validation"
    assert event["issueCount"] == 2


def test_validation_unresolved_is_client_facing():
    """The regression that motivated all of this: it must reach the browser."""
    assert "validation_unresolved" in CLIENT_EVENT_TYPES
