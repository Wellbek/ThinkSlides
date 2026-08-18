"""
Diagrams: drawn, not diffused.

Most of what a professional deck means by "a graphic" is not a photograph. It
is a process flow, a 2x2, a funnel, a timeline, a pyramid, a value chain, a
Venn or an org chart - structure, made visible.

Three properties follow from drawing them out of the same marks the rest of the
slide is made of, and a diffusion model has none of the three:

* it inherits the deck's palette and type, so it belongs to the page rather
  than sitting on it,
* it is vector at render time, so it is crisp at any zoom, and
* **it cannot hallucinate a label.** The words on it are the words that were
  passed in. That is the property that matters most here, because this engine's
  whole discipline is that nothing may look sourced when it is not.

They also work with nothing configured. A diagram needs no connection, no
credential and no network, which is why this is the default for anything
structural and image generation is the exception.

Geometry
--------

Every kind lays itself out inside the rect it is given, in inches, and returns
``Primitive`` objects - so a diagram is measured by the same bounding box that
draws it, is contained by the same region model, and is contrast-checked by the
same pass as everything else.

The one shared rule is that **type never scales below legibility**. A diagram
squeezed into a quarter page drops detail lines before it shrinks its labels
past reading size, because a diagram nobody can read is worse than one carrying
less.
"""

from __future__ import annotations

import math
from typing import Optional

from app import colors
from app.renderer import primitives
from app.renderer.layout import PositionConfig
from app.renderer.primitives import Primitive

DIAGRAM_KINDS: tuple[str, ...] = (
    "flow",
    "matrix2x2",
    "funnel",
    "timeline",
    "pyramid",
    "valueChain",
    "venn",
    "orgChart",
)

# Below this a label stops being readable on a projected slide, so detail is
# dropped instead. Same reasoning as MAX_LABELLED_POINTS in the chart renderer:
# the cap exists because the alternative is an unreadable smear.
MIN_LABEL_PT = 8.0
MIN_DETAIL_PT = 7.0

# A detail line under a node is the first thing to go when the box is small.
# Two lines of 7pt in a 0.4in box is a grey block, not a caption.
DETAIL_MIN_BOX_IN = 0.62

GAP_IN = 0.10
NODE_PAD_IN = 0.07
HAIRLINE_PT = 0.75
CONNECTOR_PT = 1.1

# How much of a node's tint shows through. A filled box in the accent colour at
# full strength is a button; at a wash it is a diagram node.
TINT_ALPHA = 0.14
BAND_ALPHA = 0.22

# A node's outline has to be visible against the ground it is drawn on. Below
# this the box disappears and the diagram loses a step without saying so.
MIN_NODE_CONTRAST = 1.6

# How tall each kind wants to be, as a share of its width.
#
# A diagram fills the rect it is handed, which is right when the rect is a real
# region and wrong when it is not: ``BlockCompiler.measure`` compiles into a
# hundred-inch box to find a block's natural height, and a shape that simply
# takes what it is given answers "a hundred inches". So each kind states the
# proportion it is drawn at, the working rect is clamped to it, and a diagram
# measures as the shape it actually is.
ASPECT: dict[str, float] = {
    "flow": 0.34,
    "valueChain": 0.26,
    "matrix2x2": 0.74,
    "funnel": 0.66,
    "pyramid": 0.58,
    "timeline": 0.44,
    "venn": 0.70,
    "orgChart": 0.52,
}


def compile_diagram(
    item: dict,
    rect: PositionConfig,
    *,
    palette: "DiagramPalette",
    label_pt: float,
    detail_pt: float,
    prefix: str = "d",
) -> list[Primitive]:
    """The marks that draw one diagram inside ``rect``."""
    payload = item.get("diagram") if isinstance(item.get("diagram"), dict) else item
    kind = str(payload.get("kind") or "").strip()
    nodes = [n for n in (payload.get("items") or []) if isinstance(n, dict)]
    if kind not in DIAGRAM_KINDS or not nodes or rect.width <= 0 or rect.height <= 0:
        return []

    body = rect
    out: list[Primitive] = []

    title = str(payload.get("title") or "").strip()
    if title:
        title_pt = max(label_pt, MIN_LABEL_PT + 1.0)
        title_h = primitives.measure_text(title, title_pt, rect.width, bold=True)
        out.append(_text(f"{prefix}-title", rect.left, rect.top, rect.width,
                         title_h, title, title_pt, palette.heading,
                         bold=True, font=palette.font))
        body = PositionConfig(
            left=rect.left, top=rect.top + title_h + GAP_IN,
            width=rect.width, height=max(0.2, rect.height - title_h - GAP_IN),
        )

    body = _at_aspect(kind, body)
    drawer = _DRAWERS[kind]
    out.extend(drawer(nodes, body, payload, palette, label_pt, detail_pt, prefix))
    return [p for p in out if p is not None]


