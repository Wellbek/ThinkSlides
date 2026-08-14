"""
Measure one rendered page.

Every number the style-fidelity harness reports comes from here, and the same
function measures the reference deck and our own output. That is deliberate:
a score is only a comparison if both sides were measured the same way, and the
two sides arrive in different shapes - the reference deck's charts are vector
drawings, ours are embedded PNGs; the reference's grounds are filled paths,
ours are PowerPoint slide backgrounds. Everything below therefore works off
what a PDF page actually contains, after both have been through the same
LibreOffice/PDF pipeline, rather than off either side's source format.

Nothing here calls a model. Every metric is arithmetic over the page's text
spans, drawing paths, images and pixels, so a scorecard is reproducible and a
regression is attributable.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Optional, Sequence

# --------------------------------------------------------------------------
# Tuning constants. Every one of these was set by measuring the design-set
# pages of the reference deck (see corpus.DESIGN_PAGES) and checking the
# result against what is visibly on the page.
# --------------------------------------------------------------------------

# The content area, as a fraction of the page. Chrome (eyebrow, footer, page
# number, source line) lives outside it, so ink coverage measures the slide's
# argument rather than its furniture.
CONTENT_INSET_X = 0.035
CONTENT_INSET_TOP = 0.045
CONTENT_INSET_BOTTOM = 0.085

# Ink is sampled on a grid rather than by summing bounding-box areas, so
# overlapping boxes cannot push coverage above 1.0.
INK_GRID_COLS = 96
INK_GRID_ROWS = 54

# A filled path this large is the page's ground, not a mark on it.
GROUND_AREA_FRACTION = 0.15

# Grounds are read off the rendered pixels rather than off the filled paths,
# because paint order decides what a reader sees: the reference deck's split
# pages paint white paper first and a navy half over it, and a path-area
# reading calls that "one white ground covering everything".
GROUND_GRID_COLS = 24
GROUND_GRID_ROWS = 14
GROUND_RASTER_DPI = 24
# A colour has to hold this share of the ground grid to count as a ground.
GROUND_CELL_FRACTION = 0.12
# Two grounds this size each is a split canvas.
GROUND_SPLIT_CELL_FRACTION = 0.18
# Colours closer than this collapse into one ground, so anti-aliasing at a
# split boundary does not read as a third canvas.
GROUND_MERGE_DISTANCE = 24

# Text at or below this size is apparatus (footnotes, sources, axis labels),
# not body copy, and is excluded from block clustering.
APPARATUS_MAX_PT = 9.5
# Lines closer than this merge into one cluster. Measured off the reference:
# wrapped lines within a paragraph sit 0-4pt apart, a card's heading sits
# ~10pt above its body, and consecutive row bands sit ~15pt apart. 6pt
# therefore keeps a paragraph whole while still separating the fifteen
# sentence-cells of reference page 12's comparison matrix, which a larger
# threshold collapses into one slab and reports as the sparsest page in the
# deck.
BLOCK_MERGE_X_PT = 10.0
BLOCK_MERGE_Y_PT = 6.0
# Two clusters sharing a horizontal row are one row band when one is much
# narrower than the other - that is a label column beside a prose column, as
# on reference page 40. Two cells of similar width sharing a row is a split
# canvas (pages 30, 66), and its halves stay two blocks.
ROW_BAND_MAX_CELLS = 2
ROW_BAND_MIN_SPAN = 0.6
ROW_BAND_MIN_WIDTH_RATIO = 2.5

# Chrome zones, as fractions of the page.
EYEBROW_MAX_Y = 0.075
FOOTER_MIN_Y = 0.88
PAGE_NUMBER_MIN_X = 0.88

# A cluster of vector marks this large is an exhibit. Item count is not a
# usable signal: PyMuPDF returns a chart's whole bar series as one path with
# many subpaths, so the reference deck's bar charts arrive as two drawings
# while a row of four icon badges arrives as four. Painted area is what
# separates them.
CHART_MIN_BBOX_FRACTION = 0.03
CHART_MIN_PAINTED_FRACTION = 0.012
CHART_CLUSTER_GAP_PT = 18.0
# An embedded image this large is an exhibit, not an icon.
IMAGE_MIN_AREA_FRACTION = 0.02
# An exhibit built from at most this many colours is a chart; more than that
# and it is a photograph. Colour count is the discriminator rather than area
# or flatness because it survives both modalities: the reference deck's bar
# charts are vector paths in three colours, ours are PNGs in five, and the
# five photographs on reference page 168 run to hundreds either way.
CHART_MAX_COLORS = 10
# ...counting only colours holding at least this share of the region, so
# anti-aliasing along a bar edge does not inflate the count.
CHART_COLOR_MIN_SHARE = 0.01

# Line detection inside a chart region, on a rasterised crop.
CHART_RASTER_DPI = 110
# Exhibit classification needs enough resolution that anti-aliasing along a
# bar edge is a minority of pixels; at the ground grid's 24dpi it is not.
EXHIBIT_CLASSIFY_DPI = 72
CHART_LINE_SPAN_FRACTION = 0.55
CHART_LINE_MAX_THICKNESS_PX = 3

# The archetype signature grid. Coarse on purpose: it should say "dark left
# third, content right" and not encode which words are on the page.
SIGNATURE_COLS = 12
SIGNATURE_ROWS = 8

_SOURCE_LINE_RE = re.compile(r"^\s*sources?\s*[::]", re.IGNORECASE)
_PAGE_NUMBER_RE = re.compile(r"^\s*\d{1,3}\s*$")
_FOOTNOTE_MARKER_RE = re.compile(r"^[\d*†‡]{1,2}$")
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-%$.]*")


@dataclass
class ChartRegion:
    """An exhibit found on the page, and how it is drawn."""
    bbox: tuple[float, float, float, float]
    source: str                      # "vector" | "image"
    kind: str = "chart"              # "chart" | "figure"
    has_horizontal_gridlines: bool = False
    has_vertical_axis: bool = False
    has_horizontal_axis: bool = False
    gridline_count: int = 0


@dataclass
class PageMetrics:
    """Everything the harness knows about one rendered page."""
    page_number: int
    width_pt: float
    height_pt: float

    ink_coverage: float = 0.0
    block_count: int = 0
    word_count: int = 0
    char_count: int = 0

    grounds: list[tuple[str, float]] = field(default_factory=list)
    has_ground_split: bool = False

    type_ramp: dict[str, int] = field(default_factory=dict)      # "14.0" -> chars
    faces: dict[str, int] = field(default_factory=dict)          # font name -> chars
    display_face: Optional[str] = None
    display_size_pt: Optional[float] = None
    body_face: Optional[str] = None
    body_size_pt: Optional[float] = None
    display_is_serif: bool = False
    text_colors: dict[str, int] = field(default_factory=dict)    # "#RRGGBB" -> chars

    has_eyebrow: bool = False
    has_page_number: bool = False
    has_brand_mark: bool = False
    has_footnote_block: bool = False
    has_source_line: bool = False
    superscript_markers: int = 0

    charts: list[ChartRegion] = field(default_factory=list)
    signature: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["charts"] = [asdict(c) for c in self.charts]
        return data


# --------------------------------------------------------------------------
# Geometry helpers
# --------------------------------------------------------------------------

def _area(rect: Sequence[float]) -> float:
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def _intersects(a: Sequence[float], b: Sequence[float], pad_x: float = 0.0,
                pad_y: float = 0.0) -> bool:
    return not (
        a[2] + pad_x < b[0] or b[2] + pad_x < a[0]
        or a[3] + pad_y < b[1] or b[3] + pad_y < a[1]
    )


def _union(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float, float]:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _rgb_int_to_hex(value: Any) -> Optional[str]:
    """PyMuPDF hands colours back as an int, or a 0-1 float triple."""
    if value is None:
        return None
    if isinstance(value, (tuple, list)):
        if len(value) < 3:
            return None
        r, g, b = (max(0, min(255, int(round(float(c) * 255)))) for c in value[:3])
        return f"#{r:02X}{g:02X}{b:02X}"
    try:
        as_int = int(value)
    except (TypeError, ValueError):
        return None
    return f"#{as_int & 0xFFFFFF:06X}"


def _is_serif(font_name: str) -> bool:
    lowered = (font_name or "").lower()
    if any(k in lowered for k in ("arial", "helvetica", "sans", "verdana", "tahoma",
                                  "calibri", "roboto", "inter", "futura", "gill")):
        return False
    return any(k in lowered for k in ("georgia", "times", "serif", "garamond", "book",
                                      "cambria", "merriweather", "playfair", "minion",
                                      "caslon", "baskerville", "didot", "bodoni"))


# --------------------------------------------------------------------------
# The measurement
# --------------------------------------------------------------------------

def measure_page(page, page_number: Optional[int] = None) -> PageMetrics:
    """Measure a single ``fitz.Page``."""
    width, height = float(page.rect.width), float(page.rect.height)
    metrics = PageMetrics(
        page_number=page_number if page_number is not None else page.number + 1,
        width_pt=width,
        height_pt=height,
    )

    spans = _collect_spans(page)
    drawings = _collect_drawings(page, width, height)
    images = _collect_images(page, width, height)
    ground_cells = _ground_cells(page)

    _measure_grounds(metrics, ground_cells)
    _measure_type(metrics, spans)
    _measure_chrome(metrics, spans, width, height)
    metrics.superscript_markers = _count_superscript_markers(page)

    metrics.charts = _find_charts(page, drawings, images, width, height)
    _measure_chart_style(page, metrics.charts)

    content_rect = (
        width * CONTENT_INSET_X,
        height * CONTENT_INSET_TOP,
        width * (1.0 - CONTENT_INSET_X),
        height * (1.0 - CONTENT_INSET_BOTTOM),
    )
    metrics.ink_coverage = _measure_ink(spans, drawings, images, content_rect, width, height)
    metrics.block_count = _count_blocks(metrics, spans, content_rect)
    metrics.signature = _signature(spans, drawings, images, ground_cells, width, height)
    return metrics


def measure_pdf(pdf_path: str, pages: Optional[Iterable[int]] = None) -> list[PageMetrics]:
    """Measure a whole PDF, or the given 1-based pages of it."""
    import fitz

    doc = fitz.open(pdf_path)
    try:
        wanted = list(pages) if pages is not None else list(range(1, len(doc) + 1))
        out: list[PageMetrics] = []
        for page_number in wanted:
            if not 1 <= page_number <= len(doc):
                continue
            out.append(measure_page(doc[page_number - 1], page_number))
        return out
    finally:
        doc.close()


# --------------------------------------------------------------------------
# Collection
# --------------------------------------------------------------------------

def _collect_spans(page) -> list[dict]:
    """Every text span with its box, size, face, colour and text."""
    spans: list[dict] = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text") or ""
                if not text.strip():
                    continue
                spans.append({
                    "bbox": tuple(float(v) for v in span["bbox"]),
                    "text": text,
                    "size": round(float(span.get("size", 0.0)), 1),
                    "font": str(span.get("font") or ""),
                    "color": _rgb_int_to_hex(span.get("color")) or "#000000",
                    "line_bbox": tuple(float(v) for v in line.get("bbox", span["bbox"])),
                })
    return spans


def _collect_drawings(page, width: float, height: float) -> list[dict]:
    """Filled and stroked paths, with their colour and box."""
    out: list[dict] = []
    try:
        raw = page.get_drawings()
    except Exception:
        return out
    for item in raw:
        rect = item.get("rect")
        if rect is None:
            continue
        bbox = (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
        out.append({
            "bbox": bbox,
            "area": _area(bbox),
            "fill": _rgb_int_to_hex(item.get("fill")),
            "stroke": _rgb_int_to_hex(item.get("color")),
            "width": float(item.get("width") or 0.0),
        })
    return out


def _collect_images(page, width: float, height: float) -> list[dict]:
    out: list[dict] = []
    try:
        infos = page.get_image_info()
    except Exception:
        return out
    for info in infos:
        bbox = info.get("bbox")
        if not bbox:
            continue
        rect = tuple(float(v) for v in bbox)
        if _area(rect) <= 0:
            continue
        out.append({"bbox": rect, "area": _area(rect)})
    return out


# --------------------------------------------------------------------------
# Grounds
# --------------------------------------------------------------------------

def _ground_cells(page) -> list[list[str]]:
    """
    The page's ground colour per cell of a coarse grid, read off the rendered
    pixels. Each cell reports its own modal colour, so a cell carrying a line
    of text still reports the paper behind the text rather than the ink.
    """
    try:
        import numpy as np
    except Exception:                                     # pragma: no cover
        return []
    try:
        pix = page.get_pixmap(dpi=GROUND_RASTER_DPI, alpha=False)
        array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n
        )[:, :, :3]
    except Exception:                                     # pragma: no cover
        return []
    if array.size == 0:
        return []

    height_px, width_px = array.shape[:2]
    rows: list[list[str]] = []
    for row in range(GROUND_GRID_ROWS):
        y0 = int(row * height_px / GROUND_GRID_ROWS)
        y1 = max(y0 + 1, int((row + 1) * height_px / GROUND_GRID_ROWS))
        row_colors: list[str] = []
        for col in range(GROUND_GRID_COLS):
            x0 = int(col * width_px / GROUND_GRID_COLS)
            x1 = max(x0 + 1, int((col + 1) * width_px / GROUND_GRID_COLS))
            patch = array[y0:y1, x0:x1].reshape(-1, 3)
            # Quantise before taking the mode, so anti-aliased edges do not
            # each count as their own colour, then average the winning
            # bucket's own pixels so the reported hex is the real ground and
            # not the corner of a quantisation cell.
            keys = (patch // 8).astype(np.int32)
            packed = keys[:, 0] * 4096 + keys[:, 1] * 64 + keys[:, 2]
            values, counts = np.unique(packed, return_counts=True)
            winner = int(values[int(np.argmax(counts))])
            members = patch[packed == winner]
            r, g, b = (int(round(float(v))) for v in members.mean(axis=0))
            row_colors.append(f"#{min(r, 255):02X}{min(g, 255):02X}{min(b, 255):02X}")
        rows.append(row_colors)
    return rows


def _color_distance(a: str, b: str) -> float:
    from app import colors

    rgb_a, rgb_b = colors.parse_hex(a), colors.parse_hex(b)
    if rgb_a is None or rgb_b is None:
        return 255.0
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(rgb_a, rgb_b)))


def _measure_grounds(metrics: PageMetrics, ground_cells: list[list[str]]) -> None:
    if not ground_cells:
        metrics.grounds = [("#FFFFFF", 1.0)]
        return

    flat = [color for row in ground_cells for color in row]
    total = len(flat)
    tally: dict[str, int] = {}
    for color in flat:
        for known in tally:
            if _color_distance(color, known) <= GROUND_MERGE_DISTANCE:
                tally[known] += 1
                break
        else:
            tally[color] = 1

    grounds = sorted(tally.items(), key=lambda kv: -kv[1])
    metrics.grounds = [
        (color, round(count / total, 4))
        for color, count in grounds
        if count / total >= GROUND_CELL_FRACTION
    ] or [(grounds[0][0], round(grounds[0][1] / total, 4))]

    major = [c for c, fraction in metrics.grounds if fraction >= GROUND_SPLIT_CELL_FRACTION]
    metrics.has_ground_split = len(major) >= 2


# --------------------------------------------------------------------------
# Type
# --------------------------------------------------------------------------

def _measure_type(metrics: PageMetrics, spans: list[dict]) -> None:
    ramp: dict[str, int] = {}
    faces: dict[str, int] = {}
    text_colors: dict[str, int] = {}
    words = 0
    chars = 0
    for span in spans:
        length = len(span["text"].strip())
        if not length:
            continue
        chars += length
        words += len(_WORD_RE.findall(span["text"]))
        key = f"{span['size']:.1f}"
        ramp[key] = ramp.get(key, 0) + length
        faces[span["font"]] = faces.get(span["font"], 0) + length
        text_colors[span["color"]] = text_colors.get(span["color"], 0) + length

    metrics.type_ramp = dict(sorted(ramp.items(), key=lambda kv: float(kv[0])))
    metrics.faces = dict(sorted(faces.items(), key=lambda kv: -kv[1]))
    metrics.text_colors = dict(sorted(text_colors.items(), key=lambda kv: -kv[1]))
    metrics.word_count = words
    metrics.char_count = chars

    body_spans = [s for s in spans if s["size"] > APPARATUS_MAX_PT]
    if body_spans:
        # Body is the size carrying the most copy; display is the largest size
        # that carries a real line rather than a stray glyph.
        by_size: dict[float, int] = {}
        by_size_face: dict[float, dict[str, int]] = {}
        for span in body_spans:
            length = len(span["text"].strip())
            by_size[span["size"]] = by_size.get(span["size"], 0) + length
            by_size_face.setdefault(span["size"], {})
            face_counts = by_size_face[span["size"]]
            face_counts[span["font"]] = face_counts.get(span["font"], 0) + length

        body_size = max(by_size, key=lambda s: by_size[s])
        display_candidates = [s for s, n in by_size.items() if n >= 8 and s > body_size]
        display_size = max(display_candidates) if display_candidates else body_size

        metrics.body_size_pt = body_size
        metrics.display_size_pt = display_size
        metrics.body_face = max(by_size_face[body_size], key=by_size_face[body_size].get)
        metrics.display_face = max(
            by_size_face[display_size], key=by_size_face[display_size].get
        )
        metrics.display_is_serif = _is_serif(metrics.display_face or "")


# --------------------------------------------------------------------------
# Chrome and apparatus
# --------------------------------------------------------------------------

def _measure_chrome(metrics: PageMetrics, spans: list[dict],
                    width: float, height: float) -> None:
    for span in spans:
        x0, y0, x1, y1 = span["bbox"]
        text = span["text"].strip()
        small = span["size"] <= APPARATUS_MAX_PT + 2.5

        if small and y1 <= height * EYEBROW_MAX_Y and x0 >= width * 0.4:
            metrics.has_eyebrow = True

        if y0 >= height * FOOTER_MIN_Y:
            if _PAGE_NUMBER_RE.match(text) and x0 >= width * PAGE_NUMBER_MIN_X:
                metrics.has_page_number = True
            elif x0 >= width * 0.6 and not _PAGE_NUMBER_RE.match(text):
                metrics.has_brand_mark = True

        if _SOURCE_LINE_RE.match(text):
            metrics.has_source_line = True
        elif (span["size"] <= APPARATUS_MAX_PT and y0 >= height * 0.82
                and x1 <= width * 0.75):
            metrics.has_footnote_block = True


def _count_superscript_markers(page) -> int:
    """
    Count runs typeset markedly smaller than, and raised above, the text they
    sit beside on the same line - the reference deck's footnote markers.
    """
    count = 0
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            raw_spans = [s for s in line.get("spans", []) if (s.get("text") or "").strip()]
            if len(raw_spans) < 2:
                continue
            base = max(float(s.get("size", 0.0)) for s in raw_spans)
            for span in raw_spans:
                text = (span.get("text") or "").strip()
                size = float(span.get("size", 0.0))
                if not _FOOTNOTE_MARKER_RE.match(text):
                    continue
                if size >= base * 0.85:
                    continue
                count += 1
    return count


# --------------------------------------------------------------------------
# Ink and blocks
# --------------------------------------------------------------------------

def _measure_ink(spans: list[dict], drawings: list[dict], images: list[dict],
                 content_rect: tuple[float, float, float, float],
                 width: float, height: float) -> float:
    page_area = width * height
    cell_w = width / INK_GRID_COLS
    cell_h = height / INK_GRID_ROWS
    inked: set[tuple[int, int]] = set()

    def mark(bbox: Sequence[float]) -> None:
        x0 = max(bbox[0], content_rect[0])
        y0 = max(bbox[1], content_rect[1])
        x1 = min(bbox[2], content_rect[2])
        y1 = min(bbox[3], content_rect[3])
        if x1 <= x0 or y1 <= y0:
            return
        for col in range(int(x0 // cell_w), int(math.ceil(x1 / cell_w))):
            for row in range(int(y0 // cell_h), int(math.ceil(y1 / cell_h))):
                if 0 <= col < INK_GRID_COLS and 0 <= row < INK_GRID_ROWS:
                    inked.add((col, row))

    for span in spans:
        mark(span["bbox"])
    for image in images:
        mark(image["bbox"])
    for item in drawings:
        # Grounds are the page, not marks on it.
        if page_area and item["area"] / page_area >= GROUND_AREA_FRACTION:
            continue
        mark(item["bbox"])

    total_cols = max(1, int(math.ceil(content_rect[2] / cell_w)) - int(content_rect[0] // cell_w))
    total_rows = max(1, int(math.ceil(content_rect[3] / cell_h)) - int(content_rect[1] // cell_h))
    total = total_cols * total_rows
    return round(len(inked) / total, 4) if total else 0.0


def _count_blocks(metrics: PageMetrics, spans: list[dict],
                  content_rect: tuple[float, float, float, float]) -> int:
    """
    Cluster body-size text into the units a reader sees as blocks - a band, a
    card, a callout - and add the page's charts and exhibits.

    Apparatus (footnotes, sources, axis labels) and chrome are excluded: they
    are on every page of a well-made deck and would swamp the count that is
    supposed to say how much argument the page carries.
    """
    boxes: list[tuple[float, float, float, float]] = []
    seen_lines: set[tuple[float, float, float, float]] = set()
    for span in spans:
        if span["size"] <= APPARATUS_MAX_PT:
            continue
        line_bbox = span["line_bbox"]
        if line_bbox in seen_lines:
            continue
        if not _intersects(line_bbox, content_rect):
            continue
        # A chart's own labels belong to the chart, not to the page's prose.
        if any(_intersects(line_bbox, chart.bbox) for chart in metrics.charts):
            continue
        seen_lines.add(line_bbox)
        boxes.append(line_bbox)

    clusters = _merge_boxes(boxes, BLOCK_MERGE_X_PT, BLOCK_MERGE_Y_PT)
    clusters = _merge_row_bands(clusters, content_rect)
    return len(clusters) + len(metrics.charts)


def _merge_boxes(boxes: list[tuple[float, float, float, float]],
                 pad_x: float, pad_y: float) -> list[tuple[float, float, float, float]]:
    """Union every box that touches another, transitively."""
    clusters = [tuple(b) for b in sorted(boxes, key=lambda b: (b[1], b[0]))]
    changed = True
    while changed:
        changed = False
        for i in range(len(clusters)):
            for j in range(len(clusters) - 1, i, -1):
                if _intersects(clusters[i], clusters[j], pad_x, pad_y):
                    clusters[i] = _union(clusters[i], clusters[j])
                    clusters.pop(j)
                    changed = True
            if changed:
                break
    return clusters


def _merge_row_bands(clusters: list[tuple[float, float, float, float]],
                     content_rect: tuple[float, float, float, float],
                     ) -> list[tuple[float, float, float, float]]:
    """
    Collapse a label column beside a prose column into the one row band a
    reader sees, while leaving a card row as its separate cards.

    Both look identical to a proximity clustering - cells side by side with a
    gutter between them - and the distinction that matters is how many cells
    share the row. Reference page 40 is seven bands of two cells; page 95 is
    one row of four cards. Two cells spanning the width is a band; three or
    more is a row of cards.
    """
    content_width = max(1.0, content_rect[2] - content_rect[0])
    rows: list[list[tuple[float, float, float, float]]] = []
    for cluster in sorted(clusters, key=lambda b: b[1]):
        for row in rows:
            if any(min(c[3], cluster[3]) - max(c[1], cluster[1]) > 0 for c in row):
                row.append(cluster)
                break
        else:
            rows.append([cluster])

    merged: list[tuple[float, float, float, float]] = []
    for row in rows:
        if 2 <= len(row) <= ROW_BAND_MAX_CELLS:
            span = max(c[2] for c in row) - min(c[0] for c in row)
            widths = sorted(max(1.0, c[2] - c[0]) for c in row)
            if (span / content_width >= ROW_BAND_MIN_SPAN
                    and widths[-1] / widths[0] >= ROW_BAND_MIN_WIDTH_RATIO):
                box = row[0]
                for other in row[1:]:
                    box = _union(box, other)
                merged.append(box)
                continue
        merged.extend(row)
    return merged


# --------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------

def _find_charts(page, drawings: list[dict], images: list[dict],
                 width: float, height: float) -> list[ChartRegion]:
    """
    A chart is either an embedded raster of consequential size (how our own
    renderer emits one) or a dense cluster of vector paths (how the reference
    deck's are drawn). Both are found here so the style checks downstream do
    not care which pipeline produced the page.
    """
    page_area = width * height
    regions: list[ChartRegion] = []

    for image in images:
        if not page_area or image["area"] / page_area < IMAGE_MIN_AREA_FRACTION:
            continue
        # One picture can be reported once per XObject reference; a region
        # already covering this box is the same exhibit.
        if any(_intersects(image["bbox"], r.bbox) for r in regions):
            continue
        regions.append(ChartRegion(bbox=image["bbox"], source="image"))

    marks = [
        item for item in drawings
        if page_area and 0 < item["area"] / page_area < GROUND_AREA_FRACTION
    ]
    clusters: list[dict] = []
    for item in sorted(marks, key=lambda d: (d["bbox"][1], d["bbox"][0])):
        for cluster in clusters:
            if _intersects(item["bbox"], cluster["bbox"],
                           CHART_CLUSTER_GAP_PT, CHART_CLUSTER_GAP_PT):
                cluster["bbox"] = _union(cluster["bbox"], item["bbox"])
                cluster["painted"] += item["area"]
                break
        else:
            clusters.append({"bbox": item["bbox"], "painted": item["area"]})

    for cluster in clusters:
        if not page_area:
            continue
        if _area(cluster["bbox"]) / page_area < CHART_MIN_BBOX_FRACTION:
            continue
        if cluster["painted"] / page_area < CHART_MIN_PAINTED_FRACTION:
            continue
        if any(_intersects(cluster["bbox"], r.bbox) for r in regions):
            continue
        regions.append(ChartRegion(bbox=cluster["bbox"], source="vector"))

    for region in regions:
        region.kind = _classify_exhibit(page, region.bbox)
    return regions


def _classify_exhibit(page, bbox: tuple[float, float, float, float]) -> str:
    """A chart is drawn from a handful of colours; a photograph is not."""
    try:
        import fitz
        import numpy as np
    except Exception:                                     # pragma: no cover
        return "chart"
    try:
        pix = page.get_pixmap(clip=fitz.Rect(*bbox), dpi=EXHIBIT_CLASSIFY_DPI, alpha=False)
        array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n
        )[:, :, :3]
    except Exception:                                     # pragma: no cover
        return "chart"
    if array.size == 0:
        return "chart"
    keys = (array.reshape(-1, 3) // 16).astype(np.int32)
    packed = keys[:, 0] * 256 + keys[:, 1] * 16 + keys[:, 2]
    _, counts = np.unique(packed, return_counts=True)
    significant = int((counts / float(packed.size) >= CHART_COLOR_MIN_SHARE).sum())
    return "chart" if significant <= CHART_MAX_COLORS else "figure"


def _measure_chart_style(page, charts: list[ChartRegion]) -> None:
    """
    Rasterise each chart region and look for the long straight lines that
    make an axis and a gridline. Rasterising is what lets one check cover both
    a vector chart and an embedded PNG: whatever drew it, the question "is
    there a rule spanning most of this box" has the same answer in pixels.
    """
    if not charts:
        return
    try:
        import fitz
        import numpy as np
    except Exception:                                     # pragma: no cover
        return

    for chart in charts:
        if chart.kind != "chart":
            continue
        try:
            clip = fitz.Rect(*chart.bbox)
            if clip.width < 8 or clip.height < 8:
                continue
            pix = page.get_pixmap(clip=clip, dpi=CHART_RASTER_DPI, alpha=False)
            array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n
            )
        except Exception:                                 # pragma: no cover
            continue
        if array.size == 0:
            continue

        gray = array[:, :, :3].mean(axis=2)
        background = float(np.median(gray))
        ink = np.abs(gray - background) > 24

        height_px, width_px = ink.shape
        min_h_span = int(width_px * CHART_LINE_SPAN_FRACTION)
        min_v_span = int(height_px * CHART_LINE_SPAN_FRACTION)

        row_runs = _long_runs(ink, axis=1, minimum=min_h_span)
        col_runs = _long_runs(ink, axis=0, minimum=min_v_span)

        horizontal = _thin_bands(row_runs)
        vertical = _thin_bands(col_runs)

        # A rule sitting at the very bottom/left of the plot is the axis; the
        # rest, spread through the plot, are gridlines.
        chart.has_horizontal_axis = any(index >= height_px * 0.75 for index in horizontal)
        chart.has_vertical_axis = any(index <= width_px * 0.25 for index in vertical)
        interior = [i for i in horizontal if height_px * 0.05 < i < height_px * 0.75]
        chart.gridline_count = len(interior)
        chart.has_horizontal_gridlines = len(interior) >= 2


def _long_runs(ink, axis: int, minimum: int) -> list[int]:
    """Indices of rows (axis=1) or columns (axis=0) that are mostly inked."""
    import numpy as np

    counts = ink.sum(axis=axis)
    return [int(i) for i in np.nonzero(counts >= minimum)[0]]


def _thin_bands(indices: list[int]) -> list[int]:
    """Collapse consecutive indices into one line, dropping thick slabs."""
    bands: list[int] = []
    run: list[int] = []
    for index in indices:
        if run and index == run[-1] + 1:
            run.append(index)
            continue
        if run and len(run) <= CHART_LINE_MAX_THICKNESS_PX:
            bands.append(run[len(run) // 2])
        run = [index]
    if run and len(run) <= CHART_LINE_MAX_THICKNESS_PX:
        bands.append(run[len(run) // 2])
    return bands


# --------------------------------------------------------------------------
# Archetype signature
# --------------------------------------------------------------------------

def _signature(spans: list[dict], drawings: list[dict], images: list[dict],
               ground_cells: list[list[str]], width: float, height: float) -> list[float]:
    """
    A coarse ink map plus a coarse map of how dark the ground is, as a vector
    two pages of the same archetype should sit close in. Deliberately blunt:
    it should say "dark rail on the left, exhibit on the right" and know
    nothing about which words are on the page.
    """
    cell_w = width / SIGNATURE_COLS
    cell_h = height / SIGNATURE_ROWS
    grid = [0.0] * (SIGNATURE_COLS * SIGNATURE_ROWS)
    page_area = width * height

    def add(bbox: Sequence[float], weight: float) -> None:
        for col in range(SIGNATURE_COLS):
            for row in range(SIGNATURE_ROWS):
                cell = (col * cell_w, row * cell_h, (col + 1) * cell_w, (row + 1) * cell_h)
                overlap = _rect_overlap(bbox, cell)
                if overlap <= 0:
                    continue
                index = row * SIGNATURE_COLS + col
                grid[index] = min(1.0, grid[index] + weight * overlap / (cell_w * cell_h))

    for span in spans:
        add(span["bbox"], 1.0)
    for image in images:
        add(image["bbox"], 1.0)
    for item in drawings:
        if page_area and item["area"] / page_area >= GROUND_AREA_FRACTION:
            continue
        add(item["bbox"], 0.8)

    from app import colors

    darkness = [0.0] * (SIGNATURE_COLS * SIGNATURE_ROWS)
    if ground_cells:
        source_rows, source_cols = len(ground_cells), len(ground_cells[0])
        for row in range(SIGNATURE_ROWS):
            for col in range(SIGNATURE_COLS):
                src_row = min(source_rows - 1, row * source_rows // SIGNATURE_ROWS)
                src_col = min(source_cols - 1, col * source_cols // SIGNATURE_COLS)
                ground = ground_cells[src_row][src_col]
                darkness[row * SIGNATURE_COLS + col] = 1.0 - colors.relative_luminance(ground)

    return [round(v, 4) for v in (*grid, *darkness)]


def _rect_overlap(a: Sequence[float], b: Sequence[float]) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def signature_distance(a: Sequence[float], b: Sequence[float]) -> float:
    """Euclidean distance between two page signatures, normalised to 0-1."""
    if not a or not b or len(a) != len(b):
        return 1.0
    total = sum((x - y) ** 2 for x, y in zip(a, b))
    return min(1.0, math.sqrt(total / len(a)))
