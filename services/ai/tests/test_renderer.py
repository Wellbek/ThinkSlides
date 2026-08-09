"""
Tests for the PPTX renderer.
"""

import pytest
import os
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from app.renderer.pptx_renderer import PPTXRenderer, NS
from app.design_system import COLOR_THEMES
from app.renderer.default_template import _hex_to_rgbcolor


class TestPPTXRendererDefault:
    """Test rendering with the default theme (no template)."""

    @pytest.fixture
    def renderer(self):
        return PPTXRenderer(template_path=None)

    def test_render_empty_presentation(self, renderer):
        """Test rendering a presentation with no slides."""
        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
            output_path = f.name

        try:
            result = renderer.render(
                slides=[],
                metadata={"title": "Test"},
                output_path=output_path,
            )

            assert os.path.exists(result)
            assert os.path.getsize(result) > 0
        finally:
            os.unlink(output_path)

    def test_render_single_slide(self, renderer):
        """Test rendering a single text slide."""
        slides = [
            {
                "slideIndex": 0,
                "layoutId": "1",
                "layoutName": "Default",
                "contents": [
                    {
                        "type": "text",
                        "placeholderIdx": 0,
                        "runs": [
                            {"text": "Hello World", "bold": True},
                        ],
                    }
                ],
            }
        ]

        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
            output_path = f.name

        try:
            result = renderer.render(
                slides=slides,
                metadata={"title": "Test Presentation", "author": "Test Author"},
                output_path=output_path,
            )

            assert os.path.exists(result)
            assert os.path.getsize(result) > 0

            # Verify it's a valid PPTX by opening with python-pptx
            from pptx import Presentation
            prs = Presentation(result)
            assert len(prs.slides) == 1
        finally:
            os.unlink(output_path)

    def test_render_multiple_slides(self, renderer):
        """Test rendering multiple slides."""
        slides = [
            {
                "slideIndex": 0,
                "layoutId": "1",
                "layoutName": "Default",
                "contents": [
                    {
                        "type": "text",
                        "placeholderIdx": 0,
                        "runs": [{"text": "Title Slide", "bold": True}],
                    }
                ],
            },
            {
                "slideIndex": 1,
                "layoutId": "1",
                "layoutName": "Default",
                "contents": [
                    {
                        "type": "text",
                        "placeholderIdx": 0,
                        "runs": [
                            {"text": "Point 1", "bullet": True, "level": 0},
                            {"text": "Point 2", "bullet": True, "level": 0},
                        ],
                    }
                ],
            },
        ]

        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
            output_path = f.name

        try:
            result = renderer.render(
                slides=slides,
                metadata={"title": "Multi-Slide Test"},
                output_path=output_path,
            )

            assert os.path.exists(result)
            from pptx import Presentation
            prs = Presentation(result)
            assert len(prs.slides) == 2
        finally:
            os.unlink(output_path)


def _write_minimal_rels(rels_path: str, extra_relationships: str = "") -> None:
    os.makedirs(os.path.dirname(rels_path), exist_ok=True)
    with open(rels_path, "w", encoding="utf-8") as f:
        f.write(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            '<Relationship Id="rId1" Type=".../slideMaster" Target="slideMasters/slideMaster1.xml"/>\n'
            f"{extra_relationships}"
            "</Relationships>"
        )


