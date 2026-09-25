"""
An exhibit gets a slot it can be read in, inside the page's margins.

The Kubernetes run (#351) composed onto an authored language whose exhibit
slots were never tall enough for a table: its stacked arrangements shrank a
figure band to a fifth of an inch, the selector charged that less than the
text overflow of every alternative, and a five-row table was drawn as a
sliver with its rows printed over each other. The language's figure slot also
bled off the page, so each diagram and table was set hard against its edge.
"""

from app.design import authored, run, select
from app.design.bind import _fill as bind_fill
from app.design.material import MaterialItem
from app.design.schema import Exhibit, Figure, Frame, FracPlace

ROWS = [["Dimension", "Rolling", "Blue-green", "Canary"],
        *[[f"Row {i}", "Incremental pod replacement",
           "Single Service/ingress cutover", "Ingress-weighted steps"]
          for i in range(5)]]
TABLE = MaterialItem(id="exhibit-0", kind="table",
                     payload={"table": {"headerRow": True, "rows": ROWS}})


class TestAnExhibitIsReadableWhereItIsPut:
    def test_a_table_is_refused_a_slot_far_shorter_than_it_needs(self):
        pack = run.load("bxsl-investor")
        assert select.legibility_shortfall(TABLE, pack, (11.7, 0.4)) > \
            select.LEGIBLE_TOLERANCE
        assert select.legibility_shortfall(TABLE, pack, (11.7, 4.0)) == 0.0

    def test_the_floor_is_measured_at_the_slot_width(self):
        pack = run.load("bxsl-investor")
        assert select.exhibit_floor_in(TABLE, pack, 5.0) > \
            select.exhibit_floor_in(TABLE, pack, 11.0)

    def test_prose_is_not_an_exhibit(self):
        pack = run.load("bxsl-investor")
        prose = MaterialItem(id="text-0", kind="text", text="A paragraph.")
        assert select.legibility_shortfall(prose, pack, (1.0, 0.1)) == 0.0


class TestAnOverfullPageShrinksItsProseFirst:
    def test_a_figure_keeps_its_height_while_prose_can_give(self):
        # heading, figure, prose, prose - wanting more than the page.
        natural = [0.2, 0.34, 0.3, 0.3]
        heights = authored._fill(natural, [False, True, True, True], 0.01,
                                 protected=[False, True, False, False])
        assert abs(heights[1] - 0.34) < 1e-9
        assert sum(heights) + 0.03 <= 1.0 + 1e-9

    def test_without_a_figure_everything_shrinks_as_before(self):
        natural = [0.2, 0.5, 0.5]
        guarded = authored._fill(natural, [False, True, True], 0.01,
                                 protected=[False, False, False])
        assert guarded == authored._fill(natural, [False, True, True], 0.01)


class TestAnExhibitStaysInsideTheMargins:
    @staticmethod
    def _frame(content):
        return Frame(id="figure-0", role="figure",
                     place=FracPlace(x=0.0, y=0.2, w=0.5, h=0.5),
                     bleed=("left",), content=(content,))

    def test_a_table_in_a_bleeding_frame_does_not_bleed(self):
        pack = run.load("bxsl-investor")
        frame = self._frame(Exhibit(exhibit_kind="table", payload={}))
        item = TABLE
        (bound,) = bind_fill((frame,), {"figure-0": [item]}, pack)
        assert bound.bleed == ()

    def test_a_photograph_still_bleeds(self):
        pack = run.load("bxsl-investor")
        photo = MaterialItem(id="figure-0", kind="figure",
                             payload={"source": {"type": "url", "url": "x"}})
        frame = self._frame(Figure(source={"type": "url", "url": "x"}))
        (bound,) = bind_fill((frame,), {"figure-0": [photo]}, pack)
        assert bound.bleed == ("left",)


