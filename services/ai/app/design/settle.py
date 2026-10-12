"""
Short material, set at its own size (#378).

An archetype states each frame's height for a page of the material it was
measured on. A page with less - two sentences beside a three-step flow - kept
those heights: the copy held a frame running to the bottom margin, the flow
was drawn at its own shape inside a frame several times its height, and the
material bunched at the top or floated, leaving the bottom third of the page
empty. Validation flagged it every round, and the ladder could not help: a
`replace` moved it to another arrangement with the same heights, and a
redraft only changes the words.

So once a page is bound, each row of it is cut to what it holds, the rows are
closed up at the archetype's own gap, and the space that frees is given back
around the material instead of under it: the heading stays where the
archetype puts it, the rows below it are set as one group, placed in the
space under the heading at its optical centre, and an exhibit shorter than
the copy beside it is centred on that copy.

Only what has a size of its own is cut. Copy is measured as it will be set.
A table is planned as the renderer will plan it, and a diagram or a row of
figures is compiled and measured, so neither number can drift from what is
drawn. A chart, a photograph or anything else that fills its frame has no
height of its own, and a row holding one keeps the height the archetype gave
it - which also leaves a page built round a chart exactly as it was.

The same pages `bind._compact` closes up are the only ones touched: a clean
vertical stack of grid-placed frames, which is what an authored language
derives. A measured pack is left as its own pages were.
"""

from __future__ import annotations

from typing import Optional

from app.design.pack import Pack
from app.design.schema import Exhibit, Frame, GridPlace, GridSpec, TextBlock

# Below this share of the content box, the space a page would free is not
# worth moving anything for: a page that nearly fills its arrangement is the
# arrangement working.
MIN_SLACK = 0.08

# Where the group below the heading sits in the space it has: this share of
# the leftover above it, the rest below. A block centred exactly reads as
# sitting low, so it is set at the optical centre, a little above.
OPTICAL_SHARE = 0.4

# The roles whose pages are an argument set under a heading. An opener, a
# divider or a closing page composes its space on purpose.
SETTLED_ROLES = ("content", "exhibit")

# A page of prose alone that fills at most this share of the space under its
# heading is short: set at body size and centred in that space, three
# sentences floated mid-page as an island apart from their heading (#392).
SHORT_PROSE_SHARE = 0.5

# Where short prose starts under its heading: this share of the content box
# below it, the archetype's own gap if that is larger. Close enough to read
# as the heading's lead, far enough not to crowd it.
LEAD_GAP = 0.10

# The rung short prose is set in, a step up from the body, as a publication
# sets a short text page as its lead paragraph.
LEAD_ROLE = "standfirst"