class TestPresentationRelsRidAllocation:
    """Regression tests: rId{i+2} was hardcoded, assuming rId1 is always the
    slideMaster and rId2..N+1 are free - any template whose
    presentation.xml.rels already used those IDs for something else (a
    theme, printerSettings, ...) got duplicate Id attributes, which is
    invalid OOXML and makes PowerPoint report the file as needing repair.
    """

    @pytest.fixture
    def renderer(self):
        return PPTXRenderer(template_path=None)

    def test_new_slide_rids_do_not_collide_with_existing_relationships(self, renderer, tmp_path):
        working_dir = str(tmp_path)
        rels_path = os.path.join(working_dir, "ppt", "_rels", "presentation.xml.rels")
        # A theme relationship already occupies rId2 - the exact ID the old
        # hardcoded scheme would have reassigned to the first new slide.
        _write_minimal_rels(
            rels_path,
            '<Relationship Id="rId2" Type=".../theme" Target="theme/theme1.xml"/>\n',
        )

        rids = renderer._update_presentation_rels(working_dir, slide_count=2)

        assert rids == ["rId3", "rId4"]

        tree = ET.parse(rels_path)
        ids = [rel.get("Id") for rel in tree.getroot()]
        assert len(ids) == len(set(ids)), f"duplicate relationship IDs: {ids}"
        assert "rId2" in ids  # the pre-existing theme relationship survives untouched

    def test_creates_sldidlst_when_the_template_omits_it_entirely(self, renderer, tmp_path):
        """Regression: our own generated default template (0 slides) omits
        <p:sldIdLst> rather than emitting it empty. The renderer used to
        bail out silently here, producing a 0-slide package that still
        reported success - the "downloaded pptx is empty" failure mode."""
        working_dir = str(tmp_path)
        rels_path = os.path.join(working_dir, "ppt", "_rels", "presentation.xml.rels")
        _write_minimal_rels(rels_path)
        pres_path = os.path.join(working_dir, "ppt", "presentation.xml")
        os.makedirs(os.path.dirname(pres_path), exist_ok=True)
        with open(pres_path, "w", encoding="utf-8") as f:
            f.write(
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
                '<p:sldSz cx="9144000" cy="6858000"/>'
                "</p:presentation>"
            )

        renderer._update_presentation_xml(working_dir, slide_count=2)

        tree = ET.parse(pres_path)
        root = tree.getroot()
        sldIdLst = root.find("p:sldIdLst", NS)
        assert sldIdLst is not None
        assert len(list(sldIdLst)) == 2
        # Schema order: sldIdLst must land between sldMasterIdLst and sldSz.
        tags = [el.tag.split("}")[-1] for el in root]
        assert tags.index("sldMasterIdLst") < tags.index("sldIdLst") < tags.index("sldSz")

    def test_presentation_xml_and_rels_agree_on_the_same_rids(self, renderer, tmp_path):
        working_dir = str(tmp_path)
        rels_path = os.path.join(working_dir, "ppt", "_rels", "presentation.xml.rels")
        _write_minimal_rels(
            rels_path,
            '<Relationship Id="rId2" Type=".../theme" Target="theme/theme1.xml"/>\n',
        )
        pres_path = os.path.join(working_dir, "ppt", "presentation.xml")
        os.makedirs(os.path.dirname(pres_path), exist_ok=True)
        with open(pres_path, "w", encoding="utf-8") as f:
            f.write(
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                "<p:sldIdLst/></p:presentation>"
            )

        renderer._update_presentation_xml(working_dir, slide_count=2)

        tree = ET.parse(pres_path)
        sldIdLst = tree.getroot().find(".//p:sldIdLst", NS)
        xml_rids = [sldId.get(f"{{{NS['r']}}}id") for sldId in sldIdLst]

        rels_tree = ET.parse(rels_path)
        rels_ids = {rel.get("Id") for rel in rels_tree.getroot()}

        assert xml_rids == ["rId3", "rId4"]
        assert set(xml_rids).issubset(rels_ids)


