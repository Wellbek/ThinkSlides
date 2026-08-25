"""
Tool: generate_image

An illustration, generated - but only of something the deck actually
established, and never of anything that could be mistaken for evidence.

Three constraints, and each of them is a function signature rather than a
sentence in a prompt.

**Registered only when there is a provider.** Absent an ``image`` connection the
tool does not exist, and the drafting prompt keeps saying image generation does
not exist - which is then true. A tool that exists but always fails teaches a
model to keep trying it.

**Grounding is required.** The image illustrates something the slide already
says: its key points, a research finding, or a source document. A call with none
is a tool error, not a warning. The model states *what the image is of*; this
tool states *how it should look*, composing the provider prompt from that
grounding plus the deck's own design direction. Same division as
``compose_slide_regions``: intent from the model, execution from code.

**Nothing that reads as data.** A diffusion model asked for "a chart showing
revenue growth" returns a beautiful, entirely fictional chart, indistinguishable
from a real one on the page. That is the single worst thing this system could
emit, in an engine whose whole discipline is that nothing may look sourced when
it is not - the empty-cell table check refuses a table of dashes for the same
reason, and projected chart values are hatched for the same reason. So a subject
describing a chart, a figure or chart furniture is refused here, the provider is
told the same thing on every request, and the ground-truth pass checks the
returned pixels (#179).

Every generated image carries ``provenance: "generated"`` and its grounding
through to the rendered slide, and renders with a visible credit line.
"""

from __future__ import annotations

import base64
import re
import time

from app.agent import run_context
from app.agent.registry import ToolParameter, ToolRegistry, ToolResult
from app.agent.tools._binding import bind as _bind
from app.agent.tools._binding import region_param as _region_param
from app.agent.tools._binding import slot_param as _slot_param
from app.agent.tools._json_extract import coerce_json_arg
from app.connections import image_provider
from app.observability.logging import get_logger

logger = get_logger(__name__)

RATIOS = ("landscape", "portrait", "square")

MAX_SUBJECT_CHARS = 240
MIN_SUBJECT_CHARS = 12
MAX_GROUNDING_REFS = 6

# The credit line every generated image renders with. Short, and it says the
# thing that matters: this was drawn, not photographed or measured.
CREDIT = "Illustration generated for this deck"

# Words that describe evidence rather than illustration. A subject containing
# one is refused outright - not softened, not warned about - because the failure
# it prevents is a fabricated chart that nothing downstream can tell from a real
# one.
DATA_WORDS = (
    "chart", "graph", "plot", "axis", "axes", "gridline", "grid line",
    "tick mark", "legend", "bar chart", "pie chart", "line graph", "histogram",
    "scatter", "dashboard", "infographic", "table of", "spreadsheet",
    "data visualisation", "data visualization", "trend line", "x-axis",
    "y-axis", "labelled", "labeled", "annotated", "with labels", "callout",
    "screenshot", "dial", "gauge", "speedometer", "meter showing",
)

# A figure in the subject is a figure the image would be asked to draw.
FIGURE_RE = re.compile(
    r"(\d[\d,.]*\s*(%|percent|per cent|bn|billion|m\b|million|k\b|x\b))"
    r"|([$£€]\s*\d)"
    r"|(\b\d{1,3}(,\d{3})+\b)",
    re.IGNORECASE,
)

# How the deck's chosen design direction becomes a visual instruction. Keyed by
# the direction's ground and motif axes, so an image sits inside the deck's look
# rather than beside it.
GROUND_STYLE = {
    "light": "a light, airy composition on a pale ground",
    "dark": "a deep, low-key composition on a dark ground",
    "duotone": "a restrained duotone composition",
}
MOTIF_STYLE = {
    "geometric": "clean geometric abstraction, flat shapes",
    "organic": "soft organic forms with gentle gradients",
    "editorial": "editorial illustration, confident line and flat colour",
    "photographic": "photographic, natural light, shallow depth of field",
    "minimal": "extreme restraint, one idea, generous empty space",
}
DEFAULT_STYLE = "clean editorial illustration, flat colour, generous empty space"


