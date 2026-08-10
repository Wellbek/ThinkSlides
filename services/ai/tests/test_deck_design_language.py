"""
The built-in deck's design language: light/dark rhythm, the repeated accent
motif, the footer, and the catalog geometry actually reaching the slide.

Issue #56's finding was that the design system's palette, typography and
layout catalog were all extracted, shown to the model, and then discarded -
the only token that reached a rendered slide was ``primary``, as the title's
text colour. These tests assert the tokens land.
"""

import os
import tempfile

import pytest
from pptx import Presentation
from pptx.util import Emu

from app import colors
from app.design_system import get_default_design_system
from app.renderer.pptx_renderer import (
    CHROME_ACCENT_RULE,
    CHROME_FOOTER,
    PPTXRenderer,
    is_chrome_shape,
)

BRAND = "#00704A"


def _slide(layout_id, title, body=None):
    contents = [{
        "type": "text", "placeholderIdx": 0,
        "runs": [{"text": title, "bold": True}],
    }]
    if body:
        contents.append({
            "type": "text", "placeholderIdx": 1,
            "runs": [{"text": t, "bullet": True, "level": 0} for t in body],
        })
    return {"slideIndex": 0, "layoutId": layout_id, "layoutName": layout_id,
            "contents": contents}


@pytest.fixture
def deck():
    """A cover, a content slide and a closing slide, rendered in the brand."""
    slides = [
        _slide("title", "Cover", ["A subtitle line"]),
        _slide("content", "Where the business stands", ["First point", "Second point"]),
        _slide("conclusion", "What we recommend", ["Do the thing"]),
    ]
    renderer = PPTXRenderer(template_path=None, color_theme=BRAND)
    with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
        output_path = f.name
    try:
        renderer.render(slides=slides, metadata={"title": "Deck Title"},
                        output_path=output_path)
        yield Presentation(output_path)
    finally:
        os.unlink(output_path)


def _background(slide) -> str:
    return colors.to_hex(tuple(slide.background.fill.fore_color.rgb))


def _chrome(slide, name):
    return [s for s in slide.shapes if (s.name or "") == name]


def _text_of(slide, name):
    return " ".join(s.text_frame.text for s in _chrome(slide, name) if s.has_text_frame)


class TestLightDarkRhythm:
    def test_cover_and_closing_are_dark_and_content_is_light(self, deck):
        """A deck of seven identical white pages is the thing being fixed."""
        cover, content, closing = deck.slides

        assert colors.is_dark(_background(cover))
        assert not colors.is_dark(_background(content))
        assert colors.is_dark(_background(closing))

    def test_text_on_a_dark_slide_stays_legible(self, deck):
        """Body text used to keep the master's black whatever was behind it."""
        cover = deck.slides[0]
        background = _background(cover)

        runs = [
            run
            for shape in cover.shapes
            if shape.has_text_frame and not is_chrome_shape(shape)
            for paragraph in shape.text_frame.paragraphs
            for run in paragraph.runs
        ]
        assert runs

        for run in runs:
            rgb = run.font.color.rgb
            assert rgb is not None, "run inherited the master's colour on a dark slide"
            assert colors.contrast_ratio(colors.to_hex(tuple(rgb)), background) >= 4.5


class TestRepeatedMotif:
    def test_every_slide_carries_the_accent_rule(self, deck):
        for index, slide in enumerate(deck.slides):
            assert _chrome(slide, CHROME_ACCENT_RULE), f"slide {index + 1} has no motif"

    def test_the_rule_is_drawn_in_the_palette_accent(self, deck):
        palette = {
            c["name"]: c["value"]
            for c in get_default_design_system(color_theme=BRAND)["palette"]["colors"]
        }
        rule = _chrome(deck.slides[1], CHROME_ACCENT_RULE)[0]

        assert colors.to_hex(tuple(rule.fill.fore_color.rgb)) == palette["accent"].upper()

    def test_the_rule_carries_no_inherited_shadow(self, deck):
        """Autoshapes inherit the theme's effectRef, which LibreOffice drew as
        a grey shadow under a 4pt bar - a rendering artifact, not a motif."""
        from pptx.oxml.ns import qn

        rule = _chrome(deck.slides[1], CHROME_ACCENT_RULE)[0]
        assert rule._element.find(qn("p:style")) is None


class TestFooter:
    def test_content_slides_carry_the_deck_title_and_a_slide_number(self, deck):
        text = _text_of(deck.slides[1], CHROME_FOOTER)

        assert "Deck Title" in text
        assert "2" in text

    def test_the_cover_carries_no_footer(self, deck):
        """No real deck numbers its cover or repeats its own title under it."""
        assert _chrome(deck.slides[0], CHROME_FOOTER) == []


