"""
The block vocabulary, and the two properties that make it worth having.

A block is not just a drawing routine. It compiles to primitives, which is
what lets its measurement be the extent of the very marks it will draw rather
than a parallel estimate; and it carries meaning, which is what the density
budget, the planner and the eval harness read. Both are tested here, along
with the acceptance case: the reference pages the epic exists to reproduce,
built from hand-written slide JSON.
"""

import base64
import io
import os
import shutil
import tempfile

import pytest
from pptx import Presentation

from app import colors
from app.renderer import icons, primitives as prim
from app.renderer.blocks import (
    BLOCK_PAYLOAD_KEY, BLOCK_TYPES, BlockCompiler, block_entry_count, resolve_tone,
)
from app.renderer.deck_theme import DeckTheme, ROLE_CONTENT
from app.renderer.layout import PositionConfig
from app.renderer.pptx_renderer import PPTXRenderer
from app.renderer.preview_generator import PreviewGenerator
from app.agent.tools.validate_rendered_output import ValidateRenderedOutputTool
from app.design_system import get_default_design_system
from app.renderer.typography import TypeScale

NAVY = "#051C2C"
RECT = PositionConfig(left=1.0, top=1.5, width=10.0, height=4.0)


def _compiler(ground="#FFFFFF", design_system=None):
    design_system = design_system or get_default_design_system()
    theme = DeckTheme.from_design_system(design_system)
    return BlockCompiler(
        style=theme.style_on(ground, ROLE_CONTENT),
        type_scale=TypeScale.from_design_system(design_system),
        design_system=design_system,
    )


def _tiny_png():
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (12, 8), (40, 90, 160)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


STAT_BLOCK = {"type": "statCallout", "statCallouts": [
    {"value": "56%", "caption": "Share of respondents adopting AI"},
    {"value": "94.4%", "caption": "Improvement in training speed since 2018"},
]}
ICON_BLOCK = {"type": "iconCard", "iconCards": [
    {"icon": "security", "heading": "Zero-trust architecture",
     "body": ["All entities must prove trustworthiness."]},
    {"icon": "identity", "heading": "Digital identity",
     "body": ["Mechanisms for characterising an entity."]},
]}
ACCENT_BLOCK = {"type": "accentCard", "columns": 2, "accentCards": [
    {"heading": "Sustainable food source", "tone": "benefit",
     "body": ["Production techniques are more accessible."]},
    {"heading": "High prices", "tone": "risk",
     "body": ["Cultivated meat is priced higher than traditional meat."]},
]}
BAND_BLOCK = {"type": "rowBand", "rowBands": [
    {"icon": "telecommunications", "label": "Telecommunications",
     "prose": "Increase in revenue streams from technologies such as MEC."},
    {"icon": "manufacturing", "label": "Manufacturing",
     "prose": "Improvements in networking and data latency."},
    {"icon": "retail", "label": "Retail",
     "prose": "Improvements in advanced analytics use cases."},
]}
BANNER_BLOCK = {"type": "banner",
                "banner": {"text": "Digital trust addresses risk across data and AI"}}
LEGEND_BLOCK = {"type": "legend", "legend": {"items": [
    {"label": "Benefits", "tone": "benefit"},
    {"label": "Risks and uncertainties", "tone": "risk"},
]}}
IMAGE_ROW_BLOCK = {"type": "imageCardRow", "imageCardRow": {
    "cards": [
        {"image": {"source": "base64", "data": _tiny_png(), "altText": "Mining"},
         "title": "Space mining", "caption": "Mine asteroids for materials."},
        {"image": {"source": "base64", "data": _tiny_png(), "altText": "Tourism"},
         "title": "Commercial tourism", "caption": "Scale paying customers."},
    ],
    "groups": [{"label": "Space economy", "span": 1},
               {"label": "Human spaceflight", "span": 1}],
}}

ALL_BLOCKS = {
    "statCallout": STAT_BLOCK, "iconCard": ICON_BLOCK, "accentCard": ACCENT_BLOCK,
    "rowBand": BAND_BLOCK, "banner": BANNER_BLOCK, "legend": LEGEND_BLOCK,
    "imageCardRow": IMAGE_ROW_BLOCK,
}


