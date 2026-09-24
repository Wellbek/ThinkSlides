"""
A design language the agent reasons out, rather than one it picks.

Directions were a point in a 4x4 enum, and the prompt required each of three
to use a different ground and a different structure - so three directions were
three picks from sixteen cells, and the only things that varied with the
subject were a hex and a two-word name. No wording of the prompt fixes a
vocabulary that does not contain the difference being asked for, and a
catalogue of ready-made packs would have been the same failure at a different
size.

The property that makes authoring safe is the split: **the agent decides the
design, code derives the geometry.** The model never emits coordinates - that
is what removed the whole class of guessed-rectangle failures - so what comes
back has to pass every gate a measured language passes.
"""
import math

import pytest

from app.design.authored import Language, PageKind, Slot, to_pack
from app.design.geometry import check_page
from app.design.roles import type_role_for
from app.design.schema import Canvas, DesignDoc, Page, PackRef
from app.design.select import _slot_frame, _slot_rect
from app.design.vocabulary import ensure

CANVAS = Canvas(w_in=13.333, h_in=7.5)


def _language(**overrides) -> Language:
    base = dict(
        name="Box office ledger",
        rationale="a franchise read as receipts",
        columns=6, body_pt=13.0,
        ratios={"headline": 2.8, "display": 4.2},
        display_face="Georgia", body_face="Arial",
        ground="#FFFFFF", ink="#1A1A1A", accent="#B3261E",
        accent_alt="#E8A33D",
        pages=(
            PageKind(name="cover", role="cover", frequency=0.1, slots=(
                Slot(kind="heading", span=5, required=True),
                Slot(kind="standfirst", span=4))),
            PageKind(name="receipts", role="content", frequency=0.5, slots=(
                Slot(kind="heading", span=6, required=True),
                Slot(kind="statistic", span=6, name="row"),
                Slot(kind="prose", span=3, name="left"),
                Slot(kind="figure", span=3))),
            PageKind(name="verdict", role="closing", frequency=0.1, slots=(
                Slot(kind="heading", span=5, required=True),)),
        ),
    )
    base.update(overrides)
    return Language(**base)


@pytest.fixture
def pack():
    return ensure(to_pack(_language(), CANVAS))


class TestTheAgentDecidesTheDesign:
    def test_the_measure_is_what_was_asked_for(self, pack):
        assert pack.grid.columns == 6
        assert pack.grid.margin.left == pytest.approx(CANVAS.w_in * 0.06, rel=0.01)

    def test_the_ramp_is_built_from_ratios_not_points(self):
        """A ratio is the decision - a display three times the body is a loud
        deck at any canvas size, and 42pt is only loud on some."""
        # Both above the canvas-relative floor, so neither is scaled on the
        # way past and what is being compared is the ratio arithmetic.
        small = to_pack(_language(body_pt=18.0), CANVAS)
        large = to_pack(_language(body_pt=36.0), CANVAS)
        assert (large.role("headline").size_pt
                == pytest.approx(2 * small.role("headline").size_pt, rel=0.01))

    def test_a_named_face_reaches_the_pack(self, pack):
        assert pack.display_face == "Georgia"
        assert pack.body_face == "Arial"

    def test_the_palette_is_by_role(self, pack):
        assert pack.colors.accent == "#B3261E"
        assert pack.colors.accent_alt == "#E8A33D"

    def test_the_page_vocabulary_is_what_was_authored(self, pack):
        authored = {a.id for a in pack.archetypes if a.note.startswith("authored")}
        assert {"cover", "receipts", "verdict"} <= authored

    def test_frequency_carries_the_house_mix(self, pack):
        """Reproducing a look means reproducing its mix."""
        assert pack.archetype("receipts").frequency == pytest.approx(0.5)

    def test_two_languages_differ_in_more_than_hue(self):
        """The failure this exists to end: three directions differing only in
        colour."""
        one = to_pack(_language(), CANVAS)
        other = to_pack(_language(
            name="Wide screen", columns=12, body_pt=10.0,
            ratios={"headline": 1.8, "display": 2.4},
            display_face="Arial",
            pages=(PageKind(name="spread", role="content", slots=(
                Slot(kind="heading", span=12, required=True),
                Slot(kind="prose", span=4), Slot(kind="prose", span=4))),),
        ), CANVAS)
        assert one.grid.columns != other.grid.columns
        assert one.role("headline").size_pt != other.role("headline").size_pt
        assert one.display_face != other.display_face
        assert {a.id for a in one.archetypes} != {a.id for a in other.archetypes}


