"""
Body copy is never set below the size its frame was designed for (#348).

Repair fitted overflowing copy by setting it one rung smaller, and checked
the result against the floor of the rung it had moved to - so body copy set
as a caption was held to the caption floor, passed, and could step again. A
Kubernetes deck's prose ended at 12.6pt against a body floor of 16.2pt. Copy
that does not fit at a readable size now stays too long, and the writer is
given a budget.
"""

from app.agent.loop import AgentLoop
from app.design import authoring, run
from app.design.authored import to_pack
from app.design.repair import designed_floor_pt
from app.design.schema import Canvas, TextBlock
from app.design.vocabulary import ensure
from tests.test_author_run import WIDE

# One paragraph of ten sentences: repair used to step it down to this pack's
# footnote rung, 11.3pt, below its 12.15pt body floor.
LONG = ("A paragraph of argument that runs on well past what a column holds at "
        "the size it was designed for, as a first draft often does. ") * 10


def _pack():
    (language,) = authoring.read({"languages": [WIDE]})
    return ensure(to_pack(language, Canvas(w_in=13.333, h_in=7.5)))


def _deck():
    def text(line):
        return {"type": "text", "runs": [{"text": line}]}

    return [{"slideIndex": 0, "contents": [text("Cover")]},
            {"slideIndex": 1, "role": "content",
             "contents": [text("A finding"), text(LONG)]},
            {"slideIndex": 2, "contents": [text("Close")]}]


def test_copy_is_never_set_below_its_frames_designed_floor():
    pack = _pack()
    doc = run.compose(_deck(), pack)
    for page in doc.pages:
        for frame in page.all_frames():
            for block in frame.content:
                if isinstance(block, TextBlock) and block.type_role in pack.type_roles:
                    assert (pack.type_roles[block.type_role].size_pt
                            >= designed_floor_pt(pack, frame) - 1e-6), frame.id


def test_copy_that_still_does_not_fit_is_reported_with_a_budget():
    pack = _pack()
    budgets = run.copy_over_budget(run.compose(_deck(), pack), pack)
    (field,) = budgets[1]
    assert field["field"] == "the body copy"
    assert 0 < field["fits"] < field["chars"]


def test_the_budget_becomes_a_redraft_finding_the_writer_can_act_on():
    loop = AgentLoop(db=None)
    loop.working_state.copy_over_budget = {1: [
        {"field": "the headline", "chars": 90, "fits": 70},
        {"field": "the body copy", "chars": 600, "fits": 600}]}
    (finding,) = loop.working_state.copy_findings()
    assert finding["slideIndex"] == 1 and finding["fix"] == "redraft"
    assert finding["detail"].startswith(
        "The headline is about 90 characters where the page sets about 70")
    assert "body copy" not in finding["detail"]


def test_a_slide_that_fits_draws_no_finding():
    loop = AgentLoop(db=None)
    loop.working_state.copy_over_budget = {1: [
        {"field": "the body copy", "chars": 400, "fits": 400}]}
    assert loop.working_state.copy_findings() == []


def test_system_filled_frames_are_never_the_writers_to_cut():
    from app.design.run import _FIELD_FOR_FRAME

    assert not {"sourceLine", "footnotes", "folio"} & set(_FIELD_FOR_FRAME)


def test_a_code_edit_drops_budgets_measured_before_it():
    """The edit changes the file without a render; a stale budget re-flagged
    the slide it had just fixed, every round."""
    import inspect

    source = inspect.getsource(AgentLoop)
    edit = source.index("self.working_state.code_edits_applied += 1")
    assert "self.working_state.copy_over_budget = {}" in source[edit:edit + 400]


def test_budgets_survive_a_pause():
    import asyncio
    import uuid

    from tests.test_agent_loop_workflow import _FakeRedis, _loop

    async def roundtrip():
        session_id = uuid.uuid4()
        loop = _loop()
        loop.redis_client = _FakeRedis()
        loop.working_state.copy_over_budget = {
            2: [{"field": "the body copy", "chars": 800, "fits": 500}]}
        await loop._save_working_state(session_id)
        resumed = _loop()
        resumed.redis_client = loop.redis_client
        await resumed._load_working_state(session_id)
        return resumed.working_state.copy_over_budget

    assert asyncio.run(roundtrip()) == {
        2: [{"field": "the body copy", "chars": 800, "fits": 500}]}
