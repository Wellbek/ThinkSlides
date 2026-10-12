"""What a prompt cache could have answered of a run (#325)."""
import asyncio
import json

from app.agent.loop import AgentLoop
from app.research import prefix
from app.research.exchange import CHARS_PER_TOKEN


def _messages(system, user="hi"):
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


LONG = "x" * int(prefix.MIN_CACHED_TOKENS * CHARS_PER_TOKEN * 2)


def _send(ledger, messages, tools=None, where=""):
    tools_text, text = prefix.serialised(messages, tools or [])
    return tools_text, text, ledger.shared(tools_text, text)


def test_a_request_shares_the_prefix_an_answered_one_sent(tmp_path):
    ledger = prefix.PrefixLedger(tmp_path / prefix.LEDGER_NAME)
    tools_text, text, shared = _send(ledger, _messages(LONG, "one"))
    first = ledger.enter(tools_text, text, shared, "phase:a")
    tools_text, text, shared = _send(ledger, _messages(LONG, "two"))
    second = ledger.enter(tools_text, text, shared, "phase:a")
    assert first["sharedChars"] == 0
    assert second["sharedChars"] >= len(LONG)
    summary = prefix.summary(tmp_path)
    assert summary["calls"] == 2 and 0.4 < summary["cacheableShare"] < 0.5


def test_requests_in_flight_together_share_nothing(tmp_path):
    ledger = prefix.PrefixLedger(tmp_path / prefix.LEDGER_NAME)
    one = _send(ledger, _messages(LONG, "one"))
    two = _send(ledger, _messages(LONG, "two"))
    assert one[2] == two[2] == 0


def test_a_short_shared_prefix_is_not_cached(tmp_path):
    ledger = prefix.PrefixLedger(tmp_path / prefix.LEDGER_NAME)
    tools_text, text, shared = _send(ledger, _messages("short", "one"))
    ledger.enter(tools_text, text, shared, "")
    assert _send(ledger, _messages("short", "two"))[2] == 0


def test_tools_come_first_so_a_different_toolset_shares_nothing(tmp_path):
    ledger = prefix.PrefixLedger(tmp_path / prefix.LEDGER_NAME)
    tools_text, text, shared = _send(ledger, _messages(LONG), [{"name": "a"}])
    ledger.enter(tools_text, text, shared, "")
    assert _send(ledger, _messages(LONG), [{"name": "b"}])[2] == 0


def test_common_prefix_across_blocks():
    a = "a" * 10000 + "XYZ"
    assert prefix.common_prefix(a, "a" * 10000 + "XQ") == 10001
    assert prefix.common_prefix(a, a) == len(a)
    assert prefix.common_prefix("abc", "xbc") == 0


def test_a_request_that_raised_is_not_counted(tmp_path):
    class Broken:
        model = "broken"

        async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096):
            raise RuntimeError("budget spent")

    ledger = prefix.PrefixLedger(tmp_path / prefix.LEDGER_NAME)
    provider = prefix.PrefixRecordingProvider(Broken(), ledger)
    try:
        asyncio.run(provider.chat(_messages(LONG)))
    except RuntimeError:
        pass
    assert prefix.summary(tmp_path)["calls"] == 0


def test_the_provider_wrapper_enters_each_call(tmp_path):
    class Echo:
        model = "echo"

        async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096):
            return {"message": {"content": "ok"}, "tool_calls": []}

    ledger = prefix.PrefixLedger(tmp_path / prefix.LEDGER_NAME)
    provider = prefix.PrefixRecordingProvider(Echo(), ledger)
    asyncio.run(provider.chat(_messages(LONG, "a")))
    asyncio.run(provider.chat(_messages(LONG, "b")))
    entries = [json.loads(line) for line in (tmp_path / prefix.LEDGER_NAME).read_text().splitlines()]
    assert [e["sharedChars"] > 0 for e in entries] == [False, True]


def _prompt(loop, context):
    return loop._build_system_prompt(context, loop.working_state)


def test_the_system_prompt_keeps_its_run_stable_head_from_step_to_step():
    """#325: what holds for the whole run - rules, tools, the design system -
    comes before anything a step changes, so consecutive requests share it."""
    loop = AgentLoop(db=None, mode="simple")
    design = {"palette": {"colors": [{"name": "primary", "value": "#123456"}]},
              "typography": {"fonts": {"majorFont": "Georgia"}}}
    early = _prompt(loop, {"design_system": design})
    ws = loop.working_state
    ws.record_completed_tool("plan_presentation_structure", {}, {"totalSlides": 3})
    ws.has_called_finalize = True
    ws.amending = True
    # The same design system with its keys in another order, as it comes
    # back from saved state.
    reordered = {"typography": design["typography"], "palette": design["palette"]}
    late = _prompt(loop, {"design_system": reordered})
    head = early[:early.index("## Next Step")]
    shared = prefix.common_prefix(early, late)
    assert early[shared:].lstrip().startswith(("## Next Step", "## Tools at this step",
                                               "## Already Completed Work", "Explain"))
    assert "## Design System Context" in early[:shared]
    assert shared > len(head) * 0.9
    # A tool offered only at some steps is described after the head.
    assert "revise_slides" in late[shared:]
    assert "revise_slides" not in early


def test_whether_a_provider_caches_is_read_through_its_wrappers():
    from app.llm.provider import (AnthropicProvider, OpenAIProvider, ZhipuProvider,
                                  caches_prefixes)
    from app.llm.traced import TracedChatProvider

    openai = OpenAIProvider.__new__(OpenAIProvider)
    assert caches_prefixes(TracedChatProvider(openai))
    assert caches_prefixes(prefix.PrefixRecordingProvider(TracedChatProvider(openai), None))
    assert not caches_prefixes(TracedChatProvider(ZhipuProvider.__new__(ZhipuProvider)))
    assert not caches_prefixes(AnthropicProvider.__new__(AnthropicProvider))
    assert not caches_prefixes(None)
