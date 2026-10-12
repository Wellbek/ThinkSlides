"""
The representation has to be able to hold a reference page.

The previous slide shape could not, and the single clearest proof was that
``regions.validate_regions`` rejected all sibling overlap - which forbids
type set over an image or a colour panel, the most common device in
publication layout. EY page 4, Microsoft's Work Trend Index and the
Blackstone deck all use it on the pages we hold up as the bar.

These tests pin the properties that make the difference, and the refusals
that keep the escape hatches honest.
"""
import pytest

from app.design.geometry import check_page, content_box, resolve_page
from app.design.schema import (
    Canvas,
    DesignDoc,
    Figure,
    Flow,
    FracPlace,
    Frame,
    GridPlace,
    GridSpec,
    Insets,
    Page,
    PackRef,
    Paragraph,
    Run,
    TextBlock,
)

CANVAS = Canvas(w_in=13.333, h_in=7.5)
GRID = GridSpec(
    columns=12, gutter_in=0.15,
    margin=Insets(top=0.5, right=0.6, bottom=0.5, left=0.6),
    baseline_in=0.16,
)


def _doc(frames, flows=(), grid=GRID) -> DesignDoc:
    return DesignDoc(
        pack=PackRef(id="test"), canvas=CANVAS, grid=grid,
        pages=(Page(id="p", archetype="a", frames=tuple(frames),
                    flows=tuple(flows)),),
    )


def _frame(fid, place, **kw):
    return Frame(id=fid, role=kw.pop("role", "panel"), place=place, **kw)


class TestTypeOverAnImageIsExpressible:
    """The device the previous representation forbade outright."""

    def test_overlap_at_different_z_is_allowed(self):
        doc = _doc([
            _frame("image", FracPlace(x=0, y=0, w=1, h=1), role="figure", z=0,
                   bleed=("top", "right", "bottom", "left")),
            _frame("headline", FracPlace(x=0.1, y=0.4, w=0.5, h=0.2),
                   role="headline", z=1),
        ])
        assert check_page(doc, doc.pages[0]) == []

    def test_overlap_at_the_same_z_is_still_refused(self):
        """Overlap with no declared order is ambiguity, not intent."""
        doc = _doc([
            _frame("a", FracPlace(x=0, y=0, w=1, h=1), z=0),
            _frame("b", FracPlace(x=0.1, y=0.4, w=0.5, h=0.2), z=0),
        ])
        problems = check_page(doc, doc.pages[0])
        assert problems and "same z" in problems[0]

    def test_a_hairline_of_shared_edge_is_not_an_overlap(self):
        """Fractional geometry rounds; two halves of a split share an edge."""
        doc = _doc([
            _frame("left", FracPlace(x=0.0, y=0, w=0.5, h=1), z=0),
            _frame("right", FracPlace(x=0.4999, y=0, w=0.5, h=1), z=0),
        ])
        assert check_page(doc, doc.pages[0]) == []


class TestBleedReachesTheTrim:
    def test_a_bleeding_frame_runs_to_the_canvas_edge(self):
        doc = _doc([
            _frame("rail", GridPlace(col=9, span=3),
                   role="rail", bleed=("top", "right", "bottom")),
        ])
        rail = resolve_page(doc, doc.pages[0])[0]
        assert rail.rect.top == pytest.approx(0.0, abs=1e-6)
        assert rail.right == pytest.approx(CANVAS.w_in, abs=1e-6)
        assert rail.bottom == pytest.approx(CANVAS.h_in, abs=1e-6)
        # ...and its left edge is still on the grid, which is the point:
        # a bleed is a grid column that runs off the page, not a free rectangle.
        box = content_box(CANVAS, GRID)
        col_w = GRID.column_width_in(CANVAS)
        assert rail.rect.left == pytest.approx(
            box.left + 9 * (col_w + GRID.gutter_in), abs=1e-6
        )

    def test_escaping_the_canvas_without_declaring_bleed_is_an_error(self):
        doc = _doc([_frame("stray", FracPlace(x=0.9, y=0, w=0.5, h=1))])
        problems = check_page(doc, doc.pages[0])
        assert problems and "without declaring a bleed" in problems[0]


