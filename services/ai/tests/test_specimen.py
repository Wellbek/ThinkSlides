"""
Previewing a design system that has no deck behind it.

Preview PNGs exist as a by-product of a finished deck. To show a user three
candidate design systems *before* anything is generated, there has to be a way
to render a design system on its own - so: three fixed lorem-ipsum slides,
through the real renderer, one LibreOffice conversion per direction.

The copy is identical in every direction on purpose. Holding it constant is
what makes three previews a comparison of design rather than a comparison of
writing, and the tests below check that property directly rather than trusting
it.
"""

import asyncio
import os
import shutil
import tempfile

import pytest

from app.design_system import get_default_design_system
from app.renderer import specimen
from app.renderer.preview_generator import PreviewGenerator


@pytest.fixture(scope="module")
def previews():
    directory = tempfile.mkdtemp(prefix="ts_specimen_out_")
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _dark_ground() -> dict:
    """A design system that sets its content slides on a dark ground."""
    design_system = get_default_design_system("monochrome")
    design_system["palette"]["colors"] = [
        {"name": "primary", "value": "#0B1F2A", "type": "hex"},
        {"name": "secondary", "value": "#1F4E79", "type": "hex"},
        {"name": "accent", "value": "#00B0F0", "type": "hex"},
        {"name": "dark", "value": "#041C2C", "type": "hex"},
        {"name": "light", "value": "#E8EEF2", "type": "hex"},
        {"name": "white", "value": "#FFFFFF", "type": "hex"},
        {"name": "gray", "value": "#8C97A8", "type": "hex"},
    ]
    return design_system


def _missing_typeface() -> dict:
    """A design system naming a font no machine here has."""
    design_system = get_default_design_system()
    design_system["typography"]["titleFont"]["typeface"] = "Definitely Not Installed"
    design_system["typography"]["bodyFont"]["typeface"] = "Also Not Installed"
    return design_system


class TestTheSpecimenDeck:
    def test_it_is_three_slides(self):
        """Decided at plan review: three specimens per direction."""
        assert len(specimen.specimen_slides()) == 3

    def test_they_are_a_cover_a_dense_page_and_a_chart(self):
        slides = specimen.specimen_slides()
        kinds = [
            {content["type"] for content in slide["contents"]}
            for slide in slides
        ]

        assert slides[0]["layoutId"] == "title"
        assert len(slides[1]["contents"][1]["runs"]) >= 5
        assert "chart" in kinds[2]

    def test_the_copy_is_fixed_so_the_comparison_is_of_design(self):
        assert specimen.specimen_slides() == specimen.specimen_slides()

    def test_the_copy_is_filler_and_the_deck_checks_would_say_so(self):
        """
        Deliberate, and worth stating: ``output_qa`` rejects leftover lorem
        ipsum, because in a generated deck it is a defect. A specimen is not a
        deck - it never goes through that check - and holding the copy
        constant is the whole point of it.
        """
        from app.agent.tools import output_qa

        issues = output_qa.check_content(specimen.specimen_slides())

        assert any("Lorem ipsum" in issue["detail"] for issue in issues)


class TestRenderingOneDirection:
    def test_a_default_design_system_produces_three_pngs(self, previews):
        urls = specimen.render_specimens(
            get_default_design_system(), "sess-default", "clean", previews
        )

        assert len(urls) == 3
        for index, url in enumerate(urls, start=1):
            assert url == f"/previews/sess-default/direction/clean/{index}.png"
            assert os.path.exists(os.path.join(
                previews, "sess-default", "direction", "clean", f"{index}.png"
            ))

    def test_a_dark_ground_system_renders(self, previews):
        urls = specimen.render_specimens(
            _dark_ground(), "sess-dark", "dark-analyst", previews
        )

        assert len(urls) == 3

    def test_a_system_naming_a_missing_typeface_still_renders(self, previews):
        """A font nobody has must degrade, not fail: the user's brand font is
        routinely not installed where we render."""
        urls = specimen.render_specimens(
            _missing_typeface(), "sess-font", "editorial", previews
        )

        assert len(urls) == 3

    def test_it_goes_through_the_real_renderer(self, previews):
        """A preview that flatters a direction the deck will not honour is
        worse than no preview."""
        from pptx import Presentation

        from app.renderer.pptx_renderer import PPTXRenderer

        work_dir = tempfile.mkdtemp(prefix="ts_specimen_real_")
        try:
            path = os.path.join(work_dir, "s.pptx")
            PPTXRenderer(template_path=None,
                         design_system=get_default_design_system()).render(
                slides=specimen.specimen_slides(),
                metadata={"title": "Design direction"}, output_path=path,
            )
            assert len(Presentation(path).slides) == 3
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_the_images_differ_between_two_different_systems(self, previews):
        """Same copy, different design - so the pixels have to differ."""
        specimen.render_specimens(get_default_design_system(), "sess-cmp",
                                  "light", previews)
        specimen.render_specimens(_dark_ground(), "sess-cmp", "dark", previews)

        def read(direction):
            path = os.path.join(previews, "sess-cmp", "direction", direction,
                                "2.png")
            with open(path, "rb") as handle:
                return handle.read()

        assert read("light") != read("dark")

    def test_a_broken_design_system_returns_nothing_rather_than_raising(
            self, previews):
        urls = specimen.render_specimens(
            {"palette": "not a palette"}, "sess-broken", "broken", previews
        )

        assert urls == []

    def test_the_urls_on_disk_can_be_listed_again(self, previews):
        specimen.render_specimens(get_default_design_system(), "sess-list",
                                  "clean", previews)

        assert specimen.specimen_urls("sess-list", "clean", previews) == [
            f"/previews/sess-list/direction/clean/{n}.png" for n in (1, 2, 3)
        ]

    def test_listing_a_direction_never_rendered_is_empty(self, previews):
        assert specimen.specimen_urls("sess-list", "nothing", previews) == []


