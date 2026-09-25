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


def _authored_pack():
    from app.design import authoring
    from app.design.authored import to_pack
    from app.design.schema import Canvas
    from app.design.vocabulary import ensure
    from tests.test_author_run import WIDE

    (language,) = authoring.read({"languages": [WIDE]})
    return ensure(to_pack(language, Canvas(w_in=13.333, h_in=7.5)))


LONG = ("A headline long enough that no arrangement in the pack can set it on "
        "two lines at its full headline size, whatever the page it lands on")


def _series(headlines):
    return ([{"slideIndex": 0, "contents": [_text("Cover"), _text("A standfirst.")]}]
            + [{"slideIndex": i + 1, "role": "content",
                "contents": [_text(h), _text("One paragraph of copy.")]}
               for i, h in enumerate(headlines)]
            + [{"slideIndex": len(headlines) + 1,
                "contents": [_text("Close"), _text("Next steps.")]}])


class TestOneHeadlineRung:
    def test_a_stepped_down_headline_takes_its_siblings_with_it(self):
        pack = _authored_pack()
        doc = run.compose(_series(["Short claim", LONG, "Another short claim"]), pack)
        assert len(set(_headline_rungs(doc))) == 1

    def test_the_rung_is_one_repair_chose_and_never_body_size(self):
        pack = _authored_pack()
        doc = run.compose(_series(["Short claim", LONG]), pack)
        body = pack.type_roles["body"].size_pt
        assert all(pack.type_roles[r].size_pt > body for r in _headline_rungs(doc))

    def test_a_measured_pack_keeps_the_rungs_it_designed(self):
        """BXSL sets some assertions in a pull-quote and some at headline
        size; one long headline set as body copy used to drag every headline
        in the deck to body size."""
        pack = run.load("bxsl-investor")
        doc = run.compose(_series([LONG, "Short", "Also short", "Short again",
                                   "Brief", "Terse"]), pack)
        body = pack.type_roles["body"].size_pt
        sizes = [pack.type_roles[r].size_pt for r in _headline_rungs(doc)]
        assert sum(1 for size in sizes if size <= body) <= 1

    def test_openers_keep_their_own_size(self):
        pack = _authored_pack()
        doc = run.compose(_series(["Short claim", LONG]), pack)
        cover = _headline_rungs(doc, ("cover",))
        assert cover and set(cover) != set(_headline_rungs(doc))


class TestDraftingIsToldHowLongAHeadlineCanBe:
    def test_an_authored_pack_states_its_tightest_headline(self):
        assert run.headline_capacity(_authored_pack()) >= 20

    def test_a_measured_pack_with_label_sized_headlines_states_nothing_it_cannot_keep(self):
        for pack_id in run.available():
            capacity = run.headline_capacity(run.load(pack_id))
            assert capacity >= 0

    def _directive(self, revising: bool):
        loop = AgentLoop(db=None)
        ws = loop.working_state
        ws.presentation_structure = {
            "totalSlides": 2, "slides": [{"title": "Cover"}, {"title": "Claim"}]}
        if revising:
            ws.accumulated_slides = [{"slideIndex": 0}, {"slideIndex": 1}]
            ws.validation_issues = [{"slideIndex": 1, "message": "Overflow"}]
            ws.revision_queue = [1]
        pack = _authored_pack()
        token = run_context.set_pack(pack)
        try:
            return loop.slide_directive(), run.headline_capacity(pack)
        finally:
            run_context.reset_pack(token)

    def test_the_draft_directive_names_it(self):
        directive, capacity = self._directive(revising=False)
        assert f"fits about {capacity} characters" in directive

    def test_the_revision_directive_names_it_too(self):
        directive, capacity = self._directive(revising=True)
        assert directive.startswith("Revise slide 2")
        assert f"fits about {capacity} characters" in directive
