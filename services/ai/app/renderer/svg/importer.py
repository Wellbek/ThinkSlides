"""
An SVG document, read as native PowerPoint shapes.

`build_diagram` offers eight typed kinds and the primitive layer beneath it
had five, none of them a path - so there was no lower level to drop to when a
figure was not one of the eight. A state machine, a sequence diagram or a
bespoke quadrant simply could not be drawn.

This is the level underneath. Both consumers - Mermaid, which renders to SVG,
and a figure the model draws itself - arrive here, which is why it is its own
thing rather than part of either.

Three decisions.

**Shapes, not a picture.** Paths become freeform shapes and text stays text,
so a user can select an element, recolour it and edit its label. Rasterising
would be easier and would make the import pointless: the deck already renders
pictures.

**The pack's palette, not the SVG's.** A colour in the source is matched to
the nearest role of the deck's own palette, so an imported figure is in the
deck's colours rather than in whatever the generator happened to emit. A
figure that arrives in Mermaid's default lilac and stays lilac is a figure
that looks pasted in.

**Nothing executes.** `<script>`, `<foreignObject>` and every external
reference are dropped. An SVG can arrive from a model, from an MCP server a
customer configured, or from a file a user uploaded, and none of those are
places to run code from.
"""

from __future__ import annotations

import colorsys
import math
import re
from dataclasses import dataclass
from typing import Optional
from xml.etree import ElementTree

from app.renderer.layout import PositionConfig
from app.renderer.primitives import Primitive, TextRunSpec
from app.renderer.svg.paths import FLATNESS, SubPath, parse

SVG_NS = "http://www.w3.org/2000/svg"

# Never imported, whatever they contain. A script is obvious; a foreignObject
# is an HTML document smuggled into a picture, and an external href is a
# request to a host we have not vetted made while rendering a customer's deck.
FORBIDDEN_TAGS = frozenset({"script", "foreignObject", "iframe", "image",
                            "use", "animate", "animateTransform", "set"})

# Elements that define something drawn elsewhere and are never drawn where
# they stand. Walked into, a sequence diagram's arrowhead definitions were
# drawn as black shapes at the diagram's top-left corner (#350).
DEFINITION_TAGS = frozenset({"defs", "marker", "symbol", "clipPath", "mask",
                             "pattern", "linearGradient", "radialGradient"})

_LENGTH = re.compile(r"^\s*(-?\d*\.?\d+)\s*(px|pt|mm|cm|in|%)?\s*$")
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

_NAMED = {
    "black": "#000000", "white": "#FFFFFF", "red": "#FF0000",
    "green": "#008000", "blue": "#0000FF", "grey": "#808080",
    "gray": "#808080", "silver": "#C0C0C0", "navy": "#000080",
    "orange": "#FFA500", "yellow": "#FFFF00", "purple": "#800080",
}


class UnsupportedSVG(ValueError):
    """The document cannot be imported as shapes, and why."""


@dataclass(frozen=True)
class ViewBox:
    """The coordinate system the document draws in."""

    x: float = 0.0
    y: float = 0.0
    width: float = 1.0
    height: float = 1.0

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 1.0


# The identity transform, as (a, b, c, d, e, f) in SVG's own order:
#   x' = a.x + c.y + e
#   y' = b.x + d.y + f
IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

_TRANSFORM = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")


def _multiply(outer, inner):
    """Two transforms, composed. Outer applies after inner, as SVG nests."""
    a1, b1, c1, d1, e1, f1 = outer
    a2, b2, c2, d2, e2, f2 = inner
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def parse_transform(value):
    """An SVG `transform` attribute as a matrix.

    Mermaid positions every node and every edge label with nested
    `<g transform="translate(...)">`, so a reader that ignores transforms
    imports a diagram with everything piled at the origin. Real-world SVG uses
    them everywhere; this is not a Mermaid concession.
    """
    result = IDENTITY
    for name, raw in _TRANSFORM.findall(value or ""):
        numbers = [float(n) for n in
                   re.findall(r"-?\d*\.?\d+(?:[eE][-+]?\d+)?", raw)]
        if name == "matrix" and len(numbers) >= 6:
            step = tuple(numbers[:6])
        elif name == "translate" and numbers:
            step = (1.0, 0.0, 0.0, 1.0, numbers[0],
                    numbers[1] if len(numbers) > 1 else 0.0)
        elif name == "scale" and numbers:
            sx = numbers[0]
            sy = numbers[1] if len(numbers) > 1 else sx
            step = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif name == "rotate" and numbers:
            angle = math.radians(numbers[0])
            cos, sin = math.cos(angle), math.sin(angle)
            step = (cos, sin, -sin, cos, 0.0, 0.0)
            if len(numbers) >= 3:
                cx, cy = numbers[1], numbers[2]
                step = _multiply(
                    (1.0, 0.0, 0.0, 1.0, cx, cy),
                    _multiply(step, (1.0, 0.0, 0.0, 1.0, -cx, -cy)))
        elif name == "skewX" and numbers:
            step = (1.0, 0.0, math.tan(math.radians(numbers[0])), 1.0, 0.0, 0.0)
        elif name == "skewY" and numbers:
            step = (1.0, math.tan(math.radians(numbers[0])), 0.0, 1.0, 0.0, 0.0)
        else:
            continue
        result = _multiply(result, step)
    return result


