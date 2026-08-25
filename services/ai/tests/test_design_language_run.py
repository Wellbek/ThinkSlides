"""
Putting a run on a pack, and what that has to guarantee.

Everything in `app/design` was built, gated and unreachable: no module under
`app/agent` imported it, so every real generation went through `select_layout`
and rendered title-plus-bullets. `app/design/run.py` is the seam, and these
are the properties that make it safe to route a real run through.

The one that matters most is that material is never dropped. `fit` refuses an
archetype that cannot carry every item precisely so that losing a paragraph is
not on the menu, and a bridge that quietly truncated instead would undo that
in one line.
"""
import pytest

from app.design import run, vocabulary
from app.design.material import shape_from
from app.design.select import choose, reading_order


def text(*lines):
    return {"type": "text", "runs": [{"text": line} for line in lines]}


PARAS = [
    "The results of our inaugural survey of insurance industry CROs "
    "demonstrate the expanding and diversifying risks insurers face today.",
    "Many of the most pressing risks relate to the profound shifts reshaping "
    "the industry and to rising regulatory requirements.",
    "That is true not only for near-term risks but also for devising plans "
    "for risks forming on the horizon.",
]

DECK = [
    {"slideIndex": 0, "contents": [text("Insurance risk in 2026")]},
    {"slideIndex": 1, "contents": [text("What the CROs told us"), text(*PARAS)]},
    {"slideIndex": 2, "contents": [text("Where this leaves us")]},
]


@pytest.fixture(scope="module", params=run.available())
def pack(request):
    return run.load(request.param)


class TestEveryPackCanBuildAWholeDeck:
    """Every archetype in every shipped pack has role `content`, because they
    were traced from sampled *design* pages. A deck needs an opener and a
    closer too, and `fit` correctly refuses a role that does not exist."""

    def test_a_publication_that_never_shows_an_opener_gets_one_composed(self):
        """EY recovers a real divider from its own pages and WTI a real
        cover, now that archetypes come from clustering rather than from five
        page numbers. Neither publication shows a *closing* page, so that one
        is still composed - which is the case this exists for."""
        for pack_id in run.available():
            roles = {a.role for a in run.load(pack_id).archetypes}
            assert "closing" in roles
        composed = vocabulary.composed_ids(run.load("ey-iif-risk"))
        assert any(c.endswith("closing") for c in composed)

    def test_loading_supplies_the_missing_roles(self, pack):
        roles = {archetype.role for archetype in pack.archetypes}
        for needed in vocabulary.REQUIRED_ROLES:
            assert needed in roles, f"{pack.id} still cannot build a {needed}"

    def test_a_composed_archetype_says_it_was_composed(self, pack):
        composed = vocabulary.composed_ids(pack)
        assert composed
        for archetype_id in composed:
            entry = pack.archetype(archetype_id)
            assert entry.frequency == 0.0, (
                "a composed page must not claim the publication uses it"
            )
            assert "Not traced" in entry.note

    def test_a_real_archetype_is_never_replaced(self, pack):
        """`ensure` is additive, and idempotent."""
        once = vocabulary.ensure(pack)
        twice = vocabulary.ensure(once)
        assert len(once.archetypes) == len(twice.archetypes)


class TestTheDisplayRungIsDerivedAndBounded:
    def test_an_opener_is_set_larger_than_the_ramp_s_top_rung(self, pack):
        """Not "larger than the headline": a pack need not have a rung named
        headline. EY's content pages set their assertion in a pull-quote, so
        its recovered ramp tops out there, and a check that demanded a
        headline would report a defect in a publication that has none."""
        top = max(role.size_pt for name, role in pack.type_roles.items()
                  if name != vocabulary.DISPLAY_ROLE)
        assert pack.type_roles["display"].size_pt >= top

    def test_it_stays_inside_a_share_of_the_page(self, pack):
        """WTI's headline/standfirst ratio is 2.94. Unclamped that sets a
        cover line at 147pt on an 11-inch page."""
        share = pack.type_roles["display"].size_pt / (pack.canvas.h_in * 72.0)
        assert vocabulary.DISPLAY_MIN_SHARE <= share <= vocabulary.DISPLAY_MAX_SHARE

    def test_the_frame_and_not_the_pack_carries_the_override(self, pack):
        """Scaling the pack's own headline role would move every content page
        with it. A composed opener's frame asks for the rung instead.

        Only composed openers: WTI recovers a real cover from its own pages,
        and a traced frame is set in whatever the publication set it in."""
        composed = [a for a in pack.archetypes
                    if a.id in vocabulary.composed_ids(pack)
                    and a.role in ("cover", "closing", "section")]
        assert composed
        for archetype in composed:
            headline = next(f for f in archetype.frames
                            if f.role == "headline")
            assert headline.type_role == vocabulary.DISPLAY_ROLE

    def test_measurement_and_drawing_read_the_same_override(self, pack):
        """The bug this guards against set a block at one size and measured it
        at another, so a page overflowed while reporting that it fit."""
        from app.design.roles import type_role_for

        # Without an override the frame's own role decides, falling back to
        # body where the pack has no such rung - EY has no `headline`.
        expected = "headline" if "headline" in pack.type_roles else "body"
        assert type_role_for("headline", "text", pack) == expected
        assert type_role_for("headline", "text", pack, "display") == "display"


