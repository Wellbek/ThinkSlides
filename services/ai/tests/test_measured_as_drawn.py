"""
Copy is measured as it is drawn (#361).

Every question about whether copy fits - a slot's `max_chars`, the selector's
strain, repair's overflow findings, the writer's per-field budget - has to
ask about the text the renderer will draw: in the slot's own face, at its
weight and case, in the rung the frame is actually set in, and with the
lines broken where words break. Each of those was left out somewhere.
"""
import pytest

from app.design import run as design_run
from app.design.authored import Language, PageKind, Slot, to_pack
from app.design.pack import TypeRole
from app.design.schema import Canvas, TextBlock
from app.design.select import _slot_rect
from app.design.setting import (SAMPLE_PROSE, as_drawn, capacity, set_copy,
                                 set_in_face)
from app.design.vocabulary import ensure
from app.renderer.typeset.shape import wrap

CANVAS = Canvas(w_in=13.333, h_in=7.5)

# Prose that is not the sample, with longer words than it has on average:
# a capacity that only holds for the sentence it was measured with is no
# capacity at all.
OTHER_PROSE = (
    "Rollouts replace pods gradually so the service keeps answering while "
    "the new version proves itself under real traffic, and any regression "
    "is caught before it reaches every user of the cluster. "
) * 40

FACE_PAIRS = [("Arial", "Arial"), ("Georgia", "Verdana"),
              ("Verdana", "Georgia")]


def _language(display: str, body: str) -> Language:
    return Language(
        name="Probe", columns=6, body_pt=14.0,
        display_face=display, body_face=body,
        pages=(
            PageKind(name="cover", role="cover", slots=(
                Slot(kind="heading", span=5, required=True),
                Slot(kind="standfirst", span=4))),
            PageKind(name="body", role="content", slots=(
                Slot(kind="heading", span=6, required=True),
                Slot(kind="prose", span=3), Slot(kind="figure", span=3))),
            PageKind(name="stats", role="content", slots=(
                Slot(kind="heading", span=6, required=True),
                Slot(kind="statistic", span=2), Slot(kind="statistic", span=2),
                Slot(kind="statistic", span=2), Slot(kind="prose", span=6))),
        ),
    )


def _text_slots(pack):
    for archetype in pack.archetypes:
        frames = {f.id: f for f in archetype.frames}
        for slot in archetype.slots:
            frame = frames[slot.frame_id]
            if "text" in slot.accepts and frame.type_role and slot.max_chars:
                yield archetype, slot, frame


