"""
A revision round redrafts every slide validation flagged, tells each one
what was wrong with it, and stops once validation has had its last look.

The first headless run showed the loop doing none of that (#299): with most
of the deck flagged, it redrafted slide 1 with no findings in the prompt,
re-rendered with the other slides untouched, and after the validation cap
drafted and rendered once more - so what shipped was never validated.
"""

import json

import pytest

from app.agent.registry import ToolResult
from tests.test_pipeline import INTAKE, _calls, _loop, _text

PLAN = {"title": "Deck", "totalSlides": 2,
        "slides": [{"title": "One"}, {"title": "Two"}]}

STATE = {
    "brief": json.loads(INTAKE), "scouted": True, "consulted_before_planning": True,
    "autonomy_confirmed": True, "reviewed_outline_with_user": True,
    "offered_directions": True, "presentation_structure": PLAN,
    "design_system": {"palette": {}, "layouts": []},
    "web_search_calls": 9, "researched_urls": [f"https://s/{i}" for i in range(8)],
    "mode": "deep", "detail_level": "detailed",
}


def _draft(index):
    return _calls(("select_layout", {"slide_index": index}),
                  ("design_slide_content", {"slide_index": index}))


def _with_validations(loop, seen, verdicts, monkeypatch):
    """Validation answers in turn from `verdicts`; everything else as the harness does."""
    run_tool = loop._execute_tool
    remaining = list(verdicts)

    async def _execute(name, args):
        if name == "validate_rendered_output":
            seen.append(name)
            issues = remaining.pop(0) if remaining else []
            return ToolResult(success=True, data={"valid": not issues, "issues": issues})
        return await run_tool(name, args)

    monkeypatch.setattr(loop, "_execute_tool", _execute)


def _prompts(loop, monkeypatch):
    """Every system prompt the model is shown, to read the drafting directive."""
    seen = []
    chat = loop._chat_with_retry

    async def _recording(messages=None, tools=None):
        seen.append(messages[0]["content"])
        return await chat(messages=messages, tools=tools)

    monkeypatch.setattr(loop, "_chat_with_retry", _recording)
    return seen


@pytest.mark.asyncio
async def test_every_flagged_slide_is_redrafted_with_its_own_finding(monkeypatch):
    flagged = [{"slideIndex": 0, "type": "overflow", "detail": "Title runs off the page"},
               {"slideIndex": 1, "type": "overlap", "detail": "Chart covers the text"}]
    loop, session_id, seen = _loop(
        [_draft(0), _draft(1),              # first pass
         _draft(0), _draft(1),              # the revision round
         _text("Done.")],
        state=STATE, monkeypatch=monkeypatch,
    )
    _with_validations(loop, seen, [flagged, []], monkeypatch)
    prompts = _prompts(loop, monkeypatch)

    [e async for e in loop.run(session_id)]

    renders = [i for i, name in enumerate(seen) if name == "finalize_and_render_pptx"]
    drafts = [i for i, name in enumerate(seen) if name == "design_slide_content"]
    assert len(renders) == 2
    # Both flagged slides are redrafted between the first and second render.
    assert len([i for i in drafts if renders[0] < i < renders[1]]) == 2
    revising = [p for p in prompts if "Revise slide" in p]
    assert any("Title runs off the page" in p and "Chart covers the text" not in p for p in revising)
    assert any("Chart covers the text" in p and "Title runs off the page" not in p for p in revising)


@pytest.mark.asyncio
async def test_nothing_is_drafted_or_rendered_after_the_last_validation(monkeypatch):
    stuck = [{"slideIndex": 0, "type": "overflow", "detail": "Title runs off the page"},
             {"slideIndex": 1, "type": "overflow", "detail": "Body runs off the page"}]
    loop, session_id, seen = _loop(
        [_draft(0), _draft(1)] + [_draft(0), _draft(1)] * 4 + [_text("Done.")],
        state=STATE, monkeypatch=monkeypatch,
    )
    _with_validations(loop, seen, [stuck] * 5, monkeypatch)

    [e async for e in loop.run(session_id)]

    last_validation = max(i for i, n in enumerate(seen) if n == "validate_rendered_output")
    after = seen[last_validation + 1:]
    assert "finalize_and_render_pptx" not in after
    assert "design_slide_content" not in after
    assert seen.count("validate_rendered_output") == loop.working_state.MAX_VALIDATION_ATTEMPTS
