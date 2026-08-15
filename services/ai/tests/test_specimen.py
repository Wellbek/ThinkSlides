"""
Previewing a design system that has no deck behind it.

Preview PNGs exist as a by-product of a finished deck. To show a user three
candidate design systems *before* anything is generated, there has to be a way
to render a design system on its own - so: three specimen slides, through the
real renderer, one LibreOffice conversion per direction.

The copy is identical in every direction and the *shape* is not. Holding the
words constant is what makes three previews a comparison of design rather than
of writing; holding the shape constant too is what made it a comparison of
colour, which is the defect #151 opened on. Both halves of that are tested
below rather than trusted.
"""

import asyncio
import os
import shutil
import tempfile

import pytest

from app import directions
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


def _kinds(slide: dict) -> set[str]:
    return {content["type"] for content in slide["contents"]}


def _words(slides: list[dict]) -> str:
    """Every string the specimen deck puts on a page, whatever shape it took."""
    found: list[str] = []

    def walk(value):
        if isinstance(value, str):
            found.append(value.lower())
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(slides)
    return " | ".join(found)


class TestTheSpecimenDeck:
    def test_it_is_three_slides(self):
        """Decided at plan review: three specimens per direction."""
        assert len(specimen.specimen_slides()) == 3

    def test_they_are_a_cover_an_argument_and_the_evidence(self):
        slides = specimen.specimen_slides()

        assert slides[0]["layoutId"] == "title"
        assert len(slides[1]["contents"][1]["runs"]) == 3
        assert "chart" in _kinds(slides[2])

    def test_every_content_slide_is_sectioned_as_a_sample(self):
        """The copy is about the user's real topic and the figures in it were
        invented to fill a layout, so it says so wherever the direction's own
        chrome shows a section."""
        for slide in specimen.specimen_slides()[1:]:
            assert slide["section"] == specimen.SECTION_LABEL

    def test_the_copy_is_fixed_for_one_content_set(self):
        assert specimen.specimen_slides() == specimen.specimen_slides()

    def test_it_never_declares_regions(self):
        """A specimen has to render under any design system, including one
        whose grammar declares none."""
        for slide in specimen.specimen_slides("bands"):
            assert "regions" not in slide


class TestTheSameContentInDifferentShapes:
    """The point of #151: what three previews compare is what each direction
    *does* with the same material."""

    @pytest.mark.parametrize("structure", sorted(specimen.ARGUMENT_BUILDERS))
    def test_every_structure_the_directions_offer_can_be_built(self, structure):
        slides = specimen.specimen_slides(structure)

        assert len(slides) == 3
        assert slides[1]["contents"][1:]

    def test_the_vocabulary_matches_the_axis_the_model_authors_on(self):
        """A direction naming a shape nothing here builds would silently
        preview as a bulleted list."""
        assert set(directions.STRUCTURES) == set(specimen.ARGUMENT_BUILDERS)
        assert set(directions.STRUCTURES) == set(specimen.EVIDENCE_BUILDERS)

    def test_no_two_structures_shape_the_argument_the_same_way(self):
        shapes = {
            structure: sorted(_kinds(specimen.specimen_slides(structure)[1]))
            for structure in specimen.ARGUMENT_BUILDERS
        }

        assert len({tuple(v) for v in shapes.values()}) == len(shapes)

    def test_no_two_structures_shape_the_evidence_the_same_way(self):
        shapes = {
            structure: sorted(_kinds(specimen.specimen_slides(structure)[2]))
            for structure in specimen.EVIDENCE_BUILDERS
        }

        assert len({tuple(v) for v in shapes.values()}) == len(shapes)

    def test_the_words_and_the_figures_are_the_same_in_every_shape(self):
        """Same content, different design - not a different specimen deck per
        direction, which would be a comparison of writing."""
        content = specimen.default_content()
        for point in content.points:
            for structure in specimen.ARGUMENT_BUILDERS:
                words = _words(specimen.specimen_slides(structure))
                assert point.figure.lower() in words, structure
                assert point.label.lower() in words, structure

    def test_every_structure_still_shows_the_data_viz_tokens(self):
        """The chart is what a preview says about series colours and axes, so
        no structure drops it."""
        for structure in specimen.ARGUMENT_BUILDERS:
            assert "chart" in _kinds(specimen.specimen_slides(structure)[2])

    def test_a_shape_nobody_implements_previews_as_a_list(self):
        assert specimen.specimen_slides("swirls") == specimen.specimen_slides("list")


