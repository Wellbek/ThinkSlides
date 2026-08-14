"""
A line-art icon vocabulary, resolved by concept and tinted to the deck.

The reference deck's icon-bearing blocks - the circular badges on pages 22,
66, 95 and 130, the icon column of the row bands on pages 12 and 40 - are not
decoration. They are how a reader scans a dense page: seven bands of prose
are a wall, and seven bands with a glyph each are a list.

Three properties matter and none of them is "have a lot of icons":

* **Consistent weight.** Glyphs drawn at different stroke widths read as
  clip-art collected from different places. Every glyph here is described in
  a unit square and stroked at one weight derived from its rendered size.
* **Tintable.** An icon is drawn in whichever colour its block asks for, so
  it belongs to the deck's palette rather than importing its own.
* **Resolvable by concept.** Content says "financial services", not
  "icon-coins-outline". A caller that has to know filenames will not use it.

Drawn with matplotlib rather than shipped as image files: it is already a
dependency (the chart renderer), it gives exact control over stroke weight
and colour, and a glyph described as geometry can be rendered at any size
without the fuzz a scaled-up PNG would have.
"""

from __future__ import annotations

import io
import re
from typing import Iterable, Optional, Sequence

# Every glyph is described in a unit square with (0, 0) bottom-left, so one
# stroke weight and one scale apply to all of them.
_UNIT = 1.0
# Stroke weight as a fraction of the rendered icon's size. Read off the
# reference's badges, where the glyph line is roughly a fiftieth of the badge.
STROKE_FRACTION = 0.055
MIN_STROKE_PT = 0.8

