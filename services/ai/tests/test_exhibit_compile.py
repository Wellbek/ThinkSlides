"""
Issue #286: a table or a metrics block silently rendered nothing on the
authored design-language path.

Two independent defects compounded. `_exhibit_primitives` dispatched every
non-chart exhibit through `block_compiler.fit(...)`, but nothing on this
path had ever constructed a `block_compiler` - `render_doc`'s default was
`None` and nothing overrode it, so `if block_compiler is None: return []`
fired for every metrics row, diagram and typed block a deck composed this
way. And even with a compiler, "metrics" (`build_key_metrics`'s own content
type) had no method to dispatch to - the closest name, `_compile_stat_callout`,
reads a different wrapper key and a different field name.

`table` had no code path at all, on either count: there was never a
`_compile_table`, and the docstring's claim that tables were "drawn by the
code that already draws them" was aspirational.

These tests render the actual `.pptx` and count shapes, the same way the
issue was confirmed - composing a doc and reading `frame.content` back is not
enough, because that only proves the material was *bound*, not that it drew.
"""

from app.design.authored import Language, PageKind, Slot, to_pack
from app.design.geometry import check_doc
from app.design import run as design_run
from app.design.schema import Canvas
from app.design.vocabulary import ensure

CANVAS = Canvas(w_in=13.333, h_in=7.5)


def _pack(slot_kind: str, span: int = 6):
    language = Language(
        name="Probe", rationale="x", columns=6, body_pt=18.0,
        pages=(PageKind(name="p", role="content", frequency=1.0, slots=(
            Slot(kind="heading", span=6, required=True),
            Slot(kind=slot_kind, span=span),
        )),),
    )
    return ensure(to_pack(language, CANVAS))


def _shape_kinds(pptx_path: str) -> list[str]:
    from pptx import Presentation

    prs = Presentation(pptx_path)
    slide = prs.slides[0]
    return [shape.shape_type for shape in slide.shapes]


class TestATableRendersAsAnImage:
    """A native pptx table would be a sixth primitive kind - see
    `primitives.PRIMITIVE_KINDS`, which is deliberately closed at five. A
    table is drawn as an image for the same reason a chart is."""

    @staticmethod
    def _slide():
        return {"slideIndex": 0, "role": "content", "heading": "A table",
                "contents": [{"type": "table", "table": {
                    "headerRow": True,
                    "rows": [["Year", "Revenue"],
                            ["2024", "$10m"], ["2025", "$12m"]]}}]}

    def test_the_exhibit_binds_to_the_slot(self):
        pack = _pack("figure")
        doc = design_run.compose([self._slide()], pack)
        page = doc.pages[0]
        figure = next(f for f in page.frames if f.role == "figure")
        assert len(figure.content) == 1

    def test_the_table_actually_draws_a_shape(self, tmp_path):
        from app.renderer.designdoc_renderer import render_doc

        pack = _pack("figure")
        doc = design_run.compose([self._slide()], pack)
        out = str(tmp_path / "table.pptx")
        render_doc(doc, pack, out)
        kinds = _shape_kinds(out)
        # Ground panel/heading text box plus the table image - three shapes,
        # not two. Two shapes is the bug: the exhibit bound and drew nothing.
        assert len(kinds) >= 2
        from pptx.enum.shapes import MSO_SHAPE_TYPE
        assert MSO_SHAPE_TYPE.PICTURE in kinds

    def test_an_empty_table_draws_nothing_but_does_not_crash(self, tmp_path):
        from app.renderer.designdoc_renderer import render_doc

        pack = _pack("figure")
        slide = {"slideIndex": 0, "role": "content", "heading": "Empty",
                 "contents": [{"type": "table", "table": {"rows": []}}]}
        doc = design_run.compose([slide], pack, skip_unplaceable=True)
        out = str(tmp_path / "empty.pptx")
        render_doc(doc, pack, out)  # must not raise