class TestTheAgentNeverDecidesGeometry:
    def test_the_language_carries_no_coordinates(self):
        """What removed the whole class of guessed-rectangle failures."""
        fields = set(Language.model_fields)
        for forbidden in ("x", "y", "left", "top", "width", "height",
                          "frames", "rect", "position"):
            assert forbidden not in fields

    def test_a_slot_declares_a_span_not_a_rectangle(self):
        """`bleed` and `behind` are declarations, not coordinates.

        A language says that a figure bleeds; *which* edges it runs off is
        derived from the row it lands in and the columns it takes. Saying
        where would be emitting geometry again, one flag at a time.
        """
        fields = set(Slot.model_fields)
        assert fields == {"kind", "span", "required", "name",
                          "bleed", "behind"}
        for forbidden in ("x", "y", "col", "row", "left", "top",
                          "width", "height", "edges", "z"):
            assert forbidden not in fields

    def test_an_impossible_span_is_clamped_rather_than_obeyed(self):
        """An agent asking for eight columns of six gets six."""
        pack = to_pack(_language(columns=4, pages=(
            PageKind(name="wide", role="content", slots=(
                Slot(kind="heading", span=40, required=True),)),)), CANVAS)
        frame = pack.archetype("wide").frames[0]
        assert frame.place.span <= 4

    def test_capacities_are_measured_not_asserted(self, pack):
        """A slot's capacity is computed at the face it is set in, the same
        way it is computed when a language is recovered from a PDF."""
        for archetype in pack.archetypes:
            for slot in archetype.slots:
                if slot.accepts and "text" not in slot.accepts:
                    continue
                assert slot.max_chars and slot.max_chars > 0


class TestAnAuthoredLanguagePassesEveryGate:
    """An invented language that cannot pass these is rejected exactly as a
    badly recovered one is."""

    def test_no_archetype_places_two_frames_on_each_other(self, pack):
        for archetype in pack.archetypes:
            page = Page(id="p", archetype=archetype.id, grid=archetype.grid,
                        frames=archetype.frames)
            doc = DesignDoc(pack=PackRef(id=pack.id), canvas=pack.canvas,
                            grid=archetype.grid or pack.grid, pages=(page,))
            assert not [p for p in check_page(doc, page) if "overlap" in p]

    def test_no_slot_is_too_short_for_the_type_it_is_set_in(self, pack):
        for archetype in pack.archetypes:
            grid = archetype.grid or pack.grid
            for slot in archetype.slots:
                if slot.accepts and "text" not in slot.accepts:
                    continue
                size = _slot_rect(archetype, slot, pack, grid)
                frame = _slot_frame(archetype, slot)
                if not size or not frame:
                    continue
                role = pack.type_roles.get(
                    type_role_for(frame.role, "text", pack, frame.type_role))
                if role:
                    assert size[1] * 72.0 >= role.line_height_pt - 0.01

    def test_the_missing_page_roles_are_still_composed(self, pack):
        """A language that forgot a divider gets one, from its own tokens."""
        assert any(a.role == "section" for a in pack.archetypes)

    def test_it_composes_and_renders_a_real_deck(self, pack, tmp_path):
        from app.design import run as design_run
        from app.renderer.designdoc_renderer import check_doc, render_doc

        def text(*lines):
            return {"type": "text", "runs": [{"text": line} for line in lines]}

        slides = [
            {"slideIndex": 0, "role": "cover",
             "contents": [text("Cars: the receipts")]},
            {"slideIndex": 1, "role": "content", "contents": [
                text("Where the money came from"),
                text("The franchise took $1.4 billion worldwide.",
                     "Merchandise outsold the box office five times over.")]},
            {"slideIndex": 2, "role": "closing",
             "contents": [text("A franchise built on toys")]},
        ]
        doc = design_run.compose(slides, pack)
        assert check_doc(doc) == []
        out = str(tmp_path / "authored.pptx")
        render_doc(doc, pack, out)
        import os
        assert os.path.getsize(out) > 0

    def test_the_legibility_floor_comes_from_the_canvas(self):
        """The floor used to be computed from `language.body_pt` itself, which
        made it a tautology: a language setting 10pt declared its own floor to
        be 7.5pt and passed, so no authored value could ever fail it."""
        from app.design.authored import (
            CANVAS_BODY_FLOOR_RATIO, POLICY_FLOOR_SHARE,
        )
        from app.renderer.typeset.policy import check, floor_for

        tiny = to_pack(_language(body_pt=9.0), CANVAS)
        loud = to_pack(_language(body_pt=30.0), CANVAS)
        assert floor_for(tiny, "body") == pytest.approx(floor_for(loud, "body"))
        assert floor_for(tiny, "body") == pytest.approx(
            CANVAS_BODY_FLOOR_RATIO * POLICY_FLOOR_SHARE * CANVAS.h_in * 72.0)
        # And what it derives is still legible by that floor, because the ramp
        # was scaled up rather than left where it was authored.
        assert check(tiny, "body", tiny.role("body").size_pt) is None


