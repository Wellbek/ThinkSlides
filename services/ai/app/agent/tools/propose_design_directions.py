"""
Tool: propose_design_directions

Three genuinely distinct design systems for the current brief, rendered as
specimen slides for the user to choose between - and, in the same card, the
colour.

The agent used to ask about visual style in words, and the user picked by
reading "professional", "bold", "monochrome" - five hardcoded palettes that
rendered the identical layouts, the identical type scale and the identical
accent rule. There was no design choice being made.

The model authors all three outright rather than picking from presets, on five
axes: where the dark tone goes, the type pairing, the structural motif, the
density posture and the structure the direction leads with. It authors a
*spec* on those axes and the code compiles the bundle - a model asked for a
whole DesignSystemBundle has to invent layout catalogues in EMU, and it will,
badly.

It also authors the **specimen copy**, once, for all three. Holding the words
constant is what makes three previews a comparison of design; holding them
constant *and* fixed at lorem ipsum is what made every run's previews
identical to every other run's.

Because nothing enforces a floor once a model is authoring outright, this
rejects its own bad output: a set that differs only in hue is re-asked, a set
that shapes its points identically everywhere is re-asked, and a direction
whose text cannot be read on its ground is dropped before anything is
rendered.

**Colour rides in the same pause.** It used to be asked in the consultation
and then was not asked at all, which left every deck in the house navy. One
card now carries both questions: which direction, and which colour. The
answers necessarily arrive together, so the specimens are honest about
ground, type, motif and structure, and a colour that differs from the one
they were drawn in is applied to the chosen direction afterwards.

**Conditioned on what the user already said.** By the time directions are
proposed the consultation has settled things, and every one of those answers
constrains all three.
"""

from __future__ import annotations

import json
import re
import time

from app import colors, directions as direction_model
from app.agent.registry import ToolParameter, ToolRegistry, ToolResult
from app.agent.run_context import require_chat_provider
from app.agent.tools._json_extract import extract_json_object
from app.agent.tools._today import recent_years, today_line
from app.observability.logging import get_logger
from app.renderer import specimen

logger = get_logger(__name__)

QUESTION = "Which design direction should the deck follow?"
COLOUR_QUESTION = "And the colour?"
# The answer that changes nothing - first in the list, because the previews
# are already drawn in the colours the directions proposed for themselves.
COLOUR_AS_PROPOSED = "As proposed - the colours in the previews"
# One retry with the problems fed back. A second retry has never yet fixed
# what the first did not, and this sits inside a pause the user is watching.
MAX_ATTEMPTS = 2
# More than this and the colour question stops being a choice and becomes a
# swatch book.
MAX_COLOUR_OPTIONS = 3

_HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}")


