"""
Measuring composition - the thing the existing scorecard cannot see.

Nine of the eleven style metrics are permutation-invariant. Take a rendered
page, shuffle every element on it to a random position, and ink coverage,
words per slide, the type ramp, the palette and the block count all come
back identical. A metric that cannot tell a designed page from a scrambled
one cannot tell whether a change to the layout engine helped, which is why
three passes of pipeline work moved the number by less than its own
run-to-run noise.

Everything here is *positional*. Each metric is a function of where things
are relative to each other and to the page, so scrambling a page is
guaranteed to move it - and `discriminate.py` turns that guarantee into a
test that every metric has to pass before it is allowed to steer anything.

All of it reads a rendered page, which is what makes the reference decks and
our own output measurable on the same terms: both are PDFs by the time they
get here.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

# Two edges within this fraction of the page width are the same edge. Set
# from the printer's tolerance rather than the renderer's: below about a
# thousandth of a page nobody can see the difference.
ALIGNMENT_TOLERANCE = 0.004
# An element smaller than this is furniture, not composition.
MIN_ELEMENT_AREA = 0.00015
# A drawing covering more of the page than this is a *ground* - the colour
# the page is set on - not something composed on it. Counting one as content
# lets a single background rectangle carry 87% of the page's area and swamp
# every area-weighted reading taken from it.
GROUND_AREA = 0.5
# The occupancy raster the empty-space search runs on. Fine enough to find a
# hole worth naming, coarse enough to stay instant.
_HOLE_RASTER = 96


@dataclass(frozen=True)
class Element:
    """One thing on the page, in fractions of the page."""

    x0: float
    y0: float
    x1: float
    y1: float
    kind: str = "text"

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    @property
    def area(self) -> float:
        return self.width * self.height


@dataclass(frozen=True)
class Baseline:
    """Where one line of text sits, and what size it is set at."""

    position_pt: float
    size_pt: float


@dataclass(frozen=True)
class PageObservation:
    """A rendered page, reduced to what composition is made of."""

    width_pt: float
    height_pt: float
    elements: tuple[Element, ...] = ()
    baselines: tuple[Baseline, ...] = ()
    # Baselines grouped by the text block they belong to. A rhythm is a
    # property of a column, and pooling two columns and sorting them
    # manufactures gaps belonging to neither - which is enough on its own to
    # read a page that keeps a strict rhythm as keeping none.
    baseline_columns: tuple[tuple[Baseline, ...], ...] = ()

    @property
    def content(self) -> tuple[Element, ...]:
        """What is composed on the page, excluding the grounds it sits on."""
        return tuple(
            e for e in self.elements
            if e.area >= MIN_ELEMENT_AREA
            and not (e.kind == "drawing" and e.area > GROUND_AREA)
        )

    @property
    def grounds(self) -> tuple[Element, ...]:
        return tuple(
            e for e in self.elements
            if e.kind == "drawing" and e.area > GROUND_AREA
        )

    @property
    def body_baseline_columns(self) -> tuple[tuple[float, ...], ...]:
        """Body-size baselines, one tuple per column, for rhythm reading."""
        dominant = self._dominant_size()
        if dominant is None:
            return ()
        out = []
        for column in self.baseline_columns:
            kept = tuple(b.position_pt for b in column
                         if abs(b.size_pt - dominant) <= 0.6)
            if len(kept) >= 2:
                out.append(kept)
        return tuple(out)

    def _dominant_size(self) -> Optional[float]:
        counts: dict[float, int] = {}
        for entry in self.baselines:
            counts[entry.size_pt] = counts.get(entry.size_pt, 0) + 1
        return max(counts, key=lambda s: counts[s]) if counts else None

    @property
    def body_baselines_pt(self) -> tuple[float, ...]:
        """Baselines of the page's dominant type size.

        A rhythm is kept by body copy. A headline, a folio and a pull-quote
        are set to their own optical positions and sit wherever they look
        right, so measuring the rhythm across every size on the page reads a
        deck that keeps one perfectly as though it kept none.
        """
        if not self.baselines:
            return ()
        counts: dict[float, int] = {}
        for entry in self.baselines:
            counts[entry.size_pt] = counts.get(entry.size_pt, 0) + 1
        dominant = max(counts, key=lambda size: counts[size])
        return tuple(b.position_pt for b in self.baselines
                     if b.size_pt == dominant)


def observe_page(page) -> PageObservation:
    """Read a fitz page into the terms every metric here is written in."""
    width, height = float(page.rect.width), float(page.rect.height)
    elements: list[Element] = []
    baselines: list[Baseline] = []
    columns: list[tuple[Baseline, ...]] = []

    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        text = "".join(
            span["text"] for line in block.get("lines", []) for span in line["spans"]
        ).strip()
        if not text:
            continue
        x0, y0, x1, y1 = block["bbox"]
        elements.append(Element(x0 / width, y0 / height, x1 / width, y1 / height,
                                "text"))
        block_lines: list[Baseline] = []
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"].strip()]
            if spans:
                block_lines.append(Baseline(
                    position_pt=max(s["bbox"][3] for s in spans),
                    size_pt=round(max(s["size"] for s in spans), 1),
                ))
        if block_lines:
            baselines.extend(block_lines)
            columns.append(tuple(block_lines))

    for entry in page.get_images(full=True):
        for rect in page.get_image_rects(entry[0]):
            elements.append(Element(rect.x0 / width, rect.y0 / height,
                                    rect.x1 / width, rect.y1 / height, "image"))

    for drawing in page.get_drawings():
        rect = drawing["rect"]
        if rect.width <= 0 or rect.height <= 0:
            continue
        elements.append(Element(rect.x0 / width, rect.y0 / height,
                                rect.x1 / width, rect.y1 / height, "drawing"))

    return PageObservation(width, height, tuple(elements), tuple(baselines),
                           tuple(columns))


# ── the metrics ────────────────────────────────────────────────────────────


def alignment_score(obs: PageObservation) -> float:
    """How much of the page lines up with the rest of it.

    A publication page puts many elements on few vertical lines. A slide
    puts each element wherever its placeholder happened to be. So: cluster
    the left and right edges, and score the ratio of elements to distinct
    edges - one edge shared by four elements is worth four edges used once.
    """
    content = obs.content
    if len(content) < 2:
        return 0.0
    positions = [e.x0 for e in content] + [e.x1 for e in content]
    clusters = _cluster(positions, ALIGNMENT_TOLERANCE)
    # Perfect: every element shares one left and one right edge -> 2 clusters.
    # Worst: nothing shares anything -> 2 * len(content) clusters.
    best, worst = 2, 2 * len(content)
    if worst == best:
        return 1.0
    return max(0.0, min(1.0, (worst - len(clusters)) / (worst - best)))


def grid_conformance(obs: PageObservation, grid) -> float:
    """Share of edge mass that lands on the grid the pack declares.

    Area-weighted, so a page number in the margin cannot outvote a body
    column. Scored against a recovered grid; with no grid there is nothing
    to conform to and the metric is unavailable rather than zero.
    """
    if grid is None:
        return float("nan")
    content = obs.content
    if not content:
        return float("nan")
    total = 0.0
    on = 0.0
    for element in content:
        weight = element.area
        for position in (element.x0, element.x1):
            total += weight
            _, distance = grid.snap(position)
            if distance <= ALIGNMENT_TOLERANCE * 1.5:
                on += weight
    return (on / total) if total else float("nan")


def baseline_rhythm(obs: PageObservation, unit_pt: float) -> float:
    """How closely the body text sits to its repeating baseline.

    Measured per column and pooled, against the unit the pack recovered.
    Reported as a score so it reads the same way as everything else here:
    1 is on the rhythm, 0 is indifferent to it.
    """
    from app.measure.rhythm import RANDOM_RESIDUAL, _best_phase

    if unit_pt <= 0:
        return float("nan")
    columns = obs.body_baseline_columns
    if not columns:
        return float("nan")
    residuals, weights = [], []
    for column in columns:
        _, residual = _best_phase(column, unit_pt)
        residuals.append(residual)
        weights.append(len(column))
    pooled = sum(r * w for r, w in zip(residuals, weights)) / sum(weights)
    return max(0.0, min(1.0, 1.0 - pooled / RANDOM_RESIDUAL))


def margin_consistency(obs: PageObservation, grid, canvas_w_in: float,
                       canvas_h_in: float) -> float:
    """Whether the page respects the margins its pack declares."""
    if grid is None:
        return float("nan")
    content = obs.content
    inside = [e for e in content if e.area < 0.85]
    if not inside:
        return float("nan")
    left = min(e.x0 for e in inside)
    right = max(e.x1 for e in inside)
    top = min(e.y0 for e in inside)
    bottom = max(e.y1 for e in inside)

    expected = (
        grid.margin.left / canvas_w_in,
        1.0 - grid.margin.right / canvas_w_in,
        grid.margin.top / canvas_h_in,
        1.0 - grid.margin.bottom / canvas_h_in,
    )
    errors = [
        abs(left - expected[0]), abs(right - expected[1]),
        abs(top - expected[2]), abs(bottom - expected[3]),
    ]
    return max(0.0, min(1.0, 1.0 - sum(errors) / len(errors) / 0.1))


def largest_hole(obs: PageObservation) -> float:
    """The biggest empty rectangle on the page, as a share of it.

    The half-finished-page detector. A title with four bullets on a
    thirteen-inch canvas is not distinguishable from a well-composed page by
    ink coverage alone - both can read as 30% - but one of them has a single
    contiguous void where the rest of the argument should be.

    **Diagnostic only**: useful to look at, not validated to score by.
    """
    content = obs.content
    if not content:
        return 1.0
    # Rasterised and solved as largest-rectangle-in-a-histogram rather than
    # searched over every pair of element edges: the exact search is O(n^4)
    # in the number of candidate coordinates, which on a real page is tens of
    # millions of overlap tests for an answer nobody needs to four decimals.
    cols = rows = _HOLE_RASTER
    occupied = [[False] * cols for _ in range(rows)]
    for element in content:
        c0 = max(0, min(cols - 1, int(element.x0 * cols)))
        c1 = max(0, min(cols, int(math.ceil(element.x1 * cols))))
        r0 = max(0, min(rows - 1, int(element.y0 * rows)))
        r1 = max(0, min(rows, int(math.ceil(element.y1 * rows))))
        for row in range(r0, r1):
            row_cells = occupied[row]
            for col in range(c0, c1):
                row_cells[col] = True

    heights = [0] * cols
    best_cells = 0
    for row in range(rows):
        for col in range(cols):
            heights[col] = 0 if occupied[row][col] else heights[col] + 1
        best_cells = max(best_cells, _largest_in_histogram(heights))
    return best_cells / float(rows * cols)


def whitespace_evenness(obs: PageObservation) -> float:
    """1 when the empty space is distributed, 0 when it pools in one hole.

    **Diagnostic only - not a scored metric.** It failed the discrimination
    gate at +0.084 against a required 0.10, and nothing that fails the gate
    is allowed to steer anything.
    """
    hole = largest_hole(obs)
    ink = sum(e.area for e in obs.content)
    empty = max(1e-6, 1.0 - min(1.0, ink))
    return max(0.0, min(1.0, 1.0 - hole / empty))


# ── composite ──────────────────────────────────────────────────────────────

# reading_order was here and is deliberately gone. It compared a page's
# content-stream order against its visual order, but a PDF's stream order is
# not its reading order - the reference scored 0.000 on its own pages - and
# the intended order only exists for pages we generated ourselves, so it was
# never comparable against a reference. The rule is that a metric which
# fails discrimination is deleted rather than tuned until it passes.
# whitespace_evenness was here and is deliberately gone too. It separated a
# real EY page from a scrambled one by only 0.084, under the 0.10 bar: the
# page's largest void is already 92% of its empty space, so scrambling
# cannot make it materially worse and the metric has almost no dynamic range
# on the pages that matter. `largest_hole` survives below as a diagnostic
# because spotting a half-finished page is genuinely useful - but it is not
# a scored metric and must not become one without passing the gate.
METRIC_NAMES = (
    "alignment", "grid_conformance", "baseline_rhythm", "margin_consistency",
)


@dataclass(frozen=True)
class CompositionScore:
    """Every positional reading of one page, and their mean."""

    values: dict[str, float] = field(default_factory=dict)

    @property
    def usable(self) -> dict[str, float]:
        return {
            k: v for k, v in self.values.items()
            if v is not None and not math.isnan(v)
        }

    @property
    def composite(self) -> float:
        usable = self.usable
        return sum(usable.values()) / len(usable) if usable else float("nan")

    def to_markdown(self) -> str:
        lines = ["| metric | score |", "| --- | --- |"]
        for name in METRIC_NAMES:
            value = self.values.get(name)
            shown = "n/a" if value is None or math.isnan(value) else f"{value:.3f}"
            lines.append(f"| {name} | {shown} |")
        lines.append(f"| **composite** | **{self.composite:.3f}** |")
        return "\n".join(lines)


def measure_page(obs: PageObservation, *, grid=None, pack_grid=None,
                 baseline_pt: float = 0.0, canvas_w_in: float = 0.0,
                 canvas_h_in: float = 0.0) -> CompositionScore:
    """Score one rendered page on every positional metric that applies."""
    return CompositionScore(values={
        "alignment": alignment_score(obs),
        "grid_conformance": grid_conformance(obs, grid),
        "baseline_rhythm": baseline_rhythm(obs, baseline_pt),
        "margin_consistency": (
            margin_consistency(obs, pack_grid, canvas_w_in, canvas_h_in)
            if pack_grid is not None and canvas_w_in and canvas_h_in
            else float("nan")
        ),
    })


# ── helpers ────────────────────────────────────────────────────────────────


def _cluster(positions: Sequence[float], tolerance: float) -> list[float]:
    """Positions grouped into the distinct edges they represent."""
    if not positions:
        return []
    ordered = sorted(positions)
    clusters = [[ordered[0]]]
    for position in ordered[1:]:
        if position - clusters[-1][-1] <= tolerance:
            clusters[-1].append(position)
        else:
            clusters.append([position])
    return [sum(c) / len(c) for c in clusters]


def _best_phase(values: Sequence[float], unit: float) -> float:
    sin_sum = cos_sum = 0.0
    for value in values:
        angle = 2.0 * math.pi * (value / unit)
        sin_sum += math.sin(angle)
        cos_sum += math.cos(angle)
    if sin_sum == 0.0 and cos_sum == 0.0:
        return 0.0
    phase = math.atan2(sin_sum, cos_sum) / (2.0 * math.pi) * unit
    return phase % unit


def _overlaps(element: Element, x0: float, y0: float, x1: float,
              y1: float) -> bool:
    return not (element.x1 <= x0 or element.x0 >= x1
                or element.y1 <= y0 or element.y0 >= y1)


def _largest_in_histogram(heights: Sequence[int]) -> int:
    """Largest rectangle under a histogram, by the standard stack sweep."""
    stack: list[int] = []
    best = 0
    for index in range(len(heights) + 1):
        current = heights[index] if index < len(heights) else 0
        while stack and heights[stack[-1]] >= current:
            height = heights[stack.pop()]
            left = stack[-1] + 1 if stack else 0
            best = max(best, height * (index - left))
        stack.append(index)
    return best


def _kendall_tau(a: Sequence[int], b: Sequence[int]) -> float:
    """Rank correlation, without pulling in scipy for one function."""
    n = len(a)
    if n < 2:
        return float("nan")
    rank = {value: index for index, value in enumerate(b)}
    concordant = discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            left = a[i] - a[j]
            right = rank[a[i]] - rank[a[j]]
            if left * right > 0:
                concordant += 1
            elif left * right < 0:
                discordant += 1
    total = concordant + discordant
    return (concordant - discordant) / total if total else float("nan")


def iter_pdf_pages(pdf_path: str, pages: Optional[Iterable[int]] = None):
    """Yield (page_number, PageObservation) for a rendered PDF."""
    import fitz

    doc = fitz.open(pdf_path)
    numbers = list(pages) if pages else range(1, doc.page_count + 1)
    for number in numbers:
        yield number, observe_page(doc[number - 1])
