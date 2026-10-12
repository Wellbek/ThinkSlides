"""
Tool: compute_visual_with_code

The block-returning half of issue #283's decision. `edit_deck_with_code`
opens a rendered `.pptx`, edits it, and saves it - there is no such file
while a slide is being drafted, so during drafting it is a tool that cannot
run. This is what fills that gap: the script computes something and draws
one figure, and the figure comes back as a content block through the same
path `build_chart` and `draw_svg` already use (`_attach_visual`), rather than
needing a return path of its own.

**Deliberately narrower than `edit_deck_with_code`.** It draws a figure. It
does not touch a `Presentation`, does not lay out a page, and does not
receive one - `SLOT_TOOLS` already lets an authored language decide where
things go (see #281's arrangement variants, which cover most of what would
otherwise be a reason to reach for a script mid-draft), and a script handed
page geometry here would be the model emitting coordinates through the one
door #277 closed on every other tool.

**The same three gates, unchanged, and the same one this adds nothing to.**
`sandbox.gate` (what the script may say), `sandbox.runner` (what the process
can reach) and `sandbox.grounding` (whether a drawn figure declares where its
numbers came from) are shared verbatim with `edit_deck_with_code` - a script
is a script, whichever half of #283 calls it. What differs is the fourth
gate: there is no deck to adopt, so what is checked instead is that the
output is a real, reasonably-sized PNG.

**Offered only when `settings.code_amendment_enabled` is set** (see
`AgentLoop._withheld_tools`), exactly like the file-editing tool - the
container hardening both depend on is an operator's decision, not a default.
"""

from __future__ import annotations

import base64
import time
from typing import Optional

from app.agent.registry import ToolParameter, ToolRegistry, ToolResult
from app.agent.sandbox import gate, grounding, runner
from app.agent.tools._binding import bind as _bind
from app.agent.tools._binding import region_param as _region_param
from app.agent.tools._binding import slot_param as _slot_param
from app.observability.logging import get_logger

logger = get_logger(__name__)

# The file the script is told to save its figure as, read back from the work
# directory the same way `edit_deck_with_code` reads back `deck.pptx` -
# `sandbox.runner.run` already generalises to "whichever named files this
# call wants back", so nothing there needed to change for a mode with no deck.
OUTPUT_NAME = "figure.png"

# A rendered chart from the ordinary path routinely reaches a few hundred KB
# at deck resolution; this is generous for a real figure and mean for
# anything that is not one.
MAX_IMAGE_BYTES = 6 * 1024 * 1024

WHEN_NOT_TO = (
    "Do NOT use this when a build_* tool already has the shape. build_chart "
    "is the answer to a quantity, build_diagram to a structure, build_table "
    "to a grid and draw_svg to a bespoke figure - all four are on-brand by "
    "construction, validated against the deck's own design, and none of them "
    "can go wrong the way a script can. Reach for this only when the figure "
    "needs a real computation over the material first, or a chart shape none "
    "of the build_* tools offer. Writing an ordinary bar chart by hand here, "
    "when build_chart would have drawn it, is strictly worse in every "
    "respect: build_chart is checked against the deck's own design and this "
    "is not."
)


