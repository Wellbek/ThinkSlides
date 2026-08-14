"""
Tools: build_stat_callouts, build_icon_cards, build_accent_cards,
build_row_bands, build_image_card_row, build_banner, build_legend

Typed constructors for the block vocabulary, following the pattern
``build_visuals`` established for charts and tables: the model states the
block through a function signature, the tool validates and normalises it, and
the loop attaches the result to the slide.

Each tool takes the whole set for a slide - all four stat callouts, all seven
row bands - rather than one item per call. That is deliberate: a dense slide
is the point of these blocks, and a per-item tool would spend a dozen agent
iterations building one page.

Every failure comes back naming the specific problem, because that is a
message the model can act on. The recurring one is worth stating here: a
block is not a bullet list with decoration. A stat callout whose value is a
sentence, or an icon card with no heading, is content that would have read
better as text, and the tool says so rather than rendering it badly.
"""

from __future__ import annotations

import time

from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.agent.tools._json_extract import coerce_json_arg
from app.observability.logging import get_logger
from app.renderer.blocks import TONES
from app.renderer.primitives import PRIMITIVE_KINDS, validate_primitives

logger = get_logger(__name__)

MAX_STAT_CALLOUTS = 4
MAX_ICON_CARDS = 5
MAX_ACCENT_CARDS = 8
MAX_ROW_BANDS = 9
MAX_IMAGE_CARDS = 6
MAX_LEGEND_ITEMS = 4
# The escape hatch is for the marks a block cannot express. A slide reaching
# for dozens of them has found a block it should have asked for by name.
MAX_PRIMITIVES = 24

# A figure is a figure. "Adoption rose to 56% of respondents" is a sentence
# set at 40pt, which is not a callout, it is a headline in the wrong place.
MAX_STAT_VALUE_CHARS = 14
MAX_LABEL_WORDS = 8
# Row-band prose is one implication, not a paragraph.
MAX_BAND_PROSE_CHARS = 220
MAX_BANNER_CHARS = 160

TONE_HELP = (
    "Semantic colour: " + ", ".join(sorted(TONES))
    + ", or an explicit #RRGGBB. Use the same tone on a card and on the "
      "legend entry explaining it - they resolve to one colour."
)


def _as_list(value) -> list:
    parsed = coerce_json_arg(value, value)
    return parsed if isinstance(parsed, list) else []


def _entries(value) -> list[dict]:
    out = []
    for entry in _as_list(value):
        entry = coerce_json_arg(entry, entry)
        if isinstance(entry, dict):
            out.append(entry)
    return out


def _fail(message: str, start: float) -> ToolResult:
    return ToolResult(success=False, error=message,
                      duration_ms=int((time.time() - start) * 1000))


def _ok(slide_index: int, content: dict, summary: str, start: float) -> ToolResult:
    logger.info("block_built", slide_index=slide_index, block=content.get("type"))
    return ToolResult(
        success=True,
        data={"slideIndex": slide_index, "content": content, "summary": summary},
        duration_ms=int((time.time() - start) * 1000),
    )


def _slide_param() -> ToolParameter:
    return ToolParameter(
        name="slide_index", type="integer",
        description="0-based index of the slide this block belongs to",
    )


def _region_param() -> ToolParameter:
    return ToolParameter(
        name="region_id", type="string", required=False,
        description=(
            "The id of the slide region this block is bound to, when the slide "
            "declares a region tree. Omit on a slide that does not."
        ),
    )


def _bind(content: dict, region_id: str) -> dict:
    if (region_id or "").strip():
        content["regionId"] = region_id.strip()
    return content