class TestMaterialIsReadFromWhatTheModelAlreadyProduces:
    def test_a_single_run_text_block_becomes_the_heading(self):
        shape = run.material_from(DECK[1], index=1, total=3)
        assert shape.heading == "What the CROs told us"

    def test_every_paragraph_survives(self):
        shape = run.material_from(DECK[1], index=1, total=3)
        body = [i.text for i in shape.items if i.role_hint == "body"]
        assert body == PARAS

    def test_an_explicit_heading_field_wins(self):
        slide = {"slideIndex": 1, "heading": "Stated",
                 "contents": [text("Not the heading")]}
        assert run.material_from(slide).heading == "Stated"

    def test_exhibits_are_recognised_by_kind(self):
        slide = {"slideIndex": 1, "contents": [
            text("H"), {"type": "chart", "chart": {}},
            {"type": "table", "table": {}},
        ]}
        kinds = {i.kind for i in run.material_from(slide).items}
        assert {"chart", "table"} <= kinds

    def test_an_unknown_block_keeps_its_copy(self):
        """Silently discarding it is the failure this layer exists to end."""
        slide = {"slideIndex": 1, "contents": [
            text("H"), {"type": "somethingNew", "runs": [{"text": "Kept."}]},
        ]}
        assert any(i.text == "Kept." for i in run.material_from(slide).items)

    def test_the_first_page_is_a_cover_and_the_last_a_closing(self):
        assert run.material_from(DECK[0], index=0, total=3).role == "cover"
        assert run.material_from(DECK[2], index=2, total=3).role == "closing"

    def test_a_declared_role_is_believed(self):
        slide = {"slideIndex": 4, "role": "section", "contents": [text("Part two")]}
        assert run.material_from(slide, index=4, total=9).role == "section"


class TestComposingADeck:
    def test_a_page_comes_out_for_every_slide(self, pack):
        doc = run.compose(DECK, pack)
        assert len(doc.pages) >= len(DECK)

    def test_pages_carry_the_role_they_were_asked_for(self, pack):
        doc = run.compose(DECK, pack)
        assert doc.pages[0].role == "cover"
        assert doc.pages[-1].role == "closing"

    def test_nothing_is_dropped(self, pack):
        """The property `fit` refuses archetypes to protect. A bridge that
        truncated instead would undo it in one line."""
        doc = run.compose(DECK, pack)
        rendered = " ".join(
            block.text
            for page in doc.pages
            for frame in page.all_frames()
            for block in frame.content
            if getattr(block, "text", "")
        )
        for paragraph in PARAS:
            assert paragraph in rendered

    def test_a_page_too_full_for_any_archetype_is_split_not_cut(self, pack):
        many = [{"slideIndex": 0, "contents": [
            text("A heading"), text(*(PARAS * 6)),
        ]}]
        doc = run.compose(many, pack)
        rendered = " ".join(
            block.text
            for page in doc.pages
            for frame in page.all_frames()
            for block in frame.content
            if getattr(block, "text", "")
        )
        assert rendered.count(PARAS[0]) == 6

    def test_the_document_validates(self, pack):
        from app.renderer.designdoc_renderer import check_doc

        assert check_doc(run.compose(DECK, pack)) == []

    def test_it_renders(self, pack, tmp_path):
        from app.renderer.designdoc_renderer import render_doc

        out = str(tmp_path / f"{pack.id}.pptx")
        render_doc(run.compose(DECK, pack), pack, out)
        import os
        assert os.path.getsize(out) > 0

    def test_page_ids_are_unique_even_after_a_split(self, pack):
        many = [{"slideIndex": i, "contents": [text("H"), text(*(PARAS * 4))]}
                for i in range(3)]
        doc = run.compose(many, pack)
        assert len({page.id for page in doc.pages}) == len(doc.pages)