def _at_aspect(kind: str, rect: PositionConfig) -> PositionConfig:
    """The rect a diagram actually draws in.

    Never taller than the shape wants to be. A flow given the whole page is
    still a band across it, not five boxes a slide high, and a measurement pass
    that hands it a hundred inches gets the flow's own proportion back rather
    than a hundred inches.
    """
    wanted = rect.width * ASPECT.get(kind, 0.5)
    if rect.height <= wanted:
        return rect
    return PositionConfig(left=rect.left, top=rect.top, width=rect.width,
                          height=wanted)


class DiagramPalette:
    """The colours and font one diagram draws in.

    Constructed from the deck's own design system by the block compiler, so a
    diagram cannot pick a colour the rest of the slide does not use. Held as a
    small object rather than six arguments because every drawer needs all of it.
    """

    def __init__(self, *, ground: str, ink: str, heading: str, accent: str,
                 rule: str, sequence: list[str], font: Optional[str] = None):
        self.ground = ground
        self.ink = ink
        self.heading = heading
        self.accent = accent
        self.rule = rule
        # Filtered by contrast against the ground, not by palette slot name.
        # A deck whose palette carries a near-background tone drew one node of
        # a five-step flow in the ground colour - an invisible box, and only
        # the one, which reads as a rendering bug rather than as a choice.
        self.sequence = [
            value for value in sequence
            if colors.parse_hex(value)
            and colors.contrast_ratio(value, ground) >= MIN_NODE_CONTRAST
        ] or [accent]
        self.font = font

    def step(self, index: int) -> str:
        """The accent for the nth node, cycling the deck's own sequence."""
        return self.sequence[index % len(self.sequence)]

    def tint(self, index: int, alpha: float = TINT_ALPHA) -> str:
        """That accent, washed back to something a label reads on.

        The ground with a little accent mixed in, not the accent lightened: a
        node on a navy half has to stay dark, and lightening an accent gives
        the same pale wash whatever it is sitting on.
        """
        return colors.mix(self.ground, self.step(index), alpha)

    def on(self, background: str) -> str:
        """Whichever of the deck's inks reads against this fill."""
        return colors.readable_on(background, self.ink)


# ── Shared marks ───────────────────────────────────────────────────────


def _text(primitive_id: str, left: float, top: float, width: float,
          height: float, text: str, size_pt: float, color: str,
          bold: bool = False, align: str = "left",
          font: Optional[str] = None, anchor: str = "top") -> Optional[Primitive]:
    if not str(text or "").strip():
        return None
    from app.renderer.primitives import TextRunSpec

    return Primitive(
        id=primitive_id, kind="text",
        box=PositionConfig(left=left, top=top, width=max(0.08, width),
                           height=max(0.06, height)),
        runs=(TextRunSpec(text=str(text), bold=bold),),
        size_pt=size_pt, color=color, bold=bold, align=align, anchor=anchor,
        font=font,
    )


def _rect(primitive_id: str, box: PositionConfig, fill: Optional[str] = None,
          stroke: Optional[str] = None, stroke_pt: float = 0.0) -> Primitive:
    return Primitive(id=primitive_id, kind="rect", box=box, fill=fill,
                     stroke=stroke, stroke_pt=stroke_pt)


def _line(primitive_id: str, x1: float, y1: float, x2: float, y2: float,
          color: str, weight: float = CONNECTOR_PT) -> Primitive:
    return Primitive(
        id=primitive_id, kind="line",
        box=PositionConfig(left=x1, top=y1, width=abs(x2 - x1) or 0.01,
                           height=abs(y2 - y1) or 0.01),
        end=(x2, y2), stroke=color, stroke_pt=weight,
    )