class TestEveryBlockTypeIsComplete:
    def test_the_vocabulary_and_the_payload_map_agree(self):
        assert BLOCK_TYPES == set(BLOCK_PAYLOAD_KEY)

    @pytest.mark.parametrize("kind", sorted(ALL_BLOCKS))
    def test_it_compiles_to_marks(self, kind):
        compiled = _compiler().compile(kind, ALL_BLOCKS[kind], RECT)

        assert compiled
        assert all(m.kind in prim.PRIMITIVE_KINDS for m in compiled)

    @pytest.mark.parametrize("kind", sorted(ALL_BLOCKS))
    def test_it_measures_to_something_it_can_draw(self, kind):
        compiler = _compiler()

        measured = compiler.measure(kind, ALL_BLOCKS[kind], RECT.width)

        assert measured > 0

    @pytest.mark.parametrize("kind", sorted(ALL_BLOCKS))
    def test_measurement_is_the_extent_of_what_is_drawn(self, kind):
        """
        The property the compile-don't-draw split buys: a block's height is
        the bounding box of the marks it will draw, not a separate estimate
        that drifts from the drawing code.
        """
        compiler = _compiler()
        measured = compiler.measure(kind, ALL_BLOCKS[kind], RECT.width)
        compiled = compiler.compile(
            kind, ALL_BLOCKS[kind],
            PositionConfig(left=0.0, top=0.0, width=RECT.width, height=100.0),
        )

        assert prim.bounds(compiled).height == pytest.approx(measured, abs=0.001)

    @pytest.mark.parametrize("kind", sorted(ALL_BLOCKS))
    def test_every_mark_carries_a_unique_id(self, kind):
        compiled = _compiler().compile(kind, ALL_BLOCKS[kind], RECT, id_prefix="s2")

        ids = [m.id for m in compiled]
        assert len(ids) == len(set(ids))
        assert all(i.startswith("s2") for i in ids)

    @pytest.mark.parametrize("kind", sorted(ALL_BLOCKS))
    def test_an_empty_payload_compiles_to_nothing(self, kind):
        empty = {"type": kind, BLOCK_PAYLOAD_KEY[kind]: {} if kind in
                 ("banner", "legend", "imageCardRow") else []}

        assert _compiler().compile(kind, empty, RECT) == []


class TestABlockFitsTheRegionItIsGiven:
    @staticmethod
    def _crowded(rows=9):
        return {"type": "rowBand", "rowBands": [
            {"icon": "growth", "label": f"Row {n}",
             "prose": "An implication long enough to wrap onto a second line "
                      "in a column of this width, several times over."}
            for n in range(rows)
        ]}

    def test_a_block_taller_than_its_region_is_scaled_down_to_fit(self):
        compiler = _compiler()
        tight = PositionConfig(left=1.0, top=1.0, width=10.0, height=3.4)

        fitted = compiler.fit("rowBand", self._crowded(), tight)

        assert prim.bounds(fitted).height <= tight.height + 0.02

    def test_an_impossible_ask_shrinks_as_far_as_it_legibly_can_and_no_further(self):
        """
        Nine dense bands do not fit two inches at any legible size. The block
        shrinks to the floor and stops - forcing it further would trade an
        overflow the rendered-output check reports for illegible type that
        nothing reports at all.
        """
        compiler = _compiler()
        impossible = PositionConfig(left=1.0, top=1.0, width=10.0, height=2.0)

        fitted = compiler.fit("rowBand", self._crowded(), impossible)
        natural = compiler.compile("rowBand", self._crowded(), impossible)

        assert prim.bounds(fitted).height < prim.bounds(natural).height
        sizes = {round(m.size_pt, 2) for m in fitted
                 if m.kind == "text" and m.id.endswith("-prose")}
        assert sizes and min(sizes) >= 6.0

    def test_a_block_that_already_fits_is_left_at_full_size(self):
        compiler = _compiler()
        roomy = PositionConfig(left=1.0, top=1.0, width=10.0, height=6.0)

        fitted = compiler.fit("statCallout", STAT_BLOCK, roomy)
        natural = compiler.compile("statCallout", STAT_BLOCK, roomy)

        assert prim.bounds(fitted).height == pytest.approx(
            prim.bounds(natural).height, abs=0.001
        )

    def test_a_shrunk_block_keeps_one_type_size_across_its_rows(self):
        """
        Fitting each row separately is how seven bands ended up set at four
        different sizes - the "one type scale per slide" smell, on one slide.
        """
        compiler = _compiler()
        tight = PositionConfig(left=1.0, top=1.0, width=10.0, height=2.2)

        fitted = compiler.fit("rowBand", BAND_BLOCK, tight)

        label_sizes = {round(m.size_pt, 2) for m in fitted
                       if m.kind == "text" and m.id.endswith("-label")}
        assert len(label_sizes) == 1