# Glyphs, as lists of primitives. Deliberately simple shapes: at the size a
# slide renders an icon, detail is noise.
#   ("line", x1, y1, x2, y2)
#   ("poly", [(x, y), ...])            open polyline
#   ("shape", [(x, y), ...])           closed polygon
#   ("circle", cx, cy, r)
#   ("dot", cx, cy, r)                 filled
#   ("arc", cx, cy, r, start_deg, end_deg)
#   ("earc", cx, cy, w, h, start_deg, end_deg)   elliptical arc
#   ("ellipse", cx, cy, w, h)
GLYPHS: dict[str, list[tuple]] = {
    "trend-up": [
        ("poly", [(0.10, 0.28), (0.36, 0.55), (0.55, 0.38), (0.88, 0.76)]),
        ("poly", [(0.66, 0.76), (0.88, 0.76), (0.88, 0.54)]),
        ("line", 0.10, 0.12, 0.90, 0.12),
    ],
    "chart-bars": [
        ("line", 0.12, 0.12, 0.12, 0.86),
        ("line", 0.12, 0.12, 0.90, 0.12),
        ("shape", [(0.26, 0.12), (0.40, 0.12), (0.40, 0.44), (0.26, 0.44)]),
        ("shape", [(0.47, 0.12), (0.61, 0.12), (0.61, 0.64), (0.47, 0.64)]),
        ("shape", [(0.68, 0.12), (0.82, 0.12), (0.82, 0.82), (0.68, 0.82)]),
    ],
    "money": [
        ("circle", 0.50, 0.50, 0.36),
        ("line", 0.50, 0.24, 0.50, 0.76),
        ("poly", [(0.62, 0.63), (0.44, 0.63), (0.38, 0.57), (0.44, 0.50),
                  (0.58, 0.50), (0.63, 0.43), (0.57, 0.37), (0.38, 0.37)]),
    ],
    "people": [
        ("circle", 0.36, 0.68, 0.14),
        ("arc", 0.36, 0.36, 0.24, 0, 180),
        ("circle", 0.70, 0.72, 0.10),
        ("arc", 0.70, 0.44, 0.17, 10, 170),
    ],
    "network": [
        ("dot", 0.50, 0.80, 0.07),
        ("dot", 0.18, 0.26, 0.07),
        ("dot", 0.82, 0.26, 0.07),
        ("dot", 0.50, 0.48, 0.07),
        ("line", 0.50, 0.73, 0.50, 0.55),
        ("line", 0.44, 0.44, 0.24, 0.31),
        ("line", 0.56, 0.44, 0.76, 0.31),
    ],
    "signal": [
        ("dot", 0.50, 0.22, 0.07),
        ("arc", 0.50, 0.22, 0.22, 30, 150),
        ("arc", 0.50, 0.22, 0.40, 30, 150),
        ("arc", 0.50, 0.22, 0.58, 35, 145),
    ],
    # The union outline of three lobes over a flat base, sampled once rather
    # than drawn as three overlapping arcs - overlapping arcs leave their
    # crossings visible inside the shape, which reads as three circles rather
    # than as one cloud.
    "cloud": [
        ("shape", [(0.133, 0.300), (0.133, 0.464), (0.167, 0.513), (0.200, 0.539),
                   (0.233, 0.553), (0.267, 0.559), (0.300, 0.559), (0.333, 0.556),
                   (0.367, 0.598), (0.400, 0.624), (0.433, 0.642), (0.467, 0.653),
                   (0.500, 0.659), (0.533, 0.660), (0.567, 0.655), (0.600, 0.645),
                   (0.633, 0.629), (0.667, 0.604), (0.700, 0.566), (0.733, 0.538),
                   (0.767, 0.540), (0.800, 0.535), (0.833, 0.521), (0.867, 0.495),
                   (0.900, 0.444), (0.900, 0.300)]),
    ],
    "chip": [
        ("shape", [(0.28, 0.28), (0.72, 0.28), (0.72, 0.72), (0.28, 0.72)]),
        ("shape", [(0.42, 0.42), (0.58, 0.42), (0.58, 0.58), (0.42, 0.58)]),
        ("line", 0.40, 0.72, 0.40, 0.88),
        ("line", 0.60, 0.72, 0.60, 0.88),
        ("line", 0.40, 0.12, 0.40, 0.28),
        ("line", 0.60, 0.12, 0.60, 0.28),
        ("line", 0.12, 0.40, 0.28, 0.40),
        ("line", 0.12, 0.60, 0.28, 0.60),
        ("line", 0.72, 0.40, 0.88, 0.40),
        ("line", 0.72, 0.60, 0.88, 0.60),
    ],
    "shield": [
        ("shape", [(0.50, 0.90), (0.82, 0.74), (0.82, 0.44),
                   (0.50, 0.12), (0.18, 0.44), (0.18, 0.74)]),
        ("poly", [(0.36, 0.54), (0.46, 0.42), (0.66, 0.66)]),
    ],
    "lock": [
        ("shape", [(0.24, 0.14), (0.76, 0.14), (0.76, 0.56), (0.24, 0.56)]),
        ("arc", 0.50, 0.56, 0.20, 0, 180),
        ("dot", 0.50, 0.34, 0.06),
    ],
    "identity": [
        ("shape", [(0.12, 0.24), (0.88, 0.24), (0.88, 0.76), (0.12, 0.76)]),
        ("circle", 0.34, 0.58, 0.10),
        ("arc", 0.34, 0.34, 0.16, 0, 180),
        ("line", 0.56, 0.60, 0.80, 0.60),
        ("line", 0.56, 0.48, 0.80, 0.48),
        ("line", 0.56, 0.36, 0.72, 0.36),
    ],
    "eye": [
        ("arc", 0.50, 0.26, 0.46, 20, 160),
        ("arc", 0.50, 0.74, 0.46, 200, 340),
        ("circle", 0.50, 0.50, 0.12),
    ],
    "factory": [
        ("poly", [(0.12, 0.16), (0.12, 0.58), (0.40, 0.42), (0.40, 0.58),
                  (0.68, 0.42), (0.68, 0.80), (0.88, 0.80), (0.88, 0.16)]),
        ("line", 0.12, 0.16, 0.88, 0.16),
    ],
    "car": [
        ("poly", [(0.12, 0.42), (0.24, 0.42), (0.34, 0.62), (0.68, 0.62),
                  (0.78, 0.42), (0.90, 0.42)]),
        ("line", 0.12, 0.42, 0.12, 0.30),
        ("line", 0.90, 0.42, 0.90, 0.30),
        ("line", 0.12, 0.30, 0.90, 0.30),
        ("circle", 0.30, 0.24, 0.09),
        ("circle", 0.72, 0.24, 0.09),
    ],
    "energy": [
        ("shape", [(0.56, 0.92), (0.28, 0.50), (0.48, 0.50),
                   (0.42, 0.10), (0.72, 0.54), (0.52, 0.54)]),
    ],
    "bulb": [
        ("arc", 0.50, 0.58, 0.26, 0, 360),
        ("line", 0.40, 0.30, 0.60, 0.30),
        ("line", 0.42, 0.20, 0.58, 0.20),
        ("line", 0.42, 0.36, 0.42, 0.30),
        ("line", 0.58, 0.36, 0.58, 0.30),
    ],
    "cart": [
        ("poly", [(0.10, 0.80), (0.24, 0.80), (0.36, 0.30), (0.84, 0.30)]),
        ("line", 0.28, 0.62, 0.90, 0.62),
        ("line", 0.90, 0.62, 0.84, 0.30),
        ("dot", 0.42, 0.16, 0.07),
        ("dot", 0.76, 0.16, 0.07),
    ],
    "health": [
        ("shape", [(0.50, 0.86), (0.26, 0.86), (0.26, 0.62), (0.14, 0.62),
                   (0.14, 0.38), (0.26, 0.38), (0.26, 0.14), (0.50, 0.14),
                   (0.74, 0.14), (0.74, 0.38), (0.86, 0.38), (0.86, 0.62),
                   (0.74, 0.62), (0.74, 0.86)]),
    ],
    "satellite": [
        ("circle", 0.50, 0.50, 0.14),
        ("shape", [(0.10, 0.62), (0.30, 0.82), (0.44, 0.68), (0.24, 0.48)]),
        ("shape", [(0.76, 0.18), (0.56, 0.38), (0.70, 0.52), (0.90, 0.32)]),
        ("arc", 0.72, 0.72, 0.16, 180, 270),
    ],
    "globe": [
        ("circle", 0.50, 0.50, 0.38),
        ("line", 0.12, 0.50, 0.88, 0.50),
        ("ellipse", 0.50, 0.50, 0.38, 0.76),
    ],
    "clock": [
        ("circle", 0.50, 0.50, 0.38),
        ("poly", [(0.50, 0.72), (0.50, 0.50), (0.68, 0.40)]),
    ],
    "search": [
        ("circle", 0.42, 0.58, 0.26),
        ("line", 0.61, 0.39, 0.86, 0.16),
    ],
    "gear": [
        ("circle", 0.50, 0.50, 0.22),
        ("circle", 0.50, 0.50, 0.09),
        ("line", 0.50, 0.72, 0.50, 0.90),
        ("line", 0.50, 0.10, 0.50, 0.28),
        ("line", 0.28, 0.50, 0.10, 0.50),
        ("line", 0.72, 0.50, 0.90, 0.50),
        ("line", 0.66, 0.66, 0.79, 0.79),
        ("line", 0.34, 0.34, 0.21, 0.21),
        ("line", 0.66, 0.34, 0.79, 0.21),
        ("line", 0.34, 0.66, 0.21, 0.79),
    ],
    "database": [
        ("ellipse", 0.50, 0.78, 0.60, 0.22),
        ("line", 0.20, 0.78, 0.20, 0.26),
        ("line", 0.80, 0.78, 0.80, 0.26),
        ("earc", 0.50, 0.26, 0.60, 0.22, 180, 360),
        ("earc", 0.50, 0.61, 0.60, 0.22, 180, 360),
        ("earc", 0.50, 0.44, 0.60, 0.22, 180, 360),
    ],
    "document": [
        ("shape", [(0.24, 0.10), (0.60, 0.10), (0.76, 0.28),
                   (0.76, 0.90), (0.24, 0.90)]),
        ("line", 0.36, 0.68, 0.64, 0.68),
        ("line", 0.36, 0.54, 0.64, 0.54),
        ("line", 0.36, 0.40, 0.56, 0.40),
    ],
    "warning": [
        ("shape", [(0.50, 0.88), (0.90, 0.14), (0.10, 0.14)]),
        ("line", 0.50, 0.66, 0.50, 0.40),
        ("dot", 0.50, 0.28, 0.05),
    ],
    "check": [
        ("circle", 0.50, 0.50, 0.38),
        ("poly", [(0.32, 0.52), (0.45, 0.36), (0.70, 0.66)]),
    ],
    "marker": [
        ("circle", 0.50, 0.50, 0.34),
        ("dot", 0.50, 0.50, 0.11),
    ],
}

