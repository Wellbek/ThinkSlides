"""
Headless runs: tasks, the claude-code exchange, and replay.

The runner exists so a run can be driven, inspected and repeated without the
UI. These tests hold the parts that decide whether a repeat is a real one:
answers are read back in the loop's own shape, recorded in order, and served
back in order - including search results, since a replay that searched again
would not be one.
"""

import asyncio
import json

import pytest

from app.llm.traced import TracedChatProvider
from app.observability import trace
from app.research.exchange import (
    Cassette,
    ClaudeCodeExchange,
    ClaudeCodeProvider,
    ReplayProvider,
    recorded_search,
    request_sha,
    to_loop_response,
)
from app.research.task import Task


def _write_task(tmp_path, **fields):
    data = {"id": "t-1", "category": "technical", "prompt": "Three slides on caching.", **fields}
    path = tmp_path / "task.yaml"
    path.write_text("\n".join(f"{k}: {json.dumps(v)}" for k, v in data.items()))
    return path


class TestTasks:
    def test_a_task_file_loads_with_its_defaults(self, tmp_path):
        task = Task.load(_write_task(tmp_path))
        assert (task.id, task.mode, task.detail) == ("t-1", "simple", "detailed")

    def test_a_typo_in_a_key_is_an_error_not_a_silent_default(self, tmp_path):
        with pytest.raises(ValueError, match="unknown keys answer"):
            Task.load(_write_task(tmp_path, answer=["first"]))

    def test_first_takes_each_questions_first_option(self):
        task = Task(id="t", category="c", prompt="p")
        reply = task.answer(0, {"questions": [
            {"question": "What kind?", "options": ["Pitch", "Poster"]},
            {"question": "Which direction?", "options": [{"label": "Dark console", "id": "dc"}]},
        ]})
        assert reply == "What kind? Pitch\nWhich direction? Dark console"

    def test_a_scripted_answer_is_sent_as_written(self):
        task = Task(id="t", category="c", prompt="p", answers=["first", "Go ahead as planned"])
        assert task.answer(1, {"question": "Shall I build this outline?"}) == "Go ahead as planned"


class TestAnswers:
    def test_an_answer_reads_back_in_the_loops_shape(self):
        response = to_loop_response({
            "content": "Building the table.",
            "tool_calls": [{"name": "build_table", "arguments": {"slide_index": 0}}],
        })
        call = response["tool_calls"][0]
        assert response["message"]["content"] == "Building the table."
        assert call["function"]["name"] == "build_table"
        assert json.loads(call["function"]["arguments"]) == {"slide_index": 0}

    @pytest.mark.asyncio
    async def test_a_claude_code_request_waits_for_its_answer_and_is_recorded(self, tmp_path):
        cassette = Cassette(tmp_path / "cassette.jsonl")
        # Wrapped as the loop wraps every provider, which is what gives the
        # call its own span to carry the estimate.
        provider = TracedChatProvider(ClaudeCodeProvider(ClaudeCodeExchange(tmp_path / "llm", cassette)))

        async def answer_when_asked():
            while not (tmp_path / "llm" / "0001.pending").exists():
                await asyncio.sleep(0.05)
            request = (tmp_path / "llm" / "0001.request.md").read_text()
            assert "Three slides" in request and "build_chart" in request
            (tmp_path / "llm" / "0001.response.json").write_text(json.dumps({"content": "ok"}))

        t = trace.begin("run")
        with trace.span("phase", "plan"):
            result, _ = await asyncio.gather(
                provider.chat([{"role": "user", "content": "Three slides"}],
                              tools=[{"function": {"name": "build_chart", "parameters": {}}}]),
                answer_when_asked(),
            )
        await trace.finish(t, [], termination=trace.COMPLETE)

        assert result["message"]["content"] == "ok"
        assert not (tmp_path / "llm" / "0001.pending").exists()
        assert [e["kind"] for e in Cassette(tmp_path / "cassette.jsonl").entries] == ["chat"]
        # What the call would have cost on an API model, estimated and marked so.
        assert t.root.attrs["inputTokens"] > 0

    @pytest.mark.asyncio
    async def test_an_unanswered_request_times_out_rather_than_hanging(self, tmp_path):
        exchange = ClaudeCodeExchange(tmp_path / "llm", Cassette(tmp_path / "c.jsonl"), timeout_s=0.2)
        with pytest.raises(TimeoutError, match="0001"):
            await ClaudeCodeProvider(exchange).chat([{"role": "user", "content": "hi"}])