class TestCloneLayoutPreservesFormatting:
    """Regression: bullets beyond what the template placeholder itself had
    paragraphs for were created with no pPr/rPr at all, so they silently
    lost the template's bullet marker, indent, and typography - making
    multi-point slides look thinner/plainer than the template's design.
    """

    @pytest.fixture
    def renderer(self):
        return PPTXRenderer(template_path=None)

    @pytest.fixture
    def default_template_layout2(self, tmp_path):
        """The bundled default template's slideLayout2.xml ("Title and
        Content") ships 5 pre-existing body paragraphs (levels 0-4) under
        placeholder idx=1 - real template content, not a synthetic fixture."""
        from app.renderer.default_template import get_default_template_path

        template_path = get_default_template_path(output_dir=str(tmp_path / "templates"))
        working_dir = tmp_path / "extracted"
        with zipfile.ZipFile(template_path) as zf:
            zf.extractall(working_dir)
        return str(working_dir / "ppt" / "slideLayouts" / "slideLayout2.xml")

    def test_extra_bullets_inherit_the_template_s_paragraph_and_run_formatting(
        self, renderer, default_template_layout2, tmp_path
    ):
        # 7 runs against a placeholder whose layout only has 5 paragraphs -
        # runs 6 and 7 must be freshly created and cloned-formatted.
        runs = [{"text": f"Point {i}", "bullet": True, "level": 0} for i in range(7)]
        slide_data = {
            "slideIndex": 0,
            "layoutId": "2",
            "contents": [{"type": "text", "placeholderIdx": 1, "runs": runs}],
        }

        xml_bytes, _image_rels = renderer._clone_layout_as_slide(
            default_template_layout2, slide_data, str(tmp_path), slide_num=1
        )

        root = ET.fromstring(xml_bytes)
        txBody = None
        for sp in root.findall(".//p:sp", NS):
            ph = sp.find(".//p:ph", NS)
            if ph is not None and ph.get("idx") == "1":
                txBody = sp.find(".//p:txBody", NS)
                break
        assert txBody is not None

        paragraphs = txBody.findall("a:p", NS)
        assert len(paragraphs) == 7

        # The two newly-created paragraphs (index 5, 6) must carry cloned
        # formatting, not bare <a:p><a:r><a:t> with nothing else.
        for extra_para in paragraphs[5:]:
            assert extra_para.find("a:pPr", NS) is not None, "new paragraph lost the template's pPr (bullet/indent)"
            run = extra_para.find("a:r", NS)
            assert run is not None
            assert run.find("a:rPr", NS) is not None, "new run lost the template's rPr (font/size/color)"
            assert run.find("a:t", NS).text == "Point 5" or run.find("a:t", NS).text == "Point 6"

        # And the reused paragraphs (0-4) still just get their text swapped in.
        for i, para in enumerate(paragraphs[:5]):
            run = para.find("a:r", NS)
            assert run.find("a:t", NS).text == f"Point {i}"


class TestPPTXRendererFromTemplate:
    """End-to-end coverage for the template-clone path (_render_from_template),
    which previously had zero test coverage even though it's the path real
    uploaded templates and the default template both go through."""

    @pytest.fixture
    def template_path(self, tmp_path):
        from app.renderer.default_template import get_default_template_path
        return get_default_template_path(output_dir=str(tmp_path / "templates"))

    def test_render_with_more_bullets_than_the_layout_has_paragraphs(self, template_path, tmp_path):
        renderer = PPTXRenderer(template_path=template_path)
        runs = [{"text": f"Point {i}", "bullet": True, "level": 0} for i in range(7)]
        slides = [
            {
                "slideIndex": 0,
                "layoutId": "2",
                "layoutName": "Title and Content",
                "contents": [{"type": "text", "placeholderIdx": 1, "runs": runs}],
            }
        ]
        output_path = str(tmp_path / "out.pptx")

        result = renderer.render(slides=slides, metadata={"title": "Test"}, output_path=output_path)

        assert os.path.exists(result)
        assert os.path.getsize(result) > 0

        from pptx import Presentation
        prs = Presentation(result)
        assert len(prs.slides) == 1

        # No duplicate relationship IDs anywhere in the repacked package.
        with zipfile.ZipFile(result) as zf:
            rels_xml = zf.read("ppt/_rels/presentation.xml.rels")
            rels_root = ET.fromstring(rels_xml)
            ids = [rel.get("Id") for rel in rels_root]
            assert len(ids) == len(set(ids)), f"duplicate relationship IDs: {ids}"