class BuildStatCalloutsTool:
    """Large figures with captions - the number is the argument."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="build_stat_callouts",
            description=(
                "Build 1-4 headline figures set large, each with a short caption "
                "underneath saying what it measures. Use this when a slide's point "
                "IS a number - '56% of firms now use AI', '$93.5 billion invested' - "
                "and a bullet would bury it. The figure must be short enough to read "
                "as a figure; if it needs a sentence, it is body copy, not a callout."
            ),
            fn=cls.run,
            parameters=[
                _slide_param(),
                ToolParameter(
                    name="callouts", type="array",
                    items={
                        "type": "object",
                        "properties": {
                            "value": {"type": "string",
                                      "description": 'The figure as it reads, e.g. "56%", "$93.5 billion", "30x"'},
                            "caption": {"type": "string",
                                        "description": "What the figure measures, in one short sentence"},
                            "tone": {"type": "string", "description": TONE_HELP},
                        },
                        "required": ["value", "caption"],
                    },
                    description="1-4 figures. More than four stop reading as headlines.",
                ),
                _region_param(),
            ],
        )

    @classmethod
    async def run(cls, slide_index: int, callouts, region_id: str = "") -> ToolResult:
        start = time.time()
        entries = _entries(callouts)
        if not entries:
            return _fail("callouts is empty - there is no figure to set.", start)
        if len(entries) > MAX_STAT_CALLOUTS:
            return _fail(
                f"{len(entries)} figures is more than a callout row can carry "
                f"(max {MAX_STAT_CALLOUTS}). Keep the ones the slide argues from.",
                start,
            )

        clean = []
        for position, entry in enumerate(entries):
            value = str(entry.get("value") or "").strip()
            caption = str(entry.get("caption") or "").strip()
            if not value:
                return _fail(f"callouts[{position}] has no value.", start)
            if len(value) > MAX_STAT_VALUE_CHARS:
                return _fail(
                    f"callouts[{position}]'s value {value!r} is {len(value)} "
                    f"characters. A callout is a figure, not a sentence set large "
                    f"(max {MAX_STAT_VALUE_CHARS}) - put the wording in the caption.",
                    start,
                )
            if not caption:
                return _fail(
                    f"callouts[{position}] has no caption. A figure with nothing "
                    "saying what it measures is not information.",
                    start,
                )
            item = {"value": value, "caption": caption}
            tone = str(entry.get("tone") or "").strip()
            if tone:
                item["tone"] = tone
            clean.append(item)

        return _ok(
            slide_index,
            _bind({"type": "statCallout", "statCallouts": clean}, region_id),
            f"{len(clean)} stat callouts attached to slide {slide_index + 1}",
            start,
        )


class BuildIconCardsTool:
    """An icon badge over a heading and body copy."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="build_icon_cards",
            description=(
                "Build 2-5 cards in a row, each a line-art icon badge over a heading "
                "and a short body. Use this for a set of parallel things - four "
                "technologies, three capabilities, five industries - where a bulleted "
                "list would flatten them into one undifferentiated column. The icon is "
                "named by CONCEPT ('financial services', 'cloud', 'privacy'), not by "
                "filename; an unrecognised concept gets a neutral marker."
            ),
            fn=cls.run,
            parameters=[
                _slide_param(),
                ToolParameter(
                    name="cards", type="array",
                    items={
                        "type": "object",
                        "properties": {
                            "icon": {"type": "string",
                                     "description": "The concept the icon should show, e.g. 'security', 'growth', 'manufacturing'"},
                            "heading": {"type": "string", "description": "The card's heading, a few words"},
                            "body": {"type": "array", "items": {"type": "string"},
                                     "description": "1-3 short paragraphs of body copy"},
                        },
                        "required": ["icon", "heading"],
                    },
                    description="2-5 cards. One card is not a row; six stop fitting.",
                ),
                _region_param(),
            ],
        )

    @classmethod
    async def run(cls, slide_index: int, cards, region_id: str = "") -> ToolResult:
        start = time.time()
        entries = _entries(cards)
        if len(entries) < 2:
            return _fail(
                "An icon-card row needs at least two cards; a single card reads "
                "better as the slide's own copy.",
                start,
            )
        if len(entries) > MAX_ICON_CARDS:
            return _fail(
                f"{len(entries)} cards will not read at slide width "
                f"(max {MAX_ICON_CARDS}). Group them, or split across slides.",
                start,
            )

        clean = []
        for position, entry in enumerate(entries):
            heading = str(entry.get("heading") or "").strip()
            if not heading:
                return _fail(f"cards[{position}] has no heading.", start)
            if len(heading.split()) > MAX_LABEL_WORDS:
                return _fail(
                    f"cards[{position}]'s heading is {len(heading.split())} words. "
                    f"A card heading is a label (max {MAX_LABEL_WORDS} words) - the "
                    "sentence belongs in the body.",
                    start,
                )
            body = [str(line).strip() for line in _as_list(entry.get("body"))
                    if str(line or "").strip()]
            clean.append({
                "icon": str(entry.get("icon") or heading).strip(),
                "heading": heading,
                "body": body,
            })

        return _ok(
            slide_index,
            _bind({"type": "iconCard", "iconCards": clean}, region_id),
            f"{len(clean)} icon cards attached to slide {slide_index + 1}",
            start,
        )