class TestTheDesignDirectionIsNotOverriddenByThePack:
    """A pack is a composition language. The colours are the one design
    decision the user was actually asked to make, and choosing a house style
    for them must not silently overrule it."""

    DIRECTION = {"palette": {"colors": [
        {"name": "primary", "value": "#1E8449"},
        {"name": "accent", "value": "#F4D03F"},
    ]}}

    def test_the_chosen_colours_reach_the_pack(self, pack):
        dressed = run.overlay(pack, self.DIRECTION)
        assert dressed.colors.accent == "#1E8449"
        assert dressed.colors.accent_alt == "#F4D03F"

    def test_roles_the_direction_does_not_name_are_left_alone(self, pack):
        dressed = run.overlay(pack, {"palette": {"colors": [
            {"name": "primary", "value": "#1E8449"},
        ]}})
        assert dressed.colors.accent_alt == pack.colors.accent_alt, (
            "an accent that gets collapsed onto the brand colour stops "
            "reading as an accent"
        )

    def test_the_composition_is_untouched(self, pack):
        dressed = run.overlay(pack, self.DIRECTION)
        assert dressed.grid == pack.grid
        assert len(dressed.archetypes) == len(pack.archetypes)

    def test_a_direction_with_nothing_to_say_changes_nothing(self, pack):
        assert run.overlay(pack, {}) is pack
        assert run.overlay(pack, None) is pack

    def test_a_named_face_is_taken(self, pack):
        dressed = run.overlay(pack, {"typography": {"fonts": {
            "majorFont": "Georgia", "minorFont": "Verdana"}}})
        assert dressed.display_face == "Georgia"
        assert dressed.body_face == "Verdana"


class TestChoosingAPackIsMeasured:
    def test_a_pack_is_chosen_for_a_deck(self):
        assert run.for_deck(DECK) is not None

    def test_the_choice_is_deterministic(self):
        assert run.for_deck(DECK).id == run.for_deck(DECK).id

    def test_it_answers_from_the_plan_before_any_copy_exists(self):
        plan = {"slides": [
            {"slideIndex": 0, "title": "Insurance risk in 2026",
             "contentType": "title", "keyPoints": []},
            {"slideIndex": 1, "title": "What the CROs told us",
             "contentType": "content", "keyPoints": PARAS},
            {"slideIndex": 2, "title": "Where this leaves us",
             "contentType": "conclusion", "keyPoints": []},
        ]}
        assert run.for_plan(plan) is not None

    def test_a_planned_title_slide_becomes_a_cover(self):
        plan = {"slides": [{"slideIndex": 0, "title": "T",
                            "contentType": "title", "keyPoints": []}]}
        assert run.slides_from_plan(plan)[0]["role"] == "cover"

    def test_an_empty_plan_chooses_nothing_rather_than_guessing(self):
        assert run.for_plan({}) is None
        assert run.for_plan(None) is None

    def test_a_named_candidate_set_is_honoured(self):
        chosen = run.for_deck(DECK, candidates=["wti-2025"])
        assert chosen.id == "wti-2025"

    def test_an_unknown_pack_names_what_exists(self):
        with pytest.raises(KeyError) as excinfo:
            run.load("no-such-pack")
        for pack_id in run.available():
            assert pack_id in str(excinfo.value)


class TestMaterialHasAnOrder:
    """Minimising each pairing independently put paragraph one in a caption
    slot at the top of the page, paragraph two in the right-hand column and
    paragraph three at the bottom left. That reads as three fragments."""

    def test_text_slots_are_ranked_down_then_across(self, pack):
        content = next(a for a in pack.archetypes if a.role == "content")
        grid = content.grid or pack.grid
        order = reading_order(content, pack, grid)
        assert order
        assert min(order.values()) == 0.0
        assert max(order.values()) == pytest.approx(1.0)

    def test_the_first_paragraph_does_not_land_after_the_last(self, pack):
        shape = shape_from("content", "A heading", PARAS)
        selection = choose(shape, pack)
        assert selection
        content = selection.archetype
        order = reading_order(content, pack, content.grid or pack.grid)
        placed = {
            placement.item.id: order.get(placement.slot.frame_id)
            for placement in selection.placements
            if placement.item.role_hint == "body"
        }
        ranked = [placed.get(f"text-{i}") for i in range(len(PARAS))]
        ranked = [value for value in ranked if value is not None]
        if len(ranked) > 1:
            assert ranked[0] <= ranked[-1]
