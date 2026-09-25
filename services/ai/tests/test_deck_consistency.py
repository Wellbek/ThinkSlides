"""
A deck keeps one voice: every content page's headline is set on one rung.

Repair fits each page on its own, and a headline that overran its lines was
stepped down the ramp on that page and no other. A Kubernetes deck (#357)
set five of its seven content headlines at 31.6pt regular and two at 45pt
bold, so it read as assembled rather than designed.
"""

from app.agent import run_context
from app.agent.loop import AgentLoop
from app.design import run
from app.design.schema import TextBlock


def _text(line):
    return {"type": "text", "runs": [{"text": line}]}


def _headline_rungs(doc, roles=("content", "exhibit")):
    return [block.type_role for page in doc.pages if page.role in roles
            for frame in page.all_frames() if frame.id == page.provenance.heading_frame
            for block in frame.content if isinstance(block, TextBlock)]


def _deck():
    long_claim = ("A headline long enough that no arrangement in the pack can "
                  "set it on two lines at its full headline size, whatever the page")
    return [
        {"slideIndex": 0, "contents": [_text("Cover"), _text("A standfirst.")]},
        {"slideIndex": 1, "role": "content",
         "contents": [_text("Short claim"), _text("One paragraph of copy.")]},
        {"slideIndex": 2, "role": "content",
         "contents": [_text(long_claim), _text("One paragraph of copy.")]},
        {"slideIndex": 3, "contents": [_text("Close"), _text("Next steps.")]},
    ]


class TestOneHeadlineRung:
    def test_every_content_headline_shares_a_rung(self):
        for pack_id in run.available():
            doc = run.compose(_deck(), run.load(pack_id))
            assert len(set(_headline_rungs(doc))) == 1, pack_id

    def test_the_rung_is_the_smallest_any_page_needed(self):
        pack = run.load("bxsl-investor")
        doc = run.compose(_deck(), pack)
        (rung,) = set(_headline_rungs(doc))
        alone = run.compose([_deck()[2]], pack)
        assert pack.type_roles[rung].size_pt <= min(
            pack.type_roles[r].size_pt for r in _headline_rungs(alone, ("content",)) or [rung])

    def test_openers_keep_their_own_size(self):
        pack = run.load("bxsl-investor")
        doc = run.compose(_deck(), pack)
        cover = _headline_rungs(doc, ("cover",))
        assert cover and set(cover) != set(_headline_rungs(doc))


class TestDraftingIsToldHowLongAHeadlineCanBe:
    def test_every_shipped_pack_states_a_capacity(self):
        for pack_id in run.available():
            assert run.headline_capacity(run.load(pack_id)) >= 20, pack_id

    def test_the_directive_names_it(self):
        loop = AgentLoop(db=None)
        loop.working_state.presentation_structure = {
            "totalSlides": 2, "slides": [{"title": "Cover"}, {"title": "Claim"}]}
        pack = run.load("bxsl-investor")
        token = run_context.set_pack(pack)
        try:
            directive = loop.slide_directive()
        finally:
            run_context.reset_pack(token)
        assert f"fits at most about {run.headline_capacity(pack)} characters" in directive
