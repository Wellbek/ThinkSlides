"""
The house style for the README's figures, after the DeepSeek technical
reports: rounded boxes in pale fills with a thin dark outline, groups in a
dashed blue outline with a bold title above them, thin black arrows, italic
annotations, and the real logo wherever a figure names a real product.

Every figure is code (`figures.py`), so a change to the system is a change to
one function and a re-run, not a redraw:

    python docs/diagrams/src/figures.py        # writes docs/diagrams/*.svg and *.png

PNGs are rendered with rsvg-convert at twice the SVG's size.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from html import escape
from typing import Iterable, Sequence

from PIL import ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.dirname(HERE)
LOGOS = os.path.join(HERE, "logos")

FONT = "Calibri, Carlito, 'Noto Sans', 'Segoe UI', Arial, sans-serif"
_FONT_FILES = {
    (False, False): "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    (True, False): "/usr/share/fonts/noto/NotoSans-Bold.ttf",
    (False, True): "/usr/share/fonts/noto/NotoSans-Italic.ttf",
}

INK = "#000000"
LINE = "#1F2D3D"
BLUE = "#2E75B6"        # group outlines and emphasis, as in the reports
MUTED = "#595959"

# What a box is, by its fill. Kept to a few, and always explained by a legend.
KINDS = {
    "code":   ("#FFF2CC", LINE),   # deterministic code
    "model":  ("#DEEBF7", LINE),   # a language-model call
    "vision": ("#E2F0D9", LINE),   # a vision-model call
    "user":   ("#EDEDED", LINE),   # the person, or a pause for them
    "store":  ("#FFFFFF", LINE),   # a data store
    "plain":  ("#FFFFFF", LINE),
    "warn":   ("#FBE5D6", LINE),   # a fallback or a refusal
}


def measure(text: str, size: float, bold: bool = False, italic: bool = False) -> float:
    font = ImageFont.truetype(_FONT_FILES[(bold, italic and not bold)], int(round(size * 4)))
    return font.getlength(text) / 4


@dataclass
class Figure:
    width: float
    height: float
    title: str
    desc: str
    parts: list[str] = field(default_factory=list)
    _markers: set = field(default_factory=set)

    # ── primitives ─────────────────────────────────────────────────────────

    def text(self, x, y, s, size=13, bold=False, italic=False, anchor="middle", color=INK):
        style = []
        if bold:
            style.append("font-weight:700")
        if italic:
            style.append("font-style:italic")
        self.parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" '
            f'fill="{color}" style="{";".join(style)}">{escape(s)}</text>')

    def lines(self, x, y, rows: Sequence, size=13, anchor="middle", leading=1.25,
              bold=False, italic=False, color=INK):
        """Several lines centred on ``y``; a row may be (text, {style})."""
        n = len(rows)
        first = y - (n - 1) * size * leading / 2 + size * 0.35
        for i, row in enumerate(rows):
            text, opts = (row, {}) if isinstance(row, str) else row
            self.text(x, first + i * size * leading, text, size=opts.get("size", size),
                      bold=opts.get("bold", bold), italic=opts.get("italic", italic),
                      anchor=anchor, color=opts.get("color", color))

    def rect(self, x, y, w, h, fill="#fff", stroke=LINE, r=7, dash=None, sw=1.1):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{r}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')

    def box(self, x, y, w, h, rows, kind="code", size=13, bold=False, sub=None,
            logos: Iterable[str] = (), logo_size=18, r=7, dash=None, align="middle"):
        """A rounded box of a kind, its text centred; ``sub`` is a smaller
        italic line under it; ``logos`` sit in a row above the text."""
        fill, stroke = KINDS[kind]
        self.rect(x, y, w, h, fill, stroke, r=r, dash=dash)
        rows = [rows] if isinstance(rows, str) else list(rows)
        logos = list(logos)
        block = len(rows) * size * 1.25 + (size * 1.15 if sub else 0) + (logo_size + 5 if logos else 0)
        top = y + (h - block) / 2
        if logos:
            total = len(logos) * logo_size + (len(logos) - 1) * 5
            lx = x + (w - total) / 2
            for i, name in enumerate(logos):
                self.logo(name, lx + i * (logo_size + 5), top, logo_size)
            top += logo_size + 5
        cx = x + w / 2 if align == "middle" else x + 10
        anchor = "middle" if align == "middle" else "start"
        for i, row in enumerate(rows):
            text, opts = (row, {}) if isinstance(row, str) else row
            self.text(cx, top + (i + 0.8) * size * 1.25 - size * 0.18, text,
                      size=opts.get("size", size), bold=opts.get("bold", bold),
                      italic=opts.get("italic", False), anchor=anchor,
                      color=opts.get("color", INK))
        if sub:
            self.text(cx, top + len(rows) * size * 1.25 + size * 0.85, sub,
                      size=size - 2, italic=True, anchor=anchor, color=MUTED)

    def group(self, x, y, w, h, title=None, color=BLUE, size=14, inside=False):
        """A dashed blue outline, its bold title just above its top-left."""
        self.rect(x, y, w, h, fill="none", stroke=color, r=4, dash="7 4", sw=1.6)
        if title:
            if inside:
                self.text(x + 10, y + size + 6, title, size=size, bold=True, anchor="start")
            else:
                self.text(x, y - 7, title, size=size, bold=True, anchor="start")

    def cylinder(self, x, y, w, h, rows, size=13, logos=(), logo_size=18, fill="#FFFFFF"):
        ry = 7
        self.parts.append(
            f'<path d="M{x},{y + ry} a{w / 2},{ry} 0 0,0 {w},0 v{h - 2 * ry} '
            f'a{w / 2},{ry} 0 0,1 {-w},0 z" fill="{fill}" stroke="{LINE}" stroke-width="1.1"/>')
        self.parts.append(
            f'<ellipse cx="{x + w / 2}" cy="{y + ry}" rx="{w / 2}" ry="{ry}" fill="{fill}" '
            f'stroke="{LINE}" stroke-width="1.1"/>')
        rows = [rows] if isinstance(rows, str) else list(rows)
        block = len(rows) * size * 1.25 + (logo_size + 5 if logos else 0)
        top = y + ry + (h - ry - block) / 2
        if logos:
            total = len(logos) * logo_size + (len(logos) - 1) * 5
            for i, name in enumerate(logos):
                self.logo(name, x + (w - total) / 2 + i * (logo_size + 5), top, logo_size)
            top += logo_size + 5
        for i, row in enumerate(rows):
            text, opts = (row, {}) if isinstance(row, str) else row
            self.text(x + w / 2, top + (i + 0.8) * size * 1.25 - size * 0.18, text,
                      size=opts.get("size", size), bold=opts.get("bold", False),
                      italic=opts.get("italic", False))

    def _marker(self, color):
        mid = "m" + color.strip("#")
        if mid not in self._markers:
            self._markers.add(mid)
            self.parts.insert(0, (
                f'<defs><marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" '
                f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                f'<path d="M0,1 L10,5 L0,9 z" fill="{color}"/></marker></defs>'))
        return mid

    def arrow(self, points, color=INK, dash=None, both=False, label=None,
              label_at=None, label_anchor="middle", sw=1.1, size=11.5, curve=False):
        """A polyline (or a smooth curve through three points) ending in a head."""
        mid = self._marker(color)
        if curve and len(points) == 3:
            (x0, y0), (cx, cy), (x1, y1) = points
            d = f"M{x0},{y0} Q{cx},{cy} {x1},{y1}"
        else:
            d = "M" + " L".join(f"{px:.1f},{py:.1f}" for px, py in points)
        dd = f' stroke-dasharray="{dash}"' if dash else ""
        start = f' marker-start="url(#{mid})"' if both else ""
        self.parts.append(
            f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}"{dd}'
            f' marker-end="url(#{mid})"{start}/>')
        if label:
            lx, ly = label_at or ((points[0][0] + points[-1][0]) / 2,
                                  (points[0][1] + points[-1][1]) / 2 - 5)
            self.text(lx, ly, label, size=size, italic=True, anchor=label_anchor, color=MUTED)

    def line(self, points, color=LINE, dash=None, sw=1.1):
        dd = f' stroke-dasharray="{dash}"' if dash else ""
        d = "M" + " L".join(f"{px:.1f},{py:.1f}" for px, py in points)
        self.parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}"{dd}/>')

    def logo(self, name, x, y, size):
        """The product's own logo, square, at (x, y)."""
        body, viewbox, colour = _logo(name)
        fill = f' fill="{colour}"' if colour else ""
        self.parts.append(
            f'<svg x="{x:.1f}" y="{y:.1f}" width="{size}" height="{size}" '
            f'viewBox="{viewbox}" preserveAspectRatio="xMidYMid meet"><g{fill}>{body}</g></svg>')

    def badge(self, x, y, n, color=BLUE):
        """A numbered circle for a step the text refers to."""
        self.parts.append(f'<circle cx="{x}" cy="{y}" r="8.5" fill="{color}"/>')
        self.text(x, y + 4, str(n), size=11, bold=True, color="#FFFFFF")

    def legend(self, x, y, items, size=12, gap=16):
        """Swatches in a row: (kind, label)."""
        cx = x
        for kind, label in items:
            if kind == "group":
                self.rect(cx, y - 10, 22, 14, fill="none", stroke=BLUE, r=3, dash="5 3", sw=1.4)
            elif kind == "dashed":
                self.line([(cx, y - 3), (cx + 22, y - 3)], color=INK, dash="4 3")
            elif kind == "arrow":
                self.arrow([(cx, y - 3), (cx + 22, y - 3)])
            else:
                fill, stroke = KINDS[kind]
                self.rect(cx, y - 10, 22, 14, fill=fill, stroke=stroke, r=3)
            self.text(cx + 28, y + 1, label, size=size, anchor="start")
            cx += 28 + measure(label, size) + gap

    # ── output ─────────────────────────────────────────────────────────────

    def svg(self) -> str:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.width} {self.height}" '
            f'width="{self.width}" height="{self.height}" role="img" aria-labelledby="t d" '
            f'font-family="{escape(FONT)}">\n'
            f'<title id="t">{escape(self.title)}</title>\n<desc id="d">{escape(self.desc)}</desc>\n'
            f'<rect width="100%" height="100%" fill="#FFFFFF"/>\n'
            + "\n".join(self.parts) + "\n</svg>\n")

    def save(self, name: str) -> str:
        path = os.path.join(OUT, f"{name}.svg")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(self.svg())
        subprocess.run(["rsvg-convert", "-z", "2", "-b", "white", "-o",
                        os.path.join(OUT, f"{name}.png"), path], check=True)
        return path