class TestRenderingThreeDirectionsAtOnce:
    @pytest.fixture(scope="class")
    @staticmethod
    def rendered(previews):
        directions = [
            {"id": "clean-minimal", "designSystem": get_default_design_system()},
            {"id": "dark-analyst", "designSystem": _dark_ground()},
            {"id": "editorial-serif", "designSystem": _missing_typeface()},
        ]
        return asyncio.run(
            specimen.render_directions(directions, "sess-three", previews)
        )

    def test_every_direction_gets_its_specimens(self, rendered):
        assert set(rendered) == {"clean-minimal", "dark-analyst",
                                 "editorial-serif"}
        assert all(len(urls) == 3 for urls in rendered.values())

    def test_the_directions_do_not_collide_on_disk(self, rendered, previews):
        root = os.path.join(previews, "sess-three", "direction")

        assert sorted(os.listdir(root)) == ["clean-minimal", "dark-analyst",
                                            "editorial-serif"]
        for direction in os.listdir(root):
            assert sorted(os.listdir(os.path.join(root, direction))) == \
                ["1.png", "2.png", "3.png"]

    def test_one_direction_failing_does_not_take_the_others(self, previews):
        directions = [
            {"id": "good", "designSystem": get_default_design_system()},
            {"id": "bad", "designSystem": {"palette": "not a palette"}},
        ]

        rendered = asyncio.run(
            specimen.render_directions(directions, "sess-partial", previews)
        )

        assert len(rendered["good"]) == 3
        assert rendered["bad"] == []

    def test_a_direction_with_no_id_is_skipped(self, previews):
        rendered = asyncio.run(specimen.render_directions(
            [{"designSystem": get_default_design_system()}], "sess-noid", previews
        ))

        assert rendered == {}


class TestDirectionPreviewsAreThrowaway:
    def test_choosing_one_removes_the_rest(self, previews):
        directions = [
            {"id": f"d{n}", "designSystem": get_default_design_system()}
            for n in range(3)
        ]
        asyncio.run(specimen.render_directions(directions, "sess-clean", previews))

        specimen.cleanup_directions("sess-clean", previews, keep="d1")

        root = os.path.join(previews, "sess-clean", "direction")
        assert os.listdir(root) == ["d1"]

    def test_cleaning_up_with_nothing_kept_removes_the_directory(self, previews):
        specimen.render_specimens(get_default_design_system(), "sess-gone",
                                  "d", previews)

        specimen.cleanup_directions("sess-gone", previews)

        assert not os.path.exists(
            os.path.join(previews, "sess-gone", "direction")
        )

    def test_the_session_ending_takes_them_with_it(self, previews):
        specimen.render_specimens(get_default_design_system(), "sess-end",
                                  "d", previews)

        PreviewGenerator(previews).cleanup_session("sess-end")

        assert not os.path.exists(os.path.join(previews, "sess-end"))

    def test_cleaning_a_session_with_no_directions_is_harmless(self, previews):
        specimen.cleanup_directions("sess-never-existed", previews)


class TestDescribingADirection:
    def test_it_reports_what_a_thumbnail_cannot_show(self):
        described = specimen.describe(get_default_design_system())

        assert described["displayFace"] == "Arial"
        assert described["displayIsSerif"] is False
        assert described["blocksMedian"]

    def test_an_empty_system_describes_as_empty_rather_than_raising(self):
        assert specimen.describe({})["displayFace"] is None
