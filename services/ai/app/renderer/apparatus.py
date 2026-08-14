"""
Per-slide footnotes and the source line.

The reference deck carries a scholarly apparatus on almost every content page:
superscript markers inside body copy, inside table cells and inside chart
labels, resolved in a small block above a source line at the foot of the page.
Page 110 has three of them, and one explains that the final bar of a chart is
extrapolated - without it the chart misstates its own data. The apparatus is
load-bearing, not ornamental.

``renderer/citations.py`` already carries a *deck-wide* trail: a run tagged
``sourceRef`` becomes a "[n]" marker and a numbered entry on a Sources slide.
That is a different instrument and both are needed. A reader looking at a chart
with an extrapolated bar needs the caveat on that page, not in an appendix
eleven slides later.

The two cannot collide, and the reason is notation rather than bookkeeping:
a deck-wide citation renders as a bracketed "[3]" on the baseline, a footnote
as a superscript "3". A slide can carry both and a reader can tell which is
which, which is the only thing that actually matters.

**Markers are written inline**, as ``[^1]``, because the three places they have
to appear - a text run, a table cell, a chart label - are all plain strings,
and any other design would need three separate mechanisms. The slide supplies
``footnotes`` and the marker indexes into it.

**Space is reserved before body layout.** An apparatus laid out after the body
is an apparatus with autofitted text on top of it, so the height is subtracted
from the content region while the slide is still being planned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from app import colors
from app.renderer import primitives
from app.renderer.deck_theme import SlideStyle
from app.renderer.layout import PositionConfig
from app.renderer.primitives import Primitive, TextRunSpec
from app.renderer.typography import TypeScale

# The inline marker. Deliberately the Markdown footnote form: it is what a
# model writes unprompted, and it cannot be confused with the bracketed "[3]"
# of a deck-wide citation.
MARKER_RE = re.compile(r"\[\^(\d{1,2})\]")

# Unicode superscript digits, for the one place a real superscript run cannot
# be set: a chart label, which is drawn by matplotlib into a PNG.
_SUPERSCRIPT_DIGITS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")

# OOXML expresses a superscript as a percentage of the baseline offset.
SUPERSCRIPT_BASELINE = 30000

MAX_FOOTNOTES_PER_SLIDE = 6

DEFAULT_FOOTNOTE_PT = 8.0
DEFAULT_SOURCE_PT = 8.0
# The gap between the apparatus and the content above it, and between the
# footnote block and the source line under it.
APPARATUS_GAP_IN = 0.06
APPARATUS_MARGIN_X = 0.033
# Distance from the bottom of the canvas to the bottom of the source line.
APPARATUS_BOTTOM_IN = 0.30
# The source line shares its baseline with the brand mark and page number in
# the opposite corner, so it stops short of them. Footnotes sit above that
# baseline and may run the full width.
SOURCE_WIDTH_FRACTION = 0.62


@dataclass(frozen=True)
class ApparatusTokens:
    """How a deck sets its footnotes and its source line."""
    footnote_size_pt: float = DEFAULT_FOOTNOTE_PT
    source_size_pt: float = DEFAULT_SOURCE_PT
    color: Optional[str] = None

    @classmethod
    def from_design_system(cls, design_system: Optional[dict]) -> "ApparatusTokens":
        declared = ((design_system or {}).get("apparatus") or {})
        if not isinstance(declared, dict):
            declared = {}
        return cls(
            footnote_size_pt=_positive(declared.get("footnoteSizePt"),
                                       DEFAULT_FOOTNOTE_PT),
            source_size_pt=_positive(declared.get("sourceSizePt"), DEFAULT_SOURCE_PT),
            color=_hex(declared.get("color")),
        )


@dataclass(frozen=True)
class SlideApparatus:
    """The footnotes and source line one slide carries."""
    footnotes: tuple[str, ...] = ()
    source_line: str = ""

    def __bool__(self) -> bool:
        return bool(self.footnotes or self.source_line)

    @classmethod
    def from_slide(cls, slide_data: dict) -> "SlideApparatus":
        raw = slide_data.get("footnotes")
        footnotes: tuple[str, ...] = ()
        if isinstance(raw, (list, tuple)):
            footnotes = tuple(
                str(entry).strip() for entry in raw[:MAX_FOOTNOTES_PER_SLIDE]
                if str(entry or "").strip()
            )
        return cls(
            footnotes=footnotes,
            source_line=str(slide_data.get("sourceLine") or "").strip(),
        )

    def height(self, tokens: ApparatusTokens, width_in: float) -> float:
        """
        How much of the slide this apparatus needs.

        Subtracted from the content region *before* the body is laid out -
        an apparatus measured afterwards is an apparatus with autofitted text
        over the top of it.
        """
        if not self:
            return 0.0
        total = 0.0
        for index, text in enumerate(self.footnotes, start=1):
            total += primitives.measure_text(
                f"{index} {text}", tokens.footnote_size_pt, width_in
            )
        if self.footnotes:
            total += APPARATUS_GAP_IN
        if self.source_line:
            total += primitives.measure_text(
                self.source_line, tokens.source_size_pt,
                min(width_in, width_in * SOURCE_WIDTH_FRACTION / (1 - 2 * APPARATUS_MARGIN_X)),
            )
        return total + APPARATUS_GAP_IN


def content_ceiling(apparatus: SlideApparatus, tokens: ApparatusTokens,
                    canvas: PositionConfig) -> float:
    """
    The lowest point on the canvas a slide's content may reach, in inches.

    Placeholder-bound content gets this applied for it - the body band is
    shortened while the slide is being planned. A *composed* slide authors its
    own regions, and shrinking those after the fact would distort a
    composition somebody wrote deliberately, so instead this is published: a
    region grammar can respect it, and the renderer reports a slide that does
    not.
    """
    if not apparatus:
        return canvas.height
    width = canvas.width * (1.0 - APPARATUS_MARGIN_X * 2)
    return canvas.height - APPARATUS_BOTTOM_IN - apparatus.height(tokens, width)


def compile_apparatus(
    apparatus: SlideApparatus,
    tokens: ApparatusTokens,
    style: SlideStyle,
    type_scale: TypeScale,
    canvas: PositionConfig,
) -> list[Primitive]:
    """The marks that draw a slide's footnote block and source line."""
    if not apparatus:
        return []

    left = canvas.width * APPARATUS_MARGIN_X
    width = canvas.width * (1.0 - APPARATUS_MARGIN_X * 2)
    colour = tokens.color or colors.mix(style.background, style.body_color, 0.72)

    marks: list[Primitive] = []
    cursor = canvas.height - APPARATUS_BOTTOM_IN - apparatus.height(tokens, width) \
        + APPARATUS_GAP_IN

    for index, text in enumerate(apparatus.footnotes, start=1):
        line = f"{index} {text}"
        height = primitives.measure_text(line, tokens.footnote_size_pt, width)
        marks.append(Primitive(
            id=f"apparatus-footnote-{index}", kind="text",
            box=PositionConfig(left=left, top=cursor, width=width, height=height),
            runs=(
                TextRunSpec(text=str(index), superscript=True),
                TextRunSpec(text=f" {text}"),
            ),
            size_pt=tokens.footnote_size_pt, color=colour,
            font=type_scale.body_font, autofit=False, chrome=True,
        ))
        cursor += height

    if apparatus.source_line:
        if apparatus.footnotes:
            cursor += APPARATUS_GAP_IN
        source_width = min(width, canvas.width * SOURCE_WIDTH_FRACTION)
        height = primitives.measure_text(apparatus.source_line,
                                         tokens.source_size_pt, source_width)
        marks.append(Primitive(
            id="apparatus-source", kind="text",
            box=PositionConfig(left=left, top=cursor, width=source_width,
                               height=height),
            runs=(TextRunSpec(text=apparatus.source_line),),
            size_pt=tokens.source_size_pt, color=colour,
            font=type_scale.body_font, autofit=False, chrome=True,
        ))
    return marks