class TestCatalogGeometryIsApplied:
    def test_the_title_lands_on_the_catalog_rectangle(self, deck):
        """The model plans against the catalog; the slide must use it too.

        Deferring to the stock layout's own geometry meant the model designed
        for one rectangle and python-pptx's rescaled 4:3 Office template
        supplied another.
        """
        catalog = get_default_design_system()
        content = next(l for l in catalog["layouts"] if l["layoutId"] == "content")
        spec = next(p for p in content["placeholders"] if p["type"] == "title")

        title = next(s for s in deck.slides[1].shapes
                     if s.has_text_frame
                     and s.text_frame.text == "Where the business stands")

        assert title.left == Emu(spec["xEmu"])
        assert title.top == Emu(spec["yEmu"])
        assert title.width == Emu(spec["cxEmu"])

    def test_nothing_is_drawn_below_the_footer_band(self, deck):
        """Content running under the footer is the deck's own chrome colliding
        with its content, which no amount of rewording by the model can fix."""
        catalog = get_default_design_system()
        footer_top = catalog["spacing"]["footer"]["yEmu"]

        for index, slide in enumerate(deck.slides):
            for shape in slide.shapes:
                if is_chrome_shape(shape) or shape.top is None or shape.height is None:
                    continue
                assert shape.top + shape.height <= footer_top, (
                    f"slide {index + 1}: {shape.name} runs into the footer band"
                )


class TestInheritedStylingIsNeutralised:
    def test_a_closing_title_is_not_shouted(self, deck):
        """The stock "Section Header" layout declares cap="all" on its title,
        so "What we recommend" came back as "WHAT WE RECOMMEND"."""
        from pptx.oxml.ns import qn

        title = next(s for s in deck.slides[2].shapes
                     if s.has_text_frame and s.text_frame.text == "What we recommend")
        run = title.text_frame.paragraphs[0].runs[0]

        assert run._r.find(qn("a:rPr")).get("cap") == "none"


class TestTypographyReachesTheSlide:
    def test_every_run_names_the_design_system_font(self, deck):
        """`run.font.name` was never assigned, so the design system advertised
        Arial to the model and the deck rendered in the base template's
        theme font."""
        expected = get_default_design_system()["typography"]["bodyFont"]["typeface"]

        runs = [
            run
            for slide in deck.slides
            for shape in slide.shapes
            if shape.has_text_frame and not is_chrome_shape(shape)
            for paragraph in shape.text_frame.paragraphs
            for run in paragraph.runs
        ]
        assert runs
        assert all(run.font.name == expected for run in runs)

    def test_an_uploaded_templates_fonts_and_sizes_are_used(self):
        """A template's typography is extracted and shown to the model; it has
        to survive as far as the rendered run, not stop at the prompt."""
        from app.renderer.typography import TypeScale

        scale = TypeScale.from_design_system({
            "typography": {
                "titleFont": {"typeface": "Georgia", "sizePt": 40, "bold": False},
                "bodyFont": {"typeface": "Verdana", "sizePt": 22},
            }
        })

        assert scale.title_style().font_name == "Georgia"
        assert scale.title_style().font_size_pt == 40
        assert scale.title_style().bold is False
        assert scale.body_style().font_name == "Verdana"
        assert scale.body_style().font_size_pt == 22

    def test_sub_bullets_step_down_but_stay_legible(self):
        from app.renderer.typography import MIN_BODY_PT, TypeScale

        scale = TypeScale.from_design_system({
            "typography": {"bodyFont": {"typeface": "Arial", "sizePt": 20}}
        })

        assert scale.body_style(0).font_size_pt > scale.body_style(1).font_size_pt
        assert scale.body_style(2).font_size_pt >= MIN_BODY_PT

    def test_a_document_sized_type_scale_is_floored_for_slides(self):
        """A template specifying 11pt body copy is describing a document."""
        from app.renderer.typography import MIN_BODY_PT, MIN_TITLE_PT, TypeScale

        scale = TypeScale.from_design_system({
            "typography": {
                "titleFont": {"typeface": "Arial", "sizePt": 14},
                "bodyFont": {"typeface": "Arial", "sizePt": 11},
            }
        })

        assert scale.title_style().font_size_pt == MIN_TITLE_PT
        assert scale.body_style().font_size_pt == MIN_BODY_PT

    def test_a_hero_title_is_larger_than_a_content_title(self, deck):
        """The cover's title is the only thing on the slide."""
        cover_title = next(
            s for s in deck.slides[0].shapes
            if s.has_text_frame and s.text_frame.text == "Cover"
        )
        content_title = next(
            s for s in deck.slides[1].shapes
            if s.has_text_frame and s.text_frame.text == "Where the business stands"
        )

        assert (cover_title.text_frame.paragraphs[0].runs[0].font.size
                > content_title.text_frame.paragraphs[0].runs[0].font.size)