class BuildAccentCardsTool:
    """Cards whose left bar carries their meaning."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="build_accent_cards",
            description=(
                "Build cards each carrying a coloured left bar whose colour is "
                "SEMANTIC - blue for a benefit, red for a risk. Use this when a slide "
                "sets two kinds of thing against each other and the reader needs to "
                "tell them apart at a glance. Pair it with build_legend so the slide "
                "says what the colours mean; a colour code nothing explains is "
                "decoration."
            ),
            fn=cls.run,
            parameters=[
                _slide_param(),
                ToolParameter(
                    name="cards", type="array",
                    items={
                        "type": "object",
                        "properties": {
                            "heading": {"type": "string", "description": "The card's claim, a short phrase"},
                            "body": {"type": "array", "items": {"type": "string"},
                                     "description": "1-2 short paragraphs supporting it"},
                            "tone": {"type": "string", "description": TONE_HELP},
                        },
                        "required": ["heading", "tone"],
                    },
                    description="2-8 cards.",
                ),
                ToolParameter(
                    name="columns", type="integer", required=False,
                    description="How many columns to lay the cards out in (default 1).",
                ),
                _region_param(),
            ],
        )

    @classmethod
    async def run(cls, slide_index: int, cards, columns: int = 1,
                  region_id: str = "") -> ToolResult:
        start = time.time()
        entries = _entries(cards)
        if len(entries) < 2:
            return _fail(
                "Accent cards distinguish things from each other, so there have to "
                "be at least two.",
                start,
            )
        if len(entries) > MAX_ACCENT_CARDS:
            return _fail(
                f"{len(entries)} cards is more than fits legibly (max {MAX_ACCENT_CARDS}).",
                start,
            )

        clean = []
        for position, entry in enumerate(entries):
            heading = str(entry.get("heading") or "").strip()
            if not heading:
                return _fail(f"cards[{position}] has no heading.", start)
            body = [str(line).strip() for line in _as_list(entry.get("body"))
                    if str(line or "").strip()]
            clean.append({
                "heading": heading,
                "body": body,
                "tone": str(entry.get("tone") or "neutral").strip(),
            })

        if len({card["tone"].lower() for card in clean}) < 2:
            return _fail(
                "Every card has the same tone, so the coloured bars distinguish "
                "nothing. Either give the cards the tones that tell them apart, or "
                "use build_icon_cards instead.",
                start,
            )

        content = {"type": "accentCard", "accentCards": clean}
        try:
            column_count = max(1, min(3, int(columns or 1)))
        except (TypeError, ValueError):
            column_count = 1
        if column_count > 1:
            content["columns"] = column_count

        return _ok(
            slide_index, _bind(content, region_id),
            f"{len(clean)} accent cards attached to slide {slide_index + 1}",
            start,
        )


class BuildRowBandsTool:
    """Hairline-ruled rows of icon, label and implication."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="build_row_bands",
            description=(
                "Build 3-9 hairline-ruled rows, each with an optional icon, a short "
                "label and one sentence of implication. This is the densest honest "
                "block in the vocabulary: use it when a slide has to carry many "
                "parallel points - every industry a trend affects, every capability a "
                "platform needs - and bullets would be a wall. Each row's prose is ONE "
                "implication, not a paragraph."
            ),
            fn=cls.run,
            parameters=[
                _slide_param(),
                ToolParameter(
                    name="rows", type="array",
                    items={
                        "type": "object",
                        "properties": {
                            "icon": {"type": "string",
                                     "description": "Concept for the row's icon; omit for no icon column"},
                            "label": {"type": "string", "description": "What the row is about, a few words"},
                            "prose": {"type": "string", "description": "The implication, one sentence"},
                        },
                        "required": ["label", "prose"],
                    },
                    description="3-9 rows.",
                ),
                _region_param(),
            ],
        )

    @classmethod
    async def run(cls, slide_index: int, rows, region_id: str = "") -> ToolResult:
        start = time.time()
        entries = _entries(rows)
        if len(entries) < 3:
            return _fail(
                "Fewer than three rows do not read as a banded set; use bullets or "
                "icon cards instead.",
                start,
            )
        if len(entries) > MAX_ROW_BANDS:
            return _fail(
                f"{len(entries)} rows is past what fits on one slide "
                f"(max {MAX_ROW_BANDS}). Split across two slides.",
                start,
            )

        clean = []
        for position, entry in enumerate(entries):
            label = str(entry.get("label") or "").strip()
            prose = str(entry.get("prose") or "").strip()
            if not label or not prose:
                return _fail(
                    f"rows[{position}] needs both a label and its implication - a "
                    "band with only a label is a bullet.",
                    start,
                )
            if len(prose) > MAX_BAND_PROSE_CHARS:
                return _fail(
                    f"rows[{position}]'s prose is {len(prose)} characters. A band "
                    f"carries one implication (max {MAX_BAND_PROSE_CHARS}); anything "
                    "longer squeezes every other row.",
                    start,
                )
            row = {"label": label, "prose": prose}
            icon = str(entry.get("icon") or "").strip()
            if icon:
                row["icon"] = icon
            clean.append(row)

        return _ok(
            slide_index,
            _bind({"type": "rowBand", "rowBands": clean}, region_id),
            f"{len(clean)} row bands attached to slide {slide_index + 1}",
            start,
        )


