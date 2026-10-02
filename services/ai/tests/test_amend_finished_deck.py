"""A message sent after a deck is finished amends that deck (#376).

The working state used to be deleted when a run completed, so a follow-up such
as "add a picture to every page" started a new deck from the intake, and the
edit tool - which needs a rendered deck - was never offered.
"""
import asyncio
import datetime
import io
import os

import pytest
from pptx import Presentation

from app.agent import pipeline
from app.agent.loop import AgentLoop
from app.agent.tools import edit_deck_with_code as edit_tool


def _finished_state(path: str) -> dict:
    return {
        "has_called_finalize": True, "last_rendered_path": path,
        "brief": {"brief": "b"}, "scouted": True, "consulted_before_planning": True,
        "offered_directions": True, "reviewed_outline_with_user": True,
        "presentation_structure": {"totalSlides": 1, "slides": [{"title": "T"}]},
        "accumulated_slides": [{"slideIndex": 0}],
        "design_serial": 3, "rendered_serial": 3, "validated_serial": 3,
        "validation_clean": True, "validation_attempts": 2,
        "code_edit_log": [{"source": "x = 1", "intent": "earlier"}],
    }


class _Row:
    def __init__(self, state, updated_at):
        self.state, self.updated_at = state, updated_at


class _Message:
    def __init__(self, content, created_at):
        self.content, self.created_at = content, created_at


class _Result:
    def __init__(self, message):
        self.message = message

    def scalar_one_or_none(self):
        return self.message


class _Db:
    def __init__(self, row, message):
        self.row, self.message = row, message
        self.added = []

    async def get(self, model, key):
        return self.row

    async def execute(self, statement):
        return _Result(self.message)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def rollback(self):
        pass


@pytest.fixture
def deck(tmp_path):
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    presentation.slides.add_slide(presentation.slide_layouts[6])
    presentation.save(path)
    return str(path)


def _loop(db):
    loop = AgentLoop(db=db, mode=None)
    loop.questions_declined = True
    return loop


NOW = datetime.datetime(2026, 10, 2, 12, 0, tzinfo=datetime.timezone.utc)


def test_a_later_message_starts_an_amendment(deck):
    db = _Db(_Row(_finished_state(deck), NOW),
             _Message("Add a logo to every page", NOW + datetime.timedelta(minutes=5)))
    loop = _loop(db)
    assert asyncio.run(loop._load_deck_snapshot("sid")) is True
    ws = loop.working_state
    assert ws.amending and ws.amend_request == "Add a logo to every page"
    # What the last run concluded does not apply to the changed deck.
    assert not ws.validation_clean and ws.validation_attempts == 0
    assert ws.has_called_finalize and ws.render_is_current


def test_a_message_older_than_the_snapshot_is_not_an_amendment(deck):
    db = _Db(_Row(_finished_state(deck), NOW),
             _Message("make a deck", NOW - datetime.timedelta(minutes=5)))
    loop = _loop(db)
    assert asyncio.run(loop._load_deck_snapshot("sid")) is False
    assert not loop.working_state.amending


def test_a_deck_whose_file_is_gone_is_not_amended(tmp_path):
    db = _Db(_Row(_finished_state(str(tmp_path / "gone.pptx")), NOW),
             _Message("change it", NOW + datetime.timedelta(minutes=1)))
    loop = _loop(db)
    assert asyncio.run(loop._load_deck_snapshot("sid")) is False
    assert not loop.working_state.has_called_finalize


def test_an_amendment_goes_to_the_change_phase_then_validates(deck):
    db = _Db(_Row(_finished_state(deck), NOW),
             _Message("Add a logo", NOW + datetime.timedelta(minutes=1)))
    loop = _loop(db)
    asyncio.run(loop._load_deck_snapshot("sid"))
    phase = pipeline.next_phase(loop)
    assert phase.name == "change"
    assert phase.tools == frozenset({"edit_deck_with_code"})
    assert "Add a logo" in phase.directive(loop)
    loop.working_state.amend_done = True
    assert pipeline.next_phase(loop).name == "validate"


def test_an_amendment_does_not_research_again(deck):
    db = _Db(_Row(_finished_state(deck), NOW),
             _Message("Add a logo", NOW + datetime.timedelta(minutes=1)))
    loop = _loop(db)
    asyncio.run(loop._load_deck_snapshot("sid"))
    research = pipeline.PHASES_BY_NAME["research"]
    assert not research.applies(loop)


def test_a_snapshot_is_written_and_read_back(deck):
    db = _Db(None, None)
    loop = _loop(db)
    loop.working_state.has_called_finalize = True
    loop.working_state.last_rendered_path = deck
    loop.working_state.code_edit_log = [{"source": "pass", "intent": "i"}]
    asyncio.run(loop._save_deck_snapshot("sid"))
    saved = db.added[0].state
    assert saved["last_rendered_path"] == deck
    assert saved["code_edit_log"] == [{"source": "pass", "intent": "i"}]


ADD_BOX = '''
from pptx import Presentation
from pptx.util import Inches
p = Presentation("deck.pptx")
for s in p.slides:
    s.shapes.add_textbox(Inches(0.2), Inches(0.2), Inches(1), Inches(0.4)).text_frame.text = "LOGO"
p.save("deck.pptx")
'''


def _texts(path):
    return [shape.text_frame.text for slide in Presentation(path).slides
            for shape in slide.shapes if shape.has_text_frame]