class TestItIsNotACatalogue:
    def test_the_pack_says_it_was_authored(self, pack):
        assert "authored" in pack.source_note

    def test_an_archetype_says_it_was_not_measured(self, pack):
        authored = [a for a in pack.archetypes if a.id == "receipts"]
        assert authored and "not measured" in authored[0].note

    def test_the_id_comes_from_the_name_the_agent_chose(self):
        assert to_pack(_language(name="Wide Screen!"), CANVAS).id == "wide-screen"


class TestALanguageThatCannotDrawItselfIsNotOffered:
    """Issue #280.

    Every direction card rendered "No preview". The design pause worked - three
    languages were authored, named and described - and all three specimen decks
    failed to compose, so the user chose a design by looking at three grey
    rectangles.

    The language below is the shape a model plausibly authors and is the exact
    reproduction: a cover carrying only a headline, and a content page whose
    figure slot is `required`.
    """

    @staticmethod
    def _brittle() -> Language:
        return Language(
            name="Scientific journal",
            rationale="a paper read as a paper",
            columns=6, body_pt=10.0,
            pages=(
                PageKind(name="cover", role="cover", frequency=0.1, slots=(
                    Slot(kind="heading", span=6, required=True),)),
                PageKind(name="figure-page", role="content", frequency=0.9,
                         slots=(
                             Slot(kind="heading", span=6, required=True),
                             Slot(kind="figure", span=3, required=True),
                             Slot(kind="prose", span=3))),
            ),
        )

    @staticmethod
    def _compose(language: Language, **kwargs):
        from app.design import run as design_run
        from app.renderer.specimen import language_specimen_slides

        pack = ensure(to_pack(language, CANVAS))
        return pack, design_run.compose(
            language_specimen_slides(), pack, title="specimen", **kwargs)

    def test_the_specimen_deck_composes_at_all(self):
        _, doc = self._compose(self._brittle())
        assert len(doc.pages) == 3

    def test_the_cover_is_still_a_cover(self):
        """The half-fix passes without this.

        Making the figure slot optional is enough to compose - and it composed
        all three pages onto the *content* archetype, so the deck silently lost
        its cover. A test asserting only "it composed" is green on the wrong
        outcome.
        """
        pack, doc = self._compose(self._brittle())
        roles = {a.id: a.role for a in pack.archetypes}
        assert roles[doc.pages[0].archetype] == "cover"

    @staticmethod
    def _slot_for(archetype, frame_role: str):
        """The slot whose frame carries this role.

        By frame rather than by frame id: the reproduction's page kind is
        *named* "figure-page", so matching "figure" against the id picks its
        heading and the test passes on the wrong slot.
        """
        ids = {f.id for f in archetype.frames if f.role == frame_role}
        return next(s for s in archetype.slots if s.frame_id in ids)

    def test_an_exhibit_slot_is_never_a_precondition(self):
        """A model writing `required` on a figure means "this page is about its
        chart". `SlotSpec.required` means "reject this archetype when the slot
        is empty", and a page with no chart is most of a deck."""
        pack = to_pack(self._brittle(), CANVAS)
        page = next(a for a in pack.archetypes if a.id == "figure-page")
        assert self._slot_for(page, "figure").required is False

    def test_a_heading_may_still_be_required(self):
        pack = to_pack(self._brittle(), CANVAS)
        page = next(a for a in pack.archetypes if a.id == "figure-page")
        assert self._slot_for(page, "headline").required is True

    def test_an_opener_gets_somewhere_to_put_its_subtitle(self):
        pack = to_pack(self._brittle(), CANVAS)
        cover = next(a for a in pack.archetypes if a.id == "cover")
        assert any(f.role == "standfirst" for f in cover.frames)

    def test_an_opener_that_said_where_is_left_alone(self):
        """Composing the missing slot is a floor, not a preference. A language
        that already declared one keeps exactly what it authored."""
        pack = to_pack(_language(), CANVAS)
        cover = next(a for a in pack.archetypes if a.id == "cover")
        assert len([f for f in cover.frames if f.role == "standfirst"]) == 1


