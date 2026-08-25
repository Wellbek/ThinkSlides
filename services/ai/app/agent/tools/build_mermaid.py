"""
Tool: build_mermaid

A graph, laid out by Mermaid and imported as native PowerPoint shapes.

`build_diagram` has eight typed kinds and they are on-brand by construction.
What it cannot do is a *graph* - a state machine, a sequence, a dependency
tree - anything where the number of nodes and edges is what the content
decides rather than what a template allows.

The trade this makes is the reason it is a third choice and not a first, and
the description says so: **Mermaid decides position, size and emphasis.** That
is right for a graph and wrong for a designed page. A deck whose figures are
all Mermaid has handed its composition to a layout engine that has never seen
the pack.
"""

from __future__ import annotations

import time

from app.agent.registry import ToolParameter, ToolRegistry, ToolResult
from app.agent.tools._binding import bind as _bind
from app.agent.tools._binding import slot_param as _slot_param
from app.observability.logging import get_logger

logger = get_logger(__name__)

WHEN_NOT_TO = (
    "Do NOT use this when build_diagram already has the shape - a process "
    "flow, a 2x2 matrix, a funnel, a timeline, a pyramid, a value chain, a "
    "Venn or an org chart are all better drawn there, because those are laid "
    "out to the deck's own grid and this is laid out by Mermaid. Do NOT use "
    "it for a quantity: a trend, a comparison or a breakdown is build_chart. "
    "Reach for it when the figure is genuinely a graph - a state machine, a "
    "sequence, a dependency tree - and the number of nodes is what the "
    "content decides."
)


class BuildMermaidTool:
    """Diagram-as-code for the graph shapes the typed vocabulary lacks."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="build_mermaid",
            description=(
                "Draw a graph from Mermaid source - a state machine, a "
                "sequence, a dependency tree. It is laid out by Mermaid and "
                "imported as native PowerPoint shapes in the deck's own "
                "colours, so every node stays selectable and every label "
                "stays editable text. " + WHEN_NOT_TO
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="slide_index", type="integer",
                    description="0-based index of the slide this graph belongs to",
                ),
                ToolParameter(
                    name="source", type="string",
                    description=(
                        "Mermaid source, starting with its diagram type - "
                        "'graph LR', 'stateDiagram-v2', 'sequenceDiagram', "
                        "'flowchart TD'. Keep every label to a word or two: "
                        "they are set in the deck's type at the deck's sizes. "
                        "Do not set colours - the deck's palette is applied."
                    ),
                ),
                ToolParameter(
                    name="caption", type="string", required=False,
                    description="One line saying what the graph shows.",
                ),
                _slot_param("'states' or 'sequence'"),
            ],
        )

    @classmethod
    async def run(cls, slide_index: int, source: str, caption: str = "",
                  slot: str = "") -> ToolResult:
        start = time.time()
        from app.agent import run_context
        from app.renderer.svg import mermaid

        problem = mermaid.check(source)
        if problem:
            return ToolResult(success=False, error=problem)

        palette = _palette_of(run_context.get_design_system())
        try:
            svg = await mermaid.render(source, palette=palette)
        except mermaid.MermaidUnavailable as exc:
            # A browser is a dependency, not a renderer. One figure falls
            # back; the deck ships. Said plainly so the model can put the
            # point in words or reach for build_diagram instead.
            logger.warning("mermaid_unavailable", error=str(exc))
            return ToolResult(
                success=False,
                error=(f"This diagram could not be laid out ({exc}). Use "
                       f"build_diagram if one of its kinds fits, or make the "
                       f"point in prose."),
            )
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))

        block = {"type": "diagram",
                 "diagram": {"format": "svg", "svg": svg,
                             "source": source.strip(),
                             "caption": str(caption or "").strip()}}
        _bind(block, slot)
        logger.info("mermaid_figure_built", slide_index=slide_index,
                    chars=len(svg))
        return ToolResult(
            success=True,
            data={"slideIndex": slide_index, "blocks": [block],
                  "summary": f"A graph for slide {slide_index + 1}."},
            duration_ms=int((time.time() - start) * 1000),
        )


def _palette_of(design_system) -> dict[str, str]:
    entries = ((design_system or {}).get("palette") or {}).get("colors") or []
    return {
        str(entry.get("name")): str(entry.get("value"))
        for entry in entries
        if isinstance(entry, dict) and entry.get("name") and entry.get("value")
    }
