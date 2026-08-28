"""
Asking the agent to design, and reading back what it decided.

The prompt is the whole of the feature: everything downstream derives geometry
from these decisions, so what matters is that they are about *this* deck
rather than three arrangements of the same furniture - which is what the 4x4
enum could only ever produce.
"""
import pytest

from app.design import authoring
from app.design.authored import Canvas, to_pack
from app.design.vocabulary import ensure

CANVAS = Canvas(w_in=13.333, h_in=7.5)

LEDGER = {
    "name": "Box office ledger", "rationale": "receipts read as a ledger",
    "columns": 6, "bodyPt": 13, "ratios": {"display": 4.2, "headline": 2.8},
    "displayFace": "Georgia", "bodyFace": "Arial", "ground": "#FFFFFF",
    "ink": "#1A1A1A", "accent": "#B3261E", "accentAlt": "#E8A33D",
    "density": "balanced",
    "pages": [
        {"name": "Receipts", "role": "content", "frequency": 0.6, "slots": [
            {"kind": "heading", "span": 6, "required": True},
            {"kind": "statistic", "span": 6},
            {"kind": "prose", "span": 3}, {"kind": "figure", "span": 3}]},
        {"name": "Cover", "role": "cover", "frequency": 0.1, "slots": [
            {"kind": "heading", "span": 5, "required": True}]},
    ],
}

WIDE = {
    "name": "Wide screen", "rationale": "a franchise shown, not tabulated",
    "columns": 12, "bodyPt": 11, "ratios": {"display": 2.6},
    "displayFace": "Arial", "bodyFace": "Arial", "accent": "#0B6E4F",
    "pages": [{"name": "Spread", "role": "content", "frequency": 0.7, "slots": [
        {"kind": "heading", "span": 12, "required": True},
        {"kind": "prose", "span": 4}, {"kind": "prose", "span": 4}]}],
}


class TestTheAskIsForDecisionsNotGeometry:
    def test_the_prompt_names_this_deck(self):
        """A deck about film receipts and one about clinical trials should not
        arrive at the same measure by coincidence."""
        assert "Cars movies" in authoring.prompt(
            "Financial overview of all Cars movies")

    def test_it_asks_for_the_decisions(self):
        text = authoring.prompt("anything")
        for decision in ("columns", "ratios", "accent", "pages", "density"):
            assert decision in text

    def test_it_forbids_geometry(self):
        text = authoring.prompt("anything")
        assert "do not give positions" in text
        assert "rectangles" in text

    def test_it_states_what_makes_a_set_good(self):
        text = authoring.prompt("anything")
        assert "differing only in hue is rejected" in text
        assert "would fit any deck" in text


class TestWhatComesBackIsRead:
    def test_a_complete_language_is_kept(self):
        [language] = authoring.read({"languages": [LEDGER]})
        assert language.name == "Box office ledger"
        assert language.columns == 6
        assert language.body_pt == 13.0
        assert language.display_face == "Georgia"
        assert language.accent == "#B3261E"

    def test_the_page_vocabulary_survives(self):
        [language] = authoring.read({"languages": [LEDGER]})
        assert {page.name for page in language.pages} == {"receipts", "cover"}
        receipts = next(p for p in language.pages if p.name == "receipts")
        assert [slot.kind for slot in receipts.slots] == [
            "heading", "statistic", "prose", "figure"]

    def test_a_language_with_no_pages_is_dropped_not_repaired(self):
        """A repaired language is one nobody designed, and offering the user a
        thing invented by a parser is worse than offering them two things
        somebody chose."""
        assert authoring.read({"languages": [{"name": "Broken", "pages": []}]}) == []

    def test_a_slot_of_an_unknown_kind_is_dropped(self):
        payload = {"languages": [dict(LEDGER, pages=[{
            "name": "odd", "slots": [{"kind": "sparkline", "span": 2},
                                     {"kind": "prose", "span": 3}]}])]}
        [language] = authoring.read(payload)
        assert [slot.kind for slot in language.pages[0].slots] == ["prose"]

    @pytest.mark.parametrize("field,value,expected", [
        ("columns", 99, 16), ("columns", 0, 2),
        ("bodyPt", 900, 40.0), ("bodyPt", 1, 5.0),
        ("marginRatio", 0.9, 0.24),
    ])
    def test_an_impossible_value_is_clamped(self, field, value, expected):
        [language] = authoring.read({"languages": [dict(LEDGER, **{field: value})]})
        actual = getattr(language, {"columns": "columns", "bodyPt": "body_pt",
                                    "marginRatio": "margin_ratio"}[field])
        assert actual == pytest.approx(expected)

    def test_a_bad_colour_falls_back_rather_than_failing(self):
        [language] = authoring.read({"languages": [dict(LEDGER, accent="reddish")]})
        assert language.accent.startswith("#")

    def test_nonsense_yields_nothing_rather_than_raising(self):
        assert authoring.read({}) == []
        assert authoring.read({"languages": "not a list"}) == []
        assert authoring.read({"languages": [None, 7, "x"]}) == []


class TestASetHasToActuallyDiffer:
    """Three languages differing only in hue is the failure this replaces.
    Checked rather than merely requested - a prompt asking for variety and a
    reader accepting sameness teaches the model that the ask was decorative."""

    def test_a_varied_set_passes(self):
        languages = authoring.read({"languages": [LEDGER, WIDE]})
        assert authoring.differs_enough(languages) is None

    def test_one_measure_across_the_set_is_named(self):
        languages = authoring.read({"languages": [LEDGER, dict(LEDGER, name="Twin")]})
        reason = authoring.differs_enough(languages)
        assert reason and "6 columns" in reason

    def test_one_page_vocabulary_across_the_set_is_named(self):
        languages = authoring.read({"languages": [
            LEDGER, dict(LEDGER, name="Twin", columns=9)]})
        reason = authoring.differs_enough(languages)
        assert reason and "same kinds of page" in reason

    def test_a_single_language_is_not_judged_against_itself(self):
        assert authoring.differs_enough(authoring.read({"languages": [LEDGER]})) is None


class TestEachOneDerivesIntoAWorkingPack:
    @pytest.mark.parametrize("raw", [LEDGER, WIDE])
    def test_it_becomes_a_pack_that_passes_its_gates(self, raw):
        from app.design.geometry import check_page
        from app.design.schema import DesignDoc, Page, PackRef

        [language] = authoring.read({"languages": [raw]})
        pack = ensure(to_pack(language, CANVAS))
        for archetype in pack.archetypes:
            page = Page(id="p", archetype=archetype.id, grid=archetype.grid,
                        frames=archetype.frames)
            doc = DesignDoc(pack=PackRef(id=pack.id), canvas=pack.canvas,
                            grid=archetype.grid or pack.grid, pages=(page,))
            assert not [p for p in check_page(doc, page) if "overlap" in p]

    def test_two_languages_produce_structurally_different_packs(self):
        one, other = (ensure(to_pack(language, CANVAS)) for language in
                      authoring.read({"languages": [LEDGER, WIDE]}))
        assert one.grid.columns != other.grid.columns
        assert one.role("headline").size_pt != other.role("headline").size_pt
        assert one.display_face != other.display_face

    def test_a_language_that_forgot_a_page_role_says_so(self):
        """`vocabulary_gaps` names what a language cannot do rather than
        leaving it silently absent."""
        [language] = authoring.read({"languages": [WIDE]})
        pack = ensure(to_pack(language, CANVAS))
        assert pack.vocabulary_gaps