class ComputeVisualWithCodeTool:
    """Agent-written Python that computes and draws one figure, sandboxed
    the same way `edit_deck_with_code` is."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="compute_visual_with_code",
            description=(
                "Write a short Python script that computes something and "
                "draws it as one figure, for a visual none of the build_* "
                "tools can express. The script is checked before it runs and "
                "the image it produces is checked before it is attached. "
                + WHEN_NOT_TO
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="slide_index", type="integer",
                    description="0-based index of the slide this figure belongs to.",
                ),
                ToolParameter(
                    name="code", type="string",
                    description=(
                        "A Python script. Compute what the figure needs and "
                        f"save it as {OUTPUT_NAME!r} in the directory the "
                        "script runs in - matplotlib's savefig(...) or PIL's "
                        "Image.save(...), at roughly 10 x 6 inches. There is "
                        "no deck here to open: this produces one figure, not "
                        "an edit. You may import math, statistics, json, csv, "
                        "re, datetime, decimal, random, textwrap, itertools, "
                        "matplotlib, numpy and PIL. You may not import "
                        "anything else, and in particular not os, sys, "
                        "subprocess, socket or this service's own modules. "
                        "No eval, no exec, no __import__. There is no network "
                        "and no filesystem outside your own directory."
                    ),
                ),
                ToolParameter(
                    name="intent", type="string",
                    description=(
                        "One line saying what this figure shows, in the terms "
                        "a reader of the slide would use - 'the four regions' "
                        "growth rates as a slope from last year to this one', "
                        "not 'a chart'."
                    ),
                ),
                ToolParameter(
                    name="inputs", type="string",
                    description=(
                        "Where every number this figure shows came from: a "
                        "document, a search result, or the conversation. "
                        "Required whenever the script draws a figure - a "
                        "chart drawn by code is a stronger claim than a "
                        "sentence, and a script with nothing declared here is "
                        "refused. Write 'none' only when the script asserts "
                        "no numbers at all."
                    ),
                ),
                _region_param(),
                _slot_param("'bridge' or 'mix'"),
            ],
        )

    @classmethod
    async def run(cls, slide_index: int, code: str, intent: str = "",
                  inputs: str = "", region_id: str = "",
                  slot: str = "") -> ToolResult:
        start = time.time()
        source = str(code or "")

        # Gate one: what the script can say. Identical to edit_deck_with_code
        # - a script is a script, whichever half of #283 called it.
        problem = gate.check(source)
        if problem:
            logger.info("visual_code_refused", reason=problem[:200])
            return ToolResult(
                success=False,
                error=(f"{problem}\n\nRewrite it within those limits, or use "
                       "a build_* tool if one has the shape."),
                data={"outcome": "refused", "source": source,
                      "reason": problem},
                duration_ms=int((time.time() - start) * 1000),
            )

        ungrounded = grounding.problem(source, inputs)
        if ungrounded:
            logger.info("visual_code_ungrounded")
            return ToolResult(
                success=False, error=ungrounded,
                data={"outcome": "refused", "source": source,
                      "reason": ungrounded},
                duration_ms=int((time.time() - start) * 1000),
            )

        # Gate two: what the process can reach. No deck is handed in - this
        # mode produces a figure, not an edit - so `runner.run` never writes
        # `deck.pptx` into the work directory at all.
        result = runner.run(source, deck=None, wants=(OUTPUT_NAME,))
        if not result.ok:
            logger.info("visual_code_failed")
            return ToolResult(
                success=False,
                error=(f"{result.failure}\n\nFix the script and call this "
                       "again - you have the error and stdout."),
                data={"outcome": "failed", "source": source,
                      "reason": result.failure, "stdout": result.stdout},
                duration_ms=int((time.time() - start) * 1000),
            )

        # Gate three: what the artefact is. There is no deck to adopt here,
        # so what stands in for `sandbox.adopt` is the one thing an image
        # output can fail to be: a real, reasonably-sized PNG.
        image_bytes = result.outputs.get(OUTPUT_NAME)
        rejected = _image_problem(image_bytes)
        if rejected:
            logger.warning("visual_code_output_rejected", reason=rejected)
            return ToolResult(
                success=False,
                error=(f"{rejected}\n\nSave the figure as {OUTPUT_NAME!r} "
                       "and call this again."),
                data={"outcome": "failed", "source": source,
                      "reason": rejected, "stdout": result.stdout},
                duration_ms=int((time.time() - start) * 1000),
            )

        block = {
            "type": "image",
            "image": {
                "source": "base64",
                "data": base64.b64encode(image_bytes).decode("ascii"),
            },
            "altText": intent.strip()[:200] or "a computed figure",
        }
        logger.info("visual_code_drawn", slide_index=slide_index,
                    bytes=len(image_bytes),
                    network_namespace=result.networked)
        return ToolResult(
            success=True,
            # "content", singular - draw_svg's own docstring records what
            # happens when a figure tool returns "blocks" instead: the tool
            # reports success, the model moves on, and the figure reaches no
            # slide at all.
            data={
                "outcome": "drawn",
                "slideIndex": slide_index,
                "content": _bind(block, region_id, slot),
                "source": source,
                "stdout": result.stdout,
                "summary": (
                    f"A computed figure for slide {slide_index + 1}: "
                    f"{intent.strip() or 'the deck was edited'}."
                ),
            },
            duration_ms=int((time.time() - start) * 1000),
        )


def _image_problem(data: Optional[bytes]) -> Optional[str]:
    """Why this is not a usable image, or None when it is."""
    if not data:
        return "No image was produced."
    if len(data) > MAX_IMAGE_BYTES:
        return (f"That image is {len(data)} bytes and the limit is "
                f"{MAX_IMAGE_BYTES}. A figure this large is not one slide's "
                "worth of drawing.")
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ("The output is not a PNG. Save it with matplotlib's "
                "savefig(..., format='png') or PIL's Image.save(..., 'PNG').")
    return None