def _arrow(prefix: str, x1: float, y: float, x2: float, color: str,
           head: float = 0.07) -> list[Primitive]:
    """A connector with a caret on the end.

    Two short strokes rather than a shape: the primitive layer has five marks
    and none of them is an arrowhead, and adding a sixth to draw a chevron
    would widen the whole vocabulary for one diagram.
    """
    return [
        _line(f"{prefix}-stem", x1, y, x2, y, color),
        _line(f"{prefix}-head-a", x2 - head, y - head * 0.62, x2, y, color),
        _line(f"{prefix}-head-b", x2 - head, y + head * 0.62, x2, y, color),
    ]


def _node_text(prefix: str, box: PositionConfig, label: str, detail: str,
               palette: DiagramPalette, label_pt: float, detail_pt: float,
               fill: str, align: str = "center") -> list[Primitive]:
    """A label, and a detail line under it where there is room for one."""
    ink = palette.on(fill)
    inner_w = max(0.1, box.width - NODE_PAD_IN * 2)
    inner_l = box.left + NODE_PAD_IN
    show_detail = bool(detail) and box.height >= DETAIL_MIN_BOX_IN

    label_h = primitives.measure_text(label, label_pt, inner_w, bold=True)
    if not show_detail:
        # Centred in the box when it is the only thing in it.
        top = box.top + max(0.0, (box.height - label_h) / 2)
        return [_text(f"{prefix}-label", inner_l, top, inner_w, label_h,
                      label, label_pt, ink, bold=True, align=align,
                      font=palette.font)]

    detail_h = primitives.measure_text(detail, detail_pt, inner_w)
    total = label_h + detail_h + 0.03
    top = box.top + max(NODE_PAD_IN * 0.6, (box.height - total) / 2)
    return [
        _text(f"{prefix}-label", inner_l, top, inner_w, label_h, label,
              label_pt, ink, bold=True, align=align, font=palette.font),
        _text(f"{prefix}-detail", inner_l, top + label_h + 0.03, inner_w,
              detail_h, detail, detail_pt, ink, align=align, font=palette.font),
    ]


def _box_height(nodes: list[dict], box_w: float, available: float,
                label_pt: float, detail_pt: float) -> float:
    """How tall a row of nodes needs to be to hold its own text.

    Sized to the content rather than to the space, because a flow handed a
    third of a page should be a band across it, not four-inch boxes with a line
    of text floating in the middle of each. Capped by what is available, so a
    diagram in a small region still fits it.
    """
    inner = max(0.1, box_w - NODE_PAD_IN * 2)
    tallest = 0.0
    for node in nodes:
        needed = primitives.measure_text(_label_of(node), label_pt, inner, bold=True)
        detail = _detail_of(node)
        if detail:
            needed += primitives.measure_text(detail, detail_pt, inner) + 0.03
        tallest = max(tallest, needed)
    return max(0.36, min(available, tallest + NODE_PAD_IN * 2.6))


def _label_of(node: dict) -> str:
    return str(node.get("label") or "").strip()


def _detail_of(node: dict) -> str:
    return str(node.get("detail") or "").strip()


# ── flow ───────────────────────────────────────────────────────────────


def _draw_flow(nodes, rect, payload, palette, label_pt, detail_pt, prefix):
    """Boxes left to right with arrows between them."""
    count = len(nodes)
    arrow_w = min(0.34, rect.width * 0.06)
    box_w = max(0.4, (rect.width - arrow_w * (count - 1)) / count)
    height = _box_height(nodes, box_w, rect.height, label_pt, detail_pt)
    top = rect.top + (rect.height - height) / 2

    out: list[Primitive] = []
    for index, node in enumerate(nodes):
        left = rect.left + index * (box_w + arrow_w)
        fill = palette.tint(index)
        box = PositionConfig(left=left, top=top, width=box_w, height=height)
        out.append(_rect(f"{prefix}-n{index}", box, fill=fill,
                         stroke=palette.step(index), stroke_pt=HAIRLINE_PT))
        out.extend(_node_text(f"{prefix}-n{index}", box, _label_of(node),
                              _detail_of(node), palette, label_pt, detail_pt,
                              fill))
        if index < count - 1:
            out.extend(_arrow(
                f"{prefix}-a{index}",
                left + box_w + arrow_w * 0.18, top + height / 2,
                left + box_w + arrow_w * 0.82, palette.rule,
            ))
    return out


