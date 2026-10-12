"""
Diagram-as-code, for the graph shapes the typed vocabulary cannot express.

`build_diagram` has eight kinds and they are on-brand by construction. What it
cannot do is a graph: a state machine, a sequence, a dependency tree, anything
where the *number* of nodes and edges is what the content decides. Laying that
out by hand is the problem Mermaid already solved, so this borrows the
solution and imports the result as shapes.

The trade is worth naming, because it is the reason this is a third choice and
not the first. **Mermaid decides position, size and emphasis.** That is exactly
right for a graph and exactly wrong for a designed page: a deck whose figures
are all Mermaid has handed its composition to a layout engine that has never
seen the pack. The tool description says so, and says what to reach for first.

Three things this has to get right.

**No network, ever.** The bundle is vendored and pinned. A render happens while
a customer's deck is being generated, and a page that fetches from a CDN
mid-render fails in an air-gapped deployment and leaks a request in every
other one. Pinning also keeps layout deterministic: Mermaid decides where
every node goes, and a minor upgrade silently redraws every diagram this
service has ever produced.

**Labels come back as text.** Mermaid puts node labels in `<foreignObject>` -
HTML smuggled into a picture - and the importer strips those, correctly, since
an SVG can arrive from anywhere. So they are lifted into real SVG text here,
where the source is known and trusted, rather than by weakening the importer.

**A browser is a dependency, not a renderer.** If Chromium is missing or the
render fails, one figure falls back and the deck ships. The blast radius of a
browser is the reason composition is not done in one.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
from typing import Optional

from app.observability.logging import get_logger

logger = get_logger(__name__)

BUNDLE = pathlib.Path(__file__).parent / "vendor" / "mermaid-11.4.1.min.js"
MERMAID_VERSION = "11.4.1"

# A graph past this size is not a diagram a reader can follow, it is a picture
# of a database. Mirrors the bounds `build_diagram` puts on its typed kinds.
MAX_NODES = 24
MAX_SOURCE_CHARS = 4000

# How long one render may take. Generous, because a first render pays for
# Chromium's start-up; bounded, because a hung browser must not hold a
# generation open.
RENDER_TIMEOUT_MS = 20000

_FOREIGN = re.compile(r"<foreignObject\b[^>]*>(.*?)</foreignObject>", re.S)
_TAG = re.compile(r"<[^>]+>")


class MermaidUnavailable(RuntimeError):
    """A browser could not be used, so no diagram was drawn."""


def _page(source: str, theme: dict) -> str:
    """The document Mermaid renders in, with the bundle inlined."""
    return (
        "<!doctype html><html><head><meta charset='utf-8'><script>"
        + BUNDLE.read_text()
        + "</script></head><body><div id='out'></div><script>"
        "window.__render = (async () => {"
        "  mermaid.initialize({startOnLoad:false, securityLevel:'strict',"
        "    theme:'base', themeVariables:" + json.dumps(theme) + "});"
        "  const {svg} = await mermaid.render('d', " + json.dumps(source) + ");"
        "  return svg;"
        "})();</script></body></html>"
    )


def theme_from(palette: dict[str, str], font: str = "Arial") -> dict:
    """Mermaid's theme variables, from the deck's own palette.

    Set here as well as remapped on import, because Mermaid picks its own
    contrasting colours for label text and borders from these - so giving it
    the deck's palette up front produces a diagram that is already close,
    rather than one recoloured after the fact into combinations nobody chose.
    """
    primary = palette.get("primary") or palette.get("accent") or "#1F4E78"
    ink = palette.get("text") or palette.get("ink") or "#1A1A1A"
    ground = palette.get("background") or palette.get("ground") or "#FFFFFF"
    return {
        "primaryColor": primary,
        "primaryTextColor": ground,
        "primaryBorderColor": primary,
        "secondaryColor": palette.get("secondary") or primary,
        "tertiaryColor": ground,
        "lineColor": ink,
        "textColor": ink,
        "background": ground,
        "fontFamily": font,
        "fontSize": "14px",
    }


def check(source: str) -> Optional[str]:
    """Why this graph cannot be drawn, or None when it can."""
    text = (source or "").strip()
    if not text:
        return "No diagram source was given."
    if len(text) > MAX_SOURCE_CHARS:
        return (f"That diagram is {len(text)} characters and the limit is "
                f"{MAX_SOURCE_CHARS}. A graph that long is not a figure a "
                f"reader can follow.")
    # Counting arrows rather than parsing: an over-large graph should be
    # refused, and refusing it does not require understanding it.
    nodes = len(set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b(?=\s*(?:\[|\(|\{|--|==|-\.))",
                              text)))
    if nodes > MAX_NODES:
        return (f"That diagram has about {nodes} nodes and the limit is "
                f"{MAX_NODES}. Split it, or show the part that carries the "
                f"point.")
    return None


# What Mermaid calls the parts of a node, so the theme can be stamped onto
# them directly. Its own stylesheet reaches these with `#id .node rect`, a
# descendant selector the importer deliberately will not resolve - half a
# cascade applied confidently is worse than none - so the renderer's own
# conventions are applied here, where they are known.
NODE_CLASSES = ("label-container", "basic", "node-bkg")
LABEL_CLASSES = ("nodeLabel", "label")


def apply_theme(svg: str, theme: dict) -> str:
    """Stamp the theme onto the elements Mermaid styles by descendant rule.

    Without this every node imports black: the fill lives in a selector the
    importer will not resolve, and a shape with no paint it can find defaults
    to black rather than to a colour nobody chose.
    """
    node_fill = theme.get("primaryColor", "#1F4E78")
    node_stroke = theme.get("primaryBorderColor", node_fill)

    def paint(match: re.Match) -> str:
        head = match.group(0)
        classes = (re.search(r'class="([^"]*)"', head) or [None, ""])[1]
        if not any(name in classes for name in NODE_CLASSES):
            return head
        head = re.sub(r'\sstyle="[^"]*"', "", head)
        return head[:-1] + f' fill="{node_fill}" stroke="{node_stroke}">'

    svg = re.sub(r"<(?:rect|polygon|circle|ellipse|path)\b[^>]*>", paint, svg)
    return svg


def inline_labels(svg: str, label_colour: str = "#FFFFFF",
                  edge_colour: str = "#1A1A1A") -> str:
    """Lift Mermaid's HTML labels into real SVG text.

    Mermaid renders node labels inside `<foreignObject>`, which the importer
    strips - an SVG can arrive from a model, an MCP server or an upload, and
    none of those are places to run HTML from. The source here is known, so
    the text is recovered and re-emitted as `<text>` rather than by relaxing
    the importer for everyone.
    """
    def replace(match: re.Match) -> str:
        # An edge label sits on the page, not knocked out of a node, so it
        # takes the ink colour. Given the node colour it comes out white on
        # white, which is not a styling defect - it is a lost label.
        on_an_edge = "edgeLabel" in match.group(1)
        colour = edge_colour if on_an_edge else label_colour
        inner = _TAG.sub(" ", match.group(1))
        text = " ".join(inner.split())
        if not text:
            return ""
        head = match.group(0)[:match.group(0).find(">") + 1]
        width = _attr(head, "width")
        height = _attr(head, "height")
        # The foreignObject's own box, so the label lands where Mermaid put
        # it: its origin is the top-left, and SVG text sits on a baseline.
        x = width / 2.0 if width else 0.0
        y = height * 0.7 if height else 10.0
        return (f'<text x="{x:.2f}" y="{y:.2f}" text-anchor="middle" '
                f'font-size="14" fill="{colour}">{_escape(text)}</text>')

    return _FOREIGN.sub(replace, svg)


def _attr(head: str, name: str) -> float:
    match = re.search(rf'{name}="([-\d.]+)"', head)
    try:
        return float(match.group(1)) if match else 0.0
    except ValueError:
        return 0.0


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


async def render(source: str, *, palette: Optional[dict[str, str]] = None,
                 font: str = "Arial") -> str:
    """This graph as an SVG document, laid out by Mermaid.

    Raises ``MermaidUnavailable`` when a browser cannot be used, which the
    caller turns into a fallback rather than a failed deck.
    """
    problem = check(source)
    if problem:
        raise ValueError(problem)
    if not BUNDLE.exists():
        raise MermaidUnavailable(
            f"the vendored Mermaid bundle is missing from {BUNDLE}")
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise MermaidUnavailable(
            "playwright is not installed, so no diagram can be laid out"
        ) from exc

    theme = theme_from(palette or {}, font)
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(args=["--no-sandbox"])
            try:
                page = await browser.new_page()
                await page.set_content(_page(source, theme),
                                       wait_until="load",
                                       timeout=RENDER_TIMEOUT_MS)
                svg = await asyncio.wait_for(
                    page.evaluate("window.__render"),
                    timeout=RENDER_TIMEOUT_MS / 1000.0,
                )
            finally:
                await browser.close()
    except Exception as exc:
        raise MermaidUnavailable(f"the diagram could not be laid out: {exc}") from exc

    if not isinstance(svg, str) or "<svg" not in svg:
        raise MermaidUnavailable("Mermaid returned nothing that parses as SVG")
    logger.info("mermaid_rendered", version=MERMAID_VERSION, chars=len(svg))
    return inline_labels(apply_theme(svg, theme),
                         theme.get("primaryTextColor", "#FFFFFF"),
                         theme.get("textColor", "#1A1A1A"))