def apply_transform(matrix, point):
    a, b, c, d, e, f = matrix
    x, y = point
    return (a * x + c * y + e, b * x + d * y + f)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _length(value: Optional[str], default: float = 0.0) -> float:
    match = _LENGTH.match(value or "")
    if not match:
        return default
    number = float(match.group(1))
    unit = match.group(2)
    # Everything is in the document's own units; the caller scales the whole
    # figure into its box, so absolute units only matter relative to one
    # another.
    return number * {"pt": 1.0, "in": 72.0, "mm": 2.8346, "cm": 28.346}.get(
        unit or "px", 1.0)


def read_viewbox(root: ElementTree.Element) -> ViewBox:
    """The document's coordinate system, from viewBox or from width/height."""
    raw = (root.get("viewBox") or "").replace(",", " ").split()
    if len(raw) == 4:
        try:
            x, y, width, height = (float(v) for v in raw)
            if width > 0 and height > 0:
                return ViewBox(x, y, width, height)
        except ValueError:
            pass
    width = _length(root.get("width"), 0.0)
    height = _length(root.get("height"), 0.0)
    if width > 0 and height > 0:
        return ViewBox(0.0, 0.0, width, height)
    raise UnsupportedSVG(
        "this SVG declares no viewBox and no usable width and height, so "
        "there is no way to know what shape it is or how to scale it"
    )


def _colour(value: Optional[str]) -> Optional[str]:
    """A paint value as a hex colour, or None for 'none' and the unparseable."""
    text = (value or "").strip().lower()
    if not text or text in ("none", "transparent", "currentcolor"):
        return None
    if text.startswith("url("):
        # A gradient or pattern reference. Not resolved, and not guessed at:
        # a figure drawn in a colour we invented is worse than one drawn in
        # the pack's own.
        return None
    if _HEX.match(text):
        if len(text) == 4:
            return "#" + "".join(ch * 2 for ch in text[1:]).upper()
        return text.upper()
    if text in _NAMED:
        return _NAMED[text]
    match = re.match(r"rgba?\(([^)]+)\)", text)
    if match:
        parts = [p.strip() for p in match.group(1).replace("/", ",").split(",")]
        try:
            r, g, b = (int(float(p.rstrip("%")) * (2.55 if "%" in p else 1))
                       for p in parts[:3])
            return "#{:02X}{:02X}{:02X}".format(
                max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))
        except ValueError:
            return None
    match = re.match(r"hsla?\(([^)]+)\)", text)
    if match:
        # Mermaid writes its derived colours this way - a note's border is
        # `hsl(52.68, 60%, 73.92%)` - and an unreadable stroke is no stroke,
        # which left a sequence diagram's notes without a box (#350).
        parts = [p for p in re.split(r"[\s,/]+", match.group(1).strip()) if p]
        try:
            hue = float(parts[0].removesuffix("deg")) / 360.0 % 1.0
            sat, light = (float(p.rstrip("%")) / 100.0 for p in parts[1:3])
        except (ValueError, IndexError):
            return None
        r, g, b = colorsys.hls_to_rgb(hue, light, sat)
        return "#{:02X}{:02X}{:02X}".format(round(r * 255), round(g * 255), round(b * 255))
    return None


# How much colour a value needs before it is worth remapping. Below this it is
# a grey, and a grey is doing a structural job.
MIN_CHROMA = 24


def _is_neutral(value: str) -> bool:
    try:
        r = int(value[1:3], 16)
        g = int(value[3:5], 16)
        b = int(value[5:7], 16)
    except (ValueError, IndexError):
        return False
    return max(r, g, b) - min(r, g, b) < MIN_CHROMA


