"""
What a pack has to be true of before anything is generated against it.

A pack is traced from a reference PDF, and the trace was taken too literally.
Every text run on the page became a content slot, so a chart's tick labels, a
data callout and a page number were all offered as somewhere to put a
paragraph. Measured across the three shipped packs, between 35% and 76% of
each pack's text slots could not hold a single line of that pack's own body
copy.

What that does to a page is not subtle. The assignment is offered an axis
label as a destination and often finds it cheapest, because a small slot has
low capacity strain for a short item - so a generated page came out as
fragments scattered into the positions a chart used to occupy, and on BXSL's
endnotes page every paragraph landed in a caption or a footnote and was set at
seven points.

These are the properties that stop that happening. The first class runs
against the committed packs and needs no PDF; the rest test the builder's own
decisions directly.
"""
import pytest

from app.design import run
from app.design.pack_build import (
    MIN_MEASURE_CHARS,
    Faces,
    _capacity_chars,
    _carries_copy,
    _char_width_pt,
    _inside_a_figure,
    _is_a_measure,
)
from app.design.roles import type_role_for
from app.design.schema import FracPlace, Frame
from app.design.select import _slot_frame, _slot_rect


@pytest.fixture(scope="module", params=run.available())
def pack(request):
    return run.load(request.param)


def _text_slots(pack):
    """Every text slot of every archetype, with its frame and resolved size."""
    for archetype in pack.archetypes:
        grid = archetype.grid or pack.grid
        for slot in archetype.slots:
            if slot.accepts and "text" not in slot.accepts:
                continue
            frame = _slot_frame(archetype, slot)
            size = _slot_rect(archetype, slot, pack, grid)
            if frame is None or size is None:
                continue
            yield archetype, slot, frame, size


def _measure_floor_in(pack) -> float:
    """The narrowest a slot may be and still be a measure, for this pack."""
    from app.renderer.typeset.fonts import width_pt

    body = pack.role("body")
    sample = ("The quick brown fox jumps over the lazy dog and keeps going "
              "until the line is comfortably longer than any measure")
    per_char = width_pt(sample, pack.face_for(body), body.size_pt) / len(sample)
    return MIN_MEASURE_CHARS * per_char / 72.0


class TestEverySlotCanHoldTheTypeItIsSetIn:
    """The gate. Run against the committed packs, so it needs no PDF."""

    def test_no_slot_is_too_short_for_one_line_of_its_own_role(self, pack):
        for archetype, slot, frame, (_, height_in) in _text_slots(pack):
            role = pack.type_roles.get(
                type_role_for(frame.role, "text", pack, frame.type_role)
            )
            if role is None:
                continue
            assert height_in * 72.0 >= role.line_height_pt - 0.01, (
                f"{pack.id}/{archetype.id}/{frame.id} is "
                f"{height_in * 72:.1f}pt tall and is set in {role.size_pt}pt "
                f"type leading {role.line_height_pt}pt"
            )

    def test_no_slot_is_too_narrow_to_set_copy_in(self, pack):
        floor = _measure_floor_in(pack)
        for archetype, slot, frame, (width_in, _) in _text_slots(pack):
            assert width_in >= floor - 0.001, (
                f"{pack.id}/{archetype.id}/{frame.id} is {width_in:.2f}in "
                f"wide; {MIN_MEASURE_CHARS} characters of this pack's body "
                f"copy need {floor:.2f}in"
            )

    def test_a_slot_states_a_capacity_it_can_actually_hold(self, pack):
        """The capacity used to be the character count that happened to be on
        the page, inflated by 40%. A slot traced from a nine-character label
        therefore advertised room for twenty characters while measuring an
        inch and a half wide, and every later stage read that number."""
        for archetype, slot, frame, (width_in, height_in) in _text_slots(pack):
            if not slot.max_chars:
                continue
            role = pack.type_roles.get(
                type_role_for(frame.role, "text", pack, frame.type_role)
            )
            if role is None:
                continue
            from app.renderer.typeset.shape import layout

            # Real words rather than a run of one narrow letter: "nnnn..."
            # is both narrower per character than prose and breakable
            # anywhere, so it would pack tighter than anything a deck
            # actually carries and make this check lenient.
            sample = ("the quick brown fox jumps over the lazy dog "
                      * 200)[:slot.max_chars]
            result = layout(
                sample, width_in * 72.0, height_in * 72.0,
                pack.face_for(role), role.size_pt,
                line_height_pt=role.line_height_pt,
            )
            assert result.overflow_pt <= height_in * 72.0, (
                f"{pack.id}/{archetype.id}/{frame.id} claims "
                f"{slot.max_chars} characters and overflows by "
                f"{result.overflow_pt:.0f}pt"
            )