class TestSemanticColourIsSaidOnceAndReadTwice:
    def test_a_card_and_the_legend_explaining_it_resolve_to_one_colour(self):
        design_system = get_default_design_system()
        compiler = _compiler(design_system=design_system)

        cards = compiler.compile("accentCard", ACCENT_BLOCK, RECT)
        legend = compiler.compile("legend", LEGEND_BLOCK, RECT)

        card_benefit = next(m.fill for m in cards if m.id.endswith("accent0-bar"))
        legend_benefit = next(m.fill for m in legend if m.id.endswith("legend0-swatch"))
        assert card_benefit == legend_benefit

    def test_a_tone_prefers_the_decks_own_palette(self):
        branded = get_default_design_system(color_theme="#00704A")
        secondary = next(c["value"] for c in branded["palette"]["colors"]
                         if c["name"] == "secondary")

        assert resolve_tone("benefit", branded) == secondary

    def test_an_explicit_hex_passes_through(self):
        assert resolve_tone("#123456", None) == "#123456"

    def test_an_unknown_tone_falls_back_rather_than_inventing_one(self):
        assert resolve_tone("chartreuse", None) == resolve_tone("neutral", None)

    def test_benefit_and_risk_are_distinguishable(self):
        benefit = resolve_tone("benefit", None)
        risk = resolve_tone("risk", None)

        assert benefit != risk


class TestBlocksColourThemselvesForTheirGround:
    def test_the_same_block_reads_differently_on_two_grounds(self):
        light = _compiler("#FFFFFF").compile("rowBand", BAND_BLOCK, RECT)
        dark = _compiler(NAVY).compile("rowBand", BAND_BLOCK, RECT)

        light_label = next(m.color for m in light if m.id.endswith("band0-label"))
        dark_label = next(m.color for m in dark if m.id.endswith("band0-label"))
        assert light_label != dark_label

    def test_text_on_a_dark_ground_clears_contrast(self):
        compiled = _compiler(NAVY).compile("iconCard", ICON_BLOCK, RECT)

        for mark in compiled:
            if mark.kind != "text":
                continue
            assert colors.contrast_ratio(mark.color, NAVY) >= colors.WCAG_AA_NORMAL


class TestIconsResolveByConceptNotByFilename:
    @pytest.mark.parametrize("concept,expected", [
        ("Financial services", "money"),
        ("Telecommunications", "signal"),
        ("Machine learning", "chip"),
        ("Automotive and assembly", "car"),
        ("Healthcare systems and services", "health"),
        ("Electric power, natural gas, and utilities", "energy"),
        ("Future of space technologies", "satellite"),
    ])
    def test_a_concept_maps_to_a_sensible_glyph(self, concept, expected):
        assert icons.resolve_glyph(concept) == expected

    def test_a_longer_phrase_wins_over_a_word_inside_it(self):
        assert icons.resolve_glyph("machine learning") == "chip"
        assert icons.resolve_glyph("learning") != "chip"

    def test_a_glyph_can_also_be_named_outright(self):
        assert icons.resolve_glyph("trend-up") == "trend-up"

    def test_an_unrecognised_concept_gets_a_neutral_marker(self):
        """A wrong icon is worse than a plain one."""
        assert icons.resolve_glyph("quixotic perambulation") == icons.DEFAULT_GLYPH

    def test_nothing_at_all_still_resolves(self):
        assert icons.resolve_glyph(None) == icons.DEFAULT_GLYPH

    def test_a_glyph_renders_in_the_colour_it_is_asked_for(self):
        from PIL import Image

        png = icons.render_icon_png("cloud", "#FF0000", size_px=64)
        image = Image.open(io.BytesIO(png)).convert("RGBA")

        reds = [p for p in image.getdata() if p[3] > 200 and p[0] > 180 and p[1] < 80]
        assert reds, "the glyph did not draw in the requested colour"

    def test_every_glyph_renders(self):
        for name in icons.glyph_names():
            assert icons.render_icon_png(name, "#000000", size_px=48)