DEFAULT_GLYPH = "marker"

# Concept keywords to glyph. Matched by whole word against the caller's text,
# longest phrase first, so "machine learning" beats "learning".
CONCEPT_GLYPHS: dict[str, str] = {
    "machine learning": "chip", "artificial intelligence": "chip",
    "applied ai": "chip", "ai": "chip", "automation": "chip",
    "algorithm": "chip", "model": "chip", "compute": "chip",
    "semiconductor": "chip", "quantum": "chip",

    "growth": "trend-up", "increase": "trend-up", "adoption": "trend-up",
    "trend": "trend-up", "momentum": "trend-up", "acceleration": "trend-up",
    "performance": "chart-bars", "benchmark": "chart-bars",
    "metric": "chart-bars", "analytics": "chart-bars", "measurement": "chart-bars",

    "investment": "money", "revenue": "money", "funding": "money",
    "cost": "money", "price": "money", "capital": "money",
    "financial services": "money", "finance": "money", "banking": "money",
    "economic": "money", "market": "money",

    "talent": "people", "workforce": "people", "employee": "people",
    "customer": "people", "consumer": "people", "people": "people",
    "team": "people", "leader": "people", "society": "people",

    "connectivity": "signal", "telecommunications": "signal",
    "telecom": "signal", "wireless": "signal", "5g": "signal",
    "network": "network", "infrastructure": "network",
    "distributed": "network", "supply chain": "network", "ecosystem": "network",

    "cloud": "cloud", "edge computing": "cloud", "saas": "cloud",
    "platform": "cloud", "hosting": "cloud",

    "security": "shield", "trust": "shield", "risk": "warning",
    "resilience": "shield", "defence": "shield", "defense": "shield",
    "compliance": "shield", "regulation": "shield", "governance": "shield",
    "encryption": "lock", "privacy": "eye", "confidential": "lock",
    "identity": "identity", "authentication": "identity", "credential": "identity",

    "manufacturing": "factory", "industrial": "factory", "production": "factory",
    "operations": "factory", "assembly": "factory",
    "automotive": "car", "mobility": "car", "transport": "car",
    "logistics": "car", "fleet": "car",
    "energy": "energy", "power": "energy", "electricity": "energy",
    "renewable": "energy", "sustainability": "energy", "climate": "energy",
    "retail": "cart", "commerce": "cart", "shopping": "cart", "store": "cart",
    "health": "health", "healthcare": "health", "medical": "health",
    "patient": "health", "clinical": "health", "bio": "health",
    "space": "satellite", "satellite": "satellite", "orbit": "satellite",
    "aerospace": "satellite",

    "innovation": "bulb", "idea": "bulb", "opportunity": "bulb",
    "insight": "bulb", "concept": "bulb",
    "global": "globe", "international": "globe", "geography": "globe",
    "region": "globe", "worldwide": "globe",
    "time": "clock", "speed": "clock", "latency": "clock", "duration": "clock",
    "research": "search", "discovery": "search", "analysis": "search",
    "process": "gear", "efficiency": "gear", "engineering": "gear",
    "system": "gear", "architecture": "gear", "technology": "gear",
    "data": "database", "storage": "database", "record": "database",
    "document": "document", "report": "document", "policy": "document",
    "standard": "document", "patent": "document",
    "warning": "warning", "uncertainty": "warning", "threat": "warning",
    "challenge": "warning", "barrier": "warning",
    "benefit": "check", "advantage": "check", "outcome": "check",
    "approval": "check", "quality": "check",
}