class ProposeDesignDirectionsTool:
    """Author three design directions and render them for the user to pick."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="propose_design_directions",
            description=(
                "Offer the user a choice between three rendered design directions, "
                "and the deck's colour, in one card. Call this ONCE, after the text "
                "consultation is closed, as the last interruption before building - "
                "never as one question among several. Each direction is a whole design "
                "system: where the dark tone goes, the type pairing, the structural "
                "motif, the density and the shape its points take. The workflow pauses "
                "until the user answers."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="brief",
                    type="string",
                    description=(
                        "What the deck is about and who it is for, in a sentence or "
                        "two, so the directions and the specimen copy suit it."
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
                        "The brand or requested colour as #RRGGBB, when the user has "
                        "already named one. All three directions are built from it and "
                        "the colour question is not asked."
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

            proposed, content, palettes, problems = await cls._author(
                brief, answers, brand_hex, uploaded
            )
            fell_back = not proposed
            if fell_back:
                # The floor, not a failure: a user still gets a choice between
                # three coherent directions, and the run still proceeds.
                logger.warning("design_directions_fell_back", problems=problems[:3])
                proposed = direction_model.default_directions(brand_hex)
                palettes = []

            if uploaded is not None:
                proposed = [direction_model.template_direction(uploaded)] + \
                    proposed[:direction_model.DIRECTION_COUNT - 1]

            rendered = await specimen.render_directions(
                proposed, session_id or "preview", content=content
            )
            for direction in proposed:
                direction["previewUrls"] = rendered.get(direction["id"], [])

            # A settled colour has already been applied to all three, and an
            # uploaded template's palette is the user's own - neither is a
            # question worth a slot on the card.
            asks_colour = not brand_hex and uploaded is None and bool(palettes)
            questions = [{
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
            }]
            if asks_colour:
                questions.append({
                    "question": COLOUR_QUESTION,
                    "options": [COLOUR_AS_PROPOSED] + [
                        f"{name} ({hex_value})" for name, hex_value in palettes
                    ],
                })

            duration_ms = int((time.time() - start) * 1000)
            logger.info(
                "design_directions_proposed",
                directions=[d["id"] for d in proposed],
                structures=[(d.get("designSystem") or {}).get("structure")
                            for d in proposed],
                previews=sum(len(d["previewUrls"]) for d in proposed),
                brand_color=brand_hex,
                asks_colour=asks_colour,
                authored_content=content is not None,
                from_template=uploaded is not None,
                fell_back=fell_back,
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
                    "questions": questions,
                    "options": [d["name"] for d in proposed],
                    "context": _context(brand_hex, uploaded is not None, asks_colour),
                },
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.error("design_directions_failed", error=str(e))
            return ToolResult(success=False, error=str(e),
                              duration_ms=int((time.time() - start) * 1000))

    @classmethod
    async def _author(
        cls, brief: str, answers: str, brand_hex: str | None, uploaded: dict | None,
    ) -> tuple[list[dict], specimen.SpecimenContent | None,
               list[tuple[str, str]], list[str]]:
        """
        Ask the model for the directions, the specimen copy and the palettes.

        One call for all three, because they are one decision: the copy has to
        be the same in every direction, and the palettes have to suit the same
        topic the directions were drawn for.

        One retry, with the problems stated back. Telling a model *what* was
        wrong with its set is worth far more than asking again and hoping, and
        it is the difference between a floor and a lottery.
        """
        llm = require_chat_provider()
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
                return [], None, [], [f"the model could not be reached ({e})"]
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
                # None, rather than the default set, when the model wrote no
                # copy: the specimen deck falls back to it either way, and
                # "authored" in the log should mean it.
                written = payload.get("content")
                return (compiled,
                        specimen.SpecimenContent.from_dict(written)
                        if isinstance(written, dict) else None,
                        _palettes(payload.get("colorOptions")),
                        [])
            logger.info("design_directions_rejected", attempt=attempt + 1,
                        problems=problems[:3])
        return [], None, [], problems


def _prompt(brief: str, answers: str, brand_hex: str | None,
            uploaded: dict | None, wanted: int, problems: list[str]) -> str:
    settled = str(answers or "").strip()
    lines = [
        today_line(),
        "",
        f"Author {wanted} genuinely different design directions for this deck, "
        "and the specimen copy all of them will be previewed with.",
        "",
        f"The deck: {brief.strip() or 'a business presentation'}",
    ]
    if settled:
        lines += ["", "What the user has already told you (these are settled - "
                  "every direction must respect them):", settled[:1500]]
    if brand_hex:
        lines += ["", f"The colour is settled at {brand_hex}. All {wanted} "
                  "directions are built from it: do not propose another hue, "
                  "and leave colorOptions empty. What you are offering a "
                  "choice between is ground, type, density and structure."]
    if uploaded is not None:
        lines += ["", "The user uploaded a template, which is offered to them "
                  "unchanged as a first direction. Yours are principled "
                  "variations on it - the same palette set more loosely, or "
                  "the same structure with the ground inverted."]

    lines += [
        "",
        "Return JSON only, in this exact shape:",
        json.dumps({
            "content": {
                "title": "the specimen cover's title, about THIS deck's subject",
                "subtitle": "one line under it",
                "headline": "a claim the deck could make, as a sentence",
                "points": [{
                    "label": "two or three words",
                    "figure": "a short figure, e.g. '24%', '$1.8m', '6 weeks'",
                    "prose": "what the figure means, as a clause of 8-12 words",
                    "icon": "the concept an icon should show, e.g. 'growth'",
                }],
                "evidenceHeadline": "a claim the chart below supports",
                "takeaway": "one sentence stating what the figures show",
                "chart": {
                    "title": "what the chart measures, with its unit",
                    "categories": recent_years(5),
                    "series": [{"name": "Series", "values": [18, 27, 36, 45, 54]}],
                },
            },
            "colorOptions": [{"name": "Forest green", "hex": "#1B4332"}],
            "directions": [{
                "id": "dark-analyst",
                "name": "Dark analyst",
                "rationale": "One line on why this direction, in plain words.",
                "ground": "light|dark|rail|split",
                "structure": "list|stats|cards|bands",
                "chartFamily": "labelled|gridded|minimal (optional - follows density if omitted)",
                "paletteHex": "#RRGGBB",
                "displayFace": "a real typeface name",
                "bodyFace": "a real typeface name",
                "displaySizePt": 30,
                "bodySizePt": 15,
                "density": "airy|balanced|dense",
                "motif": "rule|band|none",
            }],
        }, indent=2),
        "",
        "Rules for the content:",
        "- It is written ONCE and every direction is previewed with it, so the "
        "user is comparing designs rather than comparing writing. Write it "
        "about this deck's actual subject - a preview of a deck on shipping "
        "logistics must not read like a preview of a deck on anything else.",
        "- EXACTLY 3 points, parallel to each other: three shifts, three "
        "segments, three risks. Each one needs a label, a figure and a clause.",
        "- The figures are illustrative and the preview says so on the page. "
        "Make them plausible for the subject; do not present them as "
        "researched and do not attribute them to anyone.",
        "- The chart needs 3-5 categories and 1-2 series, and every series "
        "must have exactly as many values as there are categories. Values are "
        "plain numbers - the unit goes in the chart title or the series name.",
        "",
        "Rules for the directions:",
        "- EVERY DIRECTION MUST USE A DIFFERENT ground. It decides the cover, "
        "which is the first thing the user sees, so two directions sharing one "
        "are two directions they cannot tell apart. A set that repeats a ground "
        "is rejected.",
        "- EVERY DIRECTION MUST USE A DIFFERENT structure. It decides what the "
        "three points actually look like, which is what the user is choosing "
        "between: 'list' sets them as bullets and the chart alone; 'stats' as a "
        "row of oversized figures; 'cards' as accent-barred cards over a claim "
        "strip; 'bands' as ruled rows of icon, label and prose beside a table "
        "of the chart's own numbers. A set that shapes them all the same way is "
        "rejected.",
        "- The directions must also differ on at least two of: ground, type "
        "pairing, motif, density, structure. Three directions differing only in "
        "hue is a failed set and will be rejected.",
        "- ground is the deck's deployment of tone, and each renders a visibly "
        "different cover: 'light' is white throughout with the title in the "
        "brand colour; 'dark' is a dark cover with content lifted slightly off "
        "it; 'rail' is a dark cover with white content pages, the classic "
        "report shape; 'split' puts the brand colour itself on the cover and "
        "tints the content pages.",
        "- motif is what is drawn under the title: 'rule' is a short accent "
        "rule, 'band' a full-measure filled band, 'none' leaves the title "
        "alone.",
        "- A serif display over a sans body is a different deck from sans over "
        "sans. Use real typefaces (Georgia, Arial, Helvetica, Times New "
        "Roman, Verdana), never invented ones.",
        "- density sets how much the planner puts on a slide, not only the "
        "look: 'dense' is an analyst deck, 'airy' is an executive one.",
        "- chartFamily is how this direction DRAWS a chart, which is the "
        "element a reader is most often actually looking at: 'labelled' drops "
        "the axis and puts a figure at the end of every bar, so the chart is "
        "read off the bars; 'gridded' keeps a ruled grid and an axis carrying "
        "the unit, and reads as a document; 'minimal' draws no furniture at all "
        "beyond the numbers. Omit it and it follows the density, which pairs the "
        "chart with the deck's own register - name it only when the direction "
        "wants a treatment its density would not give it.",
        "- Name each direction for what it looks like - 'Dark analyst', "
        "'Editorial serif', 'Clean minimal'. Never lettered A/B/C.",
        "- The rationale is one line a non-designer can act on.",
    ]
    if not brand_hex:
        lines += [
            "",
            "Rules for the colour:",
            "- paletteHex is the hue that direction proposes for ITSELF, and "
            "it is what the user will see in its preview. Choose hues that "
            "suit the subject rather than a default corporate blue, and give "
            "the directions different ones so the set is not a single hue in "
            "three layouts.",
            "- colorOptions is 2-3 alternatives the user can override with, "
            "named in plain words with a real hex. If the deck is about a "
            "specific organisation with a known brand colour, that colour is "
            "the FIRST option, named for the organisation ('Starbucks green') "
            "with its real hex - and if you are not confident of the real hex, "
            "leave it out rather than inventing one.",
            "- Every hex must be dark enough for white text or light enough "
            "for dark text; a mid-grey that is neither is rejected as "
            "unreadable.",
        ]
    if problems:
        lines += ["", "Your previous attempt was rejected:",
                  *(f"- {problem}" for problem in problems[:4]),
                  "Fix those specifically."]
    return "\n".join(lines)


def _palettes(raw) -> list[tuple[str, str]]:
    """The model's colour alternatives, as (name, #RRGGBB) it actually parsed."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in (raw or []):
        if not isinstance(entry, dict):
            continue
        value = str(entry.get("hex") or "").strip()
        if value and not value.startswith("#"):
            value = f"#{value}"
        name = " ".join(str(entry.get("name") or "").split())
        if not name or not colors.parse_hex(value) or value.upper() in seen:
            continue
        seen.add(value.upper())
        out.append((name, value.upper()))
    return out[:MAX_COLOUR_OPTIONS]


def _context(brand_hex: str | None, from_template: bool, asks_colour: bool) -> str:
    parts = ["Three directions, rendered on the same three slides so the "
             "comparison is of design rather than of copy. The words and "
             "figures on them are a sample - the deck itself is written once "
             "you have chosen."]
    if brand_hex:
        parts.append(f"All of them are built from {brand_hex}, as you asked - "
                     "they differ on ground, type, density and structure.")
    if asks_colour:
        parts.append("Each is proposed in its own colour. Pick a different one "
                     "and it is applied to whichever direction you choose - the "
                     "previews show its structure either way.")
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