# ── valueChain ─────────────────────────────────────────────────────────


def _draw_value_chain(nodes, rect, payload, palette, label_pt, detail_pt, prefix):
    """A continuous band of stages, touching rather than arrowed.

    The difference from a flow is the claim: a flow says "then", a value chain
    says "these together make the whole". Drawn as one unbroken run so it reads
    that way, with hairlines between stages instead of arrows.
    """
    count = len(nodes)
    box_w = rect.width / count
    height = _box_height(nodes, box_w, rect.height, label_pt, detail_pt)
    top = rect.top + (rect.height - height) / 2

    out: list[Primitive] = []
    for index, node in enumerate(nodes):
        left = rect.left + index * box_w
        fill = palette.tint(index, BAND_ALPHA)
        box = PositionConfig(left=left, top=top, width=box_w, height=height)
        out.append(_rect(f"{prefix}-s{index}", box, fill=fill))
        out.extend(_node_text(f"{prefix}-s{index}", box, _label_of(node),
                              _detail_of(node), palette, label_pt, detail_pt,
                              fill))
        if index:
            out.append(_line(f"{prefix}-div{index}", left, top, left,
                             top + height, palette.ground, HAIRLINE_PT * 2))
    return out


# ── matrix2x2 ──────────────────────────────────────────────────────────


def _draw_matrix(nodes, rect, payload, palette, label_pt, detail_pt, prefix):
    """Four quadrants between two named axes.

    The axis labels sit above and below the grid rather than along its edges.
    The primitive layer has no rotation, so a vertical label has to be set
    across a narrow gutter, and "Impact" in a third of an inch comes out as
    "Im / pa / ct" - three lines of nonsense next to a clean grid. Above the
    grid it reads, and the position still says which axis it is.
    """
    axis_pt = max(MIN_DETAIL_PT, detail_pt)
    x_axis = str(payload.get("xAxis") or "").strip()
    y_axis = str(payload.get("yAxis") or "").strip()

    top_strip = primitives.measure_text(y_axis, axis_pt, rect.width) + 0.04 if y_axis else 0.0
    bottom_strip = primitives.measure_text(x_axis, axis_pt, rect.width) + 0.04 if x_axis else 0.0
    grid = PositionConfig(
        left=rect.left, top=rect.top + top_strip, width=rect.width,
        height=max(0.4, rect.height - top_strip - bottom_strip),
    )
    cell_w = (grid.width - GAP_IN) / 2
    cell_h = (grid.height - GAP_IN) / 2

    out: list[Primitive] = []
    if y_axis:
        out.append(_text(f"{prefix}-yaxis", rect.left, rect.top, rect.width,
                         max(0.1, top_strip - 0.04), y_axis, axis_pt,
                         palette.rule, align="left", font=palette.font))

    for index, node in enumerate(nodes[:4]):
        row, column = divmod(index, 2)
        left = grid.left + column * (cell_w + GAP_IN)
        top = grid.top + row * (cell_h + GAP_IN)
        fill = palette.tint(index)
        box = PositionConfig(left=left, top=top, width=cell_w, height=cell_h)
        out.append(_rect(f"{prefix}-q{index}", box, fill=fill,
                         stroke=palette.step(index), stroke_pt=HAIRLINE_PT))
        out.extend(_node_text(f"{prefix}-q{index}", box, _label_of(node),
                              _detail_of(node), palette, label_pt, detail_pt,
                              fill))

    if x_axis:
        out.append(_text(f"{prefix}-xaxis", grid.left, grid.top + grid.height + 0.04,
                         grid.width, max(0.1, bottom_strip - 0.04), x_axis,
                         axis_pt, palette.rule, align="center", font=palette.font))
    return out


# ── funnel ─────────────────────────────────────────────────────────────