# --------------------------------------------------------------------------
# Markers
# --------------------------------------------------------------------------

def split_markers(text: str) -> list[tuple[str, Optional[int]]]:
    """
    Split a string into ``(text, marker)`` parts.

    ``"AI adoption[^1] rose"`` becomes ``[("AI adoption", None), ("1", 1),
    (" rose", None)]`` - the marker part carries the number to set as a
    superscript, everything else is ordinary text.
    """
    parts: list[tuple[str, Optional[int]]] = []
    cursor = 0
    for match in MARKER_RE.finditer(text or ""):
        if match.start() > cursor:
            parts.append((text[cursor:match.start()], None))
        parts.append((match.group(1), int(match.group(1))))
        cursor = match.end()
    if cursor < len(text or ""):
        parts.append((text[cursor:], None))
    return parts or [(text or "", None)]


def has_marker(text: str) -> bool:
    return bool(MARKER_RE.search(text or ""))


def strip_markers(text: str) -> str:
    """The text without its markers - for measurement and for QA comparisons."""
    return MARKER_RE.sub("", text or "")


def to_superscript(text: str) -> str:
    """
    Replace markers with Unicode superscript digits.

    Used for chart labels, which matplotlib draws into a PNG where no run-level
    superscript exists. Everywhere else the marker is set as a real superscript
    run, because a real one scales with the type around it and a Unicode glyph
    does not.
    """
    return MARKER_RE.sub(
        lambda m: m.group(1).translate(_SUPERSCRIPT_DIGITS), text or ""
    )


def marker_numbers(text: str) -> list[int]:
    return [int(m.group(1)) for m in MARKER_RE.finditer(text or "")]


def collect_markers(slide_data: dict) -> list[int]:
    """Every footnote marker on a slide, wherever it was written."""
    found: list[int] = []
    for text in _all_text(slide_data):
        found.extend(marker_numbers(text))
    return found