def paint_of(value: Optional[str],
             palette: Optional[dict[str, str]]) -> Optional[str]:
    """A paint value, resolving a palette role name before anything else.

    `draw_svg` asks the model to colour a figure by naming one of the deck's
    roles - `fill="accent"` - so that a figure is in the deck's palette by
    construction rather than by a model guessing hex values and producing a
    deck of figures each in a slightly different blue. Honouring that contract
    is this function; without it every role name fell through to black.
    """
    text = (value or "").strip()
    if palette and text in palette:
        return _colour(palette[text])
    return _colour(text)


_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.S)

PAINT_PROPERTIES = ("fill", "stroke", "stroke-width", "font-size",
                    "font-family", "font-weight", "text-anchor",
                    "fill-opacity", "opacity", "color")


def read_stylesheet(root: ElementTree.Element) -> dict[str, dict[str, str]]:
    """Class rules from the document's own `<style>` blocks.

    Mermaid paints almost nothing with presentation attributes - its nodes and
    edges are styled by class - so a reader that looks only at attributes
    imports a diagram in which every shape defaulted to black and every label
    lost the contrast it was drawn for.

    Two selector forms are read: a bare class, and a class scoped to the
    document's own root id - `#d .node`, which is the only form Mermaid emits
    and which every element in that document matches anyway. Anything deeper
    is skipped: a descendant or attribute selector needs a real cascade to
    resolve, and half a cascade applied confidently is worse than none.
    """
    root_id = root.get("id") or ""
    sheet: dict[str, dict[str, str]] = {}
    for element in root.iter():
        if _local(element.tag) != "style":
            continue
        for selectors, body in _RULE.findall(element.text or ""):
            declarations = {}
            for declaration in body.split(";"):
                if ":" in declaration:
                    key, _, value = declaration.partition(":")
                    key = key.strip()
                    if key in PAINT_PROPERTIES:
                        declarations[key] = value.strip().rstrip("!important").strip()
            if not declarations:
                continue
            for selector in selectors.split(","):
                selector = selector.strip()
                if root_id and selector.startswith(f"#{root_id} "):
                    selector = selector[len(root_id) + 2:].strip()
                if re.fullmatch(r"\.[A-Za-z0-9_-]+", selector):
                    sheet.setdefault(selector[1:], {}).update(declarations)
    return sheet


def _style(element: ElementTree.Element,
           sheet: Optional[dict[str, dict[str, str]]] = None,
           inherited: Optional[dict[str, str]] = None) -> dict[str, str]:
    """What paints this element: inherited, then attribute, then class, then
    inline - which is the order CSS itself resolves them in.

    A presentation attribute is weaker than any stylesheet rule. Applied the
    other way round, Mermaid's message lines - drawn `stroke="none"` and
    coloured by their class - imported as invisible, and a sequence diagram
    lost every arrow (#350).
    """
    out = dict(inherited or {})
    out.update({key: value for key, value in element.attrib.items()
                if key in PAINT_PROPERTIES})
    for name in (element.get("class") or "").split():
        out.update((sheet or {}).get(name, {}))
    for declaration in (element.get("style") or "").split(";"):
        if ":" in declaration:
            key, _, value = declaration.partition(":")
            if key.strip() in PAINT_PROPERTIES:
                out[key.strip()] = value.strip()
    return out


def recolour(value: Optional[str], palette: Optional[dict[str, str]]) -> Optional[str]:
    """The pack colour nearest this one, so a figure is in the deck's palette.

    Left alone when the deck has no palette, and when the colour is black or
    white - a rule and a knockout label are structural rather than branded,
    and mapping them onto an accent is how a diagram ends up with orange
    hairlines.
    """
    if not value or not palette:
        return value
    if _is_neutral(value):
        # A rule, a hairline, a knockout label. Structural rather than
        # branded, and mapping a grey connector onto an accent is how a
        # diagram ends up with green hairlines that mean nothing.
        return value
    from app.measure.color_roles import nearest_role

    role = nearest_role(value, palette)
    return palette.get(role, value) if role else value


def _resolve(value: Optional[str],
             palette: Optional[dict[str, str]]) -> Optional[str]:
    """The colour this paint value means, in the deck's palette."""
    text = (value or "").strip()
    if palette and text in palette:
        return _colour(palette[text])
    return recolour(_colour(text), palette)


