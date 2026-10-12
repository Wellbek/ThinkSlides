"""
A figure has a shape, and the page has to know it before it places one.

The selector already prefers a slot whose aspect suits an exhibit -
`aspect_penalty` has been there since the layout engine was written - and
nothing ever told it what shape anything was. So a 7:1 graph and a square
diagram were placed identically, and the cost of a wide figure squeezed into a
column was discovered at render, where nothing can act on it.

Three properties: a figure declares its shape before placement, a figure too
small to read is reported, and the repair loop has a move it can actually make.
"""
import pytest

from app.design import run
from app.design.repair import (
    MAX_ASPECT_DRIFT,
    MIN_FIGURE_SHARE,
    PATCHES,
    apply_patch,
    diagnose,
    repair,
)
from app.design.schema import PatchOp

WIDE = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 90">'
        '<rect x="1" y="1" width="600" height="80" fill="#333"/></svg>')
SQUARE = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">'
          '<rect x="1" y="1" width="180" height="180" fill="#333"/></svg>')


def _text(*lines):
    return {"type": "text", "runs": [{"text": line} for line in lines]}


def _deck(svg):
    return [{"slideIndex": 0, "role": "content", "contents": [
        _text("Where the exposure sits"),
        _text("Cyber remains the most cited near-term risk for insurers."),
        {"type": "diagram", "diagram": {"format": "svg", "svg": svg}},
    ]}]


@pytest.fixture(scope="module", params=run.available())
def pack(request):
    return run.load(request.param)


class TestAFigureDeclaresItsShapeBeforePlacement:
    def test_an_svg_says_what_shape_it_is(self):
        """A Mermaid graph is rendered to SVG *before* the page is composed
        for exactly this reason: a figure whose shape is unknown until it is
        drawn cannot be given a slot that suits it."""
        aspect = run.natural_aspect({"diagram": {"format": "svg", "svg": WIDE}})
        assert aspect == pytest.approx(640 / 90, rel=0.01)

    def test_a_square_figure_says_so(self):
        assert run.natural_aspect(
            {"diagram": {"format": "svg", "svg": SQUARE}}) == pytest.approx(1.0)

    def test_a_typed_diagram_declares_nothing_rather_than_guessing(self):
        assert run.natural_aspect({"diagram": {"format": "typed"}}) is None

    def test_an_unparseable_figure_declares_nothing(self):
        """The renderer refuses it by name later. Nothing is guessed here."""
        assert run.natural_aspect(
            {"diagram": {"format": "svg", "svg": "not svg"}}) is None

    def test_the_declared_shape_reaches_the_composed_page(self, pack):
        doc = run.compose(_deck(WIDE), pack)
        aspects = [
            item.aspect
            for page in doc.pages
            for frame in page.all_frames()
            for item in frame.content
            if getattr(item, "aspect", None)
        ]
        assert aspects
        assert aspects[0] == pytest.approx(640 / 90, rel=0.01)

    def test_the_selector_can_see_it(self, pack):
        """`aspect_penalty` was always there; nothing ever fed it."""
        from app.design.material import MaterialItem
        from app.design.select import aspect_penalty

        wide = MaterialItem(id="f", kind="diagram", aspect=7.1)
        square_slot = (4.0, 4.0)
        wide_slot = (8.0, 1.2)
        assert aspect_penalty(wide, square_slot) > aspect_penalty(wide, wide_slot)


def _settled_to_its_shape(doc, pack) -> bool:
    """Whether settling gave the wide graph a frame of its own 7:1 shape."""
    from app.design.geometry import resolve_page

    for page in doc.pages:
        for resolved in resolve_page(doc, page):
            if any(getattr(item, "exhibit_kind", "") == "diagram"
                   for item in resolved.frame.content):
                aspect = resolved.rect.width / max(resolved.rect.height, 1e-6)
                return abs(aspect - 640 / 90) / (640 / 90) < 0.1
    return False