class BuildBannerTool:
    """A full-width one-sentence summary between two rules."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="build_banner",
            description=(
                "Build a full-width summary line, set between two rules, saying what "
                "the slide adds up to. Use it once per slide at most, under the "
                "content it summarises. One sentence: a banner that wraps to three "
                "lines is a paragraph in a box."
            ),
            fn=cls.run,
            parameters=[
                _slide_param(),
                ToolParameter(name="text", type="string",
                             description="The summary, one sentence"),
                _region_param(),
            ],
        )

    @classmethod
    async def run(cls, slide_index: int, text: str, region_id: str = "") -> ToolResult:
        start = time.time()
        summary = str(text or "").strip()
        if not summary:
            return _fail("A banner with no text is an empty box.", start)
        if len(summary) > MAX_BANNER_CHARS:
            return _fail(
                f"The banner is {len(summary)} characters. It is one line across the "
                f"slide (max {MAX_BANNER_CHARS}) - anything longer belongs in the body.",
                start,
            )
        return _ok(
            slide_index,
            _bind({"type": "banner", "banner": {"text": summary}}, region_id),
            f"banner attached to slide {slide_index + 1}", start,
        )


class BuildLegendTool:
    """Explains an encoding used elsewhere on the slide."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="build_legend",
            description=(
                "Build a legend saying what the colours on this slide mean. Call it "
                "whenever a slide uses semantic colour - accent cards, a tone on a "
                "stat callout - because a colour code the slide does not explain is "
                "decoration pretending to be information. Use the SAME tone names as "
                "the blocks it explains."
            ),
            fn=cls.run,
            parameters=[
                _slide_param(),
                ToolParameter(
                    name="items", type="array",
                    items={
                        "type": "object",
                        "properties": {
                            "label": {"type": "string", "description": 'What this colour means, e.g. "Benefits"'},
                            "tone": {"type": "string", "description": TONE_HELP},
                        },
                        "required": ["label", "tone"],
                    },
                    description="2-4 entries.",
                ),
                _region_param(),
            ],
        )

    @classmethod
    async def run(cls, slide_index: int, items, region_id: str = "") -> ToolResult:
        start = time.time()
        entries = _entries(items)
        if len(entries) < 2:
            return _fail(
                "A legend with one entry explains nothing - there is no distinction "
                "to draw.",
                start,
            )
        if len(entries) > MAX_LEGEND_ITEMS:
            return _fail(
                f"{len(entries)} legend entries is more than a slide can carry "
                f"(max {MAX_LEGEND_ITEMS}).",
                start,
            )

        clean = []
        for position, entry in enumerate(entries):
            label = str(entry.get("label") or "").strip()
            tone = str(entry.get("tone") or "").strip()
            if not label or not tone:
                return _fail(
                    f"items[{position}] needs both a label and the tone it explains.",
                    start,
                )
            clean.append({"label": label, "tone": tone})

        return _ok(
            slide_index,
            _bind({"type": "legend", "legend": {"items": clean}}, region_id),
            f"legend of {len(clean)} entries attached to slide {slide_index + 1}",
            start,
        )