class TestCapacityIsSetNotEstimated:
    BODY = TypeRole(size_pt=14.0, line_height_pt=19.6)
    HEADLINE = TypeRole(size_pt=33.6, line_height_pt=38.6, weight="bold",
                        face="display")

    def test_a_wider_face_holds_less(self):
        assert (capacity(self.BODY, "Verdana", 300.0, 8)
                < capacity(self.BODY, "Arial Narrow", 300.0, 8))

    def test_bold_holds_less_than_regular(self):
        regular = self.HEADLINE.model_copy(update={"weight": "regular"})
        assert (capacity(self.HEADLINE, "Arial", 700.0, 2)
                < capacity(regular, "Arial", 700.0, 2))

    def test_capitals_hold_less(self):
        upper = self.BODY.model_copy(update={"case": "upper"})
        assert capacity(upper, "Arial", 300.0, 4) < capacity(self.BODY, "Arial", 300.0, 4)
        assert as_drawn("Small caps", upper.model_copy(update={"case": "small"})) == "SMALL CAPS"

    def test_tracking_holds_less(self):
        tracked = self.BODY.model_copy(update={"tracking_per_mille": 100})
        assert capacity(tracked, "Arial", 300.0, 4) < capacity(self.BODY, "Arial", 300.0, 4)

    @pytest.mark.parametrize("face", ["Arial", "Georgia", "Verdana"])
    @pytest.mark.parametrize("width, lines", [(160.0, 6), (420.0, 2), (700.0, 3)])
    def test_the_sample_at_capacity_fits_its_lines(self, face, width, lines):
        """Word-wrap loss is in the number: copy as long as the capacity
        never needs more lines than the capacity was measured over."""
        held = capacity(self.HEADLINE, face, width, lines)
        text = (SAMPLE_PROSE * 4)[:held].rsplit(" ", 1)[0]
        set_lines = wrap(text, width, face, self.HEADLINE.size_pt, bold=True)
        assert len(set_lines) <= lines

    def test_set_copy_draws_what_compile_draws(self):
        """Bold and capitals are part of the measurement, not left to the
        renderer to discover."""
        upper_bold = self.BODY.model_copy(update={"weight": "bold", "case": "upper"})
        text = "Measured as drawn, not as written"
        plain = set_in_face(text, self.BODY, "Arial", 2000.0, 100.0)
        drawn = set_in_face(text, upper_bold, "Arial", 2000.0, 100.0)
        assert drawn.lines[0].width_pt > plain.lines[0].width_pt
        assert drawn.lines[0].text == text.upper()


    def test_paragraph_spacing_is_part_of_the_depth(self):
        """`compile` spaces every paragraph by its role's space before and
        after; a measurement without it reports a block that fits and draws
        one that overruns."""
        spaced = self.BODY.model_copy(update={"space_before": 2, "space_after": 2})
        one = set_in_face("A paragraph.", spaced, "Arial", 400.0, 0.0,
                          paragraph_gap_pt=12.0)
        three = set_in_face("A paragraph.\nAnother.\nA third.", spaced, "Arial",
                            400.0, 0.0, paragraph_gap_pt=12.0)
        assert three.height_pt == pytest.approx(
            one.height_pt + 2 * self.BODY.line_height_pt + 2 * 12.0)

    def test_a_long_slot_is_measured_quickly(self):
        """Setting stops once the slot's lines are full: a pack build asks
        this of every traced block at widths that rarely repeat."""
        import time

        from app.design.setting import _capacity

        caption = TypeRole(size_pt=9.0, line_height_pt=11.0)
        capacity(caption, "Arial", 100.0, 1)    # load the font first
        _capacity.cache_clear()
        started = time.perf_counter()
        capacity(caption, "Arial", 911.0, 60)
        assert time.perf_counter() - started < 0.4


class TestAnAuthoredSlotHoldsWhatItAdvertises:
    @pytest.mark.parametrize("display, body", FACE_PAIRS)
    def test_copy_of_max_chars_fits_every_slot(self, display, body):
        """#361: measured in Arial whatever the face, a cover advertised an
        eighty-character title in a band one display line tall, and every
        headline slot about half a line more than it held."""
        pack = ensure(to_pack(_language(display, body), CANVAS))
        for archetype, slot, frame in _text_slots(pack):
            role = pack.type_roles[frame.type_role]
            width_in, height_in = _slot_rect(archetype, slot, pack, pack.grid)
            text = OTHER_PROSE[:slot.max_chars].rsplit(" ", 1)[0]
            setting = set_copy(text, role, pack, width_in * 72.0,
                               height_in * 72.0)
            # One line of slack for prose that is not the sample: a capacity
            # is a measure of typical copy, not a guarantee for every string.
            assert setting.height_pt <= height_in * 72.0 + role.line_height_pt, (
                f"{display}/{body} {archetype.id} {frame.id}: "
                f"{slot.max_chars} chars need {setting.height_pt:.0f}pt "
                f"of a {height_in * 72:.0f}pt frame")

    def test_the_face_moves_the_capacity(self):
        """The same language in a wider body face holds less prose."""
        narrow = ensure(to_pack(_language("Arial", "Arial Narrow"), CANVAS))
        wide = ensure(to_pack(_language("Arial", "Verdana"), CANVAS))

        def prose(pack):
            return next(s.max_chars for a in pack.archetypes if a.id == "body"
                        for s in a.slots if s.frame_id.endswith("prose-1"))

        assert prose(wide) < prose(narrow)

    def test_an_openers_heading_is_sized_in_the_rung_it_is_drawn_in(self):
        """It was drawn in display type and its band and capacity worked out
        in headline type, a third smaller."""
        pack = ensure(to_pack(_language("Georgia", "Arial"), CANVAS))
        cover = pack.archetype("cover")
        heading = next(f for f in cover.frames if f.role == "headline")
        assert heading.type_role == "display"
        display = pack.type_roles["display"]
        _, height_in = _slot_rect(cover, cover.slots[0], pack, pack.grid)
        # A heading slot is built for two lines (`LINES_FOR_SLOT`), and they
        # are two lines of the rung it is drawn in.
        assert height_in * 72.0 >= 2 * display.line_height_pt - 0.5


