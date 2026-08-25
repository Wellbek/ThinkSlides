"""
Where a figure can come from, and which to reach for first.

Five sources now. Left unstated, the most convenient becomes the default and a
deck loses the one thing that made its figures look like a set - so the order
is written down once, and every tool that offers a figure points at it.

Two of the five were not in the original plan and are the reason this exists:
a customer's MCP server can return a drawn figure, and a vision model can
generate an image. The first goes through the same importer as everything
else; the second cannot, and that difference is the whole point of the order.
"""
from app.renderer.svg import sources


class TestTheOrderIsByHowMuchTheDeckStillControls:
    def test_a_typed_diagram_comes_first(self):
        """Nothing else is on-brand by construction."""
        assert sources.ORDER[0].tool == "build_diagram"

    def test_a_generated_image_comes_last(self):
        """Not because it looks worst. It is the only source that cannot be
        recoloured, checked or edited afterwards."""
        assert sources.ORDER[-1].tool == "generate_image"

    def test_mermaid_is_preferred_over_a_hand_drawn_figure(self):
        """Mermaid brings a layout engine; draw_svg brings nothing but the
        model's judgement."""
        assert sources.rank("build_mermaid") < sources.rank("draw_svg")

    def test_the_four_vector_sources_end_as_shapes(self):
        vector = [s.tool for s in sources.ORDER if s.vector]
        assert vector == ["build_diagram", "mcp", "build_mermaid", "draw_svg"]

    def test_only_the_raster_one_does_not(self):
        assert [s.tool for s in sources.ORDER if not s.vector] == \
            ["generate_image"]

    def test_every_source_says_why_it_sits_where_it_does(self):
        for source in sources.ORDER:
            assert source.why

    def test_an_unknown_tool_ranks_last_rather_than_first(self):
        """A new figure tool should have to earn its place, not inherit the
        top of the order by not being listed."""
        assert sources.rank("something_new") >= len(sources.ORDER)


class TestAServerThatReturnsAFigureIsAFigureSource:
    """Pasting it as a flat picture would be a silent downgrade - the kind
    nobody notices until a user tries to recolour it."""

    # A chromatic fill, deliberately: a near-neutral is exempt from palette
    # remapping, because a grey rule is structural rather than branded.
    SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50">'
           '<rect x="5" y="5" width="40" height="30" fill="#7C4DFF"/></svg>')

    def _detect(self, text):
        from app.connections.mcp_tools import _svg_figure

        return _svg_figure(text)

    def test_a_returned_document_is_recognised(self):
        assert self._detect(self.SVG) is not None

    def test_it_is_found_inside_surrounding_prose(self):
        assert self._detect(f"Here is the diagram:\n{self.SVG}\nHope it helps")

    def test_prose_that_merely_mentions_svg_is_not_a_figure(self):
        """Treating it as one would put a broken picture on a page in place of
        an answer."""
        assert self._detect("I can return an SVG if you want one.") is None

    def test_a_fragment_is_not_a_document(self):
        assert self._detect('<svg viewBox="0 0 10 10">') is None

    def test_something_the_importer_would_refuse_is_not_a_figure(self):
        """Only a document the importer will actually accept."""
        assert self._detect('<svg xmlns="http://www.w3.org/2000/svg"/>') is None

    def test_an_empty_result_is_not_a_figure(self):
        assert self._detect("") is None
        assert self._detect(None) is None

    def test_detection_is_on_content_not_on_a_tool_name(self):
        """A server called "diagrams" may return prose, and one called
        "lookup" may return a figure."""
        import inspect

        from app.connections import mcp_tools

        source = inspect.getsource(mcp_tools._svg_figure)
        assert "name" not in source.split('"""')[2]

    def test_a_recognised_figure_becomes_a_diagram_block(self):
        """The same block every other vector source produces, so it renders
        through the same hook and lands in the deck's palette."""
        from app.renderer.svg.importer import to_primitives
        from app.renderer.layout import PositionConfig

        document = self._detect(self.SVG)
        marks = to_primitives(document, PositionConfig(0, 0, 6, 4),
                              palette={"accent": "#1E8449"})
        assert marks and marks[0].kind == "path"
        assert marks[0].fill == "#1E8449"


class TestTheRasterSourceIsTreatedDifferentlyOnPurpose:
    def test_it_cannot_be_recoloured_and_the_order_says_so(self):
        generated = sources.BY_TOOL["generate_image"]
        assert not generated.vector
        assert "recoloured" in generated.why

    def test_the_note_can_be_carried_in_a_tool_description(self):
        note = sources.preference_note()
        for source in sources.ORDER:
            assert source.tool in note