class BuildImageCardRowTool:
    """Images at a shared height, each with a ruled title and caption."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="build_image_card_row",
            description=(
                "Build a row of 2-6 images at a shared height, each under an optional "
                "group heading and over a ruled title and caption. Use it when a set "
                "of things is best shown rather than described. Group headings may "
                "span unequal subsets of the row - two cards under one heading, three "
                "under another."
            ),
            fn=cls.run,
            parameters=[
                _slide_param(),
                ToolParameter(
                    name="cards", type="array",
                    items={
                        "type": "object",
                        "properties": {
                            "imageUrl": {"type": "string", "description": "URL of the image"},
                            "title": {"type": "string", "description": "The card's title, a few words"},
                            "caption": {"type": "string", "description": "One sentence saying what it is"},
                            "altText": {"type": "string", "description": "Accessibility description"},
                        },
                        "required": ["imageUrl", "title"],
                    },
                    description="2-6 cards.",
                ),
                ToolParameter(
                    name="groups", type="array", required=False,
                    items={
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "span": {"type": "integer",
                                     "description": "How many cards this heading covers"},
                        },
                        "required": ["label", "span"],
                    },
                    description=(
                        "Optional headings spanning subsets of the row, in order. "
                        "The spans must add up to the number of cards."
                    ),
                ),
                _region_param(),
            ],
        )

    @classmethod
    async def run(cls, slide_index: int, cards, groups=None,
                  region_id: str = "") -> ToolResult:
        start = time.time()
        entries = _entries(cards)
        if len(entries) < 2:
            return _fail("An image card row needs at least two cards.", start)
        if len(entries) > MAX_IMAGE_CARDS:
            return _fail(
                f"{len(entries)} images will not read at slide width "
                f"(max {MAX_IMAGE_CARDS}).",
                start,
            )

        clean = []
        for position, entry in enumerate(entries):
            url = str(entry.get("imageUrl") or entry.get("url") or "").strip()
            title = str(entry.get("title") or "").strip()
            if not url:
                return _fail(f"cards[{position}] has no imageUrl.", start)
            if not title:
                return _fail(
                    f"cards[{position}] has no title - an unlabelled picture on a "
                    "slide is a decoration.",
                    start,
                )
            card = {
                "image": {"source": "url", "data": url,
                          "altText": str(entry.get("altText") or title).strip()},
                "title": title,
            }
            caption = str(entry.get("caption") or "").strip()
            if caption:
                card["caption"] = caption
            clean.append(card)

        payload: dict = {"cards": clean}
        group_entries = _entries(groups)
        if group_entries:
            spans = []
            for position, group in enumerate(group_entries):
                label = str(group.get("label") or "").strip()
                try:
                    span = int(group.get("span") or 0)
                except (TypeError, ValueError):
                    span = 0
                if not label or span < 1:
                    return _fail(
                        f"groups[{position}] needs a label and a span of at least 1.",
                        start,
                    )
                spans.append({"label": label, "span": span})
            total = sum(g["span"] for g in spans)
            if total != len(clean):
                return _fail(
                    f"The group spans add up to {total} but there are {len(clean)} "
                    "cards. Every card belongs to exactly one group.",
                    start,
                )
            payload["groups"] = spans

        return _ok(
            slide_index,
            _bind({"type": "imageCardRow", "imageCardRow": payload}, region_id),
            f"{len(clean)} image cards attached to slide {slide_index + 1}",
            start,
        )


class DrawPrimitivesTool:
    """The escape hatch: individual marks, when no block is the right shape."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="draw_primitives",
            description=(
                "Place individual marks on a slide at exact positions - a rule, a "
                "tinted panel, a label, a picture, an outline. Use this ONLY when no "
                "block above is the right shape for what the slide needs: a block "
                "carries meaning and sizes itself, a mark does neither, so a slide "
                "built entirely from marks is a slide the rest of the pipeline "
                "cannot reason about. Geometry is FRACTIONAL (0..1) against the "
                "region named in region_id, or against the whole slide when none is "
                "named, so it survives a change of canvas. Marks are drawn behind "
                "the slide's blocks."
            ),
            fn=cls.run,
            parameters=[
                _slide_param(),
                ToolParameter(
                    name="marks", type="array",
                    items={
                        "type": "object",
                        "properties": {
                            "id": {"type": "string",
                                   "description": "A short stable name for this mark, e.g. 'divider'"},
                            "kind": {"type": "string",
                                     "enum": sorted(PRIMITIVE_KINDS),
                                     "description": "text, rect, line, ellipse or image"},
                            "regionId": {"type": "string",
                                         "description": "The region this mark is positioned inside; omit for the whole slide"},
                            "x": {"type": "number", "description": "Left edge, 0..1 of the frame"},
                            "y": {"type": "number", "description": "Top edge, 0..1 of the frame"},
                            "w": {"type": "number", "description": "Width, 0..1 of the frame"},
                            "h": {"type": "number", "description": "Height, 0..1 of the frame"},
                            "x1": {"type": "number", "description": "Line start x, 0..1 (kind=line)"},
                            "y1": {"type": "number", "description": "Line start y, 0..1 (kind=line)"},
                            "x2": {"type": "number", "description": "Line end x, 0..1 (kind=line)"},
                            "y2": {"type": "number", "description": "Line end y, 0..1 (kind=line)"},
                            "text": {"type": "string", "description": "The words (kind=text)"},
                            "sizePt": {"type": "number", "description": "Type size in points (kind=text)"},
                            "bold": {"type": "boolean"},
                            "align": {"type": "string", "enum": ["left", "center", "right"]},
                            "anchor": {"type": "string", "enum": ["top", "middle", "bottom"]},
                            "fill": {"type": "string", "description": "#RRGGBB fill"},
                            "stroke": {"type": "string", "description": "#RRGGBB stroke"},
                            "strokeWeightPt": {"type": "number"},
                            "color": {"type": "string",
                                      "description": "#RRGGBB text colour; corrected if it fails contrast against its ground"},
                            "imageUrl": {"type": "string", "description": "URL (kind=image)"},
                            "altText": {"type": "string"},
                        },
                        "required": ["kind"],
                    },
                    description="The marks to place, in back-to-front order.",
                ),
            ],
        )

    @classmethod
    async def run(cls, slide_index: int, marks) -> ToolResult:
        start = time.time()
        entries = _entries(marks)
        if not entries:
            return _fail("marks is empty - there is nothing to place.", start)
        if len(entries) > MAX_PRIMITIVES:
            return _fail(
                f"{len(entries)} marks on one slide is past what this is for "
                f"(max {MAX_PRIMITIVES}). If a slide needs that many, it is a "
                "block - say what it means with one of the build_* tools.",
                start,
            )

        clean: list[dict] = []
        for position, entry in enumerate(entries):
            kind = str(entry.get("kind") or "").strip()
            if kind not in PRIMITIVE_KINDS:
                return _fail(
                    f"marks[{position}] has kind {kind!r}; it must be one of "
                    f"{', '.join(sorted(PRIMITIVE_KINDS))}.",
                    start,
                )
            mark: dict = {"kind": kind}
            for key in ("id", "regionId", "align", "anchor"):
                value = str(entry.get(key) or "").strip()
                if value:
                    mark[key] = value
            for key in ("x", "y", "w", "h", "x1", "y1", "x2", "y2",
                        "sizePt", "strokeWeightPt"):
                if entry.get(key) is not None:
                    try:
                        mark[key] = float(entry[key])
                    except (TypeError, ValueError):
                        return _fail(
                            f"marks[{position}].{key} is not a number: "
                            f"{entry[key]!r}.",
                            start,
                        )
            for key in ("fill", "stroke", "color"):
                value = str(entry.get(key) or "").strip()
                if value:
                    mark[key] = value
            if entry.get("bold"):
                mark["bold"] = True

            if kind == "text":
                text = str(entry.get("text") or "").strip()
                if not text:
                    return _fail(
                        f"marks[{position}] is a text mark with no text, which "
                        "renders as an empty box.",
                        start,
                    )
                mark["runs"] = [{"text": text}]
            elif kind == "image":
                url = str(entry.get("imageUrl") or "").strip()
                if not url:
                    return _fail(f"marks[{position}] is an image mark with no imageUrl.",
                                 start)
                mark["image"] = {"source": "url", "data": url,
                                 "altText": str(entry.get("altText") or "").strip()}
            if entry.get("altText"):
                mark["altText"] = str(entry["altText"]).strip()
            clean.append(mark)

        errors, warnings = validate_primitives(
            clean, region_ids=[m["regionId"] for m in clean if m.get("regionId")]
        )
        # Region bindings are checked against the slide's own regions when the
        # slide is validated; here the only thing knowable is the geometry.
        errors = [e for e in errors if "does not declare" not in e]
        if errors:
            return _fail(
                "These marks would not render correctly: " + "; ".join(errors[:4]),
                start,
            )

        logger.info("primitives_built", slide_index=slide_index, count=len(clean))
        return ToolResult(
            success=True,
            data={
                "slideIndex": slide_index,
                "primitives": clean,
                "warnings": warnings,
                "summary": (
                    f"{len(clean)} marks placed on slide {slide_index + 1}"
                    + (f" ({len(warnings)} warnings)" if warnings else "")
                ),
            },
            duration_ms=int((time.time() - start) * 1000),
        )


BLOCK_BUILDER_TOOLS = (
    "build_stat_callouts", "build_icon_cards", "build_accent_cards",
    "build_row_bands", "build_banner", "build_legend", "build_image_card_row",
    "draw_primitives",
)
