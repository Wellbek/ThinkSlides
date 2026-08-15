"""
Tool: propose_design_directions

Three genuinely distinct design systems for the current brief, rendered as
specimen slides for the user to choose between.

The agent used to ask about visual style in words, and the user picked by
reading "professional", "bold", "monochrome" - five hardcoded palettes that
rendered the identical layouts, the identical type scale and the identical
accent rule. There was no design choice being made.

The model authors all three outright rather than picking from presets, on four
axes: where the dark tone goes, the type pairing, the structural motif and the
density posture. It authors a *spec* on those axes and the code compiles the
bundle - a model asked for a whole DesignSystemBundle has to invent layout
catalogues in EMU, and it will, badly.

Because nothing enforces a floor once a model is authoring outright, this
rejects its own bad output: a set that differs only in hue is re-asked, and a
direction whose text cannot be read on its ground is dropped before anything
is rendered.

**Conditioned on what the user already said.** By the time directions are
proposed the consultation has settled things, and every one of those answers
constrains all three. A run that answered "green" gets three green directions;
what they offer a choice between is ground, type and density - the things
still genuinely open. A coral direction here would be the tool ignoring an
answer it already has.
"""

from __future__ import annotations

import json
import re
import time

from app import directions as direction_model
from app.agent.registry import ToolParameter, ToolRegistry, ToolResult
from app.agent.run_context import get_chat_provider
from app.agent.tools._json_extract import extract_json_object
from app.llm.provider import get_llm_provider
from app.observability.logging import get_logger
from app.renderer import specimen

logger = get_logger(__name__)

QUESTION = "Which design direction should the deck follow?"
# One retry with the problems fed back. A second retry has never yet fixed
# what the first did not, and this sits inside a pause the user is watching.
MAX_ATTEMPTS = 2

_HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}")


