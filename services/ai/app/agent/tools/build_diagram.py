"""
Tool: build_diagram

The structural graphic, stated through a function signature.

Most of what a deck means by "a graphic" is a process flow, a 2x2, a funnel, a
timeline, a pyramid, a value chain, a Venn or an org chart. All eight are drawn
from the deck's own marks (``renderer/diagrams.py``) rather than generated,
which means the words on them are the words that were passed in - a property no
diffusion model has, and the one that matters most in an engine whose whole
discipline is that nothing may look sourced when it is not.

**No connection required.** This works offline, which is why it is the default
for anything structural and image generation is the exception.

Every kind validates its own shape, and every refusal names the specific
problem, because that is a message the model can act on. A funnel of one stage
is not a funnel; a 2x2 with three quadrants is not a 2x2; a timeline whose
dates run backwards is a timeline drawn from a list nobody sorted.
"""

from __future__ import annotations

import re
import time

from app.agent.tools._today import example_quarter
from app.agent.registry import ToolParameter, ToolRegistry, ToolResult
from app.agent.tools._binding import bind as _bind
from app.agent.tools._binding import region_param as _region_param
from app.agent.tools._binding import slot_param as _slot_param
from app.agent.tools._json_extract import coerce_json_arg
from app.observability.logging import get_logger
from app.renderer.diagrams import DIAGRAM_KINDS

logger = get_logger(__name__)

# Per-kind bounds. Each is the point past which the shape stops making its own
# argument: a nine-step flow is a list, a five-circle Venn is a mess, and a
# twelve-tier pyramid is a bar chart drawn badly.
BOUNDS: dict[str, tuple[int, int]] = {
    "flow": (2, 6),
    "valueChain": (2, 7),
    "matrix2x2": (4, 4),
    "funnel": (2, 6),
    "pyramid": (2, 5),
    "timeline": (2, 8),
    "venn": (2, 3),
    "orgChart": (2, 9),
}

# A node label is a name, not a sentence. Past this it stops fitting the box it
# is drawn in and starts being body copy that happens to be inside a rectangle.
MAX_LABEL_CHARS = 40
MAX_DETAIL_CHARS = 90
# Both axis labels are set on one line, above and below the grid. Past this
# they wrap and steal a row from the quadrants.
MAX_Y_AXIS_CHARS = 48
MAX_X_AXIS_CHARS = 48

WHAT_EACH_KIND_IS = (
    "flow: steps in sequence, one leading to the next. "
    "valueChain: stages that together make the whole, not a sequence in time. "
    "matrix2x2: exactly four quadrants against two named axes. "
    "funnel: stages narrowing, optionally with a value each. "
    "pyramid: tiers, apex first - 'strategy over capability over infrastructure'. "
    "timeline: dated events along a rule. "
    "venn: two or three overlapping sets. "
    "orgChart: a hierarchy, each node naming its parent."
)


