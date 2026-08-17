"""
Four golden decks, and CI failing on drift in either direction.

Three of them assert the exhibit capability is used. The fourth asserts it is
**not** used when it should not be, which is the failure mode every other issue
in this stack has to guard against individually: a check that pushed every page
toward four exhibits would produce four fabricated exhibits on pages whose
material supports one.

Having the thin brief here means the governing constraint - exhibit count follows
the material, never a quota - is enforced once, mechanically, rather than
re-argued in each review.

The decks also have to render. A profile that holds on JSON nobody can convert is
a profile asserting nothing.
"""

import os
import shutil
import tempfile

import pytest

from app.agent.tools import output_qa
from app.eval.golden_decks import BRIEFS, GOLDEN, GOLDEN_BY_NAME, measure
from app.renderer.pptx_renderer import PPTXRenderer

pytestmark = pytest.mark.unconfigured_workspace

NAMES = [profile.name for profile in GOLDEN]


@pytest.fixture(scope="module")
def work_dir():
    path = tempfile.mkdtemp(prefix="thinkslides_golden_")
    yield path
    shutil.rmtree(path, ignore_errors=True)


class TestTheProfilesHold:
    @pytest.mark.parametrize("name", NAMES)
    def test_the_deck_has_enough_exhibits(self, name):
        profile = GOLDEN_BY_NAME[name]
        found = measure(profile.slides)

        assert found["exhibits"] >= profile.min_exhibits, (
            f"{name} has {found['exhibits']} exhibits, expected at least "
            f"{profile.min_exhibits}"
        )

    @pytest.mark.parametrize("name", NAMES)
    def test_the_deck_has_not_grown_exhibits_it_should_not_have(self, name):
        """
        The drift that matters most. A capability that starts filling pages the
        material does not support is worse than one nobody uses.
        """
        profile = GOLDEN_BY_NAME[name]
        if profile.max_exhibits is None:
            pytest.skip("no ceiling for this deck")
        found = measure(profile.slides)

        assert found["exhibits"] <= profile.max_exhibits, (
            f"{name} has grown to {found['exhibits']} exhibits, and its whole "
            f"point is that the material carries {profile.max_exhibits}"
        )

    @pytest.mark.parametrize("name", NAMES)
    def test_the_chart_vocabulary_is_actually_used(self, name):
        profile = GOLDEN_BY_NAME[name]
        found = measure(profile.slides)

        assert len(found["chartKinds"]) >= profile.min_distinct_chart_kinds, (
            f"{name} uses {found['chartKinds']}, expected at least "
            f"{profile.min_distinct_chart_kinds} distinct kinds"
        )

    @pytest.mark.parametrize("name", NAMES)
    def test_the_diagram_vocabulary_is_actually_used(self, name):
        profile = GOLDEN_BY_NAME[name]
        found = measure(profile.slides)

        assert len(found["diagramKinds"]) >= profile.min_distinct_diagram_kinds, (
            f"{name} uses {found['diagramKinds']}, expected at least "
            f"{profile.min_distinct_diagram_kinds} distinct kinds"
        )

    @pytest.mark.parametrize("name", NAMES)
    def test_composed_pages_are_present_or_absent_as_the_brief_requires(self, name):
        profile = GOLDEN_BY_NAME[name]
        found = measure(profile.slides)

        if profile.requires_composed_page:
            assert found["composedPages"] >= 1, (
                f"{name} has no page carrying two exhibits bound to regions, "
                "which is the capability it exists to assert"
            )
        if profile.forbids_composed_page:
            assert found["composedPages"] == 0, (
                f"{name} has {found['composedPages']} composed page(s). A brief "
                "with no material behind it must not get a dashboard."
            )

    @pytest.mark.parametrize("name", NAMES)
    def test_slides_carry_no_exhibit_where_the_material_carries_none(self, name):
        profile = GOLDEN_BY_NAME[name]
        found = measure(profile.slides)

        assert found["slidesWithoutExhibit"] >= profile.min_slides_without_exhibit, (
            f"{name} has {found['slidesWithoutExhibit']} slides with no exhibit, "
            f"expected at least {profile.min_slides_without_exhibit}. A slide "
            "that gained an exhibit to fill it is the failure this asserts "
            "against."
        )

    @pytest.mark.parametrize("name", NAMES)
    def test_a_mixed_deck_shows_a_spread_rather_than_a_uniform_count(self, name):
        profile = GOLDEN_BY_NAME[name]
        if profile.allows_uniform_counts:
            pytest.skip("this deck may legitimately be uniform")
        found = measure(profile.slides)

        assert not found["uniformCounts"], (
            f"{name}'s visual slides all carry {found['perSlide']} exhibits. A "
            "spread is the point: material does not come in equal portions."
        )