def _draw_funnel(nodes, rect, payload, palette, label_pt, detail_pt, prefix):
    """Stages narrowing downward, each band's width its share of the top."""
    count = len(nodes)
    band_h = (rect.height - GAP_IN * (count - 1)) / count
    values = [_number(node.get("value")) for node in nodes]
    # Width follows the value where values were given, and narrows evenly where
    # they were not. An invented taper on real numbers would misstate them.
    top_value = values[0] if values[0] else None
    widths = []
    for index in range(count):
        if top_value and values[index]:
            share = max(0.18, min(1.0, values[index] / top_value))
        else:
            share = 1.0 - index * (0.62 / max(1, count - 1))
        widths.append(rect.width * share)

    out: list[Primitive] = []
    for index, node in enumerate(nodes):
        width = widths[index]
        left = rect.left + (rect.width - width) / 2
        top = rect.top + index * (band_h + GAP_IN)
        fill = palette.tint(index, BAND_ALPHA)
        box = PositionConfig(left=left, top=top, width=width, height=band_h)
        out.append(_rect(f"{prefix}-f{index}", box, fill=fill,
                         stroke=palette.step(index), stroke_pt=HAIRLINE_PT))
        caption = _label_of(node)
        raw = str(node.get("value") or "").strip()
        if raw:
            caption = f"{caption}  {raw}"
        out.extend(_node_text(f"{prefix}-f{index}", box, caption,
                              _detail_of(node), palette, label_pt, detail_pt,
                              fill))
    return out


# ── pyramid ────────────────────────────────────────────────────────────


def _draw_pyramid(nodes, rect, payload, palette, label_pt, detail_pt, prefix):
    """Tiers narrowing upward - the funnel's argument, inverted.

    Top tier first, because a pyramid is read from its apex: "strategy over
    capability over infrastructure", not the other way round.
    """
    count = len(nodes)
    band_h = (rect.height - GAP_IN * (count - 1)) / count

    out: list[Primitive] = []
    for index, node in enumerate(nodes):
        share = 0.34 + index * (0.66 / max(1, count - 1))
        width = rect.width * share
        left = rect.left + (rect.width - width) / 2
        top = rect.top + index * (band_h + GAP_IN)
        fill = palette.tint(index, BAND_ALPHA)
        box = PositionConfig(left=left, top=top, width=width, height=band_h)
        out.append(_rect(f"{prefix}-t{index}", box, fill=fill,
                         stroke=palette.step(index), stroke_pt=HAIRLINE_PT))
        out.extend(_node_text(f"{prefix}-t{index}", box, _label_of(node),
                              _detail_of(node), palette, label_pt, detail_pt,
                              fill))
    return out


# ── timeline ───────────────────────────────────────────────────────────


def _draw_timeline(nodes, rect, payload, palette, label_pt, detail_pt, prefix):
    """A rule with dated markers, labels alternating above and below.

    Alternating is not decoration: on eight events at deck width, labels all on
    one side overlap their neighbours, and overlapping labels are the one thing
    a timeline cannot survive.
    """
    count = len(nodes)
    axis_y = rect.top + rect.height / 2
    marker = min(0.14, rect.height * 0.10)
    span = rect.width - marker
    step = span / max(1, count - 1) if count > 1 else 0.0
    column = min(step * 0.94 if count > 1 else rect.width, rect.width / 2)

    out: list[Primitive] = [
        _line(f"{prefix}-axis", rect.left, axis_y, rect.left + rect.width,
              axis_y, palette.rule, CONNECTOR_PT),
    ]
    for index, node in enumerate(nodes):
        centre = rect.left + marker / 2 + index * step
        out.append(Primitive(
            id=f"{prefix}-m{index}", kind="ellipse",
            box=PositionConfig(left=centre - marker / 2, top=axis_y - marker / 2,
                               width=marker, height=marker),
            fill=palette.step(index),
        ))
        above = index % 2 == 0
        date = str(node.get("date") or "").strip()
        label = _label_of(node)
        left = min(max(rect.left, centre - column / 2),
                   rect.left + rect.width - column)

        date_h = primitives.measure_text(date or "—", detail_pt, column)
        label_h = primitives.measure_text(label, label_pt, column, bold=True)
        block = date_h + label_h + 0.02
        top = (axis_y - marker / 2 - 0.05 - block) if above else (
            axis_y + marker / 2 + 0.05)
        top = max(rect.top, min(top, rect.top + rect.height - block))

        out.append(_text(f"{prefix}-d{index}", left, top, column, date_h,
                         date, detail_pt, palette.step(index), bold=True,
                         align="center", font=palette.font))
        out.append(_text(f"{prefix}-l{index}", left, top + date_h + 0.02,
                         column, label_h, label, label_pt, palette.ink,
                         align="center", font=palette.font))
    return out


