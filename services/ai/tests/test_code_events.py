"""
The events that draw the agent's script into the interface.

These are the first streaming events in this system - everything else is
one-shot - so the failure they can have is one nothing else here can: a
replayed stream that appends the script to itself. `event_types.py` records
the other one in its own docstring, that `validation_unresolved` "was lost
that way for its entire life" because nobody checked an event was handled.
Both are the reason these exist.
"""


from app.agent.event_types import ALL_EVENT_TYPES, CLIENT_EVENT_TYPES
from app.agent.loop import AgentLoop

SCRIPT = "\n".join(f"line_{n} = {n}" for n in range(60))


def _loop() -> AgentLoop:
    return AgentLoop(db=None)


def _events(loop: AgentLoop, source: str, call_id: str = "call-1"):
    return list(loop._code_events({"code": source}, call_id, "validation", "amend"))


class TestTheContract:
    def test_both_names_are_declared_client_facing(self):
        """An event the browser was never told to listen for is delivered
        nowhere - no error, no warning, no type error."""
        assert {"code_delta", "code_ready"} <= CLIENT_EVENT_TYPES
        assert {"code_delta", "code_ready"} <= ALL_EVENT_TYPES

    def test_the_typescript_side_declares_them_too(self):
        """`test_sse_event_contract.py` is the general check; this names the
        two, so a rename on one side fails here with the reason visible."""
        import pathlib

        schema = pathlib.Path(__file__).resolve().parents[3] / (
            "packages/schemas/src/agent-events.ts")
        text = schema.read_text()

        assert '"code_delta"' in text
        assert '"code_ready"' in text
        assert "CodeDeltaEvent" in text
        assert "CodeReadyEvent" in text


class TestTheScriptIsStreamed:
    def test_it_arrives_in_chunks_and_then_whole(self):
        events = _events(_loop(), SCRIPT)

        deltas = [e for e in events if e["type"] == "code_delta"]
        ready = [e for e in events if e["type"] == "code_ready"]

        assert len(deltas) > 1
        assert len(ready) == 1
        assert "".join(d["text"] for d in deltas) == SCRIPT
        assert ready[0]["source"] == SCRIPT

    def test_every_delta_is_numbered_and_they_are_contiguous(self):
        """
        `seq` is what makes a delta idempotent.

        The orchestrator persists the stream and replays it after a reload,
        so a browser that already holds some chunks will be sent them again.
        Without a sequence number the store cannot tell a chunk it has from
        one it does not, and a replay appends the script to itself.
        """
        deltas = [e for e in _events(_loop(), SCRIPT)
                  if e["type"] == "code_delta"]

        assert [d["seq"] for d in deltas] == list(range(len(deltas)))

    def test_replaying_the_whole_stream_does_not_double_the_script(self):
        """The store's rule, asserted against real events: fold by stepId,
        drop a seq already folded in."""
        events = _events(_loop(), SCRIPT)
        replayed = events + events   # exactly what a reconnect delivers

        folded: dict[str, dict] = {}
        for event in replayed:
            if event["type"] != "code_delta":
                continue
            held = folded.get(event["stepId"])
            if held is None:
                folded[event["stepId"]] = dict(event)
            elif event["seq"] > held["seq"]:
                held["text"] += event["text"]
                held["seq"] = event["seq"]

        assert len(folded) == 1
        assert next(iter(folded.values()))["text"] == SCRIPT

    def test_every_event_of_one_script_shares_one_step_id(self):
        events = _events(_loop(), SCRIPT, call_id="call-42")

        assert {e["stepId"] for e in events} == {"call-42"}

    def test_an_empty_script_produces_nothing(self):
        assert _events(_loop(), "") == []