def settle(frames: tuple[Frame, ...], pack: Pack, grid: GridSpec,
           heading_frame: str, role: str) -> tuple[Frame, ...]:
    """These frames, each row cut to what it holds and the rest balanced."""
    from app.design.bind import _band, _rows, _stacked_over, _typical_gap
    from app.design.geometry import content_box

    if role not in SETTLED_ROLES:
        return frames
    placed = []
    for frame in frames:
        if frame.ground or frame.scrim:
            continue
        if _band(frame) is None or not isinstance(frame.place, GridPlace):
            return frames
        placed.append(frame)
    ordered = sorted(placed, key=lambda f: _band(f)[0])
    if len(ordered) < 2 or _stacked_over(ordered):
        return frames
    box = content_box(pack.canvas, grid)
    if box.height <= 0:
        return frames
    column = grid.column_width_in(pack.canvas)

    rows = _rows(ordered)
    heading_rows = [i for i, row in enumerate(rows)
                    if any(f.id == heading_frame for f in row)]
    # Rows above and including the heading's stay as the archetype set them.
    first = heading_rows[0] + 1 if heading_rows else 0
    body = rows[first:]
    if not body:
        return frames

    def measure(body):
        """Each row cut to what it holds; and whether every row's copy fits
        the band the archetype gave that row."""
        heights: dict[str, float] = {}
        row_heights = []
        within = True
        for row in body:
            top = min(_band(f)[0] for f in row)
            bottom = max(_band(f)[1] for f in row)
            natural = []
            for frame in row:
                width_in = (frame.place.span * column
                            + max(0, frame.place.span - 1) * grid.gutter_in)
                needed = natural_height_in(frame, width_in, pack)
                natural.append(None if needed is None else needed / box.height)
            if any(n is None for n in natural):
                row_heights.append(bottom - top)
                continue
            within = within and max(natural) <= (bottom - top) + 1e-6
            height = min(bottom - top, max(natural))
            row_heights.append(height)
            for frame in row:
                heights[frame.id] = min(_band(frame)[1] - _band(frame)[0], height)
        return heights, row_heights, within

    heights, row_heights, _ = measure(body)
    gap = _typical_gap(placed)
    start = min(_band(f)[0] for f in body[0])
    end = max(_band(f)[1] for f in body[-1])
    used = sum(row_heights) + gap * (len(body) - 1)
    ceiling = (max(_band(f)[1] for f in rows[first - 1]) if first else start)
    designed_gap = start - ceiling
    prose = _prose_only(body)
    if prose and used <= SHORT_PROSE_SHARE * (end - ceiling):
        # Short prose is the heading's lead: a rung up when that still fits
        # the space under the heading, all of it together.
        lead = _set_as_lead(body, pack)
        if lead is not None:
            lead_heights, lead_rows, lead_within = measure(lead)
            lead_used = sum(lead_rows) + gap * (len(lead) - 1)
            if lead_within and lead_used <= end - ceiling - max(designed_gap, LEAD_GAP):
                body, heights, row_heights, used = lead, lead_heights, lead_rows, lead_used
                swapped = {f.id: f for row in body for f in row}
                frames = tuple(swapped.get(f.id, f) for f in frames)
    slack = (end - start) - used
    if slack < MIN_SLACK:
        return frames

    # The group is placed in all the space under the heading, the archetype's
    # own gap below the heading included, at the optical centre - but never
    # closer to the heading than that gap. Prose alone is the heading's
    # lead and starts a fixed step under it instead: centred, a few lines
    # read as an island apart from their heading (#392).
    free = end - ceiling - used
    moved: dict[str, tuple[float, float]] = {}
    offset_below = min(free * OPTICAL_SHARE, LEAD_GAP) if prose else free * OPTICAL_SHARE
    cursor = ceiling + max(designed_gap, offset_below)
    for row, height in zip(body, row_heights):
        top = min(_band(f)[0] for f in row)
        for frame in row:
            offset = _band(frame)[0] - top
            new_h = min(heights.get(frame.id, _band(frame)[1] - _band(frame)[0]),
                        height - offset)
            if len(row) > 1 and _holds_an_exhibit(frame):
                # An exhibit beside copy is centred on it: set at the top of
                # a column the copy runs the full length of, a short flow
                # left the rest of that column empty.
                offset += (height - offset - new_h) / 2.0
            moved[frame.id] = (cursor + offset, new_h)
        cursor += height + gap

    out = []
    for frame in frames:
        if frame.id not in moved:
            out.append(frame)
            continue
        y, h = moved[frame.id]
        out.append(frame.model_copy(update={"place": frame.place.model_copy(
            update={"y": round(y, 4), "h": round(max(h, 0.01), 4)})}))
    settled = sorted((f for f in out if f.id in moved), key=lambda f: _band(f)[0])
    if _stacked_over(settled, tolerance=1e-6) or _band(settled[-1])[1] > 1.0 + 1e-6:
        return frames
    return tuple(out)


def _prose_only(body) -> bool:
    """Whether every frame under the heading holds text and nothing else."""
    return all(frame.content and not frame.children
               and all(isinstance(item, TextBlock) for item in frame.content)
               for row in body for frame in row)


