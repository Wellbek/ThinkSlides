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


def _style(element: ElementTree.Element) -> dict[str, str]:
    """Presentation attributes and the `style` attribute, merged."""
    out = {key: value for key, value in element.attrib.items()
           if key in ("fill", "stroke", "stroke-width", "font-size",
                      "font-family", "font-weight", "text-anchor",
                      "fill-opacity", "opacity")}
    for declaration in (element.get("style") or "").split(";"):
        if ":" in declaration:
            key, _, value = declaration.partition(":")
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

    primitives: list[Primitive] = []
    for index, element in enumerate(root.iter()):
        tag = _local(element.tag)
        if tag in FORBIDDEN_TAGS:
            continue
        if any(_local(a.tag) in FORBIDDEN_TAGS for a in _ancestors(root, element)):
            continue
        style = _style(element)
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
                f"{prefix}-t{index}", body_font,
                ground=_ground_under(element, root, view, palette)))
            continue

        subpaths = _subpaths_for(tag, element, tolerance)
        if not subpaths:
            continue
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
    return primitives


def _ancestors(root: ElementTree.Element,
               target: ElementTree.Element) -> list[ElementTree.Element]:
    """The chain above an element, so a child of <script> is dropped too."""
    parents = {child: parent for parent in root.iter() for child in parent}
    chain = []
    current = parents.get(target)
    while current is not None:
        chain.append(current)
        current = parents.get(current)
    return chain


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
                  palette: Optional[dict[str, str]]) -> Optional[str]:
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
        fill = _resolve(_style(element).get("fill"), palette)
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
                    identifier, body_font, ground=None) -> Primitive:
    """A label, as live text rather than as an outline of one."""
    x = _length(element.get("x"), 0.0)
    y = _length(element.get("y"), 0.0)
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
