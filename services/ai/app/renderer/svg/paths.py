"""
An SVG path, flattened to the line segments PowerPoint can draw.

`build_freeform` takes line segments and nothing else, so every curve has to
become one. Flattening is done by adaptive subdivision rather than at a fixed
step: a gentle curve needs three segments and a tight one needs thirty, and a
fixed step either wastes points on the first or visibly facets the second.

The tolerance is in the path's own units and converted at the call site, so a
curve that will be drawn an inch wide is flattened to an inch's worth of
precision rather than to a slide's.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# How far a flattened segment may sit from the true curve, as a fraction of
# the figure's own size. A quarter of a percent is below what shows at any
# size a slide is read at.
FLATNESS = 0.0025

# A curve is never divided past this, so a pathological control polygon cannot
# produce a shape with tens of thousands of points that PowerPoint then has to
# open.
MAX_DEPTH = 10

_TOKEN = re.compile(r"[MmZzLlHhVvCcSsQqTtAa]|-?\d*\.?\d+(?:[eE][-+]?\d+)?")


@dataclass
class SubPath:
    """One continuous run of a path, and whether it closes."""

    points: list[tuple[float, float]] = field(default_factory=list)
    closed: bool = False


def _flatten_cubic(p0, p1, p2, p3, tolerance, depth, out):
    """Subdivide until the control polygon is within tolerance of the chord."""
    if depth >= MAX_DEPTH:
        out.append(p3)
        return
    # Distance of each control point from the chord p0-p3. The standard
    # flatness test: when both are close, a straight line is indistinguishable
    # from the curve.
    x0, y0 = p0
    x3, y3 = p3
    dx, dy = x3 - x0, y3 - y0
    span = math.hypot(dx, dy)
    if span < 1e-12:
        far = max(math.hypot(p1[0] - x0, p1[1] - y0),
                  math.hypot(p2[0] - x0, p2[1] - y0))
    else:
        far = max(
            abs((p1[0] - x0) * dy - (p1[1] - y0) * dx) / span,
            abs((p2[0] - x0) * dy - (p2[1] - y0) * dx) / span,
        )
    if far <= tolerance:
        out.append(p3)
        return

    def mid(a, b):
        return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)

    p01, p12, p23 = mid(p0, p1), mid(p1, p2), mid(p2, p3)
    p012, p123 = mid(p01, p12), mid(p12, p23)
    centre = mid(p012, p123)
    _flatten_cubic(p0, p01, p012, centre, tolerance, depth + 1, out)
    _flatten_cubic(centre, p123, p23, p3, tolerance, depth + 1, out)


def _quadratic_to_cubic(p0, p1, p2):
    """A quadratic curve as the cubic that traces exactly the same path."""
    return (
        (p0[0] + 2.0 / 3.0 * (p1[0] - p0[0]), p0[1] + 2.0 / 3.0 * (p1[1] - p0[1])),
        (p2[0] + 2.0 / 3.0 * (p1[0] - p2[0]), p2[1] + 2.0 / 3.0 * (p1[1] - p2[1])),
    )


def _arc_to_cubics(start, rx, ry, rotation, large_arc, sweep, end):
    """An elliptical arc as cubic segments, per the SVG implementation notes."""
    if rx == 0 or ry == 0 or start == end:
        return []
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(rotation)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)
    dx2 = (start[0] - end[0]) / 2.0
    dy2 = (start[1] - end[1]) / 2.0
    x1 = cos_phi * dx2 + sin_phi * dy2
    y1 = -sin_phi * dx2 + cos_phi * dy2

    # Grow the radii when they are too small to reach, which the spec requires
    # rather than treating as an error.
    lam = (x1 * x1) / (rx * rx) + (y1 * y1) / (ry * ry)
    if lam > 1:
        scale = math.sqrt(lam)
        rx, ry = rx * scale, ry * scale

    numerator = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    denominator = rx * rx * y1 * y1 + ry * ry * x1 * x1
    factor = math.sqrt(max(0.0, numerator / denominator)) if denominator else 0.0
    if large_arc == sweep:
        factor = -factor
    cx1 = factor * rx * y1 / ry
    cy1 = -factor * ry * x1 / rx
    cx = cos_phi * cx1 - sin_phi * cy1 + (start[0] + end[0]) / 2.0
    cy = sin_phi * cx1 + cos_phi * cy1 + (start[1] + end[1]) / 2.0

    def angle(ux, uy, vx, vy):
        dot = ux * vx + uy * vy
        norm = math.hypot(ux, uy) * math.hypot(vx, vy)
        if norm == 0:
            return 0.0
        value = max(-1.0, min(1.0, dot / norm))
        sign = -1.0 if ux * vy - uy * vx < 0 else 1.0
        return sign * math.acos(value)

    theta = angle(1, 0, (x1 - cx1) / rx, (y1 - cy1) / ry)
    delta = angle((x1 - cx1) / rx, (y1 - cy1) / ry,
                  (-x1 - cx1) / rx, (-y1 - cy1) / ry)
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi

    segments = max(1, int(math.ceil(abs(delta) / (math.pi / 2))))
    step = delta / segments
    alpha = 4.0 / 3.0 * math.tan(step / 4.0)
    cubics = []
    current = start
    for index in range(segments):
        t1 = theta + index * step
        t2 = t1 + step

        def point(t):
            x = cx + rx * math.cos(t) * cos_phi - ry * math.sin(t) * sin_phi
            y = cy + rx * math.cos(t) * sin_phi + ry * math.sin(t) * cos_phi
            return (x, y)

        def derivative(t):
            dx = -rx * math.sin(t) * cos_phi - ry * math.cos(t) * sin_phi
            dy = -rx * math.sin(t) * sin_phi + ry * math.cos(t) * cos_phi
            return (dx, dy)

        p2 = point(t2)
        d1, d2 = derivative(t1), derivative(t2)
        c1 = (current[0] + alpha * d1[0], current[1] + alpha * d1[1])
        c2 = (p2[0] - alpha * d2[0], p2[1] - alpha * d2[1])
        cubics.append((current, c1, c2, p2))
        current = p2
    return cubics


def parse(d: str, *, tolerance: float = 0.0) -> list[SubPath]:
    """Every subpath of this `d` attribute, as points.

    Unsupported and malformed commands stop that subpath rather than raising:
    half a figure drawn is better than a deck that fails, and what was dropped
    is visible to whoever looks at the result.
    """
    tokens = _TOKEN.findall(d or "")
    subpaths: list[SubPath] = []
    current: SubPath | None = None
    position = (0.0, 0.0)
    start_of_subpath = (0.0, 0.0)
    last_control: tuple[float, float] | None = None
    command = ""
    index = 0

    def number() -> float:
        nonlocal index
        value = float(tokens[index])
        index += 1
        return value

    while index < len(tokens):
        token = tokens[index]
        if token.isalpha():
            command = token
            index += 1
            if command in "Zz":
                if current is not None:
                    current.closed = True
                    position = start_of_subpath
                continue
        elif not command:
            break

        relative = command.islower()
        upper = command.upper()
        try:
            if upper == "M":
                x, y = number(), number()
                if relative:
                    x, y = position[0] + x, position[1] + y
                current = SubPath(points=[(x, y)])
                subpaths.append(current)
                position = start_of_subpath = (x, y)
                command = "l" if relative else "L"
                last_control = None
                continue
            if current is None:
                current = SubPath(points=[position])
                subpaths.append(current)
            if upper == "L":
                x, y = number(), number()
                if relative:
                    x, y = position[0] + x, position[1] + y
                current.points.append((x, y))
                position = (x, y)
                last_control = None
            elif upper == "H":
                x = number()
                x = position[0] + x if relative else x
                current.points.append((x, position[1]))
                position = (x, position[1])
                last_control = None
            elif upper == "V":
                y = number()
                y = position[1] + y if relative else y
                current.points.append((position[0], y))
                position = (position[0], y)
                last_control = None
            elif upper in ("C", "S"):
                if upper == "C":
                    c1 = (number(), number())
                    c2 = (number(), number())
                else:
                    c1 = (
                        2 * position[0] - last_control[0] if last_control else position[0],
                        2 * position[1] - last_control[1] if last_control else position[1],
                    )
                    c2 = (number(), number())
                    if relative:
                        c2 = (position[0] + c2[0], position[1] + c2[1])
                end = (number(), number())
                if relative:
                    if upper == "C":
                        c1 = (position[0] + c1[0], position[1] + c1[1])
                        c2 = (position[0] + c2[0], position[1] + c2[1])
                    end = (position[0] + end[0], position[1] + end[1])
                _flatten_cubic(position, c1, c2, end, tolerance, 0, current.points)
                last_control = c2
                position = end
            elif upper in ("Q", "T"):
                if upper == "Q":
                    control = (number(), number())
                    if relative:
                        control = (position[0] + control[0], position[1] + control[1])
                else:
                    control = (
                        2 * position[0] - last_control[0] if last_control else position[0],
                        2 * position[1] - last_control[1] if last_control else position[1],
                    )
                end = (number(), number())
                if relative:
                    end = (position[0] + end[0], position[1] + end[1])
                c1, c2 = _quadratic_to_cubic(position, control, end)
                _flatten_cubic(position, c1, c2, end, tolerance, 0, current.points)
                last_control = control
                position = end
            elif upper == "A":
                rx, ry, rotation = number(), number(), number()
                large_arc, sweep = int(number()), int(number())
                end = (number(), number())
                if relative:
                    end = (position[0] + end[0], position[1] + end[1])
                for p0, c1, c2, p3 in _arc_to_cubics(
                        position, rx, ry, rotation, large_arc, sweep, end):
                    _flatten_cubic(p0, c1, c2, p3, tolerance, 0, current.points)
                position = end
                last_control = None
            else:
                break
        except (IndexError, ValueError):
            break

    return [sub for sub in subpaths if len(sub.points) >= 2]