class TestBlockEntryCount:
    @pytest.mark.parametrize("kind,expected", [
        ("statCallout", 2), ("iconCard", 2), ("accentCard", 2),
        ("rowBand", 3), ("banner", 1), ("legend", 2), ("imageCardRow", 2),
    ])
    def test_it_counts_what_the_block_carries(self, kind, expected):
        assert block_entry_count(ALL_BLOCKS[kind]) == expected

    def test_an_empty_block_carries_nothing(self):
        assert block_entry_count({"type": "banner", "banner": {"text": ""}}) == 0


# --------------------------------------------------------------------------
# The acceptance case: reference pages from hand-written slide JSON.
# --------------------------------------------------------------------------

REFERENCE_SLIDES = {
    # Page 22: split canvas, chart on the white half, icon badge and stat
    # callouts on the navy half.
    "page-22": {
        "layoutId": "content",
        "regions": [
            {"id": "left", "kind": "panel", "x": 0, "y": 0, "w": 0.5, "h": 1.0,
             "ground": "#FFFFFF", "children": [
                 {"id": "left-title", "kind": "panel", "x": 0.09, "y": 0.03,
                  "w": 0.86, "h": 0.16},
                 {"id": "left-chart", "kind": "panel", "x": 0.09, "y": 0.30,
                  "w": 0.86, "h": 0.58}]},
            {"id": "right", "kind": "panel", "x": 0.5, "y": 0, "w": 0.5, "h": 1.0,
             "ground": NAVY, "children": [
                 {"id": "r1", "kind": "band", "x": 0.06, "y": 0.10, "w": 0.88, "h": 0.20},
                 {"id": "r2", "kind": "band", "x": 0.06, "y": 0.34, "w": 0.88, "h": 0.20},
                 {"id": "r3", "kind": "band", "x": 0.06, "y": 0.58, "w": 0.88, "h": 0.20}]},
        ],
        "contents": [
            {"type": "text", "regionId": "left-title",
             "runs": [{"text": "Why should leaders pay attention?", "bullet": False}]},
            {"type": "chart", "regionId": "left-chart", "chart": {
                "chartType": "bar", "categories": ["All", "Europe", "India"],
                "series": [{"name": "2021", "values": [56.0, 51.0, 65.0]}]}},
            {"type": "iconCard", "regionId": "r1", "iconCards": [
                {"icon": "global", "heading": "Global expansion of AI"},
                {"icon": "growth", "heading": "Rapidly growing innovation"}]},
            {"type": "statCallout", "regionId": "r2", "statCallouts": [
                {"value": "56%", "caption": "Respondents adopting AI in 2021"}]},
            {"type": "statCallout", "regionId": "r3", "statCallouts": [
                {"value": "30x", "caption": "Patents filed in 2021 vs 2015"}]},
        ],
        "expect": {"iconCard", "statCallout"},
    },
    # Page 40: seven ruled row bands of icon, label and implication.
    "page-40": {
        "layoutId": "content",
        "regions": [
            {"id": "title", "kind": "banner", "x": 0.045, "y": 0.07, "w": 0.91, "h": 0.09},
            {"id": "bands", "kind": "bandRow", "x": 0.045, "y": 0.22, "w": 0.91, "h": 0.68},
        ],
        "contents": [
            {"type": "text", "regionId": "title",
             "runs": [{"text": "What industries are most affected?", "bullet": False}]},
            {"type": "rowBand", "regionId": "bands", "rowBands": [
                {"icon": "telecommunications", "label": "Telecommunications",
                 "prose": "Increase in revenue streams from technologies such as MEC."},
                {"icon": "automotive", "label": "Automotive and assembly",
                 "prose": "Improved efficiency of transportation routes."},
                {"icon": "energy", "label": "Electric power and utilities",
                 "prose": "Improvements in employee safety through real-time tracking."},
                {"icon": "manufacturing", "label": "Manufacturing",
                 "prose": "Improvements in networking and data latency."},
                {"icon": "financial services", "label": "Financial services",
                 "prose": "Sensors and monitors help insurers reduce collision."},
                {"icon": "retail", "label": "Retail",
                 "prose": "Improvements in advanced analytics use cases."},
                {"icon": "healthcare", "label": "Healthcare systems",
                 "prose": "Improvements in most digital use cases."}]},
        ],
        "expect": {"rowBand"},
    },
    # Page 95: a four-column icon-card row over a full-width banner.
    "page-95": {
        "layoutId": "content",
        "regions": [
            {"id": "title", "kind": "banner", "x": 0.045, "y": 0.06, "w": 0.91, "h": 0.09},
            {"id": "cards", "kind": "grid", "x": 0.045, "y": 0.25, "w": 0.91, "h": 0.55},
            {"id": "banner", "kind": "banner", "x": 0.045, "y": 0.83, "w": 0.91, "h": 0.09},
        ],
        "contents": [
            {"type": "text", "regionId": "title",
             "runs": [{"text": "What is the tech trend about?", "bullet": False}]},
            {"type": "iconCard", "regionId": "cards", "iconCards": [
                {"icon": "security", "heading": "Zero-trust architecture",
                 "body": ["All entities must prove trustworthiness."]},
                {"icon": "identity", "heading": "Digital identity",
                 "body": ["Mechanisms for characterising an entity."]},
                {"icon": "privacy", "heading": "Privacy engineering",
                 "body": ["Techniques enabling oversight of privacy."]},
                {"icon": "machine learning", "heading": "Explainable AI",
                 "body": ["Techniques for building trust in AI models."]}]},
            {"type": "banner", "regionId": "banner", "banner": {
                "text": "Digital trust addresses risk across data, cloud and AI"}},
        ],
        "expect": {"iconCard", "banner"},
    },
    # Page 130: a stat rail, semantic benefit/risk cards, and the legend that
    # explains them.
    "page-130": {
        "layoutId": "content",
        "regions": [
            {"id": "rail", "kind": "rail", "x": 0, "y": 0, "w": 0.20, "h": 1.0,
             "ground": NAVY, "children": [
                 {"id": "rail-stat", "kind": "panel", "x": 0.14, "y": 0.14,
                  "w": 0.78, "h": 0.45}]},
            {"id": "main", "kind": "panel", "x": 0.22, "y": 0, "w": 0.78, "h": 1.0,
             "children": [
                 {"id": "main-title", "kind": "banner", "x": 0.02, "y": 0.06,
                  "w": 0.96, "h": 0.10},
                 {"id": "main-legend", "kind": "banner", "x": 0.55, "y": 0.19,
                  "w": 0.43, "h": 0.05},
                 {"id": "main-cards", "kind": "grid", "x": 0.02, "y": 0.28,
                  "w": 0.96, "h": 0.60}]},
        ],
        "primitives": [
            {"id": "divider", "kind": "line", "regionId": "main",
             "x1": 0.02, "y1": 0.25, "x2": 0.98, "y2": 0.25,
             "stroke": "#8C97A8", "strokeWeightPt": 0.75},
        ],
        "contents": [
            {"type": "statCallout", "regionId": "rail-stat", "statCallouts": [
                {"value": "$0.8T-$1.3T", "caption": "Forecast global impact, 2030-40"}]},
            {"type": "text", "regionId": "main-title",
             "runs": [{"text": "What disruptions could the trend enable?",
                       "bullet": False}]},
            {"type": "legend", "regionId": "main-legend", "legend": {"items": [
                {"label": "Benefits", "tone": "benefit"},
                {"label": "Risks and uncertainties", "tone": "risk"}]}},
            {"type": "accentCard", "regionId": "main-cards", "columns": 2,
             "accentCards": [
                 {"heading": "Sustainable food source", "tone": "benefit",
                  "body": ["Production techniques are more accessible."]},
                 {"heading": "High prices and limited variety", "tone": "risk",
                  "body": ["Cultivated meat is priced higher than traditional meat."]},
                 {"heading": "Consumer acceptance", "tone": "benefit",
                  "body": ["Producers must strengthen confidence in safety."]},
                 {"heading": "Limited regulatory approval", "tone": "risk",
                  "body": ["Only one country approves sales today."]}]},
        ],
        "expect": {"statCallout", "legend", "accentCard"},
    },
    # Page 168: an image card row with group headings spanning unequal subsets.
    "page-168": {
        "layoutId": "content",
        "regions": [
            {"id": "title", "kind": "banner", "x": 0.045, "y": 0.06, "w": 0.91, "h": 0.09},
            {"id": "row", "kind": "grid", "x": 0.045, "y": 0.24, "w": 0.91, "h": 0.62},
        ],
        "contents": [
            {"type": "text", "regionId": "title",
             "runs": [{"text": "What disruptions could the trend enable?",
                       "bullet": False}]},
            {"type": "imageCardRow", "regionId": "row", "imageCardRow": {
                "cards": [
                    {"image": {"source": "base64", "data": _tiny_png(),
                               "altText": "Asteroid"},
                     "title": "Space mining",
                     "caption": "Mine asteroids for materials to return to Earth."},
                    {"image": {"source": "base64", "data": _tiny_png(),
                               "altText": "Assembly"},
                     "title": "In-orbit construction",
                     "caption": "Capitalise on the benefits of zero gravity."},
                    {"image": {"source": "base64", "data": _tiny_png(),
                               "altText": "Station"},
                     "title": "Commercial tourism",
                     "caption": "Scale paying customers to short spaceflights."}],
                "groups": [{"label": "Space economy", "span": 2},
                           {"label": "Human spaceflight", "span": 1}]}},
        ],
        "expect": {"imageCardRow"},
    },
}