class TestAPageWithNoReadableSlotIsStillPlaced:
    def test_a_lone_table_too_tall_for_every_slot_is_placed_cramped(self):
        """Refusing it would send the whole deck to the stock renderer."""
        rows = [["Step", "Detail"], *[[f"Step {i}", "A long detail " * 6]
                                      for i in range(40)]]
        slide = {"slideIndex": 1, "contents": [
            {"type": "text", "runs": [{"text": "Every step"}]},
            {"type": "table", "table": {"headerRow": True, "rows": rows}}]}
        cover = {"slideIndex": 0, "contents": [
            {"type": "text", "runs": [{"text": "Cover"}]}]}
        doc = run.compose([cover, slide], run.load("bxsl-investor"))
        assert any(p.provenance.source_slide == 1 for p in doc.pages)

    def test_a_page_is_not_split_for_an_exhibit_no_slot_could_hold(self):
        """Legibility depends on the slot, not the prose beside it, so
        splitting the prose only multiplied the page."""
        rows = [["Step", "Detail"], *[[f"Step {i}", "A long detail " * 6]
                                      for i in range(40)]]
        paragraphs = [{"type": "text", "runs": [{"text": f"Paragraph {i} of the "
                                                  "argument, a sentence long."}]}
                      for i in range(8)]
        slide = {"slideIndex": 1, "contents": [
            {"type": "text", "runs": [{"text": "Every step"}]}, *paragraphs,
            {"type": "table", "table": {"headerRow": True, "rows": rows}}]}
        cover = {"slideIndex": 0, "contents": [
            {"type": "text", "runs": [{"text": "Cover"}]}]}
        doc = run.compose([cover, slide], run.load("bxsl-investor"))
        pages = [p for p in doc.pages if p.provenance.source_slide == 1]
        assert len(pages) == 1


class TestOnePageNeverTakesTheDeckDown:
    """#322: a page no arrangement of the pack can carry is set on the pack's
    plain page, in its own grid, type and colours, and every other page keeps
    its arrangement."""

    def test_the_plain_page_is_sound_geometry_in_every_shipped_pack(self):
        from app.design.geometry import check_page
        from app.design.schema import DesignDoc, Page, PackRef
        from app.design.vocabulary import plain_content

        for pack_id in run.available():
            pack = run.load(pack_id)
            plain = plain_content(pack)
            doc = DesignDoc(pack=PackRef(id=pack.id, version=pack.version),
                            canvas=pack.canvas, grid=pack.grid, pages=())
            page = Page(id="p", archetype=plain.id, frames=plain.frames)
            assert check_page(doc, page) == [], pack_id

    def test_a_re_placement_that_finds_nothing_keeps_the_page_designed(self):
        """Avoiding the only arrangement that held a page must not downgrade
        it to the plain page: placing it as before beats that."""
        from app.design.vocabulary import PLAIN_ID

        pack = run.load("bxsl-investor")

        def text(line):
            return {"type": "text", "runs": [{"text": line}]}

        slides = [{"slideIndex": 0, "contents": [text("Cover")]},
                  {"slideIndex": 1, "role": "content",
                   "contents": [text("A finding"), text("One paragraph.")]},
                  {"slideIndex": 2, "contents": [text("Close")]}]
        held = run.compose(slides, pack).pages[1].archetype
        original = next(a for a in pack.archetypes if a.id == held)
        # Another arrangement is left once the one that held the page is
        # avoided, but it takes nothing except figures, so it refuses a page
        # of prose - the case where the avoid set leaves candidates, none of
        # which can hold the page.
        blocker = original.model_copy(update={"id": "blocker", "slots": tuple(
            slot.model_copy(update={"accepts": ("figure",), "required": True})
            for slot in original.slots)})
        narrowed = pack.model_copy(update={"archetypes": tuple(
            a for a in pack.archetypes
            if a.role not in ("content", "exhibit") or a.id == held) + (blocker,)})
        again = run.compose(slides, narrowed, avoid={1: frozenset({held})})
        assert again.pages[1].archetype == held != PLAIN_ID

    def test_it_is_never_offered_alongside_the_packs_own_pages(self):
        from app.design.vocabulary import PLAIN_ID

        for pack_id in run.available():
            assert PLAIN_ID not in {a.id for a in run.load(pack_id).archetypes}