class TestAPreviewShowsWhatItCan:
    """Issue #280, third part: one unplaceable page lost all three previews."""

    @staticmethod
    def _one_page_pack():
        """A pack whose only content archetype cannot hold a chart."""
        language = Language(
            name="Text only", rationale="no exhibits anywhere",
            columns=6, body_pt=18.0,
            pages=(PageKind(name="page", role="content", frequency=1.0, slots=(
                Slot(kind="heading", span=6, required=True),
                Slot(kind="prose", span=6))),),
        )
        return ensure(to_pack(language, CANVAS))

    def test_a_real_deck_still_fails_loudly(self):
        """The default stays strict. A deck that silently loses a slide is
        worse than one that fails: the user gets a file, ships it, and finds
        out in the room."""
        from app.design import run as design_run

        chart = {"type": "chart", "chartType": "bar",
                 "series": [{"name": "a", "values": [1, 2]}],
                 "categories": ["x", "y"]}
        slides = [{"slideIndex": 0, "role": "content", "heading": "H",
                   "contents": [chart]}]
        with pytest.raises(design_run.Unplaceable):
            design_run.compose(slides, self._one_page_pack())

    def test_a_specimen_drops_the_page_and_keeps_the_rest(self):
        from app.design import run as design_run
        from app.renderer.specimen import language_specimen_slides

        doc = design_run.compose(
            language_specimen_slides(), self._one_page_pack(),
            skip_unplaceable=True)
        assert 0 < len(doc.pages) < 3


class TestTypeIsSizedForASlideNotAPage:
    """Issue #282.

    A model asked for a "scientific journal" design answers with the sizes that
    reference actually uses - 10pt, 11pt - because that is what it is set in on
    A4. On a slide read across a room it is unreadable, and every other rung is
    a ratio of the body, so the whole page shrinks with it.
    """

    @staticmethod
    def _floor_pt(canvas: Canvas = CANVAS) -> float:
        from app.design.authored import CANVAS_BODY_FLOOR_RATIO

        return CANVAS_BODY_FLOOR_RATIO * canvas.h_in * 72.0

    def test_a_document_sized_ramp_is_raised_to_the_canvas(self):
        pack = to_pack(_language(body_pt=10.0), CANVAS)
        assert pack.role("body").size_pt >= self._floor_pt()

    def test_the_ratios_the_language_chose_survive_the_scaling(self):
        """Clamping the body alone would flatten the ramp, and the ramp is the
        design."""
        authored = _language(body_pt=10.0)
        pack = to_pack(authored, CANVAS)
        assert (pack.role("headline").size_pt / pack.role("body").size_pt
                == pytest.approx(authored.ratio("headline"), rel=0.02))
        assert (pack.role("display").size_pt / pack.role("body").size_pt
                == pytest.approx(authored.ratio("display"), rel=0.02))

    def test_a_language_already_above_the_floor_is_untouched(self):
        pack = to_pack(_language(body_pt=22.0), CANVAS)
        assert pack.role("body").size_pt == pytest.approx(22.0)

    def test_the_default_body_size_is_a_slide_size(self):
        """The schema default was 14.0, which is below every canvas-relative
        floor - so the default itself was being scaled on every run."""
        from app.design.authored import Language as L

        assert L.model_fields["body_pt"].default >= self._floor_pt()

    def test_the_floor_follows_the_canvas_rather_than_a_constant(self):
        """A poster is read at a metre and a slide is projected, so the floor
        is a share of the canvas rather than a number of points."""
        tall = Canvas(w_in=26.666, h_in=15.0)
        wide = to_pack(_language(body_pt=8.0), CANVAS)
        big = to_pack(_language(body_pt=8.0), tall)
        assert big.role("body").size_pt > wide.role("body").size_pt

    def test_a_scaled_language_still_composes(self):
        from app.design import run as design_run
        from app.renderer.specimen import language_specimen_slides

        pack = ensure(to_pack(_language(body_pt=9.0), CANVAS))
        doc = design_run.compose(language_specimen_slides(), pack, title="x")
        assert len(doc.pages) >= 3