class TestNoArchetypePlacesTwoFramesOnEachOther:
    """The renderer refuses a document whose sibling frames overlap at the
    same depth, so a pack carrying one composes pages that cannot ship. A
    six-slide deck once composed seven pages and rendered none of them.

    Overlap at *differing* depth is legitimate and is how publication pages
    are built - type over an image, a figure under its caption - so this is
    about ambiguity, not about overlap."""

    def test_every_archetype_is_placeable(self, pack):
        from app.design.geometry import check_page
        from app.design.schema import DesignDoc, PackRef, Page

        for archetype in pack.archetypes:
            page = Page(id="p", archetype=archetype.id, grid=archetype.grid,
                        frames=archetype.frames)
            doc = DesignDoc(pack=PackRef(id=pack.id), canvas=pack.canvas,
                            grid=archetype.grid or pack.grid, pages=(page,))
            ambiguous = [p for p in check_page(doc, page) if "overlap" in p]
            assert not ambiguous, f"{pack.id}/{archetype.id}: {ambiguous[0]}"

    def test_a_feature_the_grammar_noticed_is_not_a_frame(self, pack):
        """`exhibit` and `grid` are things the grammar reader *observed about*
        a page, not areas of it. Emitted as frames they covered the whole
        canvas, painted nothing, and collided with every real panel beneath
        them - 100% overlap on WTI page 4. The observation lives in the
        archetype's label instead."""
        for archetype in pack.archetypes:
            slotted = {slot.frame_id for slot in archetype.slots}
            for frame in archetype.frames:
                if frame.role not in ("panel", "rail", "band", "banner",
                                      "bandRow", "grid"):
                    continue
                assert frame.ground or frame.scrim or frame.children \
                    or frame.id in slotted, (
                        f"{pack.id}/{archetype.id}/{frame.id} paints nothing "
                        f"and holds nothing"
                    )

    def test_snapping_never_widens_a_frame_onto_its_neighbour(self, pack):
        """Snapping rebuilds a traced frame as the column span that best
        covers it, which is most of what puts a page on a grid - and which
        widened a frame from 2.9in of ink to a full 5.4in column, straight
        into whatever was beside it."""
        from app.design.pack_build import (
            SNAP_COLLISION_TOLERANCE, _overlap_share, _rect_of,
        )

        for archetype in pack.archetypes:
            grid = archetype.grid or pack.grid
            frames = list(archetype.frames)
            for index, one in enumerate(frames):
                for two in frames[index + 1:]:
                    if one.z != two.z:
                        continue
                    share = _overlap_share(_rect_of(one, pack.canvas, grid),
                                           _rect_of(two, pack.canvas, grid))
                    assert share <= SNAP_COLLISION_TOLERANCE + 1e-6, (
                        f"{pack.id}/{archetype.id}: {one.id} and {two.id} "
                        f"share {share:.0%} at z={one.z}"
                    )


class TestAPageOfApparatusIsNotAContentArchetype:
    """BXSL's page 20 is an endnotes page. Its only text frames are captions
    and footnotes, so a deck bound to it had every paragraph set at seven
    points. It is a real page of the publication and a useless archetype."""

    def test_a_page_with_nowhere_for_copy_is_recorded_as_such(self, pack):
        """Every archetype offered for content has somewhere content can go.
        A page of endnotes is recorded as an appendix instead."""
        for archetype in pack.archetypes:
            if archetype.role not in ("content", "exhibit"):
                continue
            assert _carries_copy(archetype.frames, archetype.slots), (
                f"{pack.id}/{archetype.id} is offered content and has "
                f"nowhere to put it"
            )

    def test_carries_copy_wants_a_frame_copy_could_go_in(self):
        from app.design.pack import SlotSpec

        footnote = Frame(id="f", role="footnotes",
                         place=FracPlace(x=0, y=0, w=1, h=0.1))
        column = Frame(id="c", role="column",
                       place=FracPlace(x=0, y=0.2, w=1, h=0.5))
        slots = [SlotSpec(frame_id="f", accepts=("text",))]
        assert not _carries_copy([footnote], slots)
        slots.append(SlotSpec(frame_id="c", accepts=("text",)))
        assert _carries_copy([footnote, column], slots)

    def test_content_does_not_go_on_an_appendix_page(self, pack):
        from app.design.material import shape_from
        from app.design.select import fit

        shape = shape_from("content", "A heading", [
            "The results of our inaugural survey demonstrate the risks.",
            "Many of the most pressing risks relate to profound shifts.",
        ])
        for archetype in pack.archetypes:
            if archetype.role == "appendix":
                assert fit(shape, archetype, pack) is None

    def test_an_apparatus_frame_refuses_the_deck_s_material(self):
        from app.design.material import MaterialItem
        from app.design.select import APPARATUS_FRAME_ROLES, _role_compatible

        paragraph = MaterialItem(id="p", kind="text", text="A paragraph.")
        for role in APPARATUS_FRAME_ROLES:
            assert not _role_compatible(role, paragraph), role
        assert _role_compatible("column", paragraph)