class BuildDiagramTool:
    """Flows, matrices, funnels and timelines - drawn, not diffused."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="build_diagram",
            description=(
                "Draw a structural graphic: a process flow, a 2x2 matrix, a "
                "funnel, a timeline, a pyramid, a value chain, a Venn or an org "
                "chart. Use this whenever the point of a slide is a STRUCTURE "
                "rather than a quantity - how something works, what the stages "
                "are, how two axes divide a field. A chart shows how much; a "
                "diagram shows how it fits together. It is drawn in the deck's "
                "own palette and type, needs nothing configured, and cannot "
                "invent a label. Prefer it over prose for anything a reader "
                "would otherwise have to hold four things in their head to "
                "follow. " + WHAT_EACH_KIND_IS
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="slide_index", type="integer",
                    description="0-based index of the slide this diagram belongs to",
                ),
                ToolParameter(
                    name="kind", type="string", enum=list(DIAGRAM_KINDS),
                    description="Which structure this is. " + WHAT_EACH_KIND_IS,
                ),
                ToolParameter(
                    name="items", type="array",
                    items={
                        "type": "object",
                        "properties": {
                            "label": {
                                "type": "string",
                                "description": "The node's name. A name, not a sentence.",
                            },
                            "detail": {
                                "type": "string",
                                "description": "One short line under the label, where there is room.",
                            },
                            "value": {
                                "type": "string",
                                "description": "funnel only: the stage's figure, e.g. '1,200' or '38%'.",
                            },
                            "date": {
                                "type": "string",
                                "description": (
                                    "timeline only: when it happened, e.g. "
                                    f"'{example_quarter()}'."
                                ),
                            },
                            "parent": {
                                "type": "string",
                                "description": "orgChart only: the label of this node's parent.",
                            },
                        },
                        "required": ["label"],
                    },
                    description=(
                        "The nodes, in reading order: left to right for a flow, "
                        "top to bottom for a funnel or pyramid, earliest first "
                        "for a timeline, top-left then clockwise for a 2x2."
                    ),
                ),
                ToolParameter(
                    name="title", type="string", required=False,
                    description="A short heading over the diagram. Omit if the slide title already says it.",
                ),
                ToolParameter(
                    name="x_axis", type="string", required=False,
                    description="matrix2x2 only: what the horizontal axis measures, e.g. 'Cost to implement'.",
                ),
                ToolParameter(
                    name="y_axis", type="string", required=False,
                    description="matrix2x2 only: what the vertical axis measures, e.g. 'Impact'.",
                ),
                ToolParameter(
                    name="overlap", type="string", required=False,
                    description="venn only: what the sets have in common, set in the middle.",
                ),
                _region_param(),
                _slot_param("'diagram', 'left', 'process'"),
            ],
        )

    @classmethod
    async def run(
        cls,
        slide_index: int,
        kind: str,
        items,
        title: str = "",
        x_axis: str = "",
        y_axis: str = "",
        overlap: str = "",
        region_id: str = "",
        slot: str = "",
    ) -> ToolResult:
        start = time.time()

        requested = str(kind or "").strip()
        canonical = _resolve_kind(requested)
        if canonical is None:
            return _fail(
                f"'{requested}' is not a diagram kind. Use one of: "
                f"{', '.join(DIAGRAM_KINDS)}.",
                start,
            )

        nodes = _entries(items)
        if not nodes:
            return _fail("items is empty - there is nothing to draw.", start)

        low, high = BOUNDS[canonical]
        if len(nodes) < low:
            return _fail(_too_few(canonical, len(nodes), low), start)
        if len(nodes) > high:
            return _fail(_too_many(canonical, len(nodes), high), start)

        clean: list[dict] = []
        for position, node in enumerate(nodes):
            label = str(node.get("label") or "").strip()
            if not label:
                return _fail(
                    f"items[{position}] has no label. Every node in a diagram is "
                    "named - an unlabelled box is a box the reader cannot use.",
                    start,
                )
            if len(label) > MAX_LABEL_CHARS:
                return _fail(
                    f"items[{position}]'s label is {len(label)} characters "
                    f"(max {MAX_LABEL_CHARS}). A node label is a name; put the "
                    "sentence in 'detail', or say it in the slide's copy.",
                    start,
                )
            entry: dict = {"label": label}
            detail = str(node.get("detail") or "").strip()
            if detail:
                if len(detail) > MAX_DETAIL_CHARS:
                    return _fail(
                        f"items[{position}]'s detail is {len(detail)} characters "
                        f"(max {MAX_DETAIL_CHARS}). It is one line under a "
                        "label, not a paragraph.",
                        start,
                    )
                entry["detail"] = detail
            for extra in ("value", "date", "parent"):
                value = str(node.get(extra) or "").strip()
                if value:
                    entry[extra] = value
            clean.append(entry)

        problem = _check_kind(canonical, clean, x_axis, y_axis)
        if problem:
            return _fail(problem, start)

        payload: dict = {"kind": canonical, "items": clean}
        if str(title or "").strip():
            payload["title"] = str(title).strip()
        if canonical == "matrix2x2":
            payload["xAxis"] = str(x_axis or "").strip()
            payload["yAxis"] = str(y_axis or "").strip()
        if canonical == "venn" and str(overlap or "").strip():
            payload["overlap"] = str(overlap).strip()

        content = _bind({"type": "diagram", "diagram": payload},
                        region_id=region_id, slot=slot or canonical)

        logger.info("diagram_built", slide_index=slide_index, kind=canonical,
                    nodes=len(clean))
        return ToolResult(
            success=True,
            data={
                "slideIndex": slide_index,
                "content": content,
                "summary": f"{canonical} diagram with {len(clean)} nodes",
            },
            duration_ms=int((time.time() - start) * 1000),
        )


# ── Per-kind checks ────────────────────────────────────────────────────


def _check_kind(kind: str, nodes: list[dict], x_axis: str, y_axis: str) -> str:
    """The problem with this diagram, or "" if there is none."""
    if kind == "matrix2x2":
        return _check_matrix(x_axis, y_axis)
    if kind == "funnel":
        return _check_funnel(nodes)
    if kind == "timeline":
        return _check_timeline(nodes)
    if kind == "orgChart":
        return _check_org_chart(nodes)
    return ""


def _check_matrix(x_axis: str, y_axis: str) -> str:
    """Both axes named. A 2x2 without them is four boxes."""
    if not str(x_axis or "").strip() or not str(y_axis or "").strip():
        return (
            "A 2x2 needs both axes named - x_axis and y_axis. Without them it "
            "is four labelled boxes, and the reader has no way to know what "
            "puts each one where it is."
        )
    if len(str(y_axis).strip()) > MAX_Y_AXIS_CHARS:
        return (
            f"y_axis is {len(str(y_axis).strip())} characters (max "
            f"{MAX_Y_AXIS_CHARS}). Name what the axis measures, not the whole "
            "finding - it is set on one line above the grid."
        )
    if len(str(x_axis).strip()) > MAX_X_AXIS_CHARS:
        return (
            f"x_axis is {len(str(x_axis).strip())} characters (max "
            f"{MAX_X_AXIS_CHARS}). Name what the axis measures, not the whole "
            "finding."
        )
    return ""


def _check_funnel(nodes: list[dict]) -> str:
    """Values, where given, have to fall.

    A funnel whose numbers rise is not a funnel, and drawing it as one would
    narrow the bands while the figures beside them grow - a graphic that
    contradicts its own labels.
    """
    values = [(index, _number(node.get("value"))) for index, node in enumerate(nodes)]
    given = [(index, value) for index, value in values if value is not None]
    if len(given) < 2:
        return ""
    if len(given) != len(nodes):
        return (
            "Some funnel stages have a value and some do not. Give every stage "
            "a value or none of them: the band widths follow the values, so a "
            "half-filled set draws a taper that means nothing."
        )
    for (first, a), (second, b) in zip(given, given[1:], strict=False):
        if b > a:
            return (
                f"Stage {second + 1}'s value ({b:g}) is larger than stage "
                f"{first + 1}'s ({a:g}). A funnel narrows. If the quantity "
                "grows, this is a flow or a bar chart, not a funnel."
            )
    return ""


def _check_timeline(nodes: list[dict]) -> str:
    """Every event dated, and the dates in order."""
    undated = [i for i, node in enumerate(nodes) if not node.get("date")]
    if undated:
        return (
            f"items[{undated[0]}] has no date. Every event on a timeline is "
            "dated - the axis is time, so an undated marker has nowhere to sit."
        )
    keys = [_date_key(str(node.get("date"))) for node in nodes]
    if all(key is not None for key in keys):
        for index in range(1, len(keys)):
            if keys[index] < keys[index - 1]:
                return (
                    f"items[{index}] ('{nodes[index]['date']}') comes before "
                    f"items[{index - 1}] ('{nodes[index - 1]['date']}'). A "
                    "timeline is drawn in the order given, so pass the events "
                    "earliest first."
                )
    return ""


def _check_org_chart(nodes: list[dict]) -> str:
    """One root, and every named parent present."""
    labels = {str(node.get("label")) for node in nodes}
    roots = [node for node in nodes if not node.get("parent")]
    if not roots:
        return (
            "Every node names a parent, so there is no root. One node has to "
            "sit at the top with no parent."
        )
    if len(roots) > 1:
        names = ", ".join(f"'{r['label']}'" for r in roots[:3])
        return (
            f"{len(roots)} nodes have no parent ({names}). An org chart has one "
            "root; give the others a parent, or draw this as a flow."
        )
    for index, node in enumerate(nodes):
        parent = str(node.get("parent") or "")
        if parent and parent not in labels:
            return (
                f"items[{index}] names parent '{parent}', which is not one of "
                "the labels in this diagram. A parent has to be a node that is "
                "here, spelled the same way."
            )
        if parent == str(node.get("label")):
            return f"items[{index}] is its own parent."
    return ""


# ── Helpers ────────────────────────────────────────────────────────────

_ALIASES = {kind.lower(): kind for kind in DIAGRAM_KINDS}
_ALIASES.update({
    "matrix": "matrix2x2",
    "2x2": "matrix2x2",
    "quadrant": "matrix2x2",
    "value_chain": "valueChain",
    "valuechain": "valueChain",
    "org_chart": "orgChart",
    "orgchart": "orgChart",
    "hierarchy": "orgChart",
    "process": "flow",
    "processflow": "flow",
    "process_flow": "flow",
})


def _resolve_kind(raw: str) -> str | None:
    """The canonical kind, from any form a caller might reasonably write.

    Aliases rather than exact matching, for the reason ``resolve_chart_type``
    has them: a type registered in one casing and requested in another matched
    nothing and silently fell through to the wrong renderer.
    """
    return _ALIASES.get(str(raw or "").strip().lower().replace(" ", ""))


def _entries(value) -> list[dict]:
    parsed = coerce_json_arg(value, value)
    if not isinstance(parsed, list):
        return []
    out = []
    for entry in parsed:
        entry = coerce_json_arg(entry, entry)
        if isinstance(entry, dict):
            out.append(entry)
        elif isinstance(entry, str) and entry.strip():
            # A bare string is a label. Accepted rather than refused: it is
            # unambiguous, and refusing it would spend a turn on syntax.
            out.append({"label": entry.strip()})
    return out


def _number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value or "").strip().replace(",", "").replace("%", "").lstrip("$£€")
    try:
        return float(text)
    except ValueError:
        return None


_MONTHS = {
    name: index for index, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun",
         "jul", "aug", "sep", "oct", "nov", "dec"), start=1)
}


def _date_key(text: str) -> tuple[int, int] | None:
    """A sortable key for a date label, or None when it is not one.

    Handles the forms a deck actually writes - "2024", "Q3 2025", "March 2026",
    "2026-03". Anything else returns None, and the order check is skipped
    rather than guessed at: refusing a timeline because a label was creative
    would be worse than not checking it.
    """
    raw = str(text or "").strip().lower()
    year = re.search(r"(19|20)\d{2}", raw)
    if not year:
        return None
    value = int(year.group(0))

    quarter = re.search(r"\bq([1-4])\b", raw)
    if quarter:
        return value, int(quarter.group(1)) * 3

    month = re.search(r"\b(" + "|".join(_MONTHS) + r")", raw)
    if month:
        return value, _MONTHS[month.group(1)]

    numeric = re.search(r"(19|20)\d{2}[-/](\d{1,2})", raw)
    if numeric:
        return value, int(numeric.group(2))

    return value, 0


def _too_few(kind: str, count: int, low: int) -> str:
    if kind == "matrix2x2":
        return (
            f"A 2x2 has four quadrants; {count} were given. If the field only "
            "divides one way, this is a comparison, not a matrix."
        )
    reasons = {
        "flow": "a single step is not a sequence",
        "valueChain": "a chain of one is a label",
        "funnel": "a funnel of one stage does not narrow",
        "pyramid": "a pyramid of one tier is a box",
        "timeline": "a timeline of one event is a date",
        "venn": "a Venn of one set has nothing to overlap",
        "orgChart": "a hierarchy of one node has no hierarchy",
    }
    return (
        f"A {kind} needs at least {low} items; {count} were given - "
        f"{reasons.get(kind, 'there is not enough here to draw')}."
    )


def _too_many(kind: str, count: int, high: int) -> str:
    reasons = {
        "flow": "past six steps a flow reads as a list, and a list should be one",
        "valueChain": "past seven stages the labels stop fitting their bands",
        "funnel": "past six stages the bands are thinner than their own labels",
        "pyramid": "past five tiers the apex is too small to carry a word",
        "timeline": "past eight events the labels collide whichever side they sit",
        "venn": "three circles is the most that can overlap legibly",
        "orgChart": "past nine nodes the boxes are narrower than the names in them",
    }
    return (
        f"{count} items is more than a {kind} can carry (max {high}) - "
        f"{reasons.get(kind, 'it stops being readable')}. Split it across two "
        "slides, or group the smaller ones."
    )


def _fail(message: str, start: float) -> ToolResult:
    return ToolResult(success=False, error=message,
                      duration_ms=int((time.time() - start) * 1000))