def _all_text(slide_data: dict) -> Iterable[str]:
    """
    Every string on a slide that a marker could have been written into.

    Runs, table cells and chart labels - the three places the reference deck
    puts them, and the reason markers are inline rather than a field.
    """
    for item in slide_data.get("contents") or []:
        if not isinstance(item, dict):
            continue
        for run in item.get("runs") or []:
            if isinstance(run, dict):
                yield str(run.get("text") or "")
        table = item.get("table")
        if isinstance(table, dict):
            for row in table.get("rows") or []:
                if isinstance(row, (list, tuple)):
                    for cell in row:
                        yield str(cell or "")
        chart = item.get("chart")
        if isinstance(chart, dict):
            yield str(chart.get("title") or "")
            for category in chart.get("categories") or []:
                yield str(category or "")
            for series in chart.get("series") or []:
                if isinstance(series, dict):
                    yield str(series.get("name") or "")
        for key in ("statCallouts", "iconCards", "accentCards", "rowBands"):
            for entry in item.get(key) or []:
                if not isinstance(entry, dict):
                    continue
                for value in entry.values():
                    if isinstance(value, str):
                        yield value
                    elif isinstance(value, (list, tuple)):
                        for line in value:
                            if isinstance(line, str):
                                yield line
        banner = item.get("banner")
        if isinstance(banner, dict):
            yield str(banner.get("text") or "")


def charts_marking_a_forecast(slide_data: dict) -> list[dict]:
    """Every chart on a slide that marks part of its data as projected."""
    found = []
    for item in (slide_data or {}).get("contents") or []:
        if not isinstance(item, dict) or item.get("type") != "chart":
            continue
        chart = item.get("chart")
        if not isinstance(chart, dict):
            continue
        for series in chart.get("series") or []:
            if not isinstance(series, dict):
                continue
            if series.get("forecastFrom") is not None or series.get("measured"):
                found.append(chart)
                break
    return found


def chart_text(chart: dict) -> list[str]:
    """Every string a chart puts on the page, where a marker could be."""
    out = [str(chart.get("title") or ""), str(chart.get("unitLine") or "")]
    out += [str(c or "") for c in chart.get("categories") or []]
    out += [str(s.get("name") or "") for s in chart.get("series") or []
            if isinstance(s, dict)]
    out += [str(p.get("label") or "") for p in chart.get("points") or []
            if isinstance(p, dict)]
    return out


def validate_apparatus(slide_data: dict) -> tuple[list[str], list[str]]:
    """
    Check a slide's footnotes against its markers.

    A marker with no footnote is a reference to nothing; a footnote with no
    marker is a note nothing points at. Both render, and both are wrong in a
    way that only a reader notices, so they are caught here.
    """
    errors: list[str] = []
    warnings: list[str] = []

    raw = slide_data.get("footnotes")
    if raw is not None and not isinstance(raw, (list, tuple)):
        return [f"'footnotes' must be an array, got {type(raw).__name__}"], warnings

    footnotes = SlideApparatus.from_slide(slide_data).footnotes
    markers = collect_markers(slide_data)

    for number in sorted(set(markers)):
        if number < 1 or number > len(footnotes):
            errors.append(
                f"footnote marker [^{number}] has no footnote - the slide "
                f"declares {len(footnotes)}"
            )
    for index in range(1, len(footnotes) + 1):
        if index not in markers:
            warnings.append(
                f"footnote {index} is never referenced - nothing on the slide "
                f"points at it"
            )
    # A projection drawn but not explained is a chart claiming a measured
    # figure it does not have. Reference page 110's dashed final bar is
    # readable *because* a footnote says it is extrapolated; without the words
    # the marking is just an unusual bar.
    for chart in charts_marking_a_forecast(slide_data):
        if not footnotes:
            errors.append(
                "a chart marks part of its data as projected, but the slide "
                "declares no footnotes - a projection has to say what it is "
                "extrapolated from"
            )
            break
        if not any(has_marker(text) for text in chart_text(chart)):
            errors.append(
                "a chart marks part of its data as projected but carries no "
                "footnote marker - put a [^n] on the category, series name or "
                "title the projection applies to"
            )
            break

    if len(footnotes) > MAX_FOOTNOTES_PER_SLIDE:
        warnings.append(
            f"{len(footnotes)} footnotes is more than a slide can carry "
            f"legibly (max {MAX_FOOTNOTES_PER_SLIDE}); the rest are dropped"
        )
    return errors, warnings


def apply_superscript(run) -> None:
    """Set a python-pptx run as a true superscript."""
    run._r.get_or_add_rPr().set("baseline", str(SUPERSCRIPT_BASELINE))


def _positive(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _hex(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    if not text.startswith("#"):
        text = f"#{text}"
    return text.upper() if colors.parse_hex(text) else None


def default_apparatus_tokens() -> dict:
    return {
        "footnoteSizePt": DEFAULT_FOOTNOTE_PT,
        "sourceSizePt": DEFAULT_SOURCE_PT,
    }