class TestSlidesAreVerticallyBalanced:
    def test_a_short_body_does_not_leave_the_bottom_half_empty(self):
        """Bullets were top-anchored at a fixed size in a fixed band, so a
        two-bullet slide left 40-60% of the page blank."""
        from app.design_system import get_default_design_system

        slides = [
            _slide("title", "Cover"),
            _slide("content", "Two points", ["Labour cost inflation",
                                             "Slower China recovery"]),
        ]
        renderer = PPTXRenderer(template_path=None, color_theme=BRAND)
        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
            output_path = f.name
        try:
            renderer.render(slides=slides, metadata={"title": "T"},
                            output_path=output_path)
            prs = Presentation(output_path)
        finally:
            os.unlink(output_path)

        catalog = get_default_design_system()
        content = next(l for l in catalog["layouts"] if l["layoutId"] == "content")
        band = next(p for p in content["placeholders"] if p["type"] == "body")

        body = next(s for s in prs.slides[1].shapes
                    if s.has_text_frame and "Labour cost inflation" in s.text_frame.text)

        # The block starts below the top of its band rather than flush with it.
        assert body.top > band["yEmu"]
        # And its type is set larger than the base body size.
        size = body.text_frame.paragraphs[0].runs[0].font.size.pt
        base = catalog["typography"]["bodyFont"]["sizePt"]
        assert size > base


class TestUnderfilledLayouts:
    def test_a_single_body_block_uses_the_full_content_band(self):
        """A twoColumn layout with one body block filled only its left column,
        so the text wrapped at half width with the other half of the slide
        blank - which is exactly what the model produces when it picks
        twoColumn and then writes a single block."""
        from app.design_system import get_default_design_system

        slides = [
            _slide("title", "Cover"),
            _slide("twoColumn", "Implications", [
                "Traditional manufacturers face increasingly competitive markets",
                "Adapting means investing in electric vehicle technology",
            ]),
        ]
        renderer = PPTXRenderer(template_path=None, color_theme=BRAND)
        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
            output_path = f.name
        try:
            renderer.render(slides=slides, metadata={"title": "T"},
                            output_path=output_path)
            prs = Presentation(output_path)
        finally:
            os.unlink(output_path)

        catalog = get_default_design_system()
        two_column = next(l for l in catalog["layouts"] if l["layoutId"] == "twoColumn")
        column_width = next(
            p for p in two_column["placeholders"] if p["name"] == "Left Column"
        )["cxEmu"]

        body = next(s for s in prs.slides[1].shapes
                    if s.has_text_frame
                    and "Traditional manufacturers" in s.text_frame.text)

        assert body.width > column_width * 1.5


class TestASlideThatCarriesBothCopyAndAVisual:
    def _render(self, slides):
        renderer = PPTXRenderer(template_path=None, color_theme=BRAND)
        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
            output_path = f.name
        try:
            renderer.render(slides=slides, metadata={"title": "T"},
                            output_path=output_path)
            return Presentation(output_path)
        finally:
            os.unlink(output_path)

    def test_the_table_keeps_a_legible_size_under_five_bullets(self):
        """Giving text every inch it measured and the object the remainder
        compressed a six-row table into 1.7in at 8pt, under five bullets, on
        a slide whose entire point was the table."""
        from app.renderer.table_layout import MIN_CELL_PT

        slides = [_slide("title", "Cover"), {
            "slideIndex": 1, "layoutId": "table", "contents": [
                {"type": "text", "runs": [{"text": "Segments", "bold": True}]},
                {"type": "text", "runs": [
                    {"text": f"A full sentence of supporting commentary, number {i}, "
                             "which runs to a comfortable line and a half of copy",
                     "bullet": True, "level": 0}
                    for i in range(5)]},
                {"type": "table", "table": {"headerRow": True, "rows": [
                    ["Segment", "Revenue ($B)", "Growth (%)"],
                    ["Gaming", "2.5", "4"],
                    ["Data Centre", "4.5", "20"],
                    ["Professional Visualization", "0.3", "6"],
                    ["Automotive", "0.2", "15"],
                    ["OEM & Others", "0.79", "-5"]]}}]}]

        prs = self._render(slides)
        table = next(s.table for s in prs.slides[1].shapes if s.has_table)
        sizes = [
            run.font.size.pt
            for row in table.rows for cell in row.cells
            for paragraph in cell.text_frame.paragraphs for run in paragraph.runs
        ]

        assert min(sizes) > MIN_CELL_PT, "the table was squeezed to its floor"

    def test_the_cover_sets_its_body_as_a_standfirst_not_a_list(self):
        """The planner is told the title slide takes 0-1 key points; the model
        routinely gives it three, and bulleting them turns the opening slide
        into an agenda nobody asked for."""
        from pptx.oxml.ns import qn

        slides = [_slide("title", "NVIDIA Q2 FY2026 Results",
                         ["Revenue grew on AI demand", "Net income rose sharply"]),
                  _slide("content", "Detail", ["A point", "Another point"])]

        prs = self._render(slides)
        cover_body = next(s for s in prs.slides[0].shapes
                          if s.has_text_frame and "Revenue grew" in s.text_frame.text)
        content_body = next(s for s in prs.slides[1].shapes
                            if s.has_text_frame and "A point" in s.text_frame.text)

        def has_glyph(shape):
            return any(
                p._p.find(qn("a:pPr")) is not None
                and p._p.find(qn("a:pPr")).find(qn("a:buChar")) is not None
                for p in shape.text_frame.paragraphs
            )

        assert not has_glyph(cover_body)
        assert has_glyph(content_body), "content slides must still be bulleted"