class TestAFigureTooSmallOrTheWrongShapeIsReported:
    def test_one_of_the_two_fires_on_a_wide_graph(self, pack):
        """No pack has a slot shaped like a 7:1 graph, so the page is composed
        anyway and the figure is either tiny or badly shaped. Which of the two
        depends on the pack; that either fires is the point - unless the page
        was settled (#378), which cuts the graph's frame to the graph's own
        shape, so there is nothing left to report."""
        doc = run.compose(_deck(WIDE), pack)
        if _settled_to_its_shape(doc, pack):
            return
        found = {
            finding.property
            for page in doc.pages
            for finding in diagnose(doc, page, pack)
            if finding.property.startswith("figure")
        }
        assert found & {"figure_scale", "figure_aspect"}

    def test_a_finding_says_what_it_measured(self, pack):
        doc = run.compose(_deck(WIDE), pack)
        if _settled_to_its_shape(doc, pack):
            pytest.skip("this pack settles the graph into a frame its own shape")
        findings = [f for page in doc.pages
                    for f in diagnose(doc, page, pack)
                    if f.property.startswith("figure")]
        assert findings
        assert findings[0].observed and findings[0].expected
        assert findings[0].frame_id

    def test_the_thresholds_are_stated_rather_than_buried(self):
        assert 0 < MIN_FIGURE_SHARE < 0.5
        assert MAX_ASPECT_DRIFT >= 2.0


class TestTheRepairLoopHasAMoveItCanMake:
    def test_set_fit_is_in_the_closed_vocabulary(self):
        assert "set_fit" in PATCHES

    def test_it_letterboxes_rather_than_crops(self, pack):
        """Choosing a different slot is not a patch - it is a different page -
        so what is left is deciding whether the reader loses the edges of the
        figure or gains some white space, and white space is the lesser loss."""
        doc = run.compose(_deck(WIDE), pack)
        page = doc.pages[0]
        target = next((frame.id for frame in page.all_frames()
                       if frame.role == "figure" and frame.content), None)
        if target is None:
            pytest.skip("this pack put the figure in no figure frame")
        patched = apply_patch(page, PatchOp(
            op="set_fit", args={"frame_id": target, "fit": "contain"}))
        fits = [item.fit for frame in patched.all_frames()
                if frame.id == target for item in frame.content
                if hasattr(item, "fit")]
        assert fits == ["contain"]

    def test_an_aspect_finding_carries_that_patch(self, pack):
        doc = run.compose(_deck(WIDE), pack)
        for page in doc.pages:
            for finding in diagnose(doc, page, pack):
                if finding.property == "figure_aspect":
                    assert finding.patch is not None
                    assert finding.patch.op == "set_fit"
                    return

    def test_a_scale_finding_carries_no_invented_patch(self, pack):
        """Nothing can be done about a figure that is simply too small once
        the page is composed, and a patch pretending otherwise is the repair
        loop lying about what it fixed."""
        doc = run.compose(_deck(WIDE), pack)
        for page in doc.pages:
            for finding in diagnose(doc, page, pack):
                if finding.property == "figure_scale":
                    assert finding.patch is None

    def test_the_loop_still_only_ratchets_upward(self, pack):
        doc = run.compose(_deck(WIDE), pack)
        for page in doc.pages:
            result = repair(doc, page, pack)
            assert result.after <= result.before

    def test_a_figure_matching_its_slot_reports_nothing(self, pack):
        """Asserted against a page built for the purpose rather than a
        composed one: no pack is guaranteed to own a slot of any particular
        shape, and BXSL's only figure slot is 4.4:1 - so a square figure there
        genuinely *is* badly shaped, and a test expecting silence would be
        asserting that a real finding is a false positive."""
        from app.design.schema import (
            DesignDoc, Exhibit, FracPlace, Frame, Page, PackRef,
        )

        width = pack.canvas.w_in * 0.5
        height = pack.canvas.h_in * 0.5
        page = Page(id="p", archetype="test", frames=(
            Frame(id="fig", role="figure",
                  place=FracPlace(x=0.1, y=0.1, w=0.5, h=0.5),
                  content=(Exhibit(exhibit_kind="diagram", payload={},
                                   aspect=width / height),)),
        ))
        doc = DesignDoc(pack=PackRef(id=pack.id), canvas=pack.canvas,
                        grid=pack.grid, pages=(page,))
        assert not [f for f in diagnose(doc, page, pack)
                    if f.property == "figure_aspect"]

    def test_a_figure_far_from_its_slot_is_reported(self, pack):
        from app.design.schema import (
            DesignDoc, Exhibit, FracPlace, Frame, Page, PackRef,
        )

        page = Page(id="p", archetype="test", frames=(
            Frame(id="fig", role="figure",
                  place=FracPlace(x=0.1, y=0.1, w=0.5, h=0.5),
                  content=(Exhibit(exhibit_kind="diagram", payload={},
                                   aspect=20.0),)),
        ))
        doc = DesignDoc(pack=PackRef(id=pack.id), canvas=pack.canvas,
                        grid=pack.grid, pages=(page,))
        assert [f for f in diagnose(doc, page, pack)
                if f.property == "figure_aspect"]
