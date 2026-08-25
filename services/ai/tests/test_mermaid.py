"""
A graph, laid out by Mermaid and imported as shapes.

`build_diagram` has eight typed kinds and they are on-brand by construction.
What it cannot do is a graph - a state machine, a sequence, a dependency tree -
where the number of nodes is what the content decides.

Every test here runs against a **recorded** render. A test that needs Chromium
is a test that gets skipped in CI and then stops describing anything, so what
is pinned is the part that has to keep working: the import, the theming, and
the label recovery.
"""
import pathlib

import pytest

from app.renderer.layout import PositionConfig
from app.renderer.svg import mermaid
from app.renderer.svg.importer import to_primitives

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "mermaid" / "flowchart.svg"
PALETTE = {"primary": "#1F4E78", "secondary": "#2980B9", "accent": "#FF7F50",
           "background": "#FFFFFF", "text": "#1A1A1A"}


@pytest.fixture(scope="module")
def rendered() -> str:
    return FIXTURE.read_text()


def _box():
    return PositionConfig(left=1.0, top=1.5, width=11.0, height=4.0)


class TestAGraphIsRefusedBeforeABrowserIsStarted:
    def test_an_empty_graph(self):
        assert mermaid.check("") is not None

    def test_a_graph_too_long_to_read(self):
        assert "limit" in mermaid.check("graph LR\n" + "A-->B\n" * 3000)

    def test_a_graph_with_too_many_nodes(self):
        source = "graph LR\n" + "\n".join(
            f"  N{i}[Node {i}] --> N{i + 1}[Node {i + 1}]" for i in range(40))
        problem = mermaid.check(source)
        assert problem and "nodes" in problem

    def test_a_reasonable_graph_passes(self):
        assert mermaid.check("graph LR\n  A[One] --> B[Two]") is None


class TestTheDecksPaletteReachesTheDiagram:
    def test_the_theme_is_built_from_the_palette(self):
        theme = mermaid.theme_from(PALETTE)
        assert theme["primaryColor"] == "#1F4E78"
        assert theme["lineColor"] == "#1A1A1A"
        assert theme["background"] == "#FFFFFF"

    def test_an_empty_palette_still_produces_a_usable_theme(self):
        theme = mermaid.theme_from({})
        assert theme["primaryColor"] and theme["textColor"]

    def test_nodes_carry_the_theme_rather_than_defaulting_to_black(self, rendered):
        """Mermaid styles its nodes with a descendant selector the importer
        deliberately will not resolve, so without stamping the theme onto them
        every node imports black - a colour nobody chose."""
        prims = to_primitives(rendered, _box(), palette=PALETTE)
        fills = {p.fill for p in prims if p.kind == "path" and p.fill}
        assert "#1F4E78" in fills
        assert fills != {"#000000"}


class TestLabelsSurviveAsText:
    def test_every_node_label_is_live_text(self, rendered):
        """Mermaid puts labels in `<foreignObject>` - HTML smuggled into a
        picture - and the importer strips those, correctly, since an SVG can
        arrive from anywhere. They are recovered here, where the source is
        known."""
        prims = to_primitives(rendered, _box(), palette=PALETTE)
        labels = {p.runs[0].text for p in prims if p.kind == "text"}
        assert {"Ingest", "Compose", "Fits?", "Render"} <= labels

    def test_a_node_label_is_knocked_out_of_its_node(self, rendered):
        prims = to_primitives(rendered, _box(), palette=PALETTE)
        node_labels = [p for p in prims if p.kind == "text"
                       and p.runs[0].text in ("Ingest", "Compose", "Render")]
        assert node_labels
        assert all(p.color == "#FFFFFF" for p in node_labels)

    def test_an_edge_label_takes_the_ink_colour(self, rendered):
        """Given the node colour it comes out white on white, which is not a
        styling defect - it is a lost label."""
        prims = to_primitives(rendered, _box(), palette=PALETTE)
        edges = [p for p in prims if p.kind == "text"
                 and p.runs[0].text in ("yes", "no")]
        assert edges
        assert all(p.color == "#1A1A1A" for p in edges)

    def test_recovery_survives_an_empty_label(self):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
               '<foreignObject width="0" height="0"><div></div></foreignObject>'
               '</svg>')
        assert "<text" not in mermaid.inline_labels(svg)

    def test_recovered_text_is_escaped(self):
        svg = ('<foreignObject width="10" height="10"><div>a &amp; b '
               '&lt;c&gt;</div></foreignObject>')
        out = mermaid.inline_labels(svg)
        assert "&amp;" in out and "<c>" not in out


class TestTheGraphIsPositionedRatherThanPiledAtTheOrigin:
    def test_nodes_land_in_different_places(self, rendered):
        """Mermaid positions every node with a nested `<g transform>`, so a
        reader that ignores transforms imports a diagram with everything on
        top of everything else."""
        prims = to_primitives(rendered, _box(), palette=PALETTE)
        labels = [p for p in prims if p.kind == "text"]
        lefts = {round(p.box.left, 2) for p in labels}
        assert len(lefts) >= 4

    def test_a_translate_moves_a_point(self):
        matrix = mermaid_transform("translate(10,20)")
        assert mermaid_apply(matrix, (1.0, 2.0)) == (11.0, 22.0)

    def test_transforms_compose_as_they_nest(self):
        from app.renderer.svg.importer import (
            IDENTITY, _multiply, apply_transform, parse_transform,
        )

        outer = _multiply(IDENTITY, parse_transform("translate(10,0)"))
        inner = _multiply(outer, parse_transform("scale(2)"))
        assert apply_transform(inner, (3.0, 1.0)) == (16.0, 2.0)


def mermaid_transform(value):
    from app.renderer.svg.importer import parse_transform

    return parse_transform(value)


def mermaid_apply(matrix, point):
    from app.renderer.svg.importer import apply_transform

    return apply_transform(matrix, point)


class TestABrowserIsADependencyNotARenderer:
    def test_a_missing_browser_falls_back_rather_than_failing_the_deck(self):
        import asyncio

        from app.agent.tools.build_mermaid import BuildMermaidTool

        async def boom(*args, **kwargs):
            raise mermaid.MermaidUnavailable("no chromium here")

        original = mermaid.render
        mermaid.render = boom
        try:
            result = asyncio.run(BuildMermaidTool.run(
                slide_index=1, source="graph LR\n  A[One] --> B[Two]"))
        finally:
            mermaid.render = original
        assert not result.success
        assert "build_diagram" in result.error

    def test_the_bundle_is_vendored_and_pinned(self):
        """A render must not make a network request, and a pinned version
        keeps layout deterministic: Mermaid decides where every node goes, and
        a minor upgrade silently redraws every diagram ever produced."""
        assert mermaid.BUNDLE.exists()
        assert mermaid.MERMAID_VERSION in mermaid.BUNDLE.name

    def test_the_dependency_is_declared(self):
        """It was installed in the venv and in no manifest, which is the state
        where something works on one machine and nowhere else."""
        root = pathlib.Path(__file__).resolve().parents[1]
        assert "playwright" in (root / "requirements.txt").read_text()
        assert "chromium" in (root / "Dockerfile").read_text()

    def test_the_description_says_when_not_to_use_it(self):
        """Mermaid decides position, size and emphasis - right for a graph and
        wrong for a designed page."""
        from app.agent.tools.build_mermaid import WHEN_NOT_TO

        assert "build_diagram" in WHEN_NOT_TO
        assert "build_chart" in WHEN_NOT_TO
