"""
Choosing a page by what it has to say, rather than by a keyword.

``select_layout`` scored a seven-value ``content_type`` enum against layout
*names* by substring match, with no model call and no knowledge of the
material - and it ran before a word of copy existed, so the geometry was
chosen and the content poured into whatever came back. With no uploaded
template it picked between eight hardcoded layouts, which is why every
generated deck looked the same.

These pin the properties of the replacement. They are about the mechanism -
that the cost discriminates, that material is never silently dropped, that
measurement and rendering agree - and deliberately not about whether a
particular page is beautiful, which is not a thing a unit test can hold.
"""
import json
import os

import pytest

from app.design.assign import INFEASIBLE, solve, total_cost
from app.design.bind import build_page
from app.design.geometry import check_page
from app.design.material import MaterialItem, shape_from
from app.design.pack import Pack
from app.design.roles import type_role_for
from app.design.schema import DesignDoc, PackRef
from app.design.select import choose, fit

PACK_PATH = os.path.join("app", "design", "packs", "ey-iif-risk.json")

LONG = [
    "The results of our inaugural survey of insurance industry CROs "
    "demonstrate the expanding and diversifying risks insurers face now, "
    "from technology disruption to new capital sources.",
    "Many of today's most pressing risks are related to the profound shifts "
    "reshaping the industry, from technology disruptions and rising "
    "regulatory requirements to new market entrants.",
    "That is true not only in terms of protecting the enterprise from "
    "near-term risks, but also for devising comprehensive plans for risks "
    "forming on the horizon.",
    "Some industry observers describe the current moment as a polycrisis, "
    "given the diversifying risks and high market volatility.",
    "But our research also presents reasons for optimism, and global "
    "economic growth is projected at 3% for 2026.",
]


@pytest.fixture(scope="module")
def pack() -> Pack:
    with open(PACK_PATH) as handle:
        return Pack.model_validate(json.load(handle))


class TestTheAssignmentIsOptimal:
    """Greedy binding puts the last two items in each other's frames."""

    def test_it_matches_the_brute_force_optimum(self):
        cost = [[4.0, 1.0, 3.0], [2.0, 0.0, 5.0], [3.0, 2.0, 2.0]]
        best = min(
            cost[0][a] + cost[1][b] + cost[2][c]
            for a in range(3) for b in range(3) for c in range(3)
            if len({a, b, c}) == 3
        )
        assert total_cost(cost, solve(cost)) == pytest.approx(best)

    def test_greedy_would_have_done_worse(self):
        """The reason this is solved rather than bound item by item: taking
        the cheapest pair first strands the rows that follow."""
        cost = [[1.0, 2.0], [1.1, 9.0]]
        greedy = cost[0][0] + cost[1][1]      # 10.0
        assert total_cost(cost, solve(cost)) < greedy

    def test_an_infeasible_pairing_is_never_chosen(self):
        cost = [[INFEASIBLE, 3.0], [2.0, INFEASIBLE]]
        assert solve(cost) == [1, 0]

    def test_more_material_than_slots_leaves_some_unassigned(self):
        assert sum(x is not None for x in solve([[1.0], [2.0], [3.0]])) == 1


class TestMaterialIsDescribedBeforeAnyLayoutIsChosen:
    def test_it_counts_what_is_actually_there(self):
        shape = shape_from("content", "A heading", LONG)
        counts = shape.counts()
        assert counts["text"] == len(LONG) + 1  # the heading is material too
        assert shape.total_chars > 0

    def test_the_heading_is_material_and_knows_it_is_a_heading(self):
        shape = shape_from("content", "A heading", LONG)
        heading = next(i for i in shape.items if i.id == "heading")
        assert heading.role_hint == "heading"
        assert heading.importance == 1.0

    def test_a_thin_page_offers_no_pull_quote(self):
        """A quote pulled from two paragraphs sets one of them twice."""
        assert shape_from("content", "H", ["Short copy, one line only."]).quote == ""

    def test_a_full_page_offers_one(self):
        assert shape_from("content", "H", LONG).quote


class TestTheCostDiscriminates:
    def test_an_archetype_that_cannot_carry_the_material_is_not_a_candidate(
        self, pack
    ):
        """Dropping material is the worst outcome available, so it is not on
        the menu - costing it let a page that silently lost five of its seven
        paragraphs score better than one that placed all of them."""
        shape = shape_from("content", "A heading", LONG)
        rejected = [a.id for a in pack.archetypes if fit(shape, a, pack) is None]
        assert rejected, "every archetype accepted material it cannot hold"

    def test_thin_material_scores_badly_against_every_archetype(self, pack):
        """The right answer for a hundred words against a pack built for six
        hundred is 'none of these', and the cost should say so."""
        thin = shape_from("content", "A heading", ["One short paragraph."])
        full = shape_from("content", "A heading", LONG)
        thin_best = choose(thin, pack)
        full_best = choose(full, pack)
        assert thin_best and full_best
        assert thin_best.cost > full_best.cost

    def test_density_is_measured_in_characters_not_item_counts(self, pack):
        """Three paragraphs and eight paragraphs are both 'several items';
        one fills a spread and the other leaves it two thirds white."""
        short = shape_from("content", "H", ["Tiny.", "Also tiny.", "Tiny too."])
        long_ = shape_from("content", "H", LONG)
        a = choose(short, pack)
        b = choose(long_, pack)
        assert a and b
        assert a.terms["density"] > b.terms["density"]

    def test_selection_is_deterministic(self, pack):
        shape = shape_from("content", "A heading", LONG)
        assert choose(shape, pack).archetype.id == choose(shape, pack).archetype.id


