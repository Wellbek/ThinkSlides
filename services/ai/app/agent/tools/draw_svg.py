"""
Tool: draw_svg

A figure the typed vocabulary cannot express, drawn rather than chosen from a
list. `build_diagram` has eight kinds and they are on-brand by construction;
this is what a page reaches for when none of the eight is the shape of the
idea - a state machine, a bespoke quadrant, an annotated schematic.

It is the most expressive of the figure tools and the one with the fewest
guard-rails, so the guard-rails it has are worth stating.

**A declared viewBox, and the figure is fitted into whatever slot it lands
in.** The model does not know where on the page its figure will go, and should
not: the pack decides that. So it draws in its own coordinates and the
importer scales the result without distorting it.

**The pack's palette by role, not by hex.** A figure is asked for in the names
of the deck's own colours, and anything else it uses is mapped to the nearest
role on import. A model choosing its own hex values produces a deck of
figures, each in a slightly different blue.

**Say when not to use it.** The most expressive tool becomes the default
unless its description says otherwise, and a deck whose every figure is
hand-drawn has no house style left.
"""

from __future__ import annotations

import time

from app.agent.registry import ToolParameter, ToolRegistry, ToolResult
from app.agent.tools._binding import bind as _bind
from app.agent.tools._binding import slot_param as _slot_param
from app.observability.logging import get_logger

logger = get_logger(__name__)

# Big enough for a real figure, small enough that a model cannot spend a whole
# response on one. A diagram needing more than this is a diagram that should
# have been several.
MAX_SVG_CHARS = 12000

# Colour roles a figure may name. Anything else it writes is mapped to the
# nearest of these on import.
PALETTE_ROLES = ("primary", "secondary", "accent", "gray", "background", "text")

WHEN_NOT_TO = (
    "Do NOT use this when build_diagram already has the shape - a process "
    "flow, a 2x2 matrix, a funnel, a timeline, a pyramid, a value chain, a "
    "Venn or an org chart are all better drawn there, because those are "
    "on-brand by construction and this is not. Do NOT use it for a quantity: "
    "a trend, a comparison or a breakdown is build_chart. Do NOT use it to "
    "draw a chart by hand - a hand-drawn axis with invented tick values is "
    "the worst thing this system can emit. Reach for it only when the shape "
    "of the idea is genuinely not one of those."
)


class DrawSvgTool:
    """Let the model author a figure, inside the pack's constraints."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="draw_svg",
            description=(
                "Draw a bespoke figure as SVG, for a structure the typed "
                "diagram vocabulary cannot express - a state machine, an "
                "annotated schematic, a bespoke quadrant. It is imported as "
                "native PowerPoint shapes, so every node stays selectable and "
                "every label stays editable text. " + WHEN_NOT_TO
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="slide_index", type="integer",
                    description="0-based index of the slide this figure belongs to",
                ),
                ToolParameter(
                    name="svg", type="string",
                    description=(
                        "A complete <svg> element with a viewBox. Draw in the "
                        "viewBox's own coordinates - the figure is scaled to "
                        "whatever slot the page gives it, so do not try to "
                        "guess the size it will be. Use rect, circle, "
                        "ellipse, line, polyline, polygon, path and text. "
                        "Colour it with the deck's own palette by naming a "
                        "role: " + ", ".join(PALETTE_ROLES) + ". A colour "
                        "outside the palette is mapped to the nearest role. "
                        "No script, no foreignObject, no external images or "
                        "fonts - they are stripped. Keep every label a word "
                        "or two: they are set in the deck's type at the "
                        "deck's sizes."
                    ),
                ),
                ToolParameter(
                    name="caption", type="string", required=False,
                    description="One line saying what the figure shows.",
                ),
                _slot_param("'schematic' or 'statemachine'"),
            ],
        )

    @classmethod
    async def run(cls, slide_index: int, svg: str, caption: str = "",
                  slot: str = "") -> ToolResult:
        start = time.time()
        source = str(svg or "").strip()
        if not source:
            return ToolResult(success=False, error="No SVG was provided.")
        if len(source) > MAX_SVG_CHARS:
            return ToolResult(
                success=False,
                error=(f"That figure is {len(source)} characters and the limit "
                       f"is {MAX_SVG_CHARS}. A diagram needing more than that "
                       f"is a diagram that should have been several - split "
                       f"it across slides, or use build_diagram if one of its "
                       f"eight kinds fits."),
            )

        # Refused here rather than at render, so the model gets to try again
        # while it still has the context to. A figure that fails silently at
        # render is a hole in a page nobody can explain.
        from app.renderer.layout import PositionConfig
        from app.renderer.svg.importer import UnsupportedSVG, to_primitives

        probe = PositionConfig(left=0.0, top=0.0, width=6.0, height=4.0)
        try:
            marks = to_primitives(source, probe)
        except UnsupportedSVG as exc:
            return ToolResult(
                success=False,
                error=f"That SVG cannot be imported: {exc}",
            )
        if not marks:
            return ToolResult(
                success=False,
                error=("Nothing in that SVG could be drawn. Every element was "
                       "either empty, unsupported, or one of the kinds that "
                       "are stripped for safety."),
            )

        block = {
            "type": "diagram",
            "diagram": {"format": "svg", "svg": source,
                        "caption": str(caption or "").strip()},
        }
        _bind(block, slot)
        shapes = sum(1 for mark in marks if mark.kind == "path")
        labels = sum(1 for mark in marks if mark.kind == "text")
        logger.info("svg_figure_drawn", slide_index=slide_index,
                    shapes=shapes, labels=labels, chars=len(source))
        return ToolResult(
            success=True,
            data={"slideIndex": slide_index, "blocks": [block],
                  "shapes": shapes, "labels": labels,
                  "summary": (f"A drawn figure for slide {slide_index + 1}: "
                              f"{shapes} shape(s), {labels} label(s).")},
            duration_ms=int((time.time() - start) * 1000),
        )