class TestPPTXRendererColorTheme:
    """A color_theme choice (see app.design_system.COLOR_THEMES) must
    actually change the rendered title color on the no-template path -
    otherwise asking the user for a preference and then ignoring it would
    be worse than not asking at all."""

    def _title_slide(self):
        return [{
            "slideIndex": 0,
            "layoutId": "1",
            "layoutName": "Default",
            "contents": [{
                "type": "text",
                "placeholderIdx": 0,
                "runs": [{"text": "Title", "bold": True, "fontSizePt": 36}],
            }],
        }]

    def _render_and_get_title_run(self, color_theme):
        renderer = PPTXRenderer(template_path=None, color_theme=color_theme)
        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
            output_path = f.name
        try:
            result = renderer.render(
                slides=self._title_slide(), metadata={"title": "T"}, output_path=output_path,
            )
            from pptx import Presentation
            prs = Presentation(result)
            slide = prs.slides[0]
            for shape in slide.shapes:
                if shape.has_text_frame and shape.text_frame.text == "Title":
                    return shape.text_frame.paragraphs[0].runs[0]
            raise AssertionError("title run not found")
        finally:
            os.unlink(output_path)

    def test_warm_theme_applies_the_warm_primary_color_to_the_title(self):
        run = self._render_and_get_title_run("warm")
        assert run.font.color.rgb == _hex_to_rgbcolor(next(c["value"] for c in COLOR_THEMES["warm"] if c["name"] == "primary"))

    def test_no_theme_defaults_to_the_professional_primary_color(self):
        run = self._render_and_get_title_run(None)
        assert run.font.color.rgb == _hex_to_rgbcolor(next(c["value"] for c in COLOR_THEMES["professional"] if c["name"] == "primary"))

    def test_unknown_theme_falls_back_to_professional(self):
        run = self._render_and_get_title_run("not-a-real-theme")
        assert run.font.color.rgb == _hex_to_rgbcolor(next(c["value"] for c in COLOR_THEMES["professional"] if c["name"] == "primary"))

    def test_different_themes_produce_different_title_colors(self):
        warm_run = self._render_and_get_title_run("warm")
        mono_run = self._render_and_get_title_run("monochrome")
        assert warm_run.font.color.rgb != mono_run.font.color.rgb


class TestPPTXRendererDefaultShapeSizing:
    """Regression: PositionConfig's fields are documented as inches (plain
    floats - see layout.py), but _add_text_box/_add_image used to pass them
    straight to python-pptx's add_textbox/add_picture, which interpret a
    bare number as EMU (1 inch = 914400 EMU). A "1.5 inch" position became a
    shape 1.5 EMU wide - sub-pixel, invisible - on every slide of the
    no-template render path. python-pptx (and this test) can still read the
    (garbage) values back, which is exactly why this shipped unnoticed:
    only an actual rendering engine (PowerPoint/LibreOffice) showed a blank
    slide. Confirmed by converting a rendered file with LibreOffice headless
    and finding zero extractable text before this fix, full text after.
    """

    def test_text_box_is_sized_in_inches_not_raw_emu(self):
        from pptx import Presentation
        from pptx.util import Inches

        renderer = PPTXRenderer(template_path=None)
        slides = [{
            "slideIndex": 0,
            "layoutId": "1",
            "layoutName": "Default",
            "contents": [{
                "type": "text",
                "placeholderIdx": 0,
                "runs": [{"text": "Title", "bold": True, "fontSizePt": 36}],
            }],
        }]
        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
            output_path = f.name
        try:
            renderer.render(slides=slides, metadata={"title": "T"}, output_path=output_path)
            prs = Presentation(output_path)
            shape = next(s for s in prs.slides[0].shapes if s.has_text_frame and not s.is_placeholder)

            # The old bug produced shapes a handful of EMU wide/tall; a
            # correctly-converted inch-based size is always comfortably
            # above a tenth of an inch for any real slide element.
            assert shape.width > Inches(0.1)
            assert shape.height > Inches(0.1)
            # And matches what LayoutEngine.calculate_title_position actually
            # specified (1 inch margin either side on the default 10in slide).
            assert shape.width == Inches(8.0)
        finally:
            os.unlink(output_path)

    def test_multiple_stacked_content_blocks_use_inch_offsets(self):
        """The top_offset accumulator mixed a Length (EMU) with
        PositionConfig's inch-float on the second+ content block - fixed to
        stay in inches throughout."""
        from pptx import Presentation
        from pptx.util import Inches

        renderer = PPTXRenderer(template_path=None)
        slides = [{
            "slideIndex": 0,
            "layoutId": "1",
            "layoutName": "Default",
            "contents": [
                {"type": "text", "placeholderIdx": 0, "runs": [{"text": "First block"}]},
                {"type": "text", "placeholderIdx": 1, "runs": [{"text": "Second block"}]},
            ],
        }]
        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
            output_path = f.name
        try:
            renderer.render(slides=slides, metadata={"title": "T"}, output_path=output_path)
            prs = Presentation(output_path)
            text_shapes = [s for s in prs.slides[0].shapes if s.has_text_frame and not s.is_placeholder]
            assert len(text_shapes) == 2
            for shape in text_shapes:
                assert shape.top < Inches(1000), "top offset leaked raw EMU into the inches field"
                assert shape.height > Inches(0.1)
        finally:
            os.unlink(output_path)


