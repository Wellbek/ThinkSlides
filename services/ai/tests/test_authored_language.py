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
        fields = set(Slot.model_fields)
        assert fields == {"kind", "span", "required", "name"}

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