class TestAnOpenersTitleRestsOnWhatFollows:
    def test_a_display_title_is_anchored_to_the_bottom_of_its_band(self):
        """A one-line title hung from the top of a two-line band left an
        empty line between itself and its standfirst."""
        pack = ensure(to_pack(_language("Georgia", "Arial"), CANVAS))
        doc = design_run.compose([
            {"slideIndex": 0, "role": "cover", "heading": "The year ahead",
             "subheading": "Where the next phase of growth comes from"},
        ], pack, skip_unplaceable=True)
        blocks = [b for f in doc.pages[0].frames for b in f.content
                  if isinstance(b, TextBlock)]
        title = next(b for b in blocks if b.type_role == "display")
        assert title.anchor == "bottom"
        assert all(b.anchor == "top" for b in blocks if b is not title)


class TestASurvivorIsCutToItsCopy:
    """A prose cell beside a figure is as tall as the figure. Dropping the
    figure left the prose at that height, so the hole moved inside it and
    the next row sat a figure's height further down."""

    @staticmethod
    def _pack():
        return ensure(to_pack(Language(
            name="Flow", rationale="a probe", columns=6, body_pt=18.0,
            pages=(PageKind(name="flow", role="content", frequency=1.0, slots=(
                Slot(kind="heading", span=6, required=True),
                Slot(kind="prose", span=2, name="before"),
                Slot(kind="figure", span=4),
                Slot(kind="prose", span=6, name="after"))),),
        ), CANVAS))

    def _page(self, pack):
        from app.design.bind import build_page
        from app.design.material import shape_from
        from app.design.select import fit

        shape = shape_from("content", "A heading", [
            "One point that runs a reasonable length.",
            "A second point of about the same size."])
        selection = fit(shape, pack.archetype("flow"), pack)
        assert selection is not None
        return build_page("p1", shape, selection, pack)

    def test_the_next_row_follows_the_copy_not_the_lost_figure(self):
        pack = self._pack()
        archetype = pack.archetype("flow")
        figure = next(f for f in archetype.frames if f.role == "figure")
        page = self._page(pack)
        columns = sorted((f for f in page.frames if f.role == "column"),
                         key=lambda f: f.place.y)
        assert len(columns) == 2
        first, second = columns
        # The first column's copy is two short lines at 18pt; the figure's
        # band was far taller than that.
        assert first.place.h < figure.place.h / 2
        assert second.place.y < figure.place.y + figure.place.h / 2

    def test_a_page_that_kept_every_frame_is_untouched(self):
        """Only a row that lost a member is cut down."""
        from app.design.bind import _compact

        pack = self._pack()
        frames = pack.archetype("flow").frames
        assert _compact(frames, frames, pack, pack.grid) == frames


class TestAMeasuredPacksSlotIsMeasuredInItsOwnFace:
    def test_a_headline_slot_is_measured_in_the_display_face_and_weight(self):
        """Every slot of a pack recovered from a PDF was measured in the body
        face at regular weight - a headline drawn bold in the display face
        included."""
        from app.design.pack_build import Faces, _capacity_chars
        from app.measure.type_roles import RungEvidence

        roles = {"headline": RungEvidence(size_pt=28.0, fonts={"Face-Bold": 1}),
                 "body": RungEvidence(size_pt=12.0, fonts={"Face": 1})}
        block = {"x": 0.1, "y": 0.1, "w": 0.6, "h": 0.15, "chars": 40}
        page = (960.0, 540.0)

        def held(faces):
            return _capacity_chars(block, "headline", roles, faces, *page)

        wide_display = held(Faces(body="Arial Narrow", display="Verdana"))
        narrow_display = held(Faces(body="Verdana", display="Arial Narrow"))
        assert wide_display < narrow_display