class TestTheReferencePagesAreReproducible:
    """
    The acceptance criterion: every block present and correctly placed, from
    hand-written slide JSON. Not pixel-identical to the reference - the copy
    and the palette are ours - but structurally the same page.
    """

    @pytest.fixture(scope="class")
    @staticmethod
    def rendered():
        work_dir = tempfile.mkdtemp(prefix="ts_blocks_ref_")
        slides = []
        for index, name in enumerate(sorted(REFERENCE_SLIDES)):
            slide = {k: v for k, v in REFERENCE_SLIDES[name].items() if k != "expect"}
            slide["slideIndex"] = index
            slides.append(slide)
        try:
            pptx_path = os.path.join(work_dir, "reference.pptx")
            PPTXRenderer(template_path=None, color_theme="#00A9F4").render(
                slides=slides, metadata={"title": "Reference"},
                output_path=pptx_path,
            )
            pdf_path = PreviewGenerator.convert_to_pdf(pptx_path, work_dir)
            assert pdf_path, "LibreOffice could not convert the reference deck"
            yield {"pptx": pptx_path, "pdf": pdf_path,
                   "order": sorted(REFERENCE_SLIDES)}
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_nothing_overflows_or_overlaps(self, rendered):
        issues = ValidateRenderedOutputTool._check_geometry(rendered["pdf"])

        assert issues == [], "\n".join(
            f"  slide {i.get('slideIndex')}: {i['detail']}" for i in issues
        )

    @pytest.mark.parametrize("name", sorted(REFERENCE_SLIDES))
    def test_every_block_reaches_the_slide(self, name, rendered):
        index = rendered["order"].index(name)
        slide = Presentation(rendered["pptx"]).slides[index]
        ids = {prim.shape_primitive_id(s) for s in slide.shapes}
        ids.discard(None)

        for kind in REFERENCE_SLIDES[name]["expect"]:
            assert any(kind in (i or "") for i in ids), (
                f"{name}: no marks from a {kind} block - got {sorted(ids)[:8]}"
            )

    def test_the_split_canvas_page_carries_both_grounds(self, rendered):
        from app.renderer.pptx_renderer import CHROME_REGION_GROUND

        index = rendered["order"].index("page-22")
        slide = Presentation(rendered["pptx"]).slides[index]
        grounds = {
            colors.to_hex(tuple(s.fill.fore_color.rgb)) for s in slide.shapes
            if (s.name or "") == CHROME_REGION_GROUND
        }

        assert NAVY in grounds and "#FFFFFF" in grounds

    def test_the_band_page_draws_seven_rules(self, rendered):
        index = rendered["order"].index("page-40")
        slide = Presentation(rendered["pptx"]).slides[index]
        rules = [s for s in slide.shapes
                 if (prim.shape_primitive_id(s) or "").endswith("-rule")]

        assert len(rules) == 7

    def test_the_semantic_cards_page_uses_two_bar_colours(self, rendered):
        index = rendered["order"].index("page-130")
        slide = Presentation(rendered["pptx"]).slides[index]
        bars = {
            colors.to_hex(tuple(s.fill.fore_color.rgb)) for s in slide.shapes
            if (prim.shape_primitive_id(s) or "").endswith("-bar")
        }

        assert len(bars) == 2

    def test_the_authored_divider_is_drawn(self, rendered):
        """The escape hatch, used on a page that is otherwise all blocks."""
        index = rendered["order"].index("page-130")
        slide = Presentation(rendered["pptx"]).slides[index]

        assert any(prim.shape_primitive_id(s) == "divider" for s in slide.shapes)

    def test_the_image_row_places_its_group_headings(self, rendered):
        index = rendered["order"].index("page-168")
        slide = Presentation(rendered["pptx"]).slides[index]
        headings = [s for s in slide.shapes
                    if "-group" in (prim.shape_primitive_id(s) or "")]

        assert len(headings) == 2