def test_recorded_edits_are_put_back_after_a_render(deck):
    """A render rebuilds the file from the slides and drops the edit; replay
    puts it back, so a requested change survives a revision round."""
    assert "LOGO" not in _texts(deck)
    problems = edit_tool.replay(deck, [{"source": ADD_BOX, "intent": "logo"}])
    assert problems == []
    assert _texts(deck) == ["LOGO"]


def test_a_replayed_edit_that_fails_is_reported_and_the_rest_stand(deck):
    log = [{"source": "import os", "intent": "bad"},
           {"source": ADD_BOX, "intent": "logo"}]
    problems = edit_tool.replay(deck, log)
    assert len(problems) == 1 and problems[0].startswith("bad")
    assert _texts(deck) == ["LOGO"]


def test_previews_are_drawn_again_from_the_edited_file(deck, monkeypatch):
    from app.renderer import preview_generator

    seen = []

    def fake(self, path, session_id):
        seen.append((path, session_id))
        return ["/fresh/1.png"]

    monkeypatch.setattr(preview_generator.PreviewGenerator, "generate_previews", fake)
    loop = _loop(_Db(None, None))
    loop.working_state.last_rendered_path = deck
    loop.working_state.pending_slide_previews = [{"slideIndex": 0, "previewUrl": "/old/1.png"}]
    loop.working_state.pending_render_result = {"previewUrls": ["/old/1.png"]}
    asyncio.run(loop._refresh_previews("sid"))
    assert seen == [(deck, "sid")]
    assert loop.working_state.pending_slide_previews[0]["previewUrl"] == "/fresh/1.png"
    assert loop.working_state.pending_render_result["previewUrls"] == ["/fresh/1.png"]


def _png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buffer, "PNG")
    return buffer.getvalue()


ADD_PICTURE = '''
from pptx import Presentation
from pptx.util import Inches
p = Presentation("deck.pptx")
for s in p.slides:
    s.shapes.add_picture("picture_1.png", Inches(0.2), Inches(0.2), Inches(0.5), Inches(0.5))
p.save("deck.pptx")
'''


def _pictures(path):
    return sum(1 for slide in Presentation(path).slides
               for shape in slide.shapes if shape.shape_type == 13)


def test_a_script_can_place_a_picture_the_user_supplied(deck):
    import base64

    from app.agent import run_context

    loop = _loop(_Db(None, None))
    loop._install_assets([{"data": "data:image/png;base64," + base64.b64encode(_png()).decode(),
                           "context": "the client's logo"}])
    assert list(run_context.get_assets()) == ["picture_1.png"]
    assert "picture_1.png (the client's logo)" in loop.asset_directive()
    assert edit_tool.replay(deck, [{"source": ADD_PICTURE, "intent": "logo"}]) == []
    assert _pictures(deck) == 1


def test_without_pictures_the_model_is_told_to_draw_a_stand_in():
    loop = _loop(_Db(None, None))
    loop._install_assets([])
    assert "stand-in" in loop.asset_directive()


def test_an_amendment_sets_the_deck_path_for_the_tools(deck):
    """The path used to be set only where the deck was rendered, so a request
    arriving in a new process found no deck to edit."""
    from app.agent import run_context

    token = run_context.set_rendered_path(None)
    try:
        db = _Db(_Row(_finished_state(deck), NOW),
                 _Message("Add a logo", NOW + datetime.timedelta(minutes=1)))
        loop = _loop(db)
        asyncio.run(loop._load_deck_snapshot("sid"))
        # What `_run` does once the snapshot has made this an amendment.
        if loop.working_state.amending:
            run_context.set_rendered_path(loop.working_state.last_rendered_path)
        assert run_context.get_rendered_path() == deck
    finally:
        run_context.reset_rendered_path(token)


def test_more_pictures_than_the_sandbox_takes_are_not_offered():
    import base64

    from app.agent.sandbox import runner

    loop = _loop(_Db(None, None))
    entry = {"data": "data:image/png;base64," + base64.b64encode(_png()).decode()}
    loop._install_assets([dict(entry) for _ in range(runner.MAX_ASSETS + 5)])
    assert len(loop.asset_notes) == runner.MAX_ASSETS


def test_an_amendment_with_nothing_staged_publishes_the_edited_pages(deck, monkeypatch):
    from app.renderer import preview_generator

    monkeypatch.setattr(preview_generator.PreviewGenerator, "generate_previews",
                        lambda self, path, session_id: ["/a/1.png", "/a/2.png"])
    loop = _loop(_Db(None, None))
    loop.working_state.last_rendered_path = deck
    loop.working_state.previews_published = True
    asyncio.run(loop._refresh_previews("sid"))
    staged = loop.working_state.pending_slide_previews
    assert [p["previewUrl"] for p in staged] == ["/a/1.png", "/a/2.png"]
    assert not loop.working_state.previews_published


def test_a_failed_restore_leaves_a_clean_state(deck, monkeypatch):
    db = _Db(_Row(_finished_state(deck), NOW),
             _Message("change it", NOW + datetime.timedelta(minutes=1)))
    loop = _loop(db)
    loop.working_state.accumulated_slides = [{"half": "restored"}]
    monkeypatch.setattr(loop, "_apply_state_dict", lambda state, sid: False)
    assert asyncio.run(loop._load_deck_snapshot("sid")) is False
    assert loop.working_state.accumulated_slides == []