class TestAPageMayPutThingsBesideEachOther:
    """Issue #281.

    Every page an authored language composed was a single column down the left
    of the slide. `_archetype` emitted `col=0` for every slot and only ever
    advanced downward, so `span` narrowed a frame without moving anything up
    beside it: a half-width figure left the right half of the page empty, and
    a deck of them read as plain and unfinished.

    The whole rule is that consecutive slots whose spans fit the grid share a
    row. It adds no authoring vocabulary - `Slot.span` already said how wide a
    thing was, and `PageKind.slots` already had an order.
    """

    @staticmethod
    def _pack(**page):
        base = dict(name="page", role="content", frequency=1.0)
        base.update(page)
        return to_pack(_language(body_pt=18.0, pages=(PageKind(**base),)),
                       CANVAS)

    @staticmethod
    def _grid(archetype) -> dict:
        return {f.id: f.place for f in archetype.frames
                if f.place.kind == "grid"}

    def test_slots_whose_spans_fit_share_a_row(self):
        pack = self._pack(slots=(
            Slot(kind="heading", span=6, required=True),
            Slot(kind="figure", span=3),
            Slot(kind="prose", span=3)))
        places = self._grid(pack.archetype("page"))
        figure = next(p for k, p in places.items() if "figure" in k)
        prose = next(p for k, p in places.items() if "prose" in k)
        assert figure.y == pytest.approx(prose.y)
        assert figure.col == 0
        assert prose.col == figure.span

    def test_spans_that_overflow_the_grid_wrap_to_a_new_row(self):
        pack = self._pack(slots=(
            Slot(kind="prose", span=4, name="a"),
            Slot(kind="prose", span=4, name="b")))
        places = self._grid(pack.archetype("page"))
        first = places["page-a-0"]
        second = places["page-b-1"]
        assert second.col == 0
        assert second.y > first.y

    def test_the_bands_still_fill_the_page(self):
        """`_fill` distributes the page's spare height per *band*. Doing it
        per slot over-counts a row carrying two things: a figure and its
        commentary side by side measured as two stacked bands, so the stack
        came to about half the box and the rest was white."""
        pack = self._pack(slots=(
            Slot(kind="heading", span=6, required=True),
            Slot(kind="figure", span=3),
            Slot(kind="prose", span=3)))
        places = self._grid(pack.archetype("page"))
        assert max(p.y + p.h for p in places.values()) == pytest.approx(
            1.0, abs=0.02)

    def test_a_heading_is_not_stretched_to_the_figure_beside_it(self):
        """Stretching a fixed-height slot to match its row sets one line of
        display type floating in the middle of a tall box."""
        pack = self._pack(slots=(
            Slot(kind="heading", span=3, required=True),
            Slot(kind="figure", span=3)))
        places = self._grid(pack.archetype("page"))
        heading = next(p for k, p in places.items() if "heading" in k)
        figure = next(p for k, p in places.items() if "figure" in k)
        assert heading.y == pytest.approx(figure.y)
        assert heading.h < figure.h

    def test_a_composed_page_is_still_geometrically_sound(self):
        from app.design import run as design_run
        from app.design.geometry import check_doc
        from app.renderer.specimen import language_specimen_slides

        pack = ensure(to_pack(_language(body_pt=18.0), CANVAS))
        doc = design_run.compose(language_specimen_slides(), pack, title="x")
        assert check_doc(doc) == []