def _points_in(subpath: SubPath, box: ViewBox) -> tuple[tuple[float, float], ...]:
    """A subpath's points as fractions of the viewBox, y already downward."""
    return tuple(
        ((x - box.x) / box.width, (y - box.y) / box.height)
        for x, y in subpath.points
    )


def _rect_path(element: ElementTree.Element) -> list[SubPath]:
    x, y = _length(element.get("x"), 0.0), _length(element.get("y"), 0.0)
    width = _length(element.get("width"), 0.0)
    height = _length(element.get("height"), 0.0)
    if width <= 0 or height <= 0:
        return []
    return [SubPath(points=[(x, y), (x + width, y), (x + width, y + height),
                            (x, y + height)], closed=True)]


def _ellipse_path(element: ElementTree.Element, tolerance: float) -> list[SubPath]:
    if _local(element.tag) == "circle":
        rx = ry = _length(element.get("r"), 0.0)
    else:
        rx = _length(element.get("rx"), 0.0)
        ry = _length(element.get("ry"), 0.0)
    cx, cy = _length(element.get("cx"), 0.0), _length(element.get("cy"), 0.0)
    if rx <= 0 or ry <= 0:
        return []
    d = (f"M{cx - rx},{cy} A{rx},{ry} 0 1 0 {cx + rx},{cy} "
         f"A{rx},{ry} 0 1 0 {cx - rx},{cy} Z")
    return parse(d, tolerance=tolerance)


def _polygon_path(element: ElementTree.Element, close: bool) -> list[SubPath]:
    raw = (element.get("points") or "").replace(",", " ").split()
    points = []
    for index in range(0, len(raw) - 1, 2):
        try:
            points.append((float(raw[index]), float(raw[index + 1])))
        except ValueError:
            break
    if len(points) < 2:
        return []
    return [SubPath(points=points, closed=close)]


def _line_path(element: ElementTree.Element) -> list[SubPath]:
    return [SubPath(points=[
        (_length(element.get("x1"), 0.0), _length(element.get("y1"), 0.0)),
        (_length(element.get("x2"), 0.0), _length(element.get("y2"), 0.0)),
    ])]


def _text_of(element: ElementTree.Element) -> str:
    """Everything this element says, tspans included."""
    parts = [element.text or ""]
    for child in element:
        if _local(child.tag) in ("tspan", "textPath"):
            parts.append(_text_of(child))
        parts.append(child.tail or "")
    return " ".join(part.strip() for part in parts if part.strip())


def to_primitives(svg: str, box: PositionConfig, *,
                  palette: Optional[dict[str, str]] = None,
                  prefix: str = "svg",
                  body_font: str = "Arial") -> list[Primitive]:
    """Every shape and label in this document, placed inside ``box``.

    The document is scaled to fit the box without distorting it: an SVG has a
    shape of its own, and stretching a diagram to a slot's aspect is how a
    circle becomes an ellipse and a square node becomes a lozenge.
    """
    try:
        root = ElementTree.fromstring(svg)
    except ElementTree.ParseError as exc:
        raise UnsupportedSVG(f"this is not parseable as XML: {exc}") from exc
    if _local(root.tag) != "svg":
        raise UnsupportedSVG(
            f"the root element is <{_local(root.tag)}>, not <svg>")

    view = read_viewbox(root)
    # Fit without distorting, and centre what is left over.
    scale = min(box.width / view.aspect, box.height) if view.aspect else box.height
    drawn_height = scale
    drawn_width = scale * view.aspect
    frame = PositionConfig(
        left=box.left + (box.width - drawn_width) / 2.0,
        top=box.top + (box.height - drawn_height) / 2.0,
        width=drawn_width, height=drawn_height,
    )
    tolerance = max(view.width, view.height) * FLATNESS

    sheet = read_stylesheet(root)
    primitives: list[Primitive] = []
    for index, (element, matrix, style) in enumerate(
            _walk(root, IDENTITY, sheet, {})):
        tag = _local(element.tag)
        # A role name is already the deck's colour, so it is not remapped;
        # anything else is matched to the nearest role.
        fill = _resolve(style.get("fill"), palette)
        stroke = _resolve(style.get("stroke"), palette)

        if tag == "text":
            text = _text_of(element)
            if not text:
                continue
            primitives.append(_text_primitive(
                element, text, frame, view, style, fill or "#000000",
                f"{prefix}-t{index}", body_font, matrix=matrix,
                ground=_ground_under(element, root, view, palette,
                                     sheet)))
            continue

        subpaths = _subpaths_for(tag, element, tolerance)
        if not subpaths:
            continue
        subpaths = [
            SubPath(points=[apply_transform(matrix, p) for p in sub.points],
                    closed=sub.closed)
            for sub in subpaths
        ]
        if fill is None and stroke is None:
            # An SVG with no paint attribute fills black by default, and a
            # shape drawn in nothing at all is invisible rather than absent.
            fill = "#000000"
        primitives.append(Primitive(
            id=f"{prefix}-p{index}", kind="path", box=frame,
            fill=fill, stroke=stroke,
            stroke_pt=_length(style.get("stroke-width"), 1.0),
            subpaths=tuple(_points_in(s, view) for s in subpaths),
            closed=tuple(s.closed for s in subpaths),
        ))
        heads = _arrowheads(element, subpaths, _length(style.get("stroke-width"), 1.0))
        for n, head in enumerate(heads):
            primitives.append(Primitive(
                id=f"{prefix}-p{index}-head{n}", kind="path", box=frame,
                fill=stroke or fill or "#000000", stroke=None, stroke_pt=0.0,
                subpaths=(_points_in(head, view),), closed=(True,),
            ))
    return primitives