class TestReplay:
    @pytest.mark.asyncio
    async def test_answers_come_back_in_the_order_they_were_given(self, tmp_path):
        cassette = Cassette(tmp_path / "cassette.jsonl")
        cassette.append("chat", "a", {"content": "first"})
        cassette.append("search", "q", [{"url": "https://example.com"}])
        cassette.append("chat", "b", {"content": "second"})
        replay = ReplayProvider(Cassette(tmp_path / "cassette.jsonl"))

        first = await replay.chat([{"role": "user", "content": "x"}])
        second = await replay.chat([{"role": "user", "content": "y"}])

        assert [first["message"]["content"], second["message"]["content"]] == ["first", "second"]

    @pytest.mark.asyncio
    async def test_an_answer_goes_to_the_request_it_was_recorded_for(self, tmp_path):
        # Two slides compiled at once are recorded in the order their answers
        # arrived; replay asks in the order the requests were made (#323).
        first_asked = [{"role": "user", "content": "slide 1"}]
        second_asked = [{"role": "user", "content": "slide 2"}]
        cassette = Cassette(tmp_path / "cassette.jsonl")
        cassette.append("chat", request_sha(second_asked), {"content": "for slide 2"})
        cassette.append("chat", request_sha(first_asked), {"content": "for slide 1"})
        replay = ReplayProvider(Cassette(tmp_path / "cassette.jsonl"))

        first = await replay.chat(first_asked)
        second = await replay.chat(second_asked)

        assert [first["message"]["content"], second["message"]["content"]] == [
            "for slide 1", "for slide 2"]

    @pytest.mark.asyncio
    async def test_a_drifted_request_takes_the_earliest_answer_left(self, tmp_path):
        asked = [{"role": "user", "content": "slide 2"}]
        cassette = Cassette(tmp_path / "cassette.jsonl")
        cassette.append("chat", "recorded-before-a-prompt-change", {"content": "drifted"})
        cassette.append("chat", request_sha(asked), {"content": "exact"})
        replay = ReplayProvider(Cassette(tmp_path / "cassette.jsonl"))

        exact = await replay.chat(asked)
        drifted = await replay.chat([{"role": "user", "content": "reworded"}])

        assert [exact["message"]["content"], drifted["message"]["content"]] == ["exact", "drifted"]

    @pytest.mark.asyncio
    async def test_asking_for_more_than_was_recorded_is_an_error(self, tmp_path):
        replay = ReplayProvider(Cassette(tmp_path / "empty.jsonl"))
        with pytest.raises(LookupError, match="more than was recorded"):
            await replay.chat([{"role": "user", "content": "x"}])

    @pytest.mark.asyncio
    async def test_search_results_are_kept_and_served_back_without_searching(self, tmp_path):
        cassette = Cassette(tmp_path / "cassette.jsonl")
        calls = []

        async def live_search(config, query, **kwargs):
            calls.append(query)
            return [{"url": f"https://example.com/{query}"}]

        recording = recorded_search(live_search, cassette, replay=False)
        assert await recording(None, "etag") == [{"url": "https://example.com/etag"}]

        replaying = recorded_search(live_search, Cassette(tmp_path / "cassette.jsonl"), replay=True)
        assert await replaying(None, "etag") == [{"url": "https://example.com/etag"}]
        assert calls == ["etag"]


class TestInjectedProviders:
    @pytest.mark.asyncio
    async def test_a_loop_runs_on_providers_nobody_saved_in_settings(self, tmp_path):
        """A headless run must not need a workspace's credentials - and must
        not quietly fall back to them either."""
        from app.agent.loop import AgentLoop
        from app.agent.run_context import get_search_config, require_chat_provider
        from app.ai_settings import SearchConfig

        provider = ClaudeCodeProvider(
            ClaudeCodeExchange(tmp_path / "llm", Cassette(tmp_path / "c.jsonl")))
        search = SearchConfig(backend="tavily", api_key="tvly-test")
        loop = AgentLoop(db=None, mode="deep", chat_provider=provider, search_config=search)

        await loop._configure()
        try:
            assert loop.llm.inner is provider
            assert require_chat_provider().inner is provider
            assert loop.mode == "deep"
            assert get_search_config() is search
        finally:
            loop._reset_run_context()


class TestReplayFollowsTheRecording:
    @pytest.mark.asyncio
    async def test_a_failed_search_is_kept_so_later_searches_stay_in_step(self, tmp_path):
        from app.research.exchange import RecordedSearchError

        cassette = Cassette(tmp_path / "cassette.jsonl")

        async def flaky(config, query, **kwargs):
            if query == "down":
                raise TimeoutError("tavily timed out")
            return [{"url": f"https://example.com/{query}"}]

        recording = recorded_search(flaky, cassette, replay=False)
        with pytest.raises(TimeoutError):
            await recording(None, "down")
        await recording(None, "etag")

        replaying = recorded_search(flaky, Cassette(tmp_path / "cassette.jsonl"), replay=True)
        with pytest.raises(RecordedSearchError, match="timed out"):
            await replaying(None, "down")
        assert await replaying(None, "etag") == [{"url": "https://example.com/etag"}]

    def test_replay_takes_its_search_backend_from_the_recording_not_the_environment(
        self, tmp_path, monkeypatch,
    ):
        from app.research.runner import REPLAY, _search_config

        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        (tmp_path / "manifest.json").write_text(json.dumps({"search": "tavily"}))
        (tmp_path / "cassette.jsonl").write_text("")
        assert _search_config(REPLAY, tmp_path / "cassette.jsonl").backend == "tavily"

        (tmp_path / "manifest.json").write_text(json.dumps({"search": None}))
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-live")
        assert _search_config(REPLAY, tmp_path / "cassette.jsonl") is None

    @pytest.mark.asyncio
    async def test_agreeing_to_research_mid_run_uses_the_injected_backend(self):
        """Settings have no search backend on a headless run; the injected one
        has to be the one a mid-run "yes, research it" switches to."""
        from app.agent.loop import AgentLoop
        from app.agent.run_context import get_search_config
        from app.ai_settings import SearchConfig

        search = SearchConfig(backend="tavily", api_key="tvly-test")
        loop = AgentLoop(db=None, mode="simple", search_config=search)
        try:
            assert await loop._enable_deep_research() is True
            assert loop.mode == "deep"
            assert get_search_config() is search
        finally:
            loop._reset_run_context()