class TestMaterialGoesWhereItBelongs:
    def test_a_paragraph_may_not_take_a_display_frame(self, pack):
        """Not a tight fit - the wrong kind of thing. Infeasible rather than
        expensive, or the solver trades it against a rounding error and sets
        a body paragraph at pull-quote size across the page."""
        shape = shape_from("content", "A heading", LONG)
        selection = choose(shape, pack)
        assert selection
        for placement in selection.placements:
            frame = next(f for f in _frames(selection.archetype)
                         if f.id == placement.slot.frame_id)
            if frame.role in ("headline", "pullquote", "standfirst"):
                assert placement.item.role_hint in ("heading", "quote"), (
                    f"{placement.item.text[:40]!r} was bound into a "
                    f"{frame.role} frame"
                )

    def test_a_quote_never_becomes_the_headline(self, pack):
        """The guarantee that matters. A pull-quote promoted to the headline
        makes the page's assertion a sentence repeated from its own body
        copy. A quote set as a standfirst is a normal device, and one landing
        in a plain column is a legitimate fallback - it is then simply set as
        body - so neither is a fault.
        """
        shape = shape_from("content", "A heading", LONG)
        selection = choose(shape, pack)
        for placement in selection.placements:
            if placement.item.role_hint != "quote":
                continue
            frame = next(f for f in _frames(selection.archetype)
                         if f.id == placement.slot.frame_id)
            assert frame.role != "headline", (
                "a sentence lifted from the body copy was promoted to the "
                "page's headline"
            )

    def test_the_heading_prefers_a_headline_frame_when_one_exists(self, pack):
        """Stated as a preference in the cost rather than a rule, because an
        archetype without a headline frame is still usable - but a page whose
        assertion is set as a body paragraph has buried its own argument."""
        from app.design.select import DISPLAY_FRAME_ROLES, pair_cost

        archetype = next(
            (a for a in pack.archetypes
             if any(f.role == "headline" for f in _frames(a))), None
        )
        if archetype is None:
            pytest.skip("this pack extracted no headline frame")
        heading = MaterialItem(id="h", kind="text", text="A short heading",
                               importance=1.0, role_hint="heading")
        grid = archetype.grid or pack.grid
        by_role = {}
        for slot in archetype.slots:
            frame = next(f for f in _frames(archetype) if f.id == slot.frame_id)
            by_role.setdefault(frame.role, []).append(
                pair_cost(heading, slot, pack, archetype, grid)
            )
        if "headline" in by_role and "column" in by_role:
            assert min(by_role["headline"]) < min(by_role["column"])
        assert DISPLAY_FRAME_ROLES


class TestMeasurementAndRenderingAgree:
    def test_one_resolver_decides_the_type_role(self, pack):
        """When the fit cost measured the body role and the binder set the
        frame's own role, a paragraph was measured at 16pt and drawn at 30pt.
        The fit reported no overflow and the page ran off the bottom."""
        assert type_role_for("standfirst", "text", pack) == "standfirst"
        assert type_role_for("column", "text", pack) == "body"
        assert type_role_for("pullquote", "quote", pack) == "pullquote"

    def test_an_unknown_frame_role_falls_back_to_body(self, pack):
        assert type_role_for("nonesuch", "text", pack) == "body"


class TestTheBuiltPageIsRenderable:
    def test_it_places_without_geometry_problems(self, pack):
        shape = shape_from("content", "A heading", LONG)
        selection = choose(shape, pack)
        page = build_page("p1", shape, selection, pack)
        doc = DesignDoc(pack=PackRef(id=pack.id), canvas=pack.canvas,
                        grid=pack.grid, pages=(page,))
        assert check_page(doc, page) == []

    def test_every_item_reaches_the_page(self, pack):
        shape = shape_from("content", "A heading", LONG)
        selection = choose(shape, pack)
        assert selection.unplaced == ()

    def test_an_empty_frame_does_not_reserve_space(self, pack):
        """An archetype with a figure slot and a page with no figure left a
        third of the page white where the reference has a bleed panel."""
        shape = shape_from("content", "A heading", LONG)
        selection = choose(shape, pack)
        page = build_page("p1", shape, selection, pack)
        for frame in page.all_frames():
            if not frame.content and not frame.children:
                assert frame.ground or frame.scrim, (
                    f"frame {frame.id!r} is empty and carries no ground"
                )

    def test_the_page_records_why_it_looks_like_this(self, pack):
        shape = shape_from("content", "A heading", LONG)
        page = build_page("p1", shape, choose(shape, pack), pack)
        assert page.provenance.selected_by == "fit"
        assert page.provenance.fit_cost is not None


def _frames(archetype):
    for frame in archetype.frames:
        yield from frame.walk()