class GenerateImageTool:
    """A grounded illustration, from the workspace's configured provider."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="generate_image",
            description=(
                "Generate an illustration for a slide, from an image provider "
                "this workspace has configured. Use it for a COVER, a section "
                "divider, or a conceptual idea a reader would grasp faster from "
                "a picture than from prose. It must illustrate something the "
                "deck has already established - you have to say which key "
                "points or sources it comes from - and it must never carry "
                "figures, axes, labels or anything else that reads as a chart: "
                "for a structure use build_diagram, for a quantity use "
                "build_chart. Both of those are better than this whenever they "
                "apply."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="slide_index", type="integer",
                    description="0-based index of the slide this image belongs to",
                ),
                ToolParameter(
                    name="subject", type="string",
                    description=(
                        "What the image is OF, in a phrase. Describe a scene or "
                        "an idea, not a chart: 'a bridge under construction at "
                        "dawn', 'interlocking gears of different sizes'. Do not "
                        "describe how it should look - the deck's design "
                        "direction decides that."
                    ),
                ),
                ToolParameter(
                    name="grounded_in", type="array",
                    items={"type": "string"},
                    description=(
                        "REQUIRED. What this image illustrates, as the slide's "
                        "own key points, research finding ids, or source "
                        "document ids. An image with nothing behind it is "
                        "decoration presented as content, and this tool refuses "
                        "to make one."
                    ),
                ),
                ToolParameter(
                    name="alt_text", type="string", required=False,
                    description="What the image shows, for a reader who cannot see it.",
                ),
                ToolParameter(
                    name="ratio", type="string", required=False,
                    enum=list(RATIOS),
                    description="landscape (default), portrait, or square.",
                ),
                _region_param(),
                _slot_param("'illustration', 'hero'"),
            ],
        )

    @classmethod
    async def run(
        cls,
        slide_index: int,
        subject: str,
        grounded_in,
        alt_text: str = "",
        ratio: str = "landscape",
        region_id: str = "",
        slot: str = "",
    ) -> ToolResult:
        start = time.time()

        connection = run_context.get_image_connection()
        if connection is None:
            # Reachable only if the registry and the run disagree about what is
            # configured. Said plainly rather than as a provider error, because
            # the answer is to use a diagram, not to retry.
            return _fail(
                "No image provider is configured for this workspace, so images "
                "cannot be generated. Use build_diagram for anything structural.",
                start,
            )

        text = str(subject or "").strip()
        if len(text) < MIN_SUBJECT_CHARS:
            return _fail(
                "subject is too short to draw from. Say what the image is of in "
                "a phrase - a scene or an idea, not one word.",
                start,
            )
        if len(text) > MAX_SUBJECT_CHARS:
            return _fail(
                f"subject is {len(text)} characters (max {MAX_SUBJECT_CHARS}). "
                "One idea, in a phrase. A paragraph produces a muddle.",
                start,
            )

        barred = _data_bar(text)
        if barred:
            return _fail(barred, start)

        refs = _refs(grounded_in)
        if not refs:
            return _fail(
                "grounded_in is required and empty. An image has to illustrate "
                "something this deck established - name the key points, "
                "research findings or source documents it comes from. If there "
                "is nothing behind it, the slide does not need it.",
                start,
            )
        if len(refs) > MAX_GROUNDING_REFS:
            refs = refs[:MAX_GROUNDING_REFS]

        prompt = compose_prompt(text, refs, run_context.get_design_system())
        try:
            png = await image_provider.generate(
                connection, prompt,
                ratio=str(ratio or "landscape").strip().lower(),
            )
        except image_provider.ImageError as exc:
            # Reported, not raised: a provider that will not draw must not fail
            # the slide. A missing illustration is a far better outcome than a
            # missing page.
            logger.warning("image_generation_failed", slide_index=slide_index,
                           error=str(exc))
            return _fail(
                f"{exc} The slide can still be built without an illustration, "
                "or with a diagram instead.",
                start,
            )

        if not png:
            return _fail("The image provider returned nothing.", start)

        content = _bind({
            "type": "image",
            "image": {
                "source": "generated",
                "data": base64.b64encode(png).decode("ascii"),
                "altText": str(alt_text or text).strip(),
            },
            # Travels onto the slide and into the rendered file. The refs make
            # it traceable rather than merely flagged.
            "provenance": "generated",
            "groundingRefs": refs,
            "credit": CREDIT,
        }, region_id=region_id, slot=slot or "illustration")

        logger.info("image_generated", slide_index=slide_index,
                    provider=connection.connection_id, bytes=len(png),
                    grounding=len(refs))
        return ToolResult(
            success=True,
            data={
                "slideIndex": slide_index,
                "content": content,
                "summary": f"generated illustration, grounded in {len(refs)} reference(s)",
            },
            duration_ms=int((time.time() - start) * 1000),
        )


def compose_prompt(subject: str, refs: list[str], design_system: dict | None) -> str:
    """The provider prompt, composed here rather than written by the model.

    The model says what the image is of; this says how it should look and what
    it must not contain. Splitting it that way is what makes the grounding
    meaningful - a free-hand prompt could describe anything, and there would be
    no sense in which the picture came from the deck.
    """
    style = _style_of(design_system)
    # The deck's own illustration style, when it is on a pack that measured
    # one. Illustration was decided per slide before this, so two pages of one
    # deck could come back in unrelated treatments.
    from app.agent import run_context as _run_context
    from app.renderer import illustration as _illustration

    pack = _run_context.get_pack()
    if pack is not None:
        house = _illustration.style_for(pack, design_system)
        if house.measured:
            style = house.for_provider()
    context = "; ".join(ref for ref in refs[:3] if ref)
    parts = [subject.rstrip(". ") + "."]
    if context:
        parts.append(f"It illustrates: {context}.")
    parts.append(f"Style: {style}.")
    parts.append(
        "It is a slide illustration: one clear idea, uncluttered, with room "
        "around it."
    )
    return " ".join(parts)


def _style_of(design_system: dict | None) -> str:
    """The deck's chosen direction, as words a provider understands."""
    direction = (design_system or {}).get("direction") or {}
    ground = str(direction.get("ground") or "").strip().lower()
    motif = str(direction.get("motif") or "").strip().lower()
    pieces = [MOTIF_STYLE.get(motif), GROUND_STYLE.get(ground)]
    chosen = [p for p in pieces if p]
    if not chosen:
        return DEFAULT_STYLE

    palette = ((design_system or {}).get("palette") or {}).get("colors") or []
    hexes = [
        str(entry.get("value")) for entry in palette
        if isinstance(entry, dict) and str(entry.get("value") or "").startswith("#")
    ][:3]
    if hexes:
        chosen.append("a palette close to " + ", ".join(hexes))
    return ", ".join(chosen)


def _data_bar(subject: str) -> str:
    """The reason this subject may not be generated, or "" if there is none.

    Absolute, and in the builder rather than in a prompt. Illustration is
    allowed; anything that could be mistaken for evidence is not.
    """
    lowered = subject.lower()
    for word in DATA_WORDS:
        if word in lowered:
            return (
                f"'{word}' describes evidence, not illustration, and a "
                "generated image must never carry anything that reads as data - "
                "a diffusion model asked for a chart returns a beautiful "
                "fictional one that nothing downstream can tell from a real "
                "one. For a quantity use build_chart; for a structure use "
                "build_diagram."
            )
    figure = FIGURE_RE.search(subject)
    if figure:
        return (
            f"subject contains a figure ('{figure.group(0).strip()}'), and a "
            "generated image must not carry numbers. Put the figure in a chart, "
            "a metric row or the slide's copy, where it is measured rather than "
            "drawn."
        )
    return ""


def _refs(value) -> list[str]:
    parsed = coerce_json_arg(value, value)
    if isinstance(parsed, str):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []
    out = []
    for entry in parsed:
        text = str(entry or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _fail(message: str, start: float) -> ToolResult:
    return ToolResult(success=False, error=message,
                      duration_ms=int((time.time() - start) * 1000))