class TestTheGridIsAPropertyOfThePage:
    """A grid frame lines up with the grid at any depth, not with its parent.

    This is what makes alignment survive nesting: a caption inside one panel
    lines up with a chart inside another, because both asked the page for
    column 4 rather than asking their own container for a fraction.
    """

    def test_a_nested_grid_frame_aligns_with_a_top_level_one(self):
        doc = _doc([
            _frame("top", GridPlace(col=4, span=2, y=0.0, h=0.3)),
            _frame("outer", FracPlace(x=0.05, y=0.5, w=0.9, h=0.4), children=(
                _frame("nested", GridPlace(col=4, span=2, y=0.5, h=0.3)),
            )),
        ])
        placed = {f.id: f for f in resolve_page(doc, doc.pages[0])}
        assert placed["nested"].rect.left == pytest.approx(
            placed["top"].rect.left, abs=1e-9
        )
        assert placed["nested"].rect.width == pytest.approx(
            placed["top"].rect.width, abs=1e-9
        )

    def test_a_fraction_frame_is_relative_to_its_parent(self):
        doc = _doc([
            _frame("outer", FracPlace(x=0.5, y=0.0, w=0.5, h=1.0), children=(
                _frame("inner", FracPlace(x=0.5, y=0.0, w=0.5, h=1.0)),
            )),
        ])
        placed = {f.id: f for f in resolve_page(doc, doc.pages[0])}
        assert placed["inner"].rect.left == pytest.approx(
            CANVAS.w_in * 0.75, abs=1e-6
        )

    def test_columns_beyond_the_grid_are_refused(self):
        from app.design.geometry import GeometryError

        doc = _doc([_frame("wide", GridPlace(col=10, span=6))])
        with pytest.raises(GeometryError):
            resolve_page(doc, doc.pages[0])


class TestPaintOrderIsDeterministic:
    def test_frames_paint_by_z_then_document_order(self):
        doc = _doc([
            _frame("c", FracPlace(x=0, y=0, w=0.1, h=0.1), z=2),
            _frame("a", FracPlace(x=0.2, y=0, w=0.1, h=0.1), z=0),
            _frame("b", FracPlace(x=0.4, y=0, w=0.1, h=0.1), z=0),
        ])
        assert [f.id for f in resolve_page(doc, doc.pages[0])] == ["a", "b", "c"]


class TestTheDocumentRefusesIncoherence:
    def test_a_flow_naming_an_absent_frame_is_refused(self):
        with pytest.raises(ValueError):
            Page(id="p", archetype="a",
                 frames=(_frame("only", FracPlace(x=0, y=0, w=1, h=1)),),
                 flows=(Flow(id="story", frame_ids=("only", "missing")),))

    def test_a_repeated_frame_id_is_refused(self):
        with pytest.raises(ValueError):
            Page(id="p", archetype="a", frames=(
                _frame("dup", FracPlace(x=0, y=0, w=0.4, h=1)),
                _frame("dup", FracPlace(x=0.5, y=0, w=0.4, h=1)),
            ))

    def test_an_unknown_frame_role_is_refused(self):
        with pytest.raises(ValueError):
            Frame(id="x", role="decoration", place=FracPlace(x=0, y=0, w=1, h=1))

    def test_an_unknown_field_is_refused(self):
        """A misspelled key that silently does nothing is how the previous
        representation drifted from the three places that read it."""
        with pytest.raises(ValueError):
            TextBlock(type_role="body", paragrpahs=())


class TestContentBelongsToItsFrame:
    def test_content_hangs_off_the_frame_that_holds_it(self):
        frame = _frame(
            "body", GridPlace(col=0, span=4),
            content=(TextBlock(type_role="body",
                               paragraphs=(Paragraph(runs=(Run(text="hello"),)),)),),
        )
        assert frame.content[0].text == "hello"

    def test_a_figure_declares_how_it_fits_its_slot(self):
        figure = Figure(source={"source": "base64", "data": ""}, fit="cover",
                        focal_x=0.5, focal_y=0.2)
        assert figure.fit == "cover"
        assert figure.aspect_lock is True