class TestATableIsSetAtALegibleSize:
    """#357: a table's floor is an exact measurement, so it gets no
    tolerance; with the 25% the estimated floors get, half-width tables came
    out at 8 and 9pt."""

    def test_a_table_has_no_tolerance_and_a_chart_keeps_its(self):
        chart = MaterialItem(id="c", kind="chart", payload={"chart": {}})
        assert select.legible_tolerance(TABLE) == 0.0
        assert select.legible_tolerance(chart) == select.LEGIBLE_TOLERANCE

    def test_a_table_no_arrangement_can_hold_goes_on_the_plain_page(self):
        """Before being set cramped - where a six-column table came out at
        8pt and ran into the prose below it."""
        from app.design import authoring
        from app.design.authored import to_pack
        from app.design.schema import Canvas, Exhibit
        from app.design.vocabulary import PLAIN_ID, ensure
        from tests.test_author_run import CRAMPED

        (language,) = authoring.read({"languages": [CRAMPED]})
        pack = ensure(to_pack(language, Canvas(w_in=13.333, h_in=7.5)))
        slides = [{"slideIndex": 0, "contents": [
                       {"type": "text", "runs": [{"text": "Cover"}]}]},
                  {"slideIndex": 1, "role": "content", "contents": [
                       {"type": "text", "runs": [{"text": "A comparison"}]},
                       {"type": "table", "table": {"headerRow": True, "rows": ROWS}}]},
                  {"slideIndex": 2, "contents": [
                       {"type": "text", "runs": [{"text": "Close"}]}]}]
        from app.design.geometry import resolve_page

        doc = run.compose(slides, pack)
        page = doc.pages[1]
        assert page.archetype == PLAIN_ID
        table_frame = next(f for f in page.frames
                           if any(isinstance(c, Exhibit) for c in f.content))
        rect = {r.id: r for r in resolve_page(doc, page)}[table_frame.id].rect
        assert select.legibility_shortfall(TABLE, pack, (rect.width, rect.height)) == 0.0

    def test_prose_is_split_off_so_the_table_can_have_the_plain_page(self):
        """With more prose than the plain page's column holds, the page used
        to go straight to a cramped slot (a 0.33 shortfall reproduced)."""
        from app.design import authoring
        from app.design.authored import to_pack
        from app.design.schema import Canvas, Exhibit
        from app.design.vocabulary import PLAIN_ID, ensure
        from tests.test_author_run import CRAMPED

        (language,) = authoring.read({"languages": [CRAMPED]})
        pack = ensure(to_pack(language, Canvas(w_in=13.333, h_in=7.5)))
        paragraph = ("A long paragraph of argument that a page carries beside "
                     "its table, written out at the length a real draft reaches. ") * 12
        slides = [{"slideIndex": 0, "contents": [
                       {"type": "text", "runs": [{"text": "Cover"}]}]},
                  {"slideIndex": 1, "role": "content", "contents": [
                       {"type": "text", "runs": [{"text": "A comparison"}]},
                       *[{"type": "text", "runs": [{"text": paragraph}]}
                         for _ in range(3)],
                       {"type": "table", "table": {"headerRow": True, "rows": ROWS}}]},
                  {"slideIndex": 2, "contents": [
                       {"type": "text", "runs": [{"text": "Close"}]}]}]
        doc = run.compose(slides, pack)
        (page,) = [p for p in doc.pages
                   if any(isinstance(c, Exhibit) for f in p.frames for c in f.content)]
        assert page.archetype == PLAIN_ID

    def test_a_revision_that_avoids_the_plain_page_is_obeyed(self):
        from app.design import authoring
        from app.design.authored import to_pack
        from app.design.schema import Canvas
        from app.design.vocabulary import PLAIN_ID, ensure
        from tests.test_author_run import CRAMPED

        (language,) = authoring.read({"languages": [CRAMPED]})
        pack = ensure(to_pack(language, Canvas(w_in=13.333, h_in=7.5)))
        slides = [{"slideIndex": 0, "contents": [
                       {"type": "text", "runs": [{"text": "Cover"}]}]},
                  {"slideIndex": 1, "role": "content", "contents": [
                       {"type": "text", "runs": [{"text": "A comparison"}]},
                       {"type": "table", "table": {"headerRow": True, "rows": ROWS}}]},
                  {"slideIndex": 2, "contents": [
                       {"type": "text", "runs": [{"text": "Close"}]}]}]
        doc = run.compose(slides, pack, avoid={1: frozenset({PLAIN_ID})})
        assert all(p.archetype != PLAIN_ID for p in doc.pages
                   if p.provenance.source_slide == 1)