class TestTheCopyIsWrittenForTheBrief:
    def test_the_model_can_author_it(self):
        content = specimen.SpecimenContent.from_dict({
            "title": "Cold chain in 2026",
            "subtitle": "Where the margin comes from",
            "headline": "Three shifts decide the year",
            "points": [
                {"label": "Adoption", "figure": "24%", "prose": "of depots"},
                {"label": "Cost", "figure": "$1.8m", "prose": "released"},
                {"label": "Speed", "figure": "6 weeks", "prose": "to live"},
            ],
            "evidenceHeadline": "Growth held",
            "takeaway": "Both lines compounded",
            "chart": {"title": "Depots live", "categories": ["2023", "2024"],
                      "series": [{"name": "Core", "values": [18, 36]}]},
        })

        assert content.title == "Cold chain in 2026"
        assert content.points[1].figure == "$1.8m"
        assert content.series == (("Core", (18.0, 36.0)),)

    def test_it_reaches_the_slides(self):
        content = specimen.SpecimenContent.from_dict(
            {"title": "Cold chain in 2026"}
        )

        slides = specimen.specimen_slides("list", content)

        assert slides[0]["contents"][0]["runs"][0]["text"] == "Cold chain in 2026"

    def test_a_field_the_model_left_out_falls_back_rather_than_failing(self):
        content = specimen.SpecimenContent.from_dict({"title": "Only a title"})

        assert content.title == "Only a title"
        assert content.subtitle == specimen.default_content().subtitle
        assert len(content.points) == 3

    def test_nothing_authored_at_all_is_the_default_set(self):
        assert specimen.SpecimenContent.from_dict(None) == specimen.default_content()
        assert specimen.SpecimenContent.from_dict("nonsense") == \
            specimen.default_content()

    def test_two_points_instead_of_three_falls_back_to_three(self):
        """Every shape is built for three; two would leave a card row with a
        hole in it."""
        content = specimen.SpecimenContent.from_dict({"points": [
            {"label": "One", "figure": "1", "prose": "a"},
            {"label": "Two", "figure": "2", "prose": "b"},
        ]})

        assert len(content.points) == 3

    def test_a_series_that_does_not_line_up_with_its_categories_is_dropped(self):
        """A chart with holes in it says the design system is broken rather
        than that the model miscounted."""
        content = specimen.SpecimenContent.from_dict({"chart": {
            "categories": ["2023", "2024", "2025"],
            "series": [{"name": "Short", "values": [1, 2]}],
        }})

        assert content.series == specimen.default_content().series

    def test_values_that_are_not_numbers_are_dropped(self):
        content = specimen.SpecimenContent.from_dict({"chart": {
            "categories": ["2023", "2024"],
            "series": [{"name": "Bad", "values": ["up", "down"]}],
        }})

        assert content.series == specimen.default_content().series


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

    @pytest.mark.parametrize("structure", sorted(specimen.ARGUMENT_BUILDERS))
    def test_every_structure_renders_through_the_real_renderer(
            self, previews, structure):
        """A preview that flatters a direction the deck will not honour is
        worse than no preview - and a structure that will not render is the
        loudest version of that."""
        system = directions.compile_direction(directions.DirectionSpec(
            id=structure, name=structure, rationale="", structure=structure,
        ))

        urls = specimen.render_specimens(
            system, "sess-shapes", structure, previews
        )

        assert len(urls) == 3

    def test_it_goes_through_the_real_renderer(self, previews):
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

    def test_two_structures_on_one_palette_still_differ(self, previews):
        """The failure #151 reported: same palette, same everything, so the
        only difference a user could see was the colour."""
        for structure in ("list", "bands"):
            system = get_default_design_system()
            system["structure"] = structure
            specimen.render_specimens(system, "sess-shape-cmp", structure,
                                      previews)

        def read(direction):
            path = os.path.join(previews, "sess-shape-cmp", "direction",
                                direction, "2.png")
            with open(path, "rb") as handle:
                return handle.read()

        assert read("list") != read("bands")

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
        directions_ = [
            {"id": "clean-minimal", "designSystem": get_default_design_system()},
            {"id": "dark-analyst", "designSystem": _dark_ground()},
            {"id": "editorial-serif", "designSystem": _missing_typeface()},
        ]
        return asyncio.run(
            specimen.render_directions(directions_, "sess-three", previews)
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

    def test_one_content_set_is_used_for_all_of_them(self, previews):
        """What the user compares is the design, so the words cannot change
        between one preview and the next."""
        seen = []
        original = specimen.specimen_slides

        def spy(structure="list", content=None):
            seen.append(content)
            return original(structure, content)

        content = specimen.SpecimenContent.from_dict({"title": "One brief"})
        try:
            specimen.specimen_slides = spy
            asyncio.run(specimen.render_directions(
                [{"id": f"d{n}", "designSystem": get_default_design_system()}
                 for n in range(3)],
                "sess-one-content", previews, content=content,
            ))
        finally:
            specimen.specimen_slides = original

        assert seen == [content] * 3

    def test_one_direction_failing_does_not_take_the_others(self, previews):
        directions_ = [
            {"id": "good", "designSystem": get_default_design_system()},
            {"id": "bad", "designSystem": {"palette": "not a palette"}},
        ]

        rendered = asyncio.run(
            specimen.render_directions(directions_, "sess-partial", previews)
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
        directions_ = [
            {"id": f"d{n}", "designSystem": get_default_design_system()}
            for n in range(3)
        ]
        asyncio.run(specimen.render_directions(directions_, "sess-clean", previews))

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

    def test_it_reports_the_shape_the_direction_leads_with(self):
        described = specimen.describe(
            directions.compile_direction(directions.DirectionSpec(
                id="d", name="D", rationale="", structure="bands"
            ))
        )

        assert described["structure"] == "bands"

    def test_an_empty_system_describes_as_empty_rather_than_raising(self):
        assert specimen.describe({})["displayFace"] is None