class TestASecondAttemptIsShownAsADiff:
    def test_a_first_attempt_carries_no_previous(self):
        ready = [e for e in _events(_loop(), SCRIPT)
                 if e["type"] == "code_ready"][0]

        assert "previous" not in ready

    def test_a_failed_script_is_remembered_for_the_next_one(self):
        loop = _loop()
        loop._remember_code_attempt(
            {"code": "first = 1"},
            _Result(False, {"source": "first = 1", "reason": "it exploded"}),
        )

        ready = [e for e in _events(loop, "second = 2")
                 if e["type"] == "code_ready"][0]

        assert ready["previous"] == "first = 1"
        assert ready["reason"] == "it exploded"

    def test_a_script_that_worked_is_not_carried_forward(self):
        """The next edit is not a fix of it, and rendering it as a diff
        against an unrelated earlier script would say it was."""
        loop = _loop()
        loop._remember_code_attempt(
            {"code": "first = 1"}, _Result(True, {"source": "first = 1"}))

        ready = [e for e in _events(loop, "second = 2")
                 if e["type"] == "code_ready"][0]

        assert "previous" not in ready

    def test_the_reason_falls_back_to_the_tools_own_error(self):
        loop = _loop()
        loop._remember_code_attempt({"code": "x"}, _Result(False, None, "boom"))

        assert loop.working_state.last_code_attempt["reason"] == "boom"


class _Result:
    """The two fields `_remember_code_attempt` reads off a ToolResult."""

    def __init__(self, success, data, error=""):
        self.success = success
        self.data = data
        self.error = error


class TestTheAmendPhase:
    """The tool is offered only where a script is the last resort, not the
    first - it is the one tool that is not on-brand by construction."""

    def test_it_is_off_unless_an_operator_turns_it_on(self):
        from app.config import settings

        assert settings.code_amendment_enabled is False

    def test_the_phase_does_not_apply_to_a_clean_run(self, monkeypatch):
        from app.agent import pipeline
        from app.config import settings

        monkeypatch.setattr(settings, "code_amendment_enabled", True)
        loop = _loop()
        loop.working_state.has_called_finalize = True
        loop.working_state.last_rendered_path = "/tmp/deck.pptx"
        loop.working_state.validation_clean = True

        assert not pipeline._wants_code_amendment(loop)

    def test_it_applies_only_once_validation_has_run_out_of_passes(
            self, monkeypatch):
        from app.agent import pipeline
        from app.config import settings

        monkeypatch.setattr(settings, "code_amendment_enabled", True)
        loop = _loop()
        loop.working_state.has_called_finalize = True
        loop.working_state.last_rendered_path = "/tmp/deck.pptx"
        loop.working_state.validation_clean = False
        monkeypatch.setattr(
            type(loop.working_state), "at_validation_cap",
            property(lambda self: True))

        assert pipeline._wants_code_amendment(loop)

    def test_the_flag_being_off_is_enough_to_keep_it_away(self, monkeypatch):
        from app.agent import pipeline
        from app.config import settings

        monkeypatch.setattr(settings, "code_amendment_enabled", False)
        loop = _loop()
        loop.working_state.has_called_finalize = True
        loop.working_state.last_rendered_path = "/tmp/deck.pptx"
        monkeypatch.setattr(
            type(loop.working_state), "at_validation_cap",
            property(lambda self: True))

        assert not pipeline._wants_code_amendment(loop)


class _Redis:
    """The four calls `_save_working_state` makes, and a dict behind them."""

    def __init__(self):
        self.store: dict = {}

    async def setex(self, key, ttl, value):
        self.store[key] = value

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)


class TestTheChoiceSurvivesAResume:
    def test_the_amendment_marker_is_persisted(self):
        """A resumed run that forgot it had already written a script would
        write another one, which is a loop rather than a fix."""
        import asyncio
        import json
        import uuid

        loop = _loop()
        loop.redis_client = _Redis()
        loop.working_state.code_amendment_done = True

        session = uuid.uuid4()
        asyncio.run(loop._save_working_state(session))
        saved = json.loads(loop.redis_client.store[f"working_state:{session}"])

        assert saved["code_amendment_done"] is True