class TestATypeOverImageOpenerIsAuthorable:
    """The shape the schema could already express that no language could ask
    for: a full-page figure with the title laid over it, which is how the
    reference decks open."""

    @staticmethod
    def _cover():
        return ensure(to_pack(_language(body_pt=18.0, pages=(
            PageKind(name="opener", role="cover", frequency=1.0, slots=(
                Slot(kind="figure", span=6, behind=True, name="hero"),
                Slot(kind="heading", span=6, required=True),
                Slot(kind="standfirst", span=4))),
            PageKind(name="page", role="content", frequency=1.0, slots=(
                Slot(kind="heading", span=6, required=True),
                Slot(kind="figure", span=6))),
        )), CANVAS)).archetype("opener")

    def test_a_behind_slot_is_the_whole_page_underneath(self):
        cover = self._cover()
        hero = next(f for f in cover.frames if "hero" in f.id)
        ground = next(f for f in cover.frames if f.id.endswith("-ground"))
        assert ground.z < hero.z
        assert hero.place.col == 0 and hero.place.h == pytest.approx(1.0)

    def test_a_cover_with_a_figure_behind_its_type_compiles(self):
        """The cover's own ground and the behind figure shared z=0 and
        overlapped completely, so check_doc refused the page and every render
        of a deck on the language failed. The language is the one gpt-5.6-luna
        authored on the live run that lost three drafted slides to it (#306)."""
        from app.design import deck_format
        from app.design import run as design_run
        from app.design.authoring import read
        from app.design.geometry import check_doc

        (language,) = read({"languages": [{
            "name": "Legal Brief", "rationale": "Clauses beside their exceptions.",
            "columns": 4, "marginRatio": 0.075, "gutterRatio": 0.025, "bodyPt": 19.0,
            "ratios": {"display": 3.2, "headline": 2.2, "standfirst": 1.35},
            "displayFace": "Times New Roman", "bodyFace": "Arial",
            "ground": "#FBFAF7", "ink": "#252525", "accent": "#1F4E79",
            "accentAlt": "#9B6B30", "density": "dense",
            "pages": [
                {"name": "briefing-question", "role": "cover", "frequency": 0.34, "slots": [
                    {"kind": "figure", "span": 4, "behind": True},
                    {"kind": "heading", "span": 3, "required": True},
                    {"kind": "standfirst", "span": 2},
                    {"kind": "caption", "span": 2},
                    {"kind": "footnote", "span": 4}]},
                {"name": "analysis", "role": "content", "frequency": 0.66, "slots": [
                    {"kind": "heading", "span": 4, "required": True},
                    {"kind": "prose", "span": 2, "name": "left"},
                    {"kind": "figure", "span": 2}]},
            ],
        }]})
        fmt = deck_format.by_id("boardroom")
        pack = ensure(to_pack(language, fmt.canvas, floor_pt=fmt.floor_pt(fmt.canvas)))
        cover = [{"slideIndex": 0, "role": "cover", "contents": [
            {"type": "text", "runs": [{"text": "How HTTP caching decides"}]},
            {"type": "text", "runs": [{"text": "Freshness, revalidation and stale content"}]}]}]

        assert check_doc(design_run.compose(cover, pack, title="x")) == []

    def test_the_type_sits_over_it(self):
        cover = self._cover()
        hero = next(f for f in cover.frames if "hero" in f.id)
        typed = [f for f in cover.frames
                 if f.role in ("headline", "standfirst")]
        assert typed and all(f.z > hero.z for f in typed)

    def test_a_bleeding_slot_names_the_edges_it_runs_off(self):
        """Derived, not authored. A language says a figure bleeds; where it
        bleeds follows from the row it lands in and the columns it takes."""
        pack = to_pack(_language(body_pt=18.0, pages=(
            PageKind(name="p", role="content", frequency=1.0, slots=(
                Slot(kind="heading", span=6, required=True),
                Slot(kind="figure", span=3, bleed=True),
                Slot(kind="prose", span=3))),)), CANVAS)
        figure = next(f for f in pack.archetype("p").frames
                      if f.role == "figure")
        assert set(figure.bleed) == {"bottom", "left"}

    def test_a_slot_that_does_not_bleed_declares_no_edges(self):
        pack = to_pack(_language(body_pt=18.0), CANVAS)
        assert all(f.bleed == () for f in pack.archetype("receipts").frames)