class TestTextFitAutofit:
    """Unit coverage for app.renderer.text_fit, the shrink-to-fit estimator
    used to stop long text from visually overlapping other content - the
    root cause confirmed by rendering long text with LibreOffice and seeing
    real overlap even though validate_font_size only ever enforced a
    minimum size, never shrank anything."""

    def test_short_text_needs_no_shrinking(self):
        from app.renderer import text_fit
        blocks = [text_fit.TextBlock(text="Short line", font_size_pt=18)]
        scale, ln_reduction = text_fit.compute_autofit_scale(blocks, box_width_pt=400, box_height_pt=200)
        assert scale == 1.0
        assert ln_reduction == 0

    def test_long_text_in_a_small_box_is_shrunk(self):
        from app.renderer import text_fit
        long_text = " ".join(["word"] * 400)
        blocks = [text_fit.TextBlock(text=long_text, font_size_pt=20)]
        scale, _ = text_fit.compute_autofit_scale(blocks, box_width_pt=300, box_height_pt=100)
        assert scale < 1.0
        assert scale >= text_fit.MIN_FONT_SCALE

    def test_zero_size_box_is_left_alone(self):
        """No usable geometry (e.g. a placeholder with no explicit xfrm) must
        not be treated as "infinitely small" - that would force every
        placeholder down to the minimum scale for no reason."""
        from app.renderer import text_fit
        blocks = [text_fit.TextBlock(text="Anything", font_size_pt=18)]
        scale, ln_reduction = text_fit.compute_autofit_scale(blocks, box_width_pt=0, box_height_pt=0)
        assert scale == 1.0
        assert ln_reduction == 0