class ProposeDesignDirectionsTool:
    """Author three design directions and render them for the user to pick."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="propose_design_directions",
            description=(
                "Offer the user a choice between three rendered design directions. "
                "Call this ONCE, after the text consultation is closed, as the last "
                "interruption before building - never as one question among several. "
                "Each direction is a whole design system: where the dark tone goes, "
                "the type pairing, the structural motif and the density. The workflow "
                "pauses until the user picks one."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="brief",
                    type="string",
                    description=(
                        "What the deck is about and who it is for, in a sentence or "
                        "two, so the directions suit it."
                    ),
                ),
                ToolParameter(
                    name="answers",
                    type="string",
                    required=False,
                    description=(
                        "What the user has already told you, as JSON or plain text - "
                        "every answer from the earlier consultation. These constrain "
                        "all three directions: a user who asked for green gets three "
                        "green directions."
                    ),
                ),
                ToolParameter(
                    name="brand_color",
                    type="string",
                    required=False,
                    description=(
                        "The brand or requested colour as #RRGGBB, when one is known. "
                        "All three directions are built from it."
                    ),
                ),
                ToolParameter(
                    name="session_id",
                    type="string",
                    required=False,
                    description="Session the specimen previews belong to.",
                ),
            ],
        )

    @classmethod
    async def run(
        cls,
        brief: str = "",
        answers: str = "",
        brand_color: str = "",
        session_id: str = "",
        design_system: dict | None = None,
    ) -> ToolResult:
        start = time.time()
        try:
            brand_hex = _brand_hex(brand_color, answers)
            uploaded = design_system if _is_real_template(design_system) else None

            proposed, problems = await cls._author(brief, answers, brand_hex,
                                                   uploaded)
            if not proposed:
                # The floor, not a failure: a user still gets a choice between
                # three coherent directions, and the run still proceeds.
                logger.warning("design_directions_fell_back", problems=problems[:3])
                proposed = direction_model.default_directions(brand_hex)

            if uploaded is not None:
                proposed = [direction_model.template_direction(uploaded)] + \
                    proposed[:direction_model.DIRECTION_COUNT - 1]

            rendered = await specimen.render_directions(proposed,
                                                        session_id or "preview")
            for direction in proposed:
                direction["previewUrls"] = rendered.get(direction["id"], [])

            duration_ms = int((time.time() - start) * 1000)
            logger.info(
                "design_directions_proposed",
                directions=[d["id"] for d in proposed],
                previews=sum(len(d["previewUrls"]) for d in proposed),
                brand_color=brand_hex,
                from_template=uploaded is not None,
                duration_ms=duration_ms,
            )
            return ToolResult(
                success=True,
                data={
                    "type": "design_directions",
                    "directions": proposed,
                    # The same shape ask_user_question returns, so the loop
                    # pauses on it the way it already knows how to.
                    "question": QUESTION,
                    "questions": [{
                        "question": QUESTION,
                        "options": [
                            {
                                "kind": "preview",
                                "id": d["id"],
                                "label": d["name"],
                                "description": d["rationale"],
                                "previewUrls": d["previewUrls"],
                            }
                            for d in proposed
                        ],
                    }],
                    "options": [d["name"] for d in proposed],
                    "context": _context(brand_hex, uploaded is not None),
                },
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.error("design_directions_failed", error=str(e))
            return ToolResult(success=False, error=str(e),
                              duration_ms=int((time.time() - start) * 1000))

    @classmethod
    async def _author(cls, brief: str, answers: str, brand_hex: str | None,
                      uploaded: dict | None) -> tuple[list[dict], list[str]]:
        """
        Ask the model for three directions, and hold the answer to the floor.

        One retry, with the problems stated back. Telling a model *what* was
        wrong with its set is worth far more than asking again and hoping,
        and it is the difference between a floor and a lottery.
        """
        llm = get_chat_provider() or get_llm_provider()
        wanted = (direction_model.DIRECTION_COUNT - 1) if uploaded else \
            direction_model.DIRECTION_COUNT
        problems: list[str] = []

        for attempt in range(MAX_ATTEMPTS):
            prompt = _prompt(brief, answers, brand_hex, uploaded, wanted, problems)
            try:
                response = await llm.chat(
                    messages=[{"role": "user", "content": prompt}], tools=None
                )
            except Exception as e:
                # A provider that falls over must not fail the run: the user
                # still gets a choice between three coherent directions, and
                # the deck still gets built.
                logger.warning("design_directions_llm_failed", error=str(e))
                return [], [f"the model could not be reached ({e})"]
            payload = extract_json_object(
                (response.get("message") or {}).get("content", "{}")
            ) or {}
            raw = payload.get("directions")
            if not isinstance(raw, list):
                problems = ["the response carried no 'directions' array"]
                continue

            specs = [direction_model.DirectionSpec.from_dict(entry, index)
                     for index, entry in enumerate(raw) if isinstance(entry, dict)]
            compiled, problems = direction_model.validate(
                specs[:wanted], brand_hex, uploaded
            )
            # With a template in play the set is smaller by one, so "only two
            # of three survived" is the expected count rather than a failure.
            fatal = [p for p in problems if "survived" not in p] if uploaded else problems
            if compiled and len(compiled) >= wanted and not fatal:
                return compiled, []
            logger.info("design_directions_rejected", attempt=attempt + 1,
                        problems=problems[:3])
        return [], problems


def _prompt(brief: str, answers: str, brand_hex: str | None,
            uploaded: dict | None, wanted: int, problems: list[str]) -> str:
    settled = str(answers or "").strip()
    lines = [
        f"Author {wanted} genuinely different design directions for this deck.",
        "",
        f"The deck: {brief.strip() or 'a business presentation'}",
    ]
    if settled:
        lines += ["", "What the user has already told you (these are settled - "
                  "every direction must respect them):", settled[:1500]]
    if brand_hex:
        lines += ["", f"The colour is settled at {brand_hex}. All {wanted} "
                  "directions are built from it: do not propose another hue. "
                  "What you are offering a choice between is ground, type and "
                  "density."]
    if uploaded is not None:
        lines += ["", "The user uploaded a template, which is offered to them "
                  "unchanged as a first direction. Yours are principled "
                  "variations on it - the same palette set more loosely, or "
                  "the same structure with the ground inverted."]

    lines += [
        "",
        "Return JSON only, in this exact shape:",
        json.dumps({"directions": [{
            "id": "dark-analyst",
            "name": "Dark analyst",
            "rationale": "One line on why this direction, in plain words.",
            "ground": "light|dark|rail|split",
            "displayFace": "a real typeface name",
            "bodyFace": "a real typeface name",
            "displaySizePt": 30,
            "bodySizePt": 15,
            "density": "airy|balanced|dense",
            "motif": "rule|band|none",
        }]}, indent=2),
        "",
        "Rules:",
        "- The directions must differ on at least two of: ground, type "
        "pairing, motif, density. Three directions differing only in hue is a "
        "failed set and will be rejected.",
        "- ground is where the dark tone goes: 'light' is white throughout, "
        "'dark' sets content pages on the dark tone, 'rail' is white with a "
        "dark rail, 'split' is a tinted canvas.",
        "- A serif display over a sans body is a different deck from sans over "
        "sans. Use real typefaces (Georgia, Arial, Helvetica, Times New "
        "Roman, Verdana), never invented ones.",
        "- density sets how much the planner puts on a slide, not only the "
        "look: 'dense' is an analyst deck, 'airy' is an executive one.",
        "- Name each direction for what it looks like - 'Dark analyst', "
        "'Editorial serif', 'Clean minimal'. Never lettered A/B/C.",
        "- The rationale is one line a non-designer can act on.",
    ]
    if problems:
        lines += ["", "Your previous attempt was rejected:",
                  *(f"- {problem}" for problem in problems[:4]),
                  "Fix those specifically."]
    return "\n".join(lines)


def _context(brand_hex: str | None, from_template: bool) -> str:
    parts = ["Three directions, rendered on the same three slides so the "
             "comparison is of design rather than of copy."]
    if brand_hex:
        parts.append(f"All of them are built from {brand_hex}, as you asked - "
                     "they differ on ground, type and density.")
    if from_template:
        parts.append("The first is your uploaded template, unchanged.")
    return " ".join(parts)


def _brand_hex(brand_color: str, answers: str) -> str | None:
    """
    The colour this run has already settled on, if it has.

    Read from the explicit argument first and from the answers otherwise,
    because the hex the user picked is usually sitting in the text of the
    answer they gave rather than in a field.
    """
    for source in (brand_color, answers):
        found = _HEX_RE.search(str(source or ""))
        if found:
            return found.group(0).upper()
    return None


def _is_real_template(design_system: dict | None) -> bool:
    """Whether this run has an uploaded template rather than the built-in."""
    if not isinstance(design_system, dict) or not design_system:
        return False
    return str(design_system.get("sourceType") or "default") != "default"