class TestExistingContentIsUntouched:
    def test_the_four_original_content_types_are_not_block_types(self):
        assert not BLOCK_TYPES & {"text", "image", "table", "chart", "metrics"}

    def test_a_deck_of_plain_content_draws_no_block_marks(self):
        from app.eval.fixture_decks import DECKS

        work_dir = tempfile.mkdtemp(prefix="ts_blocks_plain_")
        try:
            slides, theme = DECKS["numbers"]
            path = os.path.join(work_dir, "plain.pptx")
            PPTXRenderer(template_path=None, color_theme=theme).render(
                slides=slides, metadata={"title": "Plain"}, output_path=path)

            for slide in Presentation(path).slides:
                assert not [s for s in slide.shapes if prim.is_content_shape(s)]
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


class TestAnUnfittableBlockIsReported:
    """
    The fit pass is bounded at a legibility floor, so a block can still not
    fit. That is a planning problem, and an operator should be able to see it
    without diffing a PDF.
    """

    def test_the_renderer_says_so(self, monkeypatch):
        from app.renderer import pptx_renderer

        reported = []
        monkeypatch.setattr(
            pptx_renderer.logger, "warning",
            lambda event, **kwargs: reported.append(event),
        )
        slides = [{
            "slideIndex": 0, "layoutId": "content",
            "regions": [{"id": "sliver", "kind": "panel",
                         "x": 0.05, "y": 0.1, "w": 0.9, "h": 0.12}],
            "contents": [{"type": "rowBand", "regionId": "sliver", "rowBands": [
                {"icon": "growth", "label": f"Row {n}",
                 "prose": "An implication long enough to wrap several times "
                          "over in a column of this width."}
                for n in range(9)
            ]}],
        }]
        work_dir = tempfile.mkdtemp(prefix="ts_blocks_warn_")
        try:
            PPTXRenderer(template_path=None, color_theme="professional").render(
                slides=slides, metadata={"title": "Crowded"},
                output_path=os.path.join(work_dir, "crowded.pptx"),
            )
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

        assert "block_does_not_fit_its_region" in reported