_WORD_RE = re.compile(r"[a-z0-9]+")


def resolve_glyph(concept: Optional[str]) -> str:
    """
    The glyph for a concept, by meaning rather than by name.

    Content says "financial services", not "icon-coins-outline". Matching is
    on whole words against a phrase table, longest phrase first, so
    "machine learning" wins over "learning" - and anything unrecognised gets
    a neutral marker rather than an arbitrary glyph, because a wrong icon is
    worse than a plain one.
    """
    raw = (concept or "").strip().lower()
    if not raw:
        return DEFAULT_GLYPH
    # A caller naming a glyph outright gets it, before normalisation folds
    # away the hyphen in "trend-up" and turns it into a phrase lookup.
    if raw in GLYPHS:
        return raw

    text = " ".join(_WORD_RE.findall(raw))
    if not text:
        return DEFAULT_GLYPH
    if text in GLYPHS:
        return text

    padded = f" {text} "
    for phrase in sorted(CONCEPT_GLYPHS, key=len, reverse=True):
        if f" {phrase} " in padded:
            return CONCEPT_GLYPHS[phrase]
    return DEFAULT_GLYPH


def render_icon_png(
    concept: Optional[str],
    color_hex: str,
    size_px: int = 128,
    stroke_fraction: float = STROKE_FRACTION,
) -> Optional[bytes]:
    """
    Draw a glyph as a transparent PNG in ``color_hex``.

    Returns None rather than raising: an icon that cannot be drawn should
    leave a block without its badge, not fail the deck.
    """
    glyph = GLYPHS.get(resolve_glyph(concept)) or GLYPHS[DEFAULT_GLYPH]
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Arc, Circle, Ellipse, Polygon
    except Exception:                                     # pragma: no cover
        return None

    size_px = max(16, int(size_px))
    dpi = 100
    figure = None
    try:
        figure, axes = plt.subplots(
            figsize=(size_px / dpi, size_px / dpi), dpi=dpi
        )
        figure.patch.set_alpha(0.0)
        axes.set_facecolor("none")
        axes.set_xlim(0, _UNIT)
        axes.set_ylim(0, _UNIT)
        axes.set_aspect("equal")
        axes.axis("off")
        figure.subplots_adjust(left=0, right=1, top=1, bottom=0)

        width = max(MIN_STROKE_PT, size_px * stroke_fraction * 72.0 / dpi)
        for primitive in glyph:
            _draw_primitive(axes, primitive, color_hex, width,
                            Arc, Circle, Ellipse, Polygon)

        buffer = io.BytesIO()
        figure.savefig(buffer, format="png", transparent=True, dpi=dpi)
        return buffer.getvalue()
    except Exception:                                     # pragma: no cover
        return None
    finally:
        if figure is not None:
            import matplotlib.pyplot as plt
            plt.close(figure)