class TestTheDeckLevelCheckAgreesWithTheProfiles:
    """
    The check from #186 and these fixtures have to say the same thing, or one of
    them is wrong. This is the pairing that makes both trustworthy.
    """

    def test_the_thin_deck_is_not_flagged(self):
        """Sparseness is not the smell. If this ever fails, the check inverted."""
        advisories = output_qa.check_deck(GOLDEN_BY_NAME["thin_brief"].slides)

        assert advisories == [], (
            "A legitimately thin deck was flagged, which turns the check into a "
            f"filler quota: {advisories}"
        )

    @pytest.mark.parametrize("name", ["financial_review", "market_scan",
                                      "technical_architecture"])
    def test_a_varied_deck_is_not_flagged_for_uniformity(self, name):
        advisories = [
            a for a in output_qa.check_deck(GOLDEN_BY_NAME[name].slides)
            if a["type"] == "deck_exhibits_are_uniform"
        ]

        assert advisories == [], f"{name} was called uniform: {advisories}"

    def test_a_deck_of_nine_identical_charts_still_is_flagged(self):
        """The other side of the same check, so this file cannot pass vacuously."""
        uniform = [
            {"slideIndex": i, "contents": [{"type": "chart", "chart": {
                "chartType": "bar", "categories": ["a"],
                "series": [{"name": "x", "values": [1.0]}]}}]}
            for i in range(9)
        ]

        advisories = output_qa.check_deck(uniform)

        assert any(a["type"] == "deck_exhibits_are_uniform" for a in advisories)


class TestEveryGoldenDeckRenders:
    """
    A profile that holds on JSON nobody can convert asserts nothing. This is the
    part that catches a diagram kind that stopped compiling or a composed page
    whose regions no longer resolve.
    """

    @pytest.mark.parametrize("name", NAMES)
    def test_it_renders_to_a_pptx(self, name, work_dir):
        profile = GOLDEN_BY_NAME[name]
        path = os.path.join(work_dir, f"{name}.pptx")

        PPTXRenderer(template_path=None).render(
            slides=profile.slides, metadata={"title": name}, output_path=path,
        )

        assert os.path.exists(path)
        assert os.path.getsize(path) > 10_000

    @pytest.mark.parametrize("name", NAMES)
    def test_no_exhibit_is_rendered_too_small_to_read(self, name):
        """
        The floor under the chart renderer's furniture thinning. A composed page
        that divided itself further than four ways would be caught here rather
        than shipped.
        """
        issues = output_qa.check_exhibit_legibility(GOLDEN_BY_NAME[name].slides)

        assert issues == [], f"{name}: {issues}"


class TestTheBriefsAndTheDecksStayPaired:
    def test_every_profile_names_a_brief_that_exists(self):
        for profile in GOLDEN:
            assert profile.brief_key in BRIEFS, profile.name

    def test_every_brief_has_a_deck(self):
        """
        An unpaired brief is a case an end-to-end run would exercise and the
        offline checks would not.
        """
        paired = {profile.brief_key for profile in GOLDEN}

        assert set(BRIEFS) == paired

    def test_the_thin_brief_says_it_has_no_data(self):
        """
        The fixture's whole point rests on the brief. If the brief acquires
        figures, the deck should acquire exhibits and this file should change
        deliberately rather than quietly keep passing.
        """
        assert "any data" in BRIEFS["thin_brief"].lower()