class TestAnOpenerSitsLowOnThePage:
    def test_authored_and_composed_openers_agree(self):
        """`vocabulary.py` builds a composed cover with its type low - it is
        what BXSL does. An authored cover stacked from the top, so a language
        that declared its own cover got a worse one than a language that
        forgot to."""
        from app.design.vocabulary import COVER_BASELINE

        pack = to_pack(_language(body_pt=18.0), CANVAS)
        bands = [f.place for f in pack.archetype("cover").frames
                 if f.place.kind == "grid"]
        assert max(p.y + p.h for p in bands) == pytest.approx(
            COVER_BASELINE, abs=0.02)

    def test_an_opener_with_room_already_used_is_left_alone(self):
        """A cover carrying a column of prose has already filled the page, and
        pushing it down would run it off the bottom."""
        pack = to_pack(_language(body_pt=18.0, pages=(
            PageKind(name="full", role="cover", frequency=1.0, slots=(
                Slot(kind="heading", span=6, required=True),
                Slot(kind="prose", span=6))),)), CANVAS)
        bands = [f.place for f in pack.archetype("full").frames
                 if f.place.kind == "grid"]
        assert max(p.y + p.h for p in bands) <= 1.001


class TestTheArrangementIsChosenFromTheMaterial:
    """Your note on #281: pick the layout from what the page actually carries.

    Half of that already worked - `select.choose` scores every archetype
    against the real material. What did not is that an authored page kind
    derived to exactly one geometry, so the selector was choosing from a set
    of size one and a page of prose and a page carrying a wide chart got the
    same shape.
    """

    @staticmethod
    def _pack():
        return ensure(to_pack(_language(body_pt=18.0, pages=(
            PageKind(name="page", role="content", frequency=1.0, slots=(
                Slot(kind="heading", span=6, required=True),
                Slot(kind="figure", span=3),
                Slot(kind="prose", span=3))),)), CANVAS))

    @staticmethod
    def _with_chart(aspect: float):
        from app.design.material import shape_from

        return shape_from("content", "Heading", ["one two three four."] * 3,
                          exhibits=[{"type": "chart", "chartType": "bar",
                                     "aspect": aspect,
                                     "series": [{"name": "a",
                                                 "values": [1, 2]}],
                                     "categories": ["x", "y"]}])

    def test_one_page_kind_offers_more_than_one_arrangement(self):
        ids = {a.id for a in self._pack().archetypes}
        assert {"page", "page-stacked"} <= ids

    def test_identical_arrangements_are_not_offered_twice(self):
        """A language whose slots are already full width *is* the stack."""
        pack = to_pack(_language(body_pt=18.0, pages=(
            PageKind(name="wide", role="content", frequency=1.0, slots=(
                Slot(kind="heading", span=6, required=True),
                Slot(kind="prose", span=6))),)), CANVAS)
        assert [a.id for a in pack.archetypes] == ["wide"]

    def test_the_arrangement_the_language_wrote_wins_a_tie(self):
        pack = self._pack()
        authored = pack.archetype("page")
        variant = pack.archetype("page-stacked")
        assert authored.frequency > variant.frequency

    def test_a_wide_exhibit_and_a_tall_one_land_differently(self):
        """The point of the whole thing. At the old `W_ASPECT` these were the
        same page, because the exhibit's shape reached the total divided by
        the number of placements and never decided anything."""
        from app.design.select import choose

        pack = self._pack()
        tall = choose(self._with_chart(0.7), pack)
        wide = choose(self._with_chart(6.0), pack)
        assert tall is not None and wide is not None
        assert tall.archetype.id != wide.archetype.id

    def test_an_exhibit_goes_where_its_shape_fits(self):
        from app.design.select import _slot_rect, choose

        pack = self._pack()
        for aspect in (0.7, 6.0):
            selection = choose(self._with_chart(aspect), pack)
            archetype = selection.archetype
            slot = next(s for s in archetype.slots if "figure" in s.frame_id)
            rect = _slot_rect(archetype, slot, pack,
                              archetype.grid or pack.grid)
            chosen = rect[0] / rect[1]
            others = []
            for other in pack.archetypes:
                if other.id == archetype.id or other.role != archetype.role:
                    continue
                spec = next((s for s in other.slots
                             if "figure" in s.frame_id), None)
                if spec is None:
                    continue
                box = _slot_rect(other, spec, pack, other.grid or pack.grid)
                if box and box[1]:
                    others.append(box[0] / box[1])
            assert all(abs(math.log(aspect / chosen))
                       <= abs(math.log(aspect / o)) + 1e-9 for o in others)
