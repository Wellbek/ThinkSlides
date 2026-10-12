"""
#368: about 12pt as the floor, more pages before smaller type, smaller only
when the page count is fixed - and the user told where.
"""

from app.agent.loop import AgentLoop
from app.design import repair
from app.design.run import _with_floor_record
from tests.test_replace_holds_copy import pack, slide  # noqa: F401 - fixtures


def test_a_relaxed_floor_lets_copy_go_to_the_lower_bound_and_no_further(pack, slide):  # noqa: F811
    from app.design import run as design_run

    doc = design_run.compose([slide], pack)
    page = doc.pages[0]
    frame = next(f for f in page.all_frames() if f.content and f.role != "figure")
    held = repair.designed_floor_pt(pack, frame)
    with repair.relaxed_floor(10.0):
        assert repair.designed_floor_pt(pack, frame) == min(held, 10.0)
    assert repair.designed_floor_pt(pack, frame) == held


def test_a_page_set_below_its_floor_says_how_far(pack, slide, monkeypatch):  # noqa: F811
    from app.design import run as design_run

    doc = design_run.compose([slide], pack)
    assert doc.pages[0].provenance.below_floor_pt is None
    # As though every frame were designed for larger copy than it holds.
    monkeypatch.setattr(repair, "unrelaxed_floor_pt", lambda pack, frame: 99.0)
    marked = _with_floor_record(doc.pages[0], pack)
    assert marked.provenance.below_floor_pt is not None


def test_the_free_and_fixed_modes_compose_a_page_that_fits_the_same(pack, slide):  # noqa: F811
    from app.design import run as design_run

    free = design_run.compose([slide], pack, pages="free")
    fixed = design_run.compose([slide], pack, pages="fixed")
    assert len(free.pages) == len(fixed.pages) == 1
    assert fixed.pages[0].provenance.below_floor_pt is None


def test_the_closing_message_names_slides_set_below_the_floor_and_why():
    loop = AgentLoop(db=None, mode="simple")
    loop.working_state.brief = {"slideCount": 3}
    loop.working_state.below_floor = {1: 10.5}
    seen = {}

    async def _ask(prompt, chat_messages, *, phase_label):
        seen["prompt"] = prompt
        yield "result", "done"

    loop._ask_model = _ask
    import asyncio

    async def _close():
        return [e async for e in loop._closing_message({}, [])]

    asyncio.run(_close())
    assert "slide 2 at 10.5pt" in seen["prompt"]
    assert "3 slides the user asked for" in seen["prompt"]


def test_a_render_that_set_slides_small_leaves_them_for_the_close():
    import asyncio
    import uuid

    from app.agent.registry import ToolResult
    from tests.test_agent_loop_workflow import _dispatch

    loop = AgentLoop(db=None, mode="simple")
    loop.working_state.presentation_structure = {"totalSlides": 2, "slides": [{}, {}]}
    loop.working_state.accumulated_slides = [{"slideIndex": 0}, {"slideIndex": 1}]
    loop.working_state.autonomy_confirmed = True

    async def _execute(name, args):
        return ToolResult(success=True, data={
            "filePath": "/x.pptx", "previewUrls": [],
            "belowFloor": {"pages": [2], "slides": {"1": 10.5}}})

    loop._execute_tool = _execute
    asyncio.run(_dispatch(loop, "finalize_and_render_pptx", {"session_id": str(uuid.uuid4())}))
    assert loop.working_state.below_floor == {1: 10.5}
