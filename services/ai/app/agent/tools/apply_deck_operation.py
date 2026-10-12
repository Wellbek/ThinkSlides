"""
Tool: apply_deck_operation

A typed change to the whole deck - something added on top of pages, a
recolour, a link on every mention of a phrase - recorded in the run and
applied by the render to every deck it writes (#376).

This sits between the engine's rules and `edit_deck_with_code`. A script can
do anything, and the probes showed what that costs: two turns reading the
deck before each edit, coordinates the model had to choose, and an edit that
outlives a re-render only by being run again over a different page. Here the
model says *what* and *where* in words the vocabulary has - "the user's logo,
top right, every page" - and the code decides the coordinates, keeps the
element clear of the page's content on every render, and reports a page it
had to leave out instead of covering something.

Nothing is applied here. The tool checks the operation and hands it back; the
loop records it and re-renders, so there is one way an operation reaches a
deck and it is the same on the first render and the tenth.
"""

from __future__ import annotations

from app.agent.registry import ToolParameter, ToolRegistry, ToolResult
from app.agent.tools._json_extract import coerce_json_arg
from app.renderer import deck_ops

OPERATION_GUIDE = (
    "One JSON object, or a list of them for a request with several parts. "
    "Each one's 'kind' is one of:\n"
    "- 'overlay': something on top of pages. 'element' is "
    "{'type':'text','text':...} ({page} is the page number, {pages} the "
    "count; optional size_pt, color, bold, italic), "
    "{'type':'image','asset':<a picture the user supplied, by name>}, "
    "{'type':'qr','data':<url or text>}, "
    "{'type':'shape','shape':'rect'|'ellipse'|'line'} or "
    "{'type':'progress'} (a bar along the top or bottom edge as long as the "
    "share of the deck reached). 'anchor' is top-left, top, top-right, left, "
    "center, right, bottom-left, bottom or bottom-right. 'pages' is 'all', "
    "'all-but-cover', 'first', 'last' or a list of page numbers from 1. "
    "Optional: width_in, height_in, opacity, rotation_deg, link (a URL), and "
    "layer 'watermark' for a faint mark set across the content (text or a "
    "picture).\n"
    "- 'recolour': {'role':'accent'|'accent_alt','to':'#RRGGBB'} - every use "
    "of that colour, theme included.\n"
    "- 'link': {'phrase':...,'url':...} - every mention of the phrase becomes "
    "a link, in its own colour, underlined.\n"
    "Colours are a hex value or one of the deck's roles: "
    + ", ".join(deck_ops.COLOR_ROLES) + ". Leave a colour out to use the "
    "deck's own. Give an 'id' to replace an operation added earlier."
)


class ApplyDeckOperationTool:
    """A typed deck-wide change, checked here and applied at every render."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="apply_deck_operation",
            description=(
                "Add something to the pages of the whole deck (a logo, a "
                "watermark, a footer or page numbers, a QR code, a progress "
                "bar, a shape), recolour one of its colours everywhere, or "
                "link every mention of a phrase - or remove one of those "
                "again. Placement is by anchor, never coordinates: the "
                "element is kept clear of each page's content, shrunk where "
                "it has to be, and left off a page it would cover, which the "
                "result reports. It holds through every later re-render. "
                "Prefer this to edit_deck_with_code for anything it can say."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="action", type="string",
                    description="'add' (the default) or 'remove'.",
                    required=False,
                ),
                ToolParameter(
                    name="operation", type="string",
                    description=OPERATION_GUIDE,
                    required=False,
                ),
                ToolParameter(
                    name="id", type="string",
                    description="For 'remove': the id of the operation to remove.",
                    required=False,
                ),
            ],
        )

    @classmethod
    async def run(cls, action: str = "add", operation=None, id: str = "") -> ToolResult:
        from app.agent import run_context

        action = str(action or "add").strip().lower()
        if action == "remove":
            target = str(id or "").strip() or str(
                (coerce_json_arg(operation, {}) or {}).get("id") or "").strip()
            if not target:
                return ToolResult(success=False,
                                  error="Name the id of the operation to remove.")
            return ToolResult(success=True, data={
                "action": "remove", "id": target,
                "summary": f"Operation {target!r} is removed; the deck is rendered again without it.",
            })
        if action != "add":
            return ToolResult(success=False, error="action is 'add' or 'remove'.")

        raw = coerce_json_arg(operation, None)
        items = raw if isinstance(raw, list) else [raw]
        if not items or not all(isinstance(item, dict) for item in items):
            return ToolResult(success=False, error=(
                "operation has to be a JSON object or a list of them.\n\n"
                + OPERATION_GUIDE))
        parsed = []
        for number, item in enumerate(items, start=1):
            try:
                parsed.append(deck_ops.parse(item))
            except ValueError as exc:
                which = f"Operation {number}" if len(items) > 1 else "That operation"
                return ToolResult(success=False, error=(
                    f"{which} is not valid, and nothing was recorded: {exc}"))
        assets = run_context.get_assets()
        for op in parsed:
            element = getattr(op, "element", None)
            if isinstance(element, deck_ops.ImageElement) and element.asset not in assets:
                names = ", ".join(sorted(assets)) or "the user supplied none"
                return ToolResult(success=False, error=(
                    f"There is no picture named {element.asset!r} ({names}). "
                    "A picture has to come from the user; say so rather than "
                    "drawing a stand-in."))
        return ToolResult(success=True, data={
            "action": "add",
            "operations": [op.model_dump(mode="json") for op in parsed],
            "summary": ("Recorded; the deck is rendered again with "
                        + ("it" if len(parsed) == 1 else f"all {len(parsed)}")
                        + ", and the render reports any page it had to leave "
                        "out."),
        })
