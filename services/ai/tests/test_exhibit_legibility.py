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