def _set_as_lead(body, pack: Pack):
    """The rows with every body-rung block set in the lead rung, or None
    when the pack has no such rung or any block is set in another one."""
    if LEAD_ROLE not in pack.type_roles:
        return None
    if any(item.type_role != "body" for row in body for frame in row
           for item in frame.content):
        return None
    return [[frame.model_copy(update={"content": tuple(
                item.model_copy(update={"type_role": LEAD_ROLE}) for item in frame.content)})
             for frame in row] for row in body]


def _holds_an_exhibit(frame: Frame) -> bool:
    return any(isinstance(item, Exhibit) for item in frame.content)


def natural_height_in(frame: Frame, width_in: float, pack: Pack) -> Optional[float]:
    """How tall this frame needs to be for what it holds, or None if what it
    holds fills whatever it is given."""
    if frame.children or not frame.content:
        return None
    pad_in = pack.spacing.inches(frame.pad)
    inner = width_in - 2 * pad_in
    if inner <= 0:
        return None
    gap_in = pack.spacing.inches(1)
    tallest = 0.0
    for item in frame.content:
        needed = _item_height_in(item, inner, pack)
        if needed is None:
            return None
        tallest = max(tallest, needed)
    # `compile_page` splits a frame holding several things into equal bands,
    # so it needs the tallest of them as many times over.
    count = len(frame.content)
    return count * tallest + (count - 1) * gap_in + 2 * pad_in


def _item_height_in(item, width_in: float, pack: Pack) -> Optional[float]:
    if isinstance(item, TextBlock):
        from app.design.setting import set_copy

        role = pack.type_roles.get(item.type_role)
        if role is None:
            return None
        setting = set_copy(item.text, role, pack, width_in * 72.0, 0.0,
                           columns=item.columns, column_gap=item.column_gap)
        # One line of the block's own leading over the measured depth: the
        # window in which PowerPoint or LibreOffice breaks a line a word
        # earlier than the typesetter (see `bind._fitted_to_copy`).
        return (setting.height_pt + role.line_height_pt) / 72.0
    if not isinstance(item, Exhibit) or item.caption is not None:
        return None
    if item.exhibit_kind == "table":
        return _table_height_in(item.payload, width_in)
    if item.exhibit_kind in ("diagram", "metrics"):
        return _compiled_height_in(item, width_in, pack)
    return None


def _table_height_in(payload: dict, width_in: float) -> Optional[float]:
    from app.renderer import table_layout, table_style

    table = payload.get("table") or payload
    rows = [[str(c) for c in row] for row in (table.get("rows") or [])]
    if not rows:
        return None
    layout = table_layout.plan_table(
        rows, has_header=bool(table.get("headerRow")),
        region_width_in=width_in, region_height_in=100.0,
        family=table_style.resolve_table_style({}).font_name)
    return layout.height_in if layout is not None and not layout.overflows else None


def _compiled_height_in(item: Exhibit, width_in: float, pack: Pack) -> Optional[float]:
    from app.design.compile import _EXHIBIT_TO_BLOCK_KIND
    from app.renderer.designdoc_renderer import _block_compiler

    payload = item.payload or {}
    diagram = payload.get("diagram") if isinstance(payload.get("diagram"), dict) else {}
    if diagram.get("format") == "svg":
        return _svg_height_in(diagram.get("svg") or "", width_in)
    kind = _EXHIBIT_TO_BLOCK_KIND.get(item.exhibit_kind, item.exhibit_kind)
    compiler = _block_compiler(pack, {}, pack.colors.ground)
    height = compiler.measure(kind, payload, width_in)
    return height if height > 0 else None


def _svg_height_in(svg: str, width_in: float) -> Optional[float]:
    """An imported SVG is drawn at its own aspect, so at this width it is
    this tall - or less, if a shorter slot scales it down."""
    from xml.etree import ElementTree

    from app.renderer.svg.importer import UnsupportedSVG, read_viewbox

    try:
        view = read_viewbox(ElementTree.fromstring(svg))
    except (ElementTree.ParseError, UnsupportedSVG):
        return None
    return width_in / view.aspect if view.aspect else None
