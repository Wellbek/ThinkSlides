"""
Three defects from one real deck, and what stops each recurring.

*Financial Performance and Audience Demographics of the Cars Movie Franchise*
shipped with a rule drawn through `$1,404 billion`, the literal text
`*Cars 2* leads in worldwide earnings`, and a reference marker rendered as a
blue underlined web link on a maroon ground.
"""
import pytest

from app.renderer import emphasis


class TestMarkdownIsResolvedNotRendered:
    """A model asked for prose writes markdown sooner or later. Rendering the
    markers is the one option that is always wrong."""

    def test_emphasis_inside_a_sentence_loses_its_markers(self):
        assert emphasis.read("*Cars 2* leads in worldwide earnings").text == \
            "Cars 2 leads in worldwide earnings"

    def test_a_wholly_emphasised_run_keeps_the_emphasis(self):
        """Which is what the model meant."""
        italic = emphasis.read("*Whole thing italic*")
        assert italic.text == "Whole thing italic" and italic.italic
        bold = emphasis.read("**All bold**")
        assert bold.text == "All bold" and bold.bold

    def test_emphasis_inside_a_longer_run_is_stripped_not_styled(self):
        """A run is one style. Splitting a run to chase an asterisk is a worse
        trade than losing the emphasis."""
        read = emphasis.read("**Cars** and *Cars 2* both")
        assert read.text == "Cars and Cars 2 both"
        assert not read.bold and not read.italic

    @pytest.mark.parametrize("text", [
        "A 5 * 3 multiplication",
        "footnote marker *",
        "a * b * c",
        "no markers here",
        "",
    ])
    def test_a_lone_asterisk_is_left_alone(self, text):
        """`*` on its own is a bullet or a footnote marker in half the decks
        ever written."""
        assert emphasis.read(text).text == text

    def test_it_does_not_become_a_markdown_parser(self):
        """A deck is not markdown, and a renderer interpreting `#` will eat a
        heading that was meant to be a hash."""
        for text in ("# Not a heading", "- not a bullet", "> not a quote",
                     "`not code`"):
            assert emphasis.read(text).text == text

    def test_the_renderer_uses_it(self):
        import inspect

        from app.renderer.pptx_renderer import PPTXRenderer

        source = inspect.getsource(PPTXRenderer)
        assert "emphasis.read(" in source


class TestARuleIsNeverDrawnThroughThePage:
    """The catalog puts the accent rule at a fixed height down the page, under
    where a catalog layout *would* put a one-line title. A title that wraps
    runs straight past it."""

    def _plan(self, title_top, title_height, rule_top, body_top=None):
        from app.renderer.layout import PositionConfig
        from app.renderer.pptx_renderer import SlidePlan, SlideStyle

        return SlidePlan(
            layout_id="content",
            style=SlideStyle(role="content", background="#FFFFFF",
                             title_color="#111111", body_color="#333333",
                             bullet_color="#333333", accent_color="#1F4E78",
                             footer_color="#777777"),
            title_region=PositionConfig(0.85, title_top, 11.63, title_height),
            body_regions=([PositionConfig(0.85, body_top, 11.63, 4.0)]
                          if body_top is not None else []),
            accent_rule=PositionConfig(0.85, rule_top, 1.5, 0.06),
        )

    class _Shape:
        def __init__(self, top_in, height_in):
            from pptx.util import Inches

            self.top = Inches(top_in)
            self.height = Inches(height_in)

    class _Slide:
        def __init__(self, shapes):
            self.shapes = shapes

    def test_a_short_title_leaves_the_rule_where_the_catalog_put_it(self):
        from app.renderer.pptx_renderer import PPTXRenderer

        plan = self._plan(title_top=0.85, title_height=0.9, rule_top=1.78)
        slide = self._Slide([self._Shape(0.85, 0.5)])
        placed = PPTXRenderer._rule_under_title(
            PPTXRenderer(design_system={}), slide, plan)
        assert placed is not None
        assert placed.top == pytest.approx(1.78)

    def test_a_wrapped_title_pushes_the_rule_down(self):
        """Rather than the rule being drawn through whatever is beneath - on
        one real deck, a line through the middle of `$1,404 billion`."""
        from app.renderer.pptx_renderer import PPTXRenderer

        plan = self._plan(title_top=0.85, title_height=0.9, rule_top=1.78,
                          body_top=4.0)
        slide = self._Slide([self._Shape(0.85, 1.4)])
        placed = PPTXRenderer._rule_under_title(
            PPTXRenderer(design_system={}), slide, plan)
        assert placed is not None
        assert placed.top > 1.78

    def test_a_rule_with_nowhere_to_go_is_not_drawn(self):
        """Chrome that cannot be placed is not drawn: a rule struck through
        the page is worse than none at all."""
        from app.renderer.pptx_renderer import PPTXRenderer

        plan = self._plan(title_top=0.85, title_height=2.6, rule_top=1.78,
                          body_top=2.2)
        slide = self._Slide([self._Shape(0.85, 2.5)])
        assert PPTXRenderer._rule_under_title(
            PPTXRenderer(design_system={}), slide, plan) is None

    def test_a_slide_with_no_title_keeps_the_catalog_position(self):
        from app.renderer.pptx_renderer import PPTXRenderer

        plan = self._plan(title_top=0.85, title_height=0.9, rule_top=1.78)
        placed = PPTXRenderer._rule_under_title(
            PPTXRenderer(design_system={}), self._Slide([]), plan)
        assert placed is not None and placed.top == pytest.approx(1.78)


class TestAReferenceMarkerIsApparatusNotALink:
    def test_the_marker_is_not_underlined(self):
        """Left to a renderer's own hyperlink styling it comes out blue and
        underlined, which on a dark ground is unreadable and reads as a broken
        link rather than as a footnote."""
        import inspect

        from app.renderer.pptx_renderer import PPTXRenderer

        source = inspect.getsource(PPTXRenderer)
        assert "marker.font.underline = False" in source

    def test_the_decks_colour_is_applied_after_the_link(self):
        """PowerPoint only substitutes its own hyperlink colour when the run
        has no explicit fill."""
        import inspect

        from app.renderer.pptx_renderer import PPTXRenderer

        source = inspect.getsource(PPTXRenderer)
        link = source.index("marker.hyperlink.address")
        colour = source.index("self._apply_text_color(marker")
        assert link < colour
