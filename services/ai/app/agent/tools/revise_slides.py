"""
Tool: revise_slides

Sends named slides of a finished deck back to drafting with the user's own
words as the finding.

`edit_deck_with_code` changes a rendered file; a render rebuilds the file from
the slides. A request about what a page says or shows - a different chart, a
shorter point, another figure - is a change to the slides, so it belongs to
drafting, which knows the design, the exhibits already attached and the
legibility floor. Handing it to a script would produce a page the design
engine never saw and the next render would discard.
"""

from __future__ import annotations

from app.agent.registry import ToolParameter, ToolRegistry, ToolResult
from app.agent.tools._json_extract import coerce_json_arg


class ReviseSlidesTool:
    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="revise_slides",
            description=(
                "Send slides of the finished deck back to be redrafted, with "
                "the user's request as the instruction. Use it when the "
                "request is about what a slide says or shows (its copy, a "
                "chart, a figure, a visual), not about where something sits "
                "or a deck-wide style, which edit_deck_with_code does."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="slide_indices", type="array",
                    description="0-based indices of the slides to redraft.",
                ),
                ToolParameter(
                    name="instruction", type="string",
                    description=(
                        "What to change, in the user's terms and with every "
                        "specific they gave (the figure, the chart type, the "
                        "wording)."
                    ),
                ),
            ],
        )

    @classmethod
    async def run(cls, slide_indices=None, instruction: str = "") -> ToolResult:
        raw = coerce_json_arg(slide_indices, None)
        indices = sorted({int(i) for i in (raw if isinstance(raw, list) else [])
                          if str(i).lstrip("-").isdigit() and int(i) >= 0})
        text = str(instruction or "").strip()
        if not indices or not text:
            return ToolResult(
                success=False,
                error="Name the slides to revise and say what to change.")
        return ToolResult(success=True, data={
            "slideIndices": indices, "instruction": text,
            "summary": ("Slides " + ", ".join(str(i + 1) for i in indices)
                        + " go back to drafting with that instruction."),
        })