# ── venn ───────────────────────────────────────────────────────────────


def _draw_venn(nodes, rect, payload, palette, label_pt, detail_pt, prefix):
    """Two or three sets, drawn as outlines.

    Outlines rather than washes, because the primitive layer has no alpha: an
    opaque fill means the last circle drawn covers the ones before it, and a
    Venn whose overlaps are hidden is three circles in a row. A line Venn shows
    every lens, which is the only thing the shape is for.

    Labels sit outside their own circle - above the top one, below the lower
    two. Inside a lens each half has a different ground behind it and no single
    ink reads on both; in a row along the bottom they stop belonging to any
    particular circle.
    """
    count = min(3, len(nodes))
    line_h = primitives.measure_text("Ay", label_pt, rect.width, bold=True)
    band = line_h + 0.06

    if count == 2:
        diameter = min(rect.height - band, rect.width / 1.62)
    else:
        diameter = min((rect.height - band * 2) / 1.62, rect.width / 1.72)
    diameter = max(0.5, diameter)
    radius = diameter / 2
    cx = rect.left + rect.width / 2

    if count == 2:
        top = rect.top + max(0.0, (rect.height - band - diameter) / 2)
        cy = top + radius
        centres = [(cx - radius * 0.52, cy), (cx + radius * 0.52, cy)]
        # Under each circle, centred on it.
        label_tops = [top + diameter + 0.04] * 2
        label_centres = [c[0] for c in centres]
    else:
        lift = radius * 0.56
        cy = rect.top + band + radius + max(0.0, (
            rect.height - band * 2 - diameter * 1.62) / 2)
        centres = [
            (cx, cy),
            (cx - radius * 0.62, cy + lift * 1.55),
            (cx + radius * 0.62, cy + lift * 1.55),
        ]
        lowest = cy + lift * 1.55 + radius
        label_tops = [cy - radius - band, lowest + 0.04, lowest + 0.04]
        label_centres = [cx, centres[1][0] - radius * 0.34,
                         centres[2][0] + radius * 0.34]

    out: list[Primitive] = []
    for index in range(count):
        ox, oy = centres[index]
        out.append(Primitive(
            id=f"{prefix}-c{index}", kind="ellipse",
            box=PositionConfig(left=ox - radius, top=oy - radius,
                               width=diameter, height=diameter),
            stroke=palette.step(index), stroke_pt=CONNECTOR_PT * 1.4,
        ))

    column = min(diameter, rect.width / count)
    for index in range(count):
        left = min(max(rect.left, label_centres[index] - column / 2),
                   rect.left + rect.width - column)
        out.append(_text(
            f"{prefix}-lab{index}", left, label_tops[index], column, line_h,
            _label_of(nodes[index]), label_pt, palette.step(index),
            bold=True, align="center", font=palette.font,
        ))

    overlap = str(payload.get("overlap") or "").strip()
    if overlap:
        # The centroid of the circles, which is where every lens meets.
        mx = sum(c[0] for c in centres[:count]) / count
        my = sum(c[1] for c in centres[:count]) / count
        width = diameter * 0.7
        out.append(_text(
            f"{prefix}-overlap", mx - width / 2, my - line_h / 2, width, line_h,
            overlap, max(MIN_DETAIL_PT, detail_pt), palette.ink,
            align="center", font=palette.font,
        ))
    return out


# ── orgChart ───────────────────────────────────────────────────────────


