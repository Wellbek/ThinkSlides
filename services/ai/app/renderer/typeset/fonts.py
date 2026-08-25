"""
Real font metrics, in place of a guess about average character width.

``text_fit`` measures text by multiplying the character count by
``AVG_CHAR_WIDTH_RATIO = 0.52``. That is a reasonable guess and it is wrong
in a way that compounds: "Illinois" and "WMMWMM" are the same length and
nearly twice apart in width, so a column of narrow text is reported as
needing more room than it does and a column of wide text less. Everything
built on that measurement - fitting, overflow detection, the density budget
- inherits the error.

This reads the actual advance widths out of the font file, which is what the
renderer that eventually draws the text will use. Resolution goes through
fontconfig so it agrees with LibreOffice, which is what rasterises our
previews and what every measurement of our own output is therefore taken
from. Asking for Arial on a machine that has Liberation and measuring
Liberation is correct; measuring an Arial we do not have would not be.
"""
from __future__ import annotations

import functools
import subprocess
from dataclasses import dataclass
from typing import Optional

# Fallbacks are only consulted when fontconfig itself has nothing, which on
# a machine with any font installed does not happen - fc-match always
# answers. Kept so a stripped container fails predictably.
DEFAULT_SANS = "DejaVu Sans"
FALLBACK_ADVANCE = 0.52


@functools.lru_cache(maxsize=256)
def resolve_path(family: str, *, bold: bool = False,
                 italic: bool = False) -> Optional[str]:
    """The font file fontconfig would use for this family.

    The same question LibreOffice asks, asked the same way, so that what we
    measure and what gets drawn are the same file.
    """
    if not family:
        family = DEFAULT_SANS
    pattern = family
    if bold:
        pattern += ":bold"
    if italic:
        pattern += ":italic"
    try:
        result = subprocess.run(
            ["fc-match", "-f", "%{file}", pattern],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    path = result.stdout.strip()
    return path or None


@dataclass
class FontMetrics:
    """Advance widths and vertical metrics for one resolved font file."""

    path: str
    units_per_em: int
    advances: dict[int, int]
    default_advance: int
    ascent: float
    descent: float
    line_gap: float
    family: str = ""

    def advance_of(self, codepoint: int) -> int:
        return self.advances.get(codepoint, self.default_advance)

    def width_pt(self, text: str, size_pt: float,
                 tracking_per_mille: int = 0) -> float:
        """Width of this string set at this size, in points."""
        if not text:
            return 0.0
        units = sum(self.advance_of(ord(ch)) for ch in text)
        width = units * size_pt / self.units_per_em
        if tracking_per_mille:
            width += len(text) * size_pt * tracking_per_mille / 1000.0
        return width

    def natural_line_height_pt(self, size_pt: float) -> float:
        """The leading the font itself asks for, when a role states none."""
        total = self.ascent - self.descent + self.line_gap
        return total * size_pt / self.units_per_em


@functools.lru_cache(maxsize=64)
def _load(path: str) -> Optional[FontMetrics]:
    try:
        from fontTools.ttLib import TTFont

        font = TTFont(path, fontNumber=0, lazy=True)
        head = font["head"]
        hhea = font["hhea"]
        hmtx = font["hmtx"]
        cmap = font.getBestCmap()
        advances: dict[int, int] = {}
        for codepoint, glyph in cmap.items():
            try:
                advances[codepoint] = hmtx[glyph][0]
            except KeyError:
                continue
        space = advances.get(ord(" "), head.unitsPerEm // 3)
        return FontMetrics(
            path=path,
            units_per_em=head.unitsPerEm or 1000,
            advances=advances,
            default_advance=space,
            ascent=float(hhea.ascent),
            descent=float(hhea.descent),
            line_gap=float(hhea.lineGap),
        )
    except Exception:
        return None


def metrics_for(family: str, *, bold: bool = False,
                italic: bool = False) -> Optional[FontMetrics]:
    """Metrics for a declared family, resolved the way the renderer will."""
    path = resolve_path(family, bold=bold, italic=italic)
    if not path:
        return None
    loaded = _load(path)
    if loaded is not None and not loaded.family:
        loaded.family = family
    return loaded


def width_pt(text: str, family: str, size_pt: float, *, bold: bool = False,
             italic: bool = False, tracking_per_mille: int = 0) -> float:
    """Width of a string, falling back to the old estimate if the font is gone.

    The fallback is the previous behaviour rather than an error: a missing
    font should degrade the measurement, not fail the render.
    """
    metrics = metrics_for(family, bold=bold, italic=italic)
    if metrics is None:
        return len(text) * size_pt * FALLBACK_ADVANCE
    return metrics.width_pt(text, size_pt, tracking_per_mille)