_CACHE: dict = {}


def _logo(name: str):
    if name in _CACHE:
        return _CACHE[name]
    with open(os.path.join(LOGOS, f"{name}.svg"), encoding="utf-8") as handle:
        raw = handle.read()
    with open(os.path.join(LOGOS, "logos.json"), encoding="utf-8") as handle:
        meta = json.load(handle)[name]
    root = re.search(r"<svg([^>]*)>", raw, flags=re.S).group(1)
    found = re.search(r'viewBox="([^"]+)"', root)
    if found:
        viewbox = found.group(1)
    else:
        width = re.search(r'\bwidth="([\d.]+)', root).group(1)
        height = re.search(r'\bheight="([\d.]+)', root).group(1)
        viewbox = f"0 0 {width} {height}"
    # Presentation attributes set on the root (a stroke colour, say) carry
    # over to the group that replaces it.
    keep = " ".join(f'{k}="{v}"' for k, v in re.findall(r'([\w:-]+)="([^"]*)"', root)
                    if k in ("fill", "stroke", "stroke-width", "fill-rule", "clip-rule"))
    body = re.sub(r"^.*?<svg[^>]*>", "", raw, flags=re.S)
    body = re.sub(r"</svg>\s*$", "", body.strip(), flags=re.S)
    body = re.sub(r"<title>.*?</title>", "", body, flags=re.S)
    if keep:
        body = f"<g {keep}>{body}</g>"
    # Simple Icons draw in currentColor; give them the brand's own colour.
    colour = meta["hex"] if meta["source"].startswith("Simple Icons") else None
    _CACHE[name] = (body, viewbox, colour)
    return _CACHE[name]
