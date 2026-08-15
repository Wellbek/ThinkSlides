"""
A template's layouts, in the language the renderer composes in.

The vision prompt asked a model to describe every layout as a list of
placeholder boxes typed title / body / subtitle / ctrTitle / sldNum / dt / ftr.
That vocabulary has no words for what the reference deck's pages are:

* page 22 is a split canvas, white left half against navy right half
* page 3 has a dark rail one third wide holding the title and a takeaway
* page 40 is seven row bands with hairline rules and a label column
* page 95 is a four-column card row above a full-width banner

Asked to describe those in a language with no words for them, the extractor
dutifully returned "some body boxes", and everything that made the template
look like itself was discarded at that step.

So this reads the grammar instead, in the region vocabulary #122 introduced -
rails, panels, columns, band rows, banners - with **fractional geometry
throughout**, because the reference deck happens to share our canvas exactly
and relying on that would be relying on luck.

**It is measured, not asked.** Where a ground changes colour, where the rules
are and how they are spaced, how many columns a row of cells falls into: these
are readings, and a vision model asked for them returns plausible numbers
rather than true ones. A separate refinement call (see the extractor) may
*name* what is found; it is never the thing that finds it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from app import colors
from app.measure.page_metrics import _collect_drawings, _ground_cells
from app.measure.structure import PageStructure, count_blocks

# A ground band narrower than this, running the full height, is a rail rather
# than half of a split. Reference page 3's rail is 0.375 of the width and page
# 22's split is 0.50, so the line sits between them with room either side.
RAIL_MAX_WIDTH = 0.42
# A ground band has to be at least this wide to be a region at all - narrower
# than this is a rule or a decorative edge, not a panel.
GROUND_MIN_WIDTH = 0.08
# ...and has to hold its colour over this much of the page's height to count
# as running full-height.
GROUND_MIN_HEIGHT = 0.75

# Rules above or below these fractions of the page are deck chrome - the rule
# under the eyebrow and the one over the footer, which are on every page. The
# reference's sit at 0.172 and 0.941, and its first content rule at 0.296.
CHROME_TOP = 0.22
CHROME_BOTTOM = 0.90

# A rule has to span this much of the page to delimit a band or a banner.
RULE_MIN_SPAN = 0.30
# Rules this close together are one rule drawn twice, which is what a stroked
# rectangle looks like from here.
RULE_MERGE = 0.004
# Four rules make three bands, which is the fewest that reads as a set rather
# than as a rule over something and a rule under it. Reference page 130 is a
# six-column card row with a rule above and below it, and at two bands it was
# read as a band row - which then hid the card row that is actually there.
BAND_MIN_RULES = 4
# Bands are evenly spaced. The widest gap may exceed the narrowest by this
# much before the rules are something other than a band row - the reference's
# seven bands vary by 1.5x because their prose wraps to different depths.
BAND_SPACING_TOLERANCE = 2.4
# A band shorter than this fraction of the page is a rule pair, not a band.
BAND_MIN_HEIGHT = 0.03

# A banner is the region between the last content rule and the footer, when
# something is written in it and it is not tall enough to be a band row.
BANNER_MAX_HEIGHT = 0.22

# A row of this many cells of similar width is a column grid. Two is a band,
# which the band rules already cover.
GRID_MIN_COLUMNS = 3
# Columns count as equal when the widest is within this ratio of the narrowest.
GRID_EQUAL_RATIO = 1.6
# A grid has to take up this much of the page to be the page's composition
# rather than a row of small labels that happen to line up - reference page 3
# has five 12pt captions in a row, and they are not a card row.
GRID_MIN_SPAN = 0.35
GRID_MIN_HEIGHT = 0.06

# What a grammar can report finding, and what it means.
RAIL = "rail"
GROUND_SPLIT = "ground split"
BAND_ROW = "band row"
CARD_ROW = "card row"
BANNER = "banner"
EXHIBIT_PANEL = "exhibit panel"


@dataclass(frozen=True)
class LayoutGrammar:
    """One layout of a template, as regions a slide could be composed into."""
    page: int
    label: str = ""
    regions: tuple[dict, ...] = ()
    features: tuple[str, ...] = ()
    columns: int = 0
    bands: int = 0
    rule_color: Optional[str] = None
    grounds: tuple[str, ...] = ()

    def has(self, feature: str) -> bool:
        return feature in self.features

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "label": self.label,
            "regions": [dict(region) for region in self.regions],
            "features": list(self.features),
            "columns": self.columns,
            "bands": self.bands,
            "ruleColor": self.rule_color,
            "grounds": list(self.grounds),
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "LayoutGrammar":
        data = data or {}
        return cls(
            page=int(data.get("page") or 0),
            label=str(data.get("label") or ""),
            regions=tuple(r for r in (data.get("regions") or [])
                          if isinstance(r, dict)),
            features=tuple(str(f) for f in (data.get("features") or [])),
            columns=int(data.get("columns") or 0),
            bands=int(data.get("bands") or 0),
            rule_color=data.get("ruleColor") or None,
            grounds=tuple(str(g) for g in (data.get("grounds") or [])),
        )


@dataclass
class TemplateGrammar:
    """Every layout a template was found to have."""
    layouts: list[LayoutGrammar] = field(default_factory=list)

    @property
    def features(self) -> set[str]:
        return {feature for layout in self.layouts for feature in layout.features}

    def to_dict(self) -> dict[str, Any]:
        return {
            "layouts": [layout.to_dict() for layout in self.layouts],
            "features": sorted(self.features),
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "TemplateGrammar":
        return cls(layouts=[LayoutGrammar.from_dict(entry)
                            for entry in ((data or {}).get("layouts") or [])])


# ── Reading one page ──────────────────────────────────────────────────

def read_page(page, page_number: int = 0, label: str = "") -> LayoutGrammar:
    """The grammar of one page: its grounds, its rules and its grid."""
    width, height = float(page.rect.width), float(page.rect.height)
    cells = _ground_cells(page)
    structure = count_blocks(page)
    rules = _rules(page, width, height)

    regions: list[dict] = []
    features: list[str] = []
    grounds = _ground_regions(cells)
    regions.extend(region for region, _ in grounds)
    if grounds:
        # A rail *is* a ground split, so reporting both says nothing. The
        # narrower reading is the one that describes the page.
        features.append(RAIL if any(f == RAIL for _, f in grounds)
                        else GROUND_SPLIT)

    bands = _band_row(rules)
    if bands:
        regions.append(bands)
        features.append(BAND_ROW)

    banner = _banner(rules, structure, bands)
    if banner:
        regions.append(banner)
        features.append(BANNER)

    grid = _column_grid(structure, exclude=[r for r in (bands, banner) if r])
    if grid:
        regions.append(grid)
        features.append(CARD_ROW)

    exhibit = _exhibit_panel(structure)
    if exhibit:
        regions.append(exhibit)
        features.append(EXHIBIT_PANEL)

    return LayoutGrammar(
        page=page_number,
        label=label,
        regions=tuple(regions),
        features=tuple(features),
        columns=len(grid.get("children") or ()) if grid else 0,
        bands=len(bands.get("children") or ()) if bands else 0,
        rule_color=_rule_color(page, width, height),
        grounds=tuple(dict.fromkeys(
            region["ground"] for region, _ in grounds if region.get("ground")
        )),
    )


def read_pdf(pdf_path: str, pages: Sequence[int],
             labels: Optional[dict[int, str]] = None) -> TemplateGrammar:
    """Read the grammar of the given 1-based pages of a PDF."""
    import fitz

    labels = labels or {}
    doc = fitz.open(pdf_path)
    try:
        return TemplateGrammar(layouts=[
            read_page(doc[n - 1], n, labels.get(n, ""))
            for n in pages if 1 <= n <= len(doc)
        ])
    finally:
        doc.close()


# ── Grounds ───────────────────────────────────────────────────────────

def _ground_regions(cells: list[list[str]]) -> list[tuple[dict, str]]:
    """
    The page's grounds, as full-height bands with a boundary between them.

    Read down the columns of the ground grid rather than across the rows,
    because the split that matters is vertical: a rail and a half-canvas are
    both a column of the page holding its own colour, and a horizontal band is
    a banner, which the rules find better than the ground does.
    """
    if not cells:
        return []
    rows, columns = len(cells), len(cells[0])
    dominant = [_dominant([cells[r][c] for r in range(rows)]) for c in range(columns)]
    coverage = [
        sum(1 for r in range(rows)
            if colors.is_dark(cells[r][c]) == colors.is_dark(dominant[c])) / rows
        for c in range(columns)
    ]

    runs: list[tuple[int, int, str]] = []
    start = 0
    for index in range(1, columns + 1):
        ends = index == columns or not _same_ground(dominant[index], dominant[start])
        if ends:
            runs.append((start, index, dominant[start]))
            start = index
    runs = [
        run for run in runs
        if (run[1] - run[0]) / columns >= GROUND_MIN_WIDTH
        and sum(coverage[run[0]:run[1]]) / (run[1] - run[0]) >= GROUND_MIN_HEIGHT
    ]
    if len(runs) < 2:
        return []

    out: list[tuple[dict, str]] = []
    for index, (first, last, ground) in enumerate(runs):
        fraction = (last - first) / columns
        rail = fraction <= RAIL_MAX_WIDTH and len(runs) == 2
        out.append((
            {
                "id": f"{'rail' if rail else 'panel'}-{index}",
                "kind": RAIL if rail else "panel",
                "x": round(first / columns, 4),
                "y": 0.0,
                "w": round(fraction, 4),
                "h": 1.0,
                "ground": ground,
            },
            RAIL if rail else GROUND_SPLIT,
        ))
    return out


def _dominant(values: Sequence[str]) -> str:
    tally: dict[str, int] = {}
    for value in values:
        tally[value] = tally.get(value, 0) + 1
    return max(tally, key=lambda key: (tally[key], key))


def _same_ground(a: str, b: str) -> bool:
    """Two ground colours that read as one - the same paper, or the same ink."""
    return colors.is_dark(a) == colors.is_dark(b)


# ── Rules, bands and banners ──────────────────────────────────────────

def _rules(page, width: float, height: float) -> list[tuple[float, float, float]]:
    """
    The page's horizontal rules, as (y, x0, x1) fractions, chrome removed.

    A rule near the top or the bottom of the page is deck furniture - the line
    under the eyebrow, the line over the footer - and it is on every page. A
    band row made of those two would be one band covering the whole page.
    """
    found: list[tuple[float, float, float]] = []
    for item in _collect_drawings(page, width, height):
        box = item["bbox"]
        span = (box[2] - box[0]) / max(1e-6, width)
        thickness = (box[3] - box[1]) / max(1e-6, height)
        if span < RULE_MIN_SPAN or thickness > 0.012:
            continue
        y = box[1] / max(1e-6, height)
        if y < CHROME_TOP or y > CHROME_BOTTOM:
            continue
        found.append((round(y, 4), round(box[0] / width, 4),
                      round(box[2] / width, 4)))

    found.sort()
    merged: list[tuple[float, float, float]] = []
    for rule in found:
        if merged and abs(rule[0] - merged[-1][0]) <= RULE_MERGE:
            continue
        merged.append(rule)
    return merged


def _rule_color(page, width: float, height: float) -> Optional[str]:
    """The colour the template rules its bands in, where it declares one."""
    for item in _collect_drawings(page, width, height):
        box = item["bbox"]
        if (box[2] - box[0]) / max(1e-6, width) < RULE_MIN_SPAN:
            continue
        if (box[3] - box[1]) / max(1e-6, height) > 0.012:
            continue
        hex_color = item.get("color") or item.get("fill")
        if isinstance(hex_color, str) and colors.parse_hex(hex_color):
            return hex_color
    return None


def _band_row(rules: Sequence[tuple[float, float, float]]) -> Optional[dict]:
    """
    A set of evenly spaced full-width rules, as a band row of bands.

    The reference's page 40 is eight rules and seven bands. What makes it a
    band row rather than eight unrelated lines is that the gaps between them
    are of a kind: a page with one rule under its title and another over its
    banner has two gaps that are nothing like each other.
    """
    wide = [rule for rule in rules if rule[2] - rule[1] >= 0.6]
    if len(wide) < BAND_MIN_RULES:
        return None

    gaps = [wide[i + 1][0] - wide[i][0] for i in range(len(wide) - 1)]
    gaps = [gap for gap in gaps if gap >= BAND_MIN_HEIGHT]
    if len(gaps) < BAND_MIN_RULES - 1:
        return None
    if max(gaps) / max(1e-6, min(gaps)) > BAND_SPACING_TOLERANCE:
        return None

    top, bottom = wide[0][0], wide[-1][0]
    left = min(rule[1] for rule in wide)
    right = max(rule[2] for rule in wide)
    height = bottom - top
    if height <= 0:
        return None

    children = []
    for index in range(len(wide) - 1):
        band_top = (wide[index][0] - top) / height
        band_height = (wide[index + 1][0] - wide[index][0]) / height
        if band_height < BAND_MIN_HEIGHT / max(1e-6, height):
            continue
        children.append({
            "id": f"band-{index}", "kind": "band",
            "x": 0.0, "y": round(band_top, 4), "w": 1.0,
            "h": round(band_height, 4),
        })
    if len(children) < BAND_MIN_RULES - 1:
        return None

    return {
        "id": "bands", "kind": "bandRow",
        "x": round(left, 4), "y": round(top, 4),
        "w": round(right - left, 4), "h": round(height, 4),
        "children": children,
    }


def _banner(rules: Sequence[tuple[float, float, float]],
            structure: PageStructure, bands: Optional[dict]) -> Optional[dict]:
    """
    A full-width strip near the foot of the page, ruled off from the content.

    Reference page 95's summary banner is the region under its last content
    rule. It is a banner rather than another band because there is only one of
    it and it sits below everything else.
    """
    wide = [rule for rule in rules if rule[2] - rule[1] >= 0.6]
    if not wide:
        return None
    if bands:
        below = [rule for rule in wide if rule[0] > bands["y"] + bands["h"] + 0.01]
        if not below:
            return None
        top = below[0][0]
    else:
        top = wide[-1][0]
    height = CHROME_BOTTOM - top
    if not 0 < height <= BANNER_MAX_HEIGHT:
        return None
    if not _writing_between(structure, top, CHROME_BOTTOM):
        return None
    return {
        "id": "banner", "kind": "banner",
        "x": round(min(r[1] for r in wide), 4), "y": round(top, 4),
        "w": round(max(r[2] for r in wide) - min(r[1] for r in wide), 4),
        "h": round(height, 4),
    }


def _writing_between(structure: PageStructure, top: float, bottom: float) -> bool:
    return any(top - 0.02 <= cell[1] <= bottom
               for row in structure.cell_rows for cell in row)


# ── Column grids ──────────────────────────────────────────────────────

def _column_grid(structure: PageStructure,
                 exclude: Sequence[dict] = ()) -> Optional[dict]:
    """
    The widest row of similar-width cells, as a grid of columns.

    Reference page 95 is four cards in a row; page 130 is six. Both are rows
    of cells that share a width, which is exactly what separates a card row
    from a label beside a paragraph.
    """
    best: Optional[Sequence] = None
    for row in structure.cell_rows:
        if len(row) < GRID_MIN_COLUMNS:
            continue
        if any(_inside(cell, region) for cell in row for region in exclude):
            continue
        widths = sorted(cell[2] - cell[0] for cell in row)
        if widths[0] <= 0 or widths[-1] / widths[0] > GRID_EQUAL_RATIO:
            continue
        if best is None or len(row) > len(best):
            best = row
    if best is None:
        return None

    left = min(cell[0] for cell in best)
    right = max(cell[2] for cell in best)
    top = min(cell[1] for cell in best)
    bottom = max(cell[3] for cell in best)
    span = max(1e-6, right - left)
    height = max(1e-6, bottom - top)
    if span < GRID_MIN_SPAN or height < GRID_MIN_HEIGHT:
        return None
    return {
        "id": "grid", "kind": "grid",
        "x": round(left, 4), "y": round(top, 4),
        "w": round(span, 4), "h": round(height, 4),
        "children": [
            {
                "id": f"column-{index}", "kind": "column",
                "x": round((cell[0] - left) / span, 4), "y": 0.0,
                "w": round((cell[2] - cell[0]) / span, 4), "h": 1.0,
            }
            for index, cell in enumerate(best)
        ],
    }


def _exhibit_panel(structure: PageStructure) -> Optional[dict]:
    """The largest exhibit on the page, as a region to compose one into."""
    if not structure.exhibit_boxes:
        return None
    box = max(structure.exhibit_boxes,
              key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
    return {
        "id": "exhibit", "kind": "panel",
        "x": round(box[0], 4), "y": round(box[1], 4),
        "w": round(box[2] - box[0], 4), "h": round(box[3] - box[1], 4),
    }


def _inside(cell: Sequence[float], region: dict) -> bool:
    top, bottom = region.get("y", 0.0), region.get("y", 0.0) + region.get("h", 0.0)
    return top - 0.01 <= cell[1] <= bottom + 0.01