def _draw_primitive(axes, primitive: Sequence, color: str, width: float,
                    Arc, Circle, Ellipse, Polygon) -> None:
    kind = primitive[0]
    if kind == "line":
        _, x1, y1, x2, y2 = primitive
        axes.plot([x1, x2], [y1, y2], color=color, linewidth=width,
                  solid_capstyle="round")
    elif kind == "poly":
        points = primitive[1]
        axes.plot([p[0] for p in points], [p[1] for p in points], color=color,
                  linewidth=width, solid_capstyle="round", solid_joinstyle="round")
    elif kind == "shape":
        axes.add_patch(Polygon(
            primitive[1], closed=True, fill=False, edgecolor=color,
            linewidth=width, joinstyle="round",
        ))
    elif kind == "circle":
        _, cx, cy, r = primitive
        axes.add_patch(Circle((cx, cy), r, fill=False, edgecolor=color,
                              linewidth=width))
    elif kind == "dot":
        _, cx, cy, r = primitive
        axes.add_patch(Circle((cx, cy), r, facecolor=color, edgecolor="none"))
    elif kind == "arc":
        _, cx, cy, r, start, end = primitive
        axes.add_patch(Arc((cx, cy), 2 * r, 2 * r, angle=0.0, theta1=start,
                           theta2=end, edgecolor=color, linewidth=width))
    elif kind == "earc":
        _, cx, cy, w, h, start, end = primitive
        axes.add_patch(Arc((cx, cy), w, h, angle=0.0, theta1=start, theta2=end,
                           edgecolor=color, linewidth=width))
    elif kind == "ellipse":
        _, cx, cy, w, h = primitive
        axes.add_patch(Ellipse((cx, cy), w, h, fill=False, edgecolor=color,
                               linewidth=width))


def known_concepts() -> Iterable[str]:
    """Every concept phrase the resolver recognises - used in tool docs."""
    return sorted(CONCEPT_GLYPHS)


def glyph_names() -> Iterable[str]:
    return sorted(GLYPHS)