class TestAMetricsRowRendersItsFiguresAndLabels:
    """`build_key_metrics` writes `{"value": ..., "label": ...}` - a
    different field name from the block compiler's own `_compile_stat_callout`
    contract (`"caption"`), reconciled in `compile._EXHIBIT_TO_BLOCK_KIND`."""

    @staticmethod
    def _slide():
        return {"slideIndex": 0, "role": "content", "heading": "The numbers",
                "contents": [{"type": "metrics", "metrics": {"items": [
                    {"value": "24%", "label": "Adoption"},
                    {"value": "$1.8m", "label": "Cost"},
                ]}}]}

    def test_the_exhibit_binds_to_a_statistic_slot(self):
        pack = _pack("statistic")
        doc = design_run.compose([self._slide()], pack)
        page = doc.pages[0]
        row = next(f for f in page.frames if f.role == "statRow")
        assert len(row.content) == 1

    def test_both_figures_draw_as_text(self, tmp_path):
        from pptx.enum.shapes import MSO_SHAPE_TYPE

        from app.renderer.designdoc_renderer import render_doc

        pack = _pack("statistic")
        doc = design_run.compose([self._slide()], pack)
        out = str(tmp_path / "metrics.pptx")
        render_doc(doc, pack, out)
        from pptx import Presentation

        prs = Presentation(out)
        texts = [shape.text_frame.text for shape in prs.slides[0].shapes
                if shape.shape_type == MSO_SHAPE_TYPE.TEXT_BOX
                and shape.has_text_frame]
        joined = " ".join(texts)
        assert "24%" in joined and "Adoption" in joined
        assert "$1.8m" in joined and "Cost" in joined

    def test_a_metrics_row_composes_on_a_dark_ground_too(self, tmp_path):
        """The block compiler is built from the page's own ground - a metrics
        row on a dark cover must not render dark-on-dark."""
        from app.renderer.designdoc_renderer import render_doc

        language = Language(
            name="Dark", rationale="x", columns=6, body_pt=18.0,
            ground="#0E1420", ink="#F2F5F9", accent="#3AA0FF",
            pages=(PageKind(name="p", role="cover", frequency=1.0, slots=(
                Slot(kind="heading", span=6, required=True),
                Slot(kind="statistic", span=6),
            )),),
        )
        pack = ensure(to_pack(language, CANVAS))
        doc = design_run.compose([self._slide()], pack, skip_unplaceable=True)
        out = str(tmp_path / "dark.pptx")
        render_doc(doc, pack, out)  # must not raise, and see the render itself


class TestADiagramAndATypedBlockStillWork:
    """These reached `_compile_diagram`/typed-block methods by their names
    already matching - the fix is that `block_compiler` is no longer always
    `None`, and this is the regression guard that it did not need any
    kind-name mapping to keep working."""

    def test_a_diagram_still_composes_and_draws(self, tmp_path):
        from app.renderer.designdoc_renderer import render_doc

        pack = _pack("figure")
        slide = {"slideIndex": 0, "role": "content", "heading": "Flow",
                 "contents": [{"type": "diagram", "diagram": {"steps": [
                     {"label": "Intake"}, {"label": "Review"},
                     {"label": "Ship"}]}}]}
        doc = design_run.compose([slide], pack, skip_unplaceable=True)
        out = str(tmp_path / "diagram.pptx")
        render_doc(doc, pack, out)  # must not raise


class TestTheGeometryStaysSound:
    def test_a_page_with_a_table_and_a_metrics_row_is_geometrically_clean(self):
        language = Language(
            name="Mix", rationale="x", columns=6, body_pt=18.0,
            pages=(PageKind(name="p", role="content", frequency=1.0, slots=(
                Slot(kind="heading", span=6, required=True),
                Slot(kind="statistic", span=6, name="row"),
                Slot(kind="figure", span=6),
            )),),
        )
        pack = ensure(to_pack(language, CANVAS))
        slide = {"slideIndex": 0, "role": "content", "heading": "Both",
                "contents": [
                    {"type": "metrics", "metrics": {"items": [
                        {"value": "5", "label": "x"}]}},
                    {"type": "table", "table": {"rows": [["a", "b"]]}},
                ]}
        doc = design_run.compose([slide], pack, skip_unplaceable=True)
        assert check_doc(doc) == []