def _draw_org_chart(nodes, rect, payload, palette, label_pt, detail_pt, prefix):
    """A root over its children, with elbow connectors.

    Two levels drawn as levels; anything deeper is drawn by depth, which keeps
    the layout honest without pretending to do tree balancing - a diagram that
    needs balancing is a diagram that needs fewer nodes.
    """
    levels = _by_depth(nodes)
    if not levels:
        return []

    row_h = (rect.height - GAP_IN * (len(levels) - 1)) / len(levels)
    widest = max(len(row) for row in levels)
    box_h = min(row_h, _box_height(nodes, rect.width / widest, row_h,
                                   label_pt, detail_pt))
    positions: dict[str, tuple[float, float]] = {}
    out: list[Primitive] = []

    for depth, row in enumerate(levels):
        top = rect.top + depth * (row_h + GAP_IN) + (row_h - box_h) / 2
        # A level with fewer nodes than the widest keeps their width rather
        # than stretching to fill the row: a lone root drawn the whole width of
        # the slide reads as a banner, not as the top of a hierarchy.
        column = rect.width / max(len(row), min(widest, len(row) + 1))
        width = max(0.4, column - GAP_IN)
        run = column * len(row)
        origin = rect.left + (rect.width - run) / 2
        for index, node in enumerate(row):
            left = origin + index * column + (column - width) / 2
            fill = palette.tint(depth)
            box = PositionConfig(left=left, top=top, width=width, height=box_h)
            key = f"{depth}-{index}"
            out.append(_rect(f"{prefix}-o{key}", box, fill=fill,
                             stroke=palette.step(depth), stroke_pt=HAIRLINE_PT))
            out.extend(_node_text(f"{prefix}-o{key}", box, _label_of(node),
                                  _detail_of(node), palette, label_pt,
                                  detail_pt, fill))
            positions[_label_of(node)] = (left + width / 2, top)
            positions[f"__bottom__{_label_of(node)}"] = (left + width / 2,
                                                         top + box_h)

    for depth, row in enumerate(levels[1:], start=1):
        for index, node in enumerate(row):
            parent = str(node.get("parent") or "").strip()
            child = positions.get(_label_of(node))
            above = positions.get(f"__bottom__{parent}")
            if not child or not above:
                continue
            mid = (above[1] + child[1]) / 2
            out.append(_line(f"{prefix}-e{depth}{index}-down", above[0],
                             above[1], above[0], mid, palette.rule, HAIRLINE_PT))
            out.append(_line(f"{prefix}-e{depth}{index}-across", above[0], mid,
                             child[0], mid, palette.rule, HAIRLINE_PT))
            out.append(_line(f"{prefix}-e{depth}{index}-up", child[0], mid,
                             child[0], child[1], palette.rule, HAIRLINE_PT))
    return out


def _by_depth(nodes: list[dict]) -> list[list[dict]]:
    """Nodes grouped by how far they sit from the root.

    A node whose parent is not in the list is treated as a root rather than
    dropped: losing a box silently is worse than drawing an extra one, and the
    builder has already refused the cases where that would be wrong.
    """
    by_label = {_label_of(n): n for n in nodes if _label_of(n)}
    depths: dict[str, int] = {}

    def depth_of(node: dict, seen: set[str]) -> int:
        label = _label_of(node)
        if label in depths:
            return depths[label]
        parent = str(node.get("parent") or "").strip()
        if not parent or parent not in by_label or parent in seen:
            depths[label] = 0
        else:
            depths[label] = depth_of(by_label[parent], seen | {label}) + 1
        return depths[label]

    for node in nodes:
        depth_of(node, set())

    deepest = max(depths.values(), default=0)
    return [
        [n for n in nodes if depths.get(_label_of(n)) == level]
        for level in range(deepest + 1)
    ]


def _number(value: object) -> Optional[float]:
    """A stage value as a number, or None when it is prose."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value or "").strip().replace(",", "").replace("%", "")
    text = text.lstrip("$£€")
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


_DRAWERS = {
    "flow": _draw_flow,
    "valueChain": _draw_value_chain,
    "matrix2x2": _draw_matrix,
    "funnel": _draw_funnel,
    "pyramid": _draw_pyramid,
    "timeline": _draw_timeline,
    "venn": _draw_venn,
    "orgChart": _draw_org_chart,
}
