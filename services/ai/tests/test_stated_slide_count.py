"""
A stated slide count is the brief, not a suggestion (#300).

The planner used to be told "Target Slide Count: 3" and, a few lines later,
the boardroom format's "Plan roughly 10-18 slides", then required a title
slide and a conclusion slide on top - one content slide for a three-slide
request. Nothing checked the plan's length but the model's own report.
"""

import json

import pytest

from app.agent.tools import plan_presentation_structure as plan_tool


def _plan(n: int, total: int | None = None) -> str:
    return json.dumps({
        "title": "T", "totalSlides": n if total is None else total,
        "slides": [{"slideIndex": i, "title": f"S{i}", "contentType": "content",
                    "keyPoints": ["p"]} for i in range(n)],
    })


class _LLM:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def chat(self, messages, tools=None):
        self.calls.append(messages)
        return {"message": {"content": self.replies.pop(0)}}


async def _run(monkeypatch, llm, **kwargs):
    monkeypatch.setattr(plan_tool, "require_chat_provider", lambda: llm)
    return await plan_tool.PlanPresentationStructureTool.run(
        content_summary="HTTP caching", deck_format="boardroom", **kwargs)


class TestThePromptStatesOneCount:
    @pytest.mark.asyncio
    async def test_a_stated_count_drops_the_formats_range(self, monkeypatch):
        llm = _LLM(_plan(3))
        await _run(monkeypatch, llm, target_slide_count=3)
        prompt = llm.calls[0][0]["content"]
        assert "Plan roughly" not in prompt
        assert "EXACTLY 3 slides, any title slide included" in prompt

    @pytest.mark.asyncio
    async def test_a_short_deck_spends_no_slide_on_a_recap(self, monkeypatch):
        llm = _LLM(_plan(6))
        await _run(monkeypatch, llm, target_slide_count=6)
        prompt = llm.calls[0][0]["content"]
        assert "Do NOT add a conclusion or summary slide or a section divider" in prompt
        assert "Last slide should be a conclusion" not in prompt

    @pytest.mark.asyncio
    async def test_a_longer_deck_keeps_its_recap_inside_the_count(self, monkeypatch):
        llm = _LLM(_plan(8))
        await _run(monkeypatch, llm, target_slide_count=8)
        assert "conclusion/summary slide, counted in the total" in llm.calls[0][0]["content"]

    @pytest.mark.asyncio
    async def test_no_stated_count_keeps_the_formats_range(self, monkeypatch):
        llm = _LLM(_plan(12))
        await _run(monkeypatch, llm)
        prompt = llm.calls[0][0]["content"]
        assert "Plan roughly 10-18 slides" in prompt
        assert "Last slide should be a conclusion/summary slide." in prompt
        assert "EXACTLY" not in prompt


class TestThePlansLengthIsCheckedInCode:
    @pytest.mark.asyncio
    async def test_a_plan_of_the_wrong_length_is_sent_back_once(self, monkeypatch):
        llm = _LLM(_plan(12), _plan(6))
        result = await _run(monkeypatch, llm, target_slide_count=6)
        assert len(llm.calls) == 2
        correction = llm.calls[1][-1]["content"]
        assert "exactly 6 slides" in correction and "lists 12 slides" in correction
        assert result.data["totalSlides"] == 6

    @pytest.mark.asyncio
    async def test_a_declared_total_that_disagrees_with_the_list_is_a_miss(self, monkeypatch):
        llm = _LLM(_plan(6, total=12), _plan(6))
        await _run(monkeypatch, llm, target_slide_count=6)
        assert "declares totalSlides 12" in llm.calls[1][-1]["content"]

    @pytest.mark.asyncio
    async def test_a_second_miss_keeps_the_closer_plan(self, monkeypatch):
        llm = _LLM(_plan(7), _plan(12))
        result = await _run(monkeypatch, llm, target_slide_count=6)
        assert len(llm.calls) == 2
        assert result.data["totalSlides"] == 7

    @pytest.mark.asyncio
    async def test_an_unparseable_correction_keeps_the_first_plan(self, monkeypatch):
        llm = _LLM(_plan(7), "sorry")
        result = await _run(monkeypatch, llm, target_slide_count=6)
        assert result.success and result.data["totalSlides"] == 7

    @pytest.mark.asyncio
    async def test_a_plan_of_the_right_length_costs_one_call(self, monkeypatch):
        llm = _LLM(_plan(6))
        await _run(monkeypatch, llm, target_slide_count=6)
        assert len(llm.calls) == 1

    @pytest.mark.asyncio
    async def test_a_poster_is_not_held_to_a_stated_count(self, monkeypatch):
        """The poster's own rule (merge into one page) owns its length."""
        llm = _LLM(_plan(3))
        monkeypatch.setattr(plan_tool, "require_chat_provider", lambda: llm)
        result = await plan_tool.PlanPresentationStructureTool.run(
            content_summary="x", deck_format="poster", target_slide_count=3)
        assert len(llm.calls) == 1 and result.data["totalSlides"] == 1


class TestReviewedGaps:
    @pytest.mark.asyncio
    async def test_a_failed_correction_keeps_the_first_plan(self, monkeypatch):
        class _Flaky(_LLM):
            async def chat(self, messages, tools=None):
                if self.calls:
                    self.calls.append(messages)
                    raise TimeoutError("provider timed out")
                return await super().chat(messages, tools)

        result = await _run(monkeypatch, _Flaky(_plan(7)), target_slide_count=6)
        assert result.success and result.data["totalSlides"] == 7

    @pytest.mark.asyncio
    async def test_the_declared_total_follows_the_listed_slides(self, monkeypatch):
        """A total that agrees with the brief but not the list must not hide the miss."""
        llm = _LLM(_plan(12, total=6), "sorry")
        result = await _run(monkeypatch, llm, target_slide_count=6)
        assert result.data["totalSlides"] == 12

    @pytest.mark.asyncio
    async def test_a_one_or_two_slide_deck_spends_nothing_on_a_cover(self, monkeypatch):
        llm = _LLM(_plan(2))
        await _run(monkeypatch, llm, target_slide_count=2)
        prompt = llm.calls[0][0]["content"]
        assert "Do NOT plan a separate title slide" in prompt
        assert "First slide should be a title slide" not in prompt

    @pytest.mark.asyncio
    async def test_three_slides_open_on_content_unless_a_cover_is_asked_for(self, monkeypatch):
        """A three-slide deck asked to cover three things spent a third of
        itself on a title (#347)."""
        llm = _LLM(_plan(3))
        await _run(monkeypatch, llm, target_slide_count=3)
        prompt = llm.calls[0][0]["content"]
        assert "Do NOT plan a separate title slide unless the request asks" in prompt
        assert "First slide should be a title slide" not in prompt

    @pytest.mark.asyncio
    async def test_four_slides_still_open_on_a_cover(self, monkeypatch):
        llm = _LLM(_plan(4))
        await _run(monkeypatch, llm, target_slide_count=4)
        assert "First slide should be a title slide" in llm.calls[0][0]["content"]