# An arrowhead's length, in multiples of the line's stroke width, and never
# less than this many document units, so a hairline still points somewhere.
ARROWHEAD_STROKES = 5.0
ARROWHEAD_MIN = 6.0


def _arrowheads(element: ElementTree.Element, subpaths: list[SubPath],
                stroke_width: float) -> list[SubPath]:
    """A filled triangle at each end a marker is attached to.

    Markers are definitions, which are not drawn where they stand (see
    DEFINITION_TAGS); what one says is "this end is an arrow", and that is
    drawn here, along the line's own last segment, in the line's colour.
    """
    ends = []
    if element.get("marker-end") or "marker-end" in (element.get("style") or ""):
        ends.append("end")
    if element.get("marker-start") or "marker-start" in (element.get("style") or ""):
        ends.append("start")
    open_paths = [sub for sub in subpaths if len(sub.points) >= 2]
    if not ends or not open_paths:
        return []
    length = max(ARROWHEAD_MIN, ARROWHEAD_STROKES * stroke_width)
    heads = []
    for end in ends:
        points = open_paths[-1].points if end == "end" else open_paths[0].points[::-1]
        tip, back = points[-1], next(
            (p for p in reversed(points[:-1]) if p != points[-1]), None)
        if back is None:
            continue
        dx, dy = tip[0] - back[0], tip[1] - back[1]
        norm = (dx * dx + dy * dy) ** 0.5
        ux, uy = dx / norm, dy / norm
        base = (tip[0] - ux * length, tip[1] - uy * length)
        half = length * 0.45
        heads.append(SubPath(points=[
            tip,
            (base[0] - uy * half, base[1] + ux * half),
            (base[0] + uy * half, base[1] - ux * half),
        ], closed=True))
    return heads


# Properties a child inherits from its parent when it says nothing itself,
# which is how an SVG group colours everything inside it at once.
INHERITED = ("fill", "stroke", "stroke-width", "font-size", "font-family",
             "font-weight", "text-anchor", "color")


def _walk(element: ElementTree.Element, matrix, sheet, inherited):
    """Every element worth drawing, with the transform and paint that reach it.

    A forbidden element takes its whole subtree with it, which is the point of
    walking rather than iterating: a `<text>` inside a `<foreignObject>` is
    smuggled HTML, not a label.
    """
    tag = _local(element.tag)
    if tag in FORBIDDEN_TAGS or tag in DEFINITION_TAGS:
        return
    here = _multiply(matrix, parse_transform(element.get("transform")))
    style = _style(element, sheet, inherited)
    yield element, here, style
    passed = {key: value for key, value in style.items() if key in INHERITED}
    for child in element:
        yield from _walk(child, here, sheet, passed)


def _subpaths_for(tag: str, element: ElementTree.Element,
                  tolerance: float) -> list[SubPath]:
    if tag == "path":
        return parse(element.get("d") or "", tolerance=tolerance)
    if tag == "rect":
        return _rect_path(element)
    if tag in ("circle", "ellipse"):
        return _ellipse_path(element, tolerance)
    if tag == "polygon":
        return _polygon_path(element, close=True)
    if tag == "polyline":
        return _polygon_path(element, close=False)
    if tag == "line":
        return _line_path(element)
    return []