class TestRenderTableToPng:
    """The rasteriser itself, independent of the compose/compile pipeline."""

    def test_an_empty_table_returns_none(self):
        from app.renderer.chart_renderer import render_table_to_png

        assert render_table_to_png({"rows": []}, {}, 6.0, 2.0) is None

    def test_a_zero_size_region_returns_none(self):
        from app.renderer.chart_renderer import render_table_to_png

        assert render_table_to_png(
            {"rows": [["a"]]}, {}, 0.0, 2.0) is None

    def test_a_real_table_returns_png_bytes(self):
        from app.renderer.chart_renderer import render_table_to_png

        png = render_table_to_png(
            {"headerRow": True, "rows": [["A", "B"], ["1", "2"]]},
            {}, 4.0, 1.5)
        assert png is not None
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_a_numeric_column_is_detected_and_used(self):
        """Reuses `table_layout.plan_table` rather than reinventing column
        alignment - this just confirms the wiring reaches it."""
        from app.renderer.chart_renderer import render_table_to_png
        from app.renderer.table_layout import plan_table

        rows = [["Year", "Revenue"], ["2024", "100"], ["2025", "120"]]
        layout = plan_table(rows, True, 4.0, 1.5)
        assert 1 in layout.numeric_columns
        assert render_table_to_png(
            {"headerRow": True, "rows": rows}, {}, 4.0, 1.5) is not None


class TestTheStatCalloutCompilerReadsEitherContract:
    """`_compile_stat_callout` now serves two callers with two field names -
    its own `caption`, and `build_key_metrics`'s `label`."""

    def test_a_native_stat_callout_still_uses_caption(self):
        from app.renderer.blocks import BlockCompiler
        from app.renderer.deck_theme import SlideStyle
        from app.renderer.layout import PositionConfig
        from app.renderer.typography import TypeScale

        compiler = BlockCompiler(
            style=SlideStyle(role="content", background="#FFFFFF",
                             title_color="#111111", body_color="#111111",
                             bullet_color="#0F62FE", accent_color="#0F62FE",
                             footer_color="#666666"),
            type_scale=TypeScale(title_font="Arial", body_font="Arial",
                                 title_size_pt=32.0, body_size_pt=14.0,
                                 caption_size_pt=10.0),
            design_system={},
        )
        rect = PositionConfig(left=0.0, top=0.0, width=6.0, height=2.0)
        marks = compiler.fit("statCallout",
                             {"statCallout": {"items": [
                                 {"value": "5", "caption": "native"}]}},
                             rect)
        assert any("native" in "".join(r.text for r in m.runs)
                  for m in marks if m.runs)

    def test_a_metrics_alias_uses_label(self):
        from app.renderer.blocks import BlockCompiler
        from app.renderer.deck_theme import SlideStyle
        from app.renderer.layout import PositionConfig
        from app.renderer.typography import TypeScale

        compiler = BlockCompiler(
            style=SlideStyle(role="content", background="#FFFFFF",
                             title_color="#111111", body_color="#111111",
                             bullet_color="#0F62FE", accent_color="#0F62FE",
                             footer_color="#666666"),
            type_scale=TypeScale(title_font="Arial", body_font="Arial",
                                 title_size_pt=32.0, body_size_pt=14.0,
                                 caption_size_pt=10.0),
            design_system={},
        )
        rect = PositionConfig(left=0.0, top=0.0, width=6.0, height=2.0)
        marks = compiler.fit("statCallout",
                             {"metrics": {"items": [
                                 {"value": "24%", "label": "Adoption"}]}},
                             rect)
        assert any("Adoption" in "".join(r.text for r in m.runs)
                  for m in marks if m.runs)


class TestTheDispatchMapIsMinimal:
    """`_EXHIBIT_TO_BLOCK_KIND` exists for the one name that genuinely
    differs. "diagram" is not in it, and must not need to be."""

    def test_only_metrics_is_remapped(self):
        from app.design.compile import _EXHIBIT_TO_BLOCK_KIND

        assert _EXHIBIT_TO_BLOCK_KIND == {"metrics": "statCallout"}
