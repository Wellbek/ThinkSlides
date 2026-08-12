"""
Canonical SSE event names.

This is the Python half of a contract shared with
``packages/schemas/src/agent-events.ts``. The two are kept in step by
``tests/test_sse_event_contract.py``, which fails if either side gains a name
the other does not have.

The names matter more than they look. ``EventSource`` dispatches by event name,
so one the browser was never told to listen for is delivered nowhere - no
error, no warning, no type error. Because the orchestrator persists the stream
anyway, the event reappears after a reload, which reads as a rendering bug
rather than a missing subscription. ``validation_unresolved`` was lost that way
for its entire life.
"""

# Events the browser subscribes to. Adding one here means adding it to
# AGENT_EVENT_TYPES in the TypeScript schema, which in turn forces a handler in
# the store (the handler map is typed Record<AgentEventType, ...>).
CLIENT_EVENT_TYPES = frozenset({
    "thought",
    "tool_call",
    "tool_result",
    "slide_finalized",
    "file_generated",
    "error",
    "progress",
    "question",
    "rate_limit",
    "validation_unresolved",
    "timer_update",
    "session_title_updated",
    "done",
})

# Events consumed inside the services and deliberately not subscribed to by the
# browser. ``deck_finalized`` is what tells api/generate.py to persist the
# rendered deck and emit ``file_generated``; the client learns about the deck
# through that instead.
INTERNAL_EVENT_TYPES = frozenset({
    "deck_finalized",
    "message_complete",
})

ALL_EVENT_TYPES = CLIENT_EVENT_TYPES | INTERNAL_EVENT_TYPES