def _ground_under(text_element, root, view: ViewBox,
                  palette: Optional[dict[str, str]],
                  sheet: Optional[dict] = None) -> Optional[str]:
    """What a label sits on, so the contrast guard judges it correctly.

    A white label over a green node is readable; told the slide is white, the
    guard flips it to dark and the node loses its knockout type. The filled
    shape whose box contains the label's anchor is what it sits on.
    """
    x = _length(text_element.get("x"), 0.0)
    y = _length(text_element.get("y"), 0.0)
    found = None
    for element in root.iter():
        tag = _local(element.tag)
        if tag not in ("rect", "circle", "ellipse", "polygon", "path"):
            continue
        fill = _resolve(_style(element, sheet).get("fill"), palette)
        if not fill:
            continue
        bounds = _bounds_of(tag, element)
        if bounds and bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]:
            found = fill
    return found


def _bounds_of(tag: str, element) -> Optional[tuple[float, float, float, float]]:
    if tag == "rect":
        x, y = _length(element.get("x"), 0.0), _length(element.get("y"), 0.0)
        w, h = _length(element.get("width"), 0.0), _length(element.get("height"), 0.0)
        return (x, y, x + w, y + h) if w > 0 and h > 0 else None
    if tag in ("circle", "ellipse"):
        cx, cy = _length(element.get("cx"), 0.0), _length(element.get("cy"), 0.0)
        if tag == "circle":
            rx = ry = _length(element.get("r"), 0.0)
        else:
            rx, ry = _length(element.get("rx"), 0.0), _length(element.get("ry"), 0.0)
        return (cx - rx, cy - ry, cx + rx, cy + ry) if rx > 0 and ry > 0 else None
    subpaths = _subpaths_for(tag, element, 1.0)
    points = [p for sub in subpaths for p in sub.points]
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _text_primitive(element, text, frame, view, style, colour,
                    identifier, body_font, matrix=IDENTITY,
                    ground=None) -> Primitive:
    """A label, as live text rather than as an outline of one."""
    x, y = apply_transform(matrix, (_length(element.get("x"), 0.0),
                                    _length(element.get("y"), 0.0)))
    size = _length(style.get("font-size"), 12.0)
    anchor = (style.get("text-anchor") or "start").strip()
    # SVG positions text by its baseline; a PowerPoint box is positioned by
    # its top, so the box is lifted by roughly the ascent.
    left = (x - view.x) / view.width
    top = max(0.0, (y - view.y - size * 0.8) / view.height)
    width = 0.6
    if anchor == "middle":
        left -= width / 2.0
    elif anchor == "end":
        left -= width
    scale = frame.height / view.height if view.height else 1.0
    return Primitive(
        id=identifier, kind="text",
        box=PositionConfig(
            left=frame.left + left * frame.width,
            top=frame.top + top * frame.height,
            width=width * frame.width,
            height=max(0.12, size * scale * 1.4),
        ),
        runs=(TextRunSpec(text=text),),
        align={"middle": "center", "end": "right"}.get(anchor, "left"),
        anchor="top", wrap=False, autofit=False,
        size_pt=max(6.0, size * scale * 72.0),
        color=colour,
        font=(style.get("font-family") or body_font).split(",")[0].strip(" '\""),
        bold=str(style.get("font-weight") or "").strip() in ("bold", "600",
                                                             "700", "800", "900"),
        ground=ground,
        alt_text=text,
    )


def legible_height_in(svg: str, width_in: float, legible_pt: float) -> float:
    """How tall a slot this wide must be for this SVG's smallest label to be
    drawn at ``legible_pt``.

    The document is scaled to fit its slot without distortion, and its text
    scales with it: a label is drawn at its font size times the drawn height
    over the viewBox height (see `_text_primitive`). A slot that fits the
    document at a smaller scale sets every label smaller, and a sequence
    diagram given a short band under the copy came out at about a third of
    its height with labels too small to read (#350). Infinite when the
    document at that height would be wider than the slot, since a wider
    slot is then the only way to read it; zero when it has no text.
    """
    try:
        root = ElementTree.fromstring(svg)
        view = read_viewbox(root)
    except (ElementTree.ParseError, UnsupportedSVG):
        return 0.0
    sheet = read_stylesheet(root)
    sizes = [_length(style.get("font-size"), 12.0)
             for element, _, style in _walk(root, IDENTITY, sheet, {})
             if _local(element.tag) == "text" and _text_of(element)]
    if not sizes or not view.height or min(sizes) <= 0:
        return 0.0
    needed = legible_pt * view.height / (min(sizes) * 72.0)
    if needed * view.aspect > width_in + 1e-6:
        return math.inf
    return needed