class TestAShippedPackIsMeasuredFromItsFrames:
    @pytest.fixture(params=design_run.available())
    def shipped(self, request):
        import json
        import os

        with open(os.path.join(design_run.PACK_DIR, f"{request.param}.json")) as f:
            raw = json.load(f)
        return raw, design_run.load(request.param)

    def test_copy_of_max_chars_fits_every_traced_slot(self, shipped):
        """Its capacities were traced before its frames were snapped, as
        characters-per-line times lines; loaded, they describe the frames."""
        _, pack = shipped
        for archetype, slot, frame in _text_slots(pack):
            from app.design.roles import type_role_for

            role = pack.type_roles.get(type_role_for(frame.role, "text", pack,
                                                     frame.type_role))
            size = _slot_rect(archetype, slot, pack, archetype.grid or pack.grid)
            if role is None or size is None:
                continue
            width_in, height_in = size
            text = OTHER_PROSE[:slot.max_chars].rsplit(" ", 1)[0] or "x"
            setting = set_copy(text, role, pack, width_in * 72.0, height_in * 72.0)
            assert setting.height_pt <= height_in * 72.0 + role.line_height_pt, (
                f"{pack.id}/{archetype.id}/{frame.id}")

    def test_the_traced_volume_is_kept_as_the_habit(self, shipped):
        """The old number was also what the page carried, which is what the
        density term reads; it moves to `typical_chars` unchanged."""
        raw, pack = shipped
        for entry in raw["archetypes"]:
            loaded = pack.archetype(entry["id"])
            by_frame = {s.frame_id: s for s in loaded.slots}
            for slot in entry["slots"]:
                if slot.get("max_chars") and slot["frame_id"] in by_frame:
                    assert by_frame[slot["frame_id"]].expected_chars == slot["max_chars"]


class TestCuttingASurvivorIsSafe:
    @staticmethod
    def _pack():
        return TestASurvivorIsCutToItsCopy._pack()

    def test_a_cut_frame_keeps_a_line_of_slack(self):
        """A renderer that breaks one word earlier must not spill the last
        line onto the row closed up beneath it."""
        pack = self._pack()
        page = TestASurvivorIsCutToItsCopy()._page(pack)
        first = min((f for f in page.frames if f.role == "column"),
                    key=lambda f: f.place.y)
        block = first.content[0]
        role = pack.type_roles[block.type_role]
        from app.design.geometry import content_box

        box = content_box(pack.canvas, pack.grid)
        column = pack.grid.column_width_in(pack.canvas)
        width = (first.place.span * column
                 + (first.place.span - 1) * pack.grid.gutter_in) * 72.0
        copy = set_copy(block.text, role, pack, width, 0.0).height_pt
        assert first.place.h * box.height * 72.0 >= copy + role.line_height_pt - 0.5

    def test_a_frame_stacked_beside_a_dropped_figure_is_not_cut(self):
        """A heading over prose beside a tall figure shares its band without
        being in a row with it - `_closed_up`'s rule, and now this one's."""
        from app.design.bind import _compact
        from app.design.schema import Frame, GridPlace, Paragraph, Run

        pack = self._pack()

        def text(value):
            return (TextBlock(type_role="body", paragraphs=(
                Paragraph(runs=(Run(text=value),)),)),)

        heading = Frame(id="h", role="column", z=1, content=text("A short line."),
                        place=GridPlace(col=0, span=3, y=0.0, h=0.4))
        prose = Frame(id="p", role="column", z=1, content=text("Another line."),
                      place=GridPlace(col=0, span=3, y=0.45, h=0.4))
        figure = Frame(id="f", role="figure", z=1,
                       place=GridPlace(col=3, span=3, y=0.0, h=0.85))
        below = Frame(id="b", role="column", z=1, content=text("Below."),
                      place=GridPlace(col=0, span=6, y=0.9, h=0.1))
        out = _compact((heading, prose, below), (heading, prose, figure, below),
                       pack, pack.grid)
        heights = {f.id: f.place.h for f in out}
        assert heights["h"] == pytest.approx(0.4)
        assert heights["p"] == pytest.approx(0.4)

    def test_a_discarded_compaction_returns_the_frames_uncut(self, monkeypatch):
        """'The page renders as its archetype states' - not shortened and
        left where it was."""
        from app.design import bind

        pack = self._pack()
        archetype = pack.archetype("flow")
        calls = {"n": 0}
        real = bind._stacked_over

        def failing_second_check(frames, tolerance=0.0):
            calls["n"] += 1
            return real(frames, tolerance) if calls["n"] == 1 else True

        monkeypatch.setattr(bind, "_stacked_over", failing_second_check)
        page = TestASurvivorIsCutToItsCopy()._page(pack)
        stated = {f.id: f.place.h for f in archetype.frames}
        for frame in page.frames:
            if frame.id in stated:
                assert frame.place.h == pytest.approx(stated[frame.id])