class TestCloneLayoutAutofit:
    """The template-clone path must never touch <a:rPr> (that's the whole
    point of cloning - see the module docstring), so overflow protection has
    to be the OOXML-native <a:normAutofit> render-time hint instead."""

    @pytest.fixture
    def renderer(self):
        return PPTXRenderer(template_path=None)

    @pytest.fixture
    def small_boxed_layout(self, tmp_path):
        """A synthetic single-placeholder layout with an explicit, small
        <a:xfrm> so the fit estimate has real geometry to judge against."""
        xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldLayout xmlns:a="{NS['a']}" xmlns:r="{NS['r']}" xmlns:p="{NS['p']}">
  <p:cSld>
    <p:spTree>
      <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
      <p:grpSpPr/>
      <p:sp>
        <p:nvSpPr>
          <p:cNvPr id="2" name="Body"/>
          <p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>
          <p:nvPr><p:ph type="body" idx="1"/></p:nvPr>
        </p:nvSpPr>
        <p:spPr>
          <a:xfrm><a:off x="457200" y="457200"/><a:ext cx="914400" cy="457200"/></a:xfrm>
        </p:spPr>
        <p:txBody>
          <a:bodyPr/>
          <a:lstStyle/>
          <a:p><a:pPr/><a:r><a:rPr sz="2000"/><a:t>placeholder</a:t></a:r></a:p>
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sldLayout>"""
        layout_path = tmp_path / "slideLayout9.xml"
        layout_path.write_text(xml)
        return str(layout_path)

    def test_overlong_text_gets_normautofit_without_touching_rpr(self, renderer, small_boxed_layout, tmp_path):
        # The box is 1in x 0.5in at 20pt - far too small for several long
        # bullet points at that size, so this must trigger autofit.
        runs = [
            {"text": "This is a fairly long bullet point that will not fit", "bullet": True},
            {"text": "Here is another long bullet point stretching the box", "bullet": True},
            {"text": "And a third one to make sure it clearly overflows", "bullet": True},
        ]
        slide_data = {
            "slideIndex": 0,
            "layoutId": "9",
            "contents": [{"type": "text", "placeholderIdx": 1, "runs": runs}],
        }

        xml_bytes, _ = renderer._clone_layout_as_slide(small_boxed_layout, slide_data, str(tmp_path), slide_num=1)
        root = ET.fromstring(xml_bytes)

        sp = next(sp for sp in root.findall(".//p:sp", NS) if sp.find(".//p:ph", NS).get("idx") == "1")
        txBody = sp.find(".//p:txBody", NS)
        bodyPr = txBody.find("a:bodyPr", NS)
        assert bodyPr is not None
        normAutofit = bodyPr.find("a:normAutofit", NS)
        assert normAutofit is not None, "overflowing placeholder must get a normAutofit hint"
        assert int(normAutofit.get("fontScale")) < 100000

    def test_short_text_gets_no_autofit_element(self, renderer, small_boxed_layout, tmp_path):
        slide_data = {
            "slideIndex": 0,
            "layoutId": "9",
            "contents": [{"type": "text", "placeholderIdx": 1, "runs": [{"text": "Hi", "bullet": True}]}],
        }
        xml_bytes, _ = renderer._clone_layout_as_slide(small_boxed_layout, slide_data, str(tmp_path), slide_num=1)
        root = ET.fromstring(xml_bytes)
        sp = next(sp for sp in root.findall(".//p:sp", NS) if sp.find(".//p:ph", NS).get("idx") == "1")
        bodyPr = sp.find(".//p:txBody/a:bodyPr", NS)
        assert bodyPr is None or bodyPr.find("a:normAutofit", NS) is None


class TestCloneLayoutImages:
    """Regression: the template-clone path used to silently drop every
    non-text content item (`if content.get("type") != "text": continue`),
    so an image the LLM placed on a slide never made it into the output
    whenever a user-uploaded template was in use - the primary path. Images
    must now render there too."""

    # A minimal valid 1x1 transparent PNG.
    ONE_PX_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="

    @pytest.fixture
    def renderer(self):
        return PPTXRenderer(template_path=None)

    @pytest.fixture
    def default_template_layout2(self, tmp_path):
        from app.renderer.default_template import get_default_template_path
        template_path = get_default_template_path(output_dir=str(tmp_path / "templates"))
        working_dir = tmp_path / "extracted"
        with zipfile.ZipFile(template_path) as zf:
            zf.extractall(working_dir)
        return str(working_dir / "ppt" / "slideLayouts" / "slideLayout2.xml"), str(working_dir)

    def test_base64_image_is_added_as_a_picture_shape(self, renderer, default_template_layout2, tmp_path):
        layout_file, working_dir = default_template_layout2
        slide_data = {
            "slideIndex": 0,
            "layoutId": "2",
            "contents": [{
                "type": "image",
                "image": {"source": "base64", "data": self.ONE_PX_PNG_B64, "altText": "Test image"},
            }],
        }

        xml_bytes, image_rels = renderer._clone_layout_as_slide(layout_file, slide_data, working_dir, slide_num=1)

        assert len(image_rels) == 1
        assert image_rels[0]["rId"] == "rId2"
        assert image_rels[0]["target"].startswith("../media/image")

        root = ET.fromstring(xml_bytes)
        pics = root.findall(".//p:pic", NS)
        assert len(pics) == 1
        blip = pics[0].find(".//a:blip", NS)
        assert blip.get(f"{{{NS['r']}}}embed") == "rId2"

        # The image bytes actually landed on disk.
        media_dir = os.path.join(working_dir, "ppt", "media")
        assert os.path.exists(media_dir)
        assert len(os.listdir(media_dir)) >= 1

    def test_data_uri_prefixed_base64_is_decoded(self, renderer, default_template_layout2, tmp_path):
        layout_file, working_dir = default_template_layout2
        slide_data = {
            "slideIndex": 0,
            "layoutId": "2",
            "contents": [{
                "type": "image",
                "image": {"source": "base64", "data": f"data:image/png;base64,{self.ONE_PX_PNG_B64}"},
            }],
        }
        xml_bytes, image_rels = renderer._clone_layout_as_slide(layout_file, slide_data, working_dir, slide_num=1)
        assert len(image_rels) == 1

    def test_unresolvable_image_is_skipped_not_crashed(self, renderer, default_template_layout2, tmp_path):
        layout_file, working_dir = default_template_layout2
        slide_data = {
            "slideIndex": 0,
            "layoutId": "2",
            "contents": [{"type": "image", "image": {"source": "base64", "data": "not-valid-base64!!"}}],
        }
        xml_bytes, image_rels = renderer._clone_layout_as_slide(layout_file, slide_data, working_dir, slide_num=1)
        assert image_rels == []
        root = ET.fromstring(xml_bytes)
        assert root.findall(".//p:pic", NS) == []

    def test_end_to_end_render_with_template_and_image_produces_valid_pptx(self, tmp_path):
        """Full render() through the template path with an image content
        item must produce a file python-pptx (and therefore PowerPoint/
        LibreOffice) can open, with the picture actually present."""
        from app.renderer.default_template import get_default_template_path
        from pptx import Presentation

        template_path = get_default_template_path(output_dir=str(tmp_path / "templates"))
        renderer = PPTXRenderer(template_path=template_path)
        slides = [{
            "slideIndex": 0,
            "layoutId": "2",
            "layoutName": "Title and Content",
            "contents": [
                {"type": "text", "placeholderIdx": 0, "runs": [{"text": "Title"}]},
                {"type": "image", "image": {"source": "base64", "data": self.ONE_PX_PNG_B64}},
            ],
        }]
        output_path = str(tmp_path / "out.pptx")
        result = renderer.render(slides=slides, metadata={"title": "T"}, output_path=output_path)

        assert os.path.exists(result)
        prs = Presentation(result)
        assert len(prs.slides) == 1
        picture_shapes = [s for s in prs.slides[0].shapes if s.shape_type == 13]  # MSO_SHAPE_TYPE.PICTURE
        assert len(picture_shapes) == 1


class TestTableRendering:
    """Tables were previously schema-legal (validate_slide_json accepted
    them, design_slide_content's prompt told the LLM to use them) but
    functionally dead - no add_table call existed anywhere in the renderer
    on either render path. Both paths must now produce a real table, styled
    from the run's design system rather than python-pptx defaults."""

    TABLE_CONTENT = {
        "type": "table",
        "table": {
            "headerRow": True,
            "rows": [["Region", "Revenue"], ["EMEA", "$4.2M"], ["APAC", "$3.1M"]],
        },
    }

    BRAND_DESIGN_SYSTEM = {
        "palette": {"colors": [{"name": "primary", "value": "#00704A", "type": "hex"}], "schemeName": "brand"},
    }

    def test_default_path_produces_a_real_table_shape(self, tmp_path):
        from pptx import Presentation

        renderer = PPTXRenderer(template_path=None, design_system=self.BRAND_DESIGN_SYSTEM)
        slides = [{
            "slideIndex": 0,
            "layoutId": "1",
            "layoutName": "Default",
            "contents": [
                {"type": "text", "placeholderIdx": 0, "runs": [{"text": "Regional Revenue", "bold": True, "fontSizePt": 36}]},
                self.TABLE_CONTENT,
            ],
        }]
        output_path = str(tmp_path / "out.pptx")
        renderer.render(slides=slides, metadata={"title": "T"}, output_path=output_path)

        prs = Presentation(output_path)
        tables = [s for s in prs.slides[0].shapes if s.has_table]
        assert len(tables) == 1
        table = tables[0].table
        assert len(table.rows) == 3
        assert len(table.columns) == 2
        assert table.cell(0, 0).text == "Region"
        assert table.cell(1, 1).text == "$4.2M"

    def test_default_path_header_row_uses_the_design_system_primary_color(self, tmp_path):
        from pptx import Presentation

        renderer = PPTXRenderer(template_path=None, design_system=self.BRAND_DESIGN_SYSTEM)
        slides = [{
            "slideIndex": 0,
            "layoutId": "1",
            "layoutName": "Default",
            "contents": [self.TABLE_CONTENT],
        }]
        output_path = str(tmp_path / "out.pptx")
        renderer.render(slides=slides, metadata={"title": "T"}, output_path=output_path)

        prs = Presentation(output_path)
        table = next(s for s in prs.slides[0].shapes if s.has_table).table
        header_cell = table.cell(0, 0)
        assert header_cell.fill.fore_color.rgb == _hex_to_rgbcolor("#00704A")

    def test_template_path_produces_a_graphic_frame_table(self, tmp_path):
        from app.renderer.default_template import get_default_template_path

        template_path = get_default_template_path(output_dir=str(tmp_path / "templates"))
        renderer = PPTXRenderer(template_path=template_path, design_system=self.BRAND_DESIGN_SYSTEM)
        slides = [{
            "slideIndex": 0,
            "layoutId": "2",
            "layoutName": "Title and Content",
            "contents": [
                {"type": "text", "placeholderIdx": 0, "runs": [{"text": "Title"}]},
                self.TABLE_CONTENT,
            ],
        }]
        output_path = str(tmp_path / "out.pptx")
        result = renderer.render(slides=slides, metadata={"title": "T"}, output_path=output_path)

        from pptx import Presentation
        prs = Presentation(result)
        assert len(prs.slides) == 1
        tables = [s for s in prs.slides[0].shapes if s.has_table]
        assert len(tables) == 1
        table = tables[0].table
        assert table.cell(0, 0).text == "Region"
        assert table.cell(2, 1).text == "$3.1M"

    def test_no_design_system_falls_back_to_default_colors(self, tmp_path):
        """A run with no extracted design system (e.g. Stage D not landed
        yet, or extraction genuinely produced nothing) must still render a
        table, just with the built-in default colors - never crash."""
        from pptx import Presentation

        renderer = PPTXRenderer(template_path=None, design_system=None)
        slides = [{
            "slideIndex": 0,
            "layoutId": "1",
            "layoutName": "Default",
            "contents": [self.TABLE_CONTENT],
        }]
        output_path = str(tmp_path / "out.pptx")
        renderer.render(slides=slides, metadata={"title": "T"}, output_path=output_path)
        prs = Presentation(output_path)
        assert len([s for s in prs.slides[0].shapes if s.has_table]) == 1

    def test_empty_rows_are_skipped_not_crashed(self, tmp_path):
        from pptx import Presentation

        renderer = PPTXRenderer(template_path=None)
        slides = [{
            "slideIndex": 0,
            "layoutId": "1",
            "layoutName": "Default",
            "contents": [{"type": "table", "table": {"rows": []}}],
        }]
        output_path = str(tmp_path / "out.pptx")
        renderer.render(slides=slides, metadata={"title": "T"}, output_path=output_path)
        prs = Presentation(output_path)
        assert len([s for s in prs.slides[0].shapes if s.has_table]) == 0
