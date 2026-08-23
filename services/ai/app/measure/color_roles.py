"""
Naming a publication's colours by the job each one does.

A palette as a list of hexes is not usable for generation: knowing that a
deck contains #FFE600 says nothing about whether that is its ground, its
body colour or the one saturated mark it allows itself once a page. Roles
are what a design language is written in, and a role is decided by *how much
page* a colour covers and *what it covers*.

The distinction that matters most here is accent from ground. Both are
saturated, and the difference is coverage: a ground is a colour a page is
set on, and an accent is a colour a page uses sparingly precisely so that it
carries emphasis. A palette that promotes an accent to a ground produces the
deck where every slide is bright yellow.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

# A colour covering more of the deck than this is a ground, whatever its
# saturation.
GROUND_COVERAGE = 0.04
# ...and one covering less than this, while being saturated, is an accent.
ACCENT_MAX_COVERAGE = 0.06
# Below this on the brightest channel a colour has no usable hue, whatever
# the arithmetic says.
MIN_CHROMA_VALUE = 60


@dataclass(frozen=True)
class ColorEvidence:
    """One colour, and how the publication actually spends it."""

    hex: str
    ink_chars: int = 0
    ground_cells: float = 0.0
    total_cells: float = 1.0

    @property
    def coverage(self) -> float:
        return self.ground_cells / self.total_cells if self.total_cells else 0.0

    @property
    def saturation(self) -> float:
        return saturation_of(self.hex)

    @property
    def luminance(self) -> float:
        return luminance_of(self.hex)


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def luminance_of(value: str) -> float:
    red, green, blue = _rgb(value)
    return (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255.0


def saturation_of(value: str) -> float:
    """Saturation, but zero for anything too dark to have a hue.

    (0, 1, 1) is arithmetically fully saturated and visually black. Without
    the floor, a near-black rule reads as the most saturated colour in the
    deck and is promoted to its accent.
    """
    red, green, blue = _rgb(value)
    high, low = max(red, green, blue), min(red, green, blue)
    if high < MIN_CHROMA_VALUE:
        return 0.0
    return (high - low) / high if high else 0.0


def _paper_color(page) -> str:
    """The colour of the page itself, sampled at a corner of the margin."""
    import fitz

    try:
        corner = fitz.Rect(0, 0, min(8.0, page.rect.width),
                           min(8.0, page.rect.height))
        pixmap = page.get_pixmap(clip=corner, matrix=fitz.Matrix(0.25, 0.25))
        if pixmap.width and pixmap.height:
            red, green, blue = pixmap.pixel(0, 0)[:3]
            return f"#{red:02X}{green:02X}{blue:02X}"
    except Exception:
        pass
    return "#FFFFFF"


def read_colors(pages) -> tuple[dict[str, ColorEvidence], list[str]]:
    """Every colour the publication *specifies*, with the evidence for its role.

    Read from vector fills and text, never from raster pixels. A page
    carrying a photograph contains thousands of saturated colours that
    belong to the photograph and not to the design language - sampling the
    rendered page reads EY's magenta cover image as its brand accent and
    misses the yellow rule it actually reserves for emphasis.
    """
    ink: dict[str, int] = defaultdict(int)
    fills: dict[str, float] = defaultdict(float)
    area = 0.0

    for _, page in pages:
        width, height = float(page.rect.width), float(page.rect.height)
        page_area = width * height
        area += page_area
        covered = 0.0
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line["spans"]:
                    text = span["text"].strip()
                    if text:
                        ink[f"#{span['color']:06X}"] += len(text)

        for drawing in page.get_drawings():
            fill = drawing.get("fill")
            if not fill:
                continue
            rect = drawing["rect"]
            if rect.width <= 0 or rect.height <= 0:
                continue
            value = "#%02X%02X%02X" % tuple(
                max(0, min(255, int(round(channel * 255)))) for channel in fill[:3]
            )
            fills[value] += rect.width * rect.height
            covered += rect.width * rect.height

        # The paper. A page that draws no background rectangle still has a
        # ground, and on a white magazine page that ground is most of what
        # you see - but it is invisible to a reading that only counts fills,
        # which then promotes a decorative navy panel to the deck's ground.
        if covered < page_area:
            fills[_paper_color(page)] += page_area - covered

    total = area or 1.0
    evidence = {}
    for value in set(ink) | set(fills):
        evidence[value] = ColorEvidence(
            hex=value,
            ink_chars=ink.get(value, 0),
            ground_cells=fills.get(value, 0.0),
            total_cells=total,
        )
    ordered = sorted(evidence.values(), key=lambda e: -e.coverage)
    return evidence, [e.hex for e in ordered]


def assign_roles(evidence: dict[str, ColorEvidence]) -> dict[str, str]:
    """Name the palette by coverage and by what each colour is spent on."""
    if not evidence:
        return {}
    values = list(evidence.values())

    grounds = sorted(
        (e for e in values if e.coverage >= GROUND_COVERAGE),
        key=lambda e: -e.coverage,
    )
    inks = sorted((e for e in values if e.ink_chars > 0),
                  key=lambda e: -e.ink_chars)

    roles: dict[str, str] = {}
    if grounds:
        roles["ground"] = grounds[0].hex
        chosen = grounds[0].hex
        light = [g for g in grounds if g.luminance >= 0.5 and g.hex != chosen]
        dark = [g for g in grounds if g.luminance < 0.5 and g.hex != chosen]
        if light:
            roles["ground_alt"] = light[0].hex
        if dark:
            roles["ground_dark"] = dark[0].hex

    if inks:
        roles["ink"] = inks[0].hex
        ground_luminance = luminance_of(roles.get("ground", "#FFFFFF"))
        # Muted is the same decision as the main ink, taken quieter: on the
        # same side of the ground, and nearer to it. Without the same-side
        # test, white text reversed out of a dark panel satisfies "nearer to
        # a white ground" and is named the muted ink.
        ink_is_dark = inks[0].luminance < ground_luminance
        muted = [
            e for e in inks[1:]
            if (e.luminance < ground_luminance) == ink_is_dark
            and abs(e.luminance - ground_luminance)
            < abs(inks[0].luminance - ground_luminance)
            and e.saturation < 0.4
        ]
        if muted:
            roles["ink_muted"] = muted[0].hex
        reversed_ink = [
            e for e in inks[1:]
            if (e.luminance < ground_luminance) != ink_is_dark
        ]
        if reversed_ink:
            roles["ink_reversed"] = reversed_ink[0].hex

    accents = sorted(
        (e for e in values
         if e.saturation >= 0.45 and 0 < e.coverage <= ACCENT_MAX_COVERAGE),
        key=lambda e: -e.saturation,
    )
    if accents:
        roles["accent"] = accents[0].hex
        if len(accents) > 1:
            roles["accent_alt"] = accents[1].hex
    return roles


def describe(evidence: dict[str, ColorEvidence], roles: dict[str, str]) -> str:
    lines = ["| role | colour | coverage | ink chars | saturation |",
             "| --- | --- | --- | --- | --- |"]
    for role, value in roles.items():
        item = evidence.get(value)
        if item is None:
            continue
        lines.append(
            f"| {role} | `{value}` | {item.coverage:.1%} | {item.ink_chars} "
            f"| {item.saturation:.2f} |"
        )
    return "\n".join(lines)


def nearest_role(value: str, roles: dict[str, str]) -> Optional[str]:
    """The role whose colour this one is closest to, for binding content."""
    if not roles:
        return None
    target = _rgb(value)
    best, best_distance = None, float("inf")
    for role, candidate in roles.items():
        other = _rgb(candidate)
        distance = sum((a - b) ** 2 for a, b in zip(target, other)) ** 0.5
        if distance < best_distance:
            best, best_distance = role, distance
    return best