class TestFigureFurnitureIsNotASlot:
    def test_a_label_inside_a_chart_belongs_to_the_chart(self):
        chart = [(0.1, 0.4, 0.5, 0.8)]
        tick = {"x": 0.12, "y": 0.75, "w": 0.04, "h": 0.02}
        assert _inside_a_figure(tick, chart)

    def test_a_caption_beneath_a_chart_does_not(self):
        chart = [(0.1, 0.4, 0.5, 0.8)]
        caption = {"x": 0.1, "y": 0.82, "w": 0.4, "h": 0.03}
        assert not _inside_a_figure(caption, chart)

    def test_a_block_straddling_the_edge_is_not_swallowed(self):
        chart = [(0.1, 0.4, 0.5, 0.8)]
        straddling = {"x": 0.45, "y": 0.7, "w": 0.4, "h": 0.05}
        assert not _inside_a_figure(straddling, chart)

    def test_nothing_belongs_to_no_figure(self):
        assert not _inside_a_figure({"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}, [])


class TestAMeasureIsWideEnoughToSetCopyIn:
    def _roles(self, pack):
        from app.measure.type_roles import RungEvidence

        # `line_height_pt` is derived from the measured gaps, so it is set by
        # handing the rung one gap of the size the pack records.
        return {
            name: RungEvidence(size_pt=role.size_pt, lines=1,
                               line_gaps=[role.line_height_pt])
            for name, role in pack.type_roles.items()
        }

    def test_a_tick_label_is_not_a_measure(self, pack):
        roles = self._roles(pack)
        page_pt = pack.canvas.w_in * 72.0
        per_char = _char_width_pt("body", roles, pack.body_face)
        narrow = {"x": 0.1, "y": 0.1,
                  "w": (per_char * 4) / page_pt, "h": 0.02}
        assert not _is_a_measure(narrow, roles, pack.body_face, page_pt)

    def test_a_column_is(self, pack):
        roles = self._roles(pack)
        page_pt = pack.canvas.w_in * 72.0
        per_char = _char_width_pt("body", roles, pack.body_face)
        wide = {"x": 0.1, "y": 0.1,
                "w": (per_char * MIN_MEASURE_CHARS * 2) / page_pt, "h": 0.2}
        assert _is_a_measure(wide, roles, pack.body_face, page_pt)

    def test_capacity_describes_the_frame_not_the_source_page(self, pack):
        """Same frame, wildly different amounts of text on the day. The
        capacity must not move."""
        roles = self._roles(pack)
        w_pt, h_pt = pack.canvas.w_in * 72.0, pack.canvas.h_in * 72.0
        block = {"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.2, "size": 16.0}
        faces = Faces(pack.body_face, pack.display_face)
        lightly = _capacity_chars({**block, "chars": 9}, "body", roles,
                                  faces, w_pt, h_pt)
        heavily = _capacity_chars({**block, "chars": 900}, "body", roles,
                                  faces, w_pt, h_pt)
        assert lightly == heavily


class TestAGeneratedPageReadsInOrder:
    """The failure this whole issue is about, stated as a property of the
    output rather than of the pack."""

    PARAS = [
        "The results of our inaugural survey of insurance industry CROs "
        "demonstrate the expanding and diversifying risks insurers face.",
        "Many of the most pressing risks relate to the profound shifts "
        "reshaping the industry and to rising regulatory requirements.",
        "That is true not only for near-term risks but also for devising "
        "plans for risks forming on the horizon.",
    ]

    def test_no_paragraph_lands_in_an_apparatus_frame(self, pack):
        from app.design.material import shape_from
        from app.design.select import APPARATUS_FRAME_ROLES, choose

        selection = choose(shape_from("content", "A heading", self.PARAS), pack)
        assert selection
        for placement in selection.placements:
            frame = _slot_frame(selection.archetype, placement.slot)
            assert frame.role not in APPARATUS_FRAME_ROLES

    def test_every_paragraph_is_set_in_a_legible_role(self, pack):
        from app.design.material import shape_from
        from app.design.select import choose
        from app.renderer.typeset.policy import check

        selection = choose(shape_from("content", "A heading", self.PARAS), pack)
        assert selection
        for placement in selection.placements:
            frame = _slot_frame(selection.archetype, placement.slot)
            name = type_role_for(frame.role, placement.item.kind, pack,
                                 frame.type_role)
            assert check(pack, name, pack.role(name).size_pt) is None


class TestAnArchetypeIsAPatternNotATrace:
    """Every archetype was one specific page, abstracted, and the ids said so:
    `page-4`, `page-8`, `page-12`. Five sampled pages became five archetypes,
    so a pack described those five pages and could not compose a sixth. The
    publication's own pages are clustered by what they look like now, and each
    group becomes one archetype represented by whichever of its pages carries
    the most."""

    def test_ids_say_what_the_page_does(self, pack):
        for archetype in pack.archetypes:
            assert not archetype.id.startswith("page-"), (
                f"{pack.id}/{archetype.id} is named after the page it was "
                f"traced from rather than after what it is"
            )

    def test_an_archetype_stands_for_more_than_the_page_it_came_from(self, pack):
        """At least one archetype represents several pages. A pack where every
        archetype covers exactly one page has not recovered a pattern."""
        traced = [a for a in pack.archetypes if a.example_pages]
        assert traced
        assert any(len(a.example_pages) > 1 for a in traced)

    def test_frequency_is_the_share_of_pages_that_look_like_it(self, pack):
        """Zero on every archetype of every pack before this, which quietly
        disabled both things that read it: ties break toward the page the
        house uses most, and the selector discourages running the same
        archetype twice so a deck reproduces the house's *mix*."""
        traced = [a for a in pack.archetypes if a.example_pages]
        assert any(a.frequency > 0 for a in traced)
        for archetype in traced:
            assert 0.0 <= archetype.frequency <= 1.0

    def test_a_page_can_state_its_own_assertion(self, pack):
        """#268's gate. Deliberately not "has a headline frame": EY sets its
        assertion in a pull-quote beside a section label, which is the house
        rather than a defect."""
        from app.design.vocabulary import _states_an_assertion

        usable = [a for a in pack.archetypes if a.role in ("content", "exhibit")]
        assert any(_states_an_assertion(pack, a) for a in usable)

    def test_a_page_can_carry_a_page_of_material(self, pack):
        """The other half of the gate. An archetype that states an assertion
        and holds two sentences is not a content page."""
        usable = [a for a in pack.archetypes if a.role in ("content", "exhibit")]
        best = max(
            sum(s.max_chars or 0 for s in a.slots
                if not s.accepts or "text" in s.accepts)
            for a in usable
        )
        assert best >= 900, f"{pack.id} tops out at {best} characters a page"

    def test_what_the_publication_never_showed_is_named(self, pack):
        """A gap is reported rather than papered over: a pack missing a
        divider should say so, so a designer knows the gap is in the source
        and not in the reading."""
        from app.design.vocabulary import gaps

        assert pack.vocabulary_gaps == gaps(pack)

    def test_a_pack_with_nothing_in_it_declares_every_gap(self, pack):
        from app.design.vocabulary import CAPABILITIES, gaps

        bare = pack.model_copy(update={"archetypes": ()})
        reported = gaps(bare)
        for capability in CAPABILITIES:
            assert capability in reported

    def test_the_house_mix_is_reproducible(self, pack):
        """A deck should look like the publication's *range* of pages, so the
        pack has to offer more than one composition to choose between."""
        usable = [a for a in pack.archetypes if a.role in ("content", "exhibit")]
        assert len(usable) >= 2
