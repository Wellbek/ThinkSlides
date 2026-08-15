"""
Which pages of a template are worth looking at.

A PDF template was sampled at fifteen evenly spaced pages and sent in one
vision call. On the 184-page reference deck that is pages 1, 14, 27, 40 and so
on - fifteen arbitrary pages. Even sampling is representative of *position in
the document*, which is not the thing anyone cares about. It is not
representative of *layout*, and an archetype appearing fewer than about twelve
times is likely to be missed entirely.

So: read every page cheaply, group the pages that look alike, and spend the
sampling budget one page per group first and only then on the common ones. A
deck with one split-canvas page still gets that page looked at.

**No model is involved.** Grouping runs off ``page_metrics.page_shape``, which
is a coarse ink map, a coarse map of how dark the ground is across the page,
and three counts. That is enough to tell a dark-rail exhibit page from a
seven-band grid, and it knows nothing about which words are on either.

The labels here are provisional and deliberately shallow - "this group looks
like an exhibit page". Naming a deck's archetypes properly, with the chapter
pattern they sit in, is #121's job, and it reads this survey rather than
re-deriving it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from app.measure.page_metrics import PageShape, page_shape, signature_distance

# How much of the distance between two pages is where their ink sits, and how
# much is the grid they are set on. Ink alone cannot tell seven row bands from
# a row of four cards - both spread ink evenly down the page - and on the
# reference those two sit 0.16 apart in ink space, closer than two pages of the
# same kind often are. The grid is what separates them.
INK_WEIGHT = 0.6
GRID_WEIGHT = 0.4

# Two pages closer than this are the same layout. Measured against the
# reference: its content pages sit 0.16-0.36 apart in ink space and its split
# canvases 0.14-0.28, while the two families are never closer than 0.51 - so
# the boundary is wide but it is not arbitrary.
CLUSTER_RADIUS = 0.24
# Clusters whose centroids end up closer than this are one cluster that the
# single pass happened to split, usually because an outlier led the second.
MERGE_RADIUS = 0.14
# How many times to re-assign every page to its nearest centroid. Two passes
# is where the reference deck stops moving.
REFINE_PASSES = 2

# A page carrying less than this many words is a cover, a divider or a plate,
# whatever its ink map says.
SPARSE_WORDS = 30

# The scales the grid features are read against, so each contributes on the
# same 0-1 footing. A page with more rows than this, or more columns, is at
# the top of the scale rather than off it.
GRID_MAX_ROWS = 8.0
GRID_MAX_COLUMNS = 6.0
GRID_MAX_BLOCKS = 18.0
GRID_MAX_EXHIBITS = 2.0

# Where the label rules cut. A page whose exhibit is surrounded by more than
# this many other blocks is a composed page that happens to contain a chart,
# not an exhibit page; four columns or more is a row of cards; and a grid
# needs enough rows to read as one.
EXHIBIT_MAX_BLOCKS = 8
CARD_ROW_MIN_COLUMNS = 4
MATRIX_MIN_COLUMNS = 3
GRID_MIN_ROWS = 4

# How many pages a vision call is given. Unchanged from the even sampler: the
# point of this module is spending the budget better, not spending more.
DEFAULT_SAMPLE = 15

_RANGE_RE = re.compile(r"^\s*(\d+)\s*(?:-|–|—|:|\.\.)\s*(\d+)\s*$")


@dataclass(frozen=True)
class PageCluster:
    """A group of pages that look like the same kind of page."""
    id: int
    label: str
    pages: tuple[int, ...]
    representative: int
    centroid: tuple[float, ...] = ()

    @property
    def size(self) -> int:
        return len(self.pages)

    def to_dict(self, with_centroid: bool = False) -> dict[str, Any]:
        data = {
            "id": self.id,
            "label": self.label,
            "pages": list(self.pages),
            "representative": self.representative,
            "size": self.size,
        }
        if with_centroid:
            data["centroid"] = [round(v, 4) for v in self.centroid]
        return data


@dataclass(frozen=True)
class TemplateSurvey:
    """What a template's pages look like, and which of them to look at."""
    page_count: int
    considered: tuple[int, ...]
    clusters: tuple[PageCluster, ...] = ()
    sample: tuple[int, ...] = ()

    def label_of(self, page: int) -> Optional[str]:
        for cluster in self.clusters:
            if page in cluster.pages:
                return cluster.label
        return None

    def sampled_labels(self) -> tuple[str, ...]:
        """Every distinct kind of page the sample actually covers."""
        seen: list[str] = []
        for page in self.sample:
            label = self.label_of(page)
            if label and label not in seen:
                seen.append(label)
        return tuple(seen)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pageCount": self.page_count,
            "pagesConsidered": list(self.considered),
            "sampledPages": list(self.sample),
            "clusters": [c.to_dict() for c in self.clusters],
        }


# ── Reading a page range ──────────────────────────────────────────────

def parse_page_range(spec: Any, page_count: int) -> list[int]:
    """
    The pages a user named, as 1-based numbers inside the document.

    Accepts what a person actually types: "20-35", "1,4,9", "20-35, 40", a
    list of numbers, or nothing at all for the whole document. Out-of-range
    numbers are dropped rather than raising, because a user who says 1-500 of
    a 184-page deck means "all of it".
    """
    if page_count <= 0:
        return []
    everything = list(range(1, page_count + 1))
    if spec is None:
        return everything

    if isinstance(spec, (list, tuple, set)):
        parts: list[str] = [str(item) for item in spec]
    else:
        text = str(spec).strip()
        if not text:
            return everything
        parts = [chunk for chunk in re.split(r"[,;]", text) if chunk.strip()]

    wanted: set[int] = set()
    for part in parts:
        match = _RANGE_RE.match(part)
        if match:
            low, high = int(match.group(1)), int(match.group(2))
            if low > high:
                low, high = high, low
            wanted.update(range(low, high + 1))
            continue
        try:
            wanted.add(int(str(part).strip()))
        except ValueError:
            continue

    pages = sorted(page for page in wanted if 1 <= page <= page_count)
    return pages or everything


# ── Surveying a document ──────────────────────────────────────────────

def survey_pdf(pdf_path: str, page_range: Any = None,
               budget: int = DEFAULT_SAMPLE) -> TemplateSurvey:
    """Read a PDF's pages, group them by layout, and choose what to sample."""
    import fitz

    doc = fitz.open(pdf_path)
    try:
        page_count = len(doc)
        considered = parse_page_range(page_range, page_count)
        shapes = [page_shape(doc[n - 1], n) for n in considered]
    finally:
        doc.close()
    return survey_shapes(shapes, page_count, budget)


def survey_shapes(shapes: Sequence[PageShape], page_count: int,
                  budget: int = DEFAULT_SAMPLE) -> TemplateSurvey:
    """The survey, from pages already read - the testable half of the above."""
    considered = tuple(shape.page_number for shape in shapes)
    if not shapes:
        return TemplateSurvey(page_count=page_count, considered=considered)
    clusters = cluster_pages(shapes)
    sample = choose_sample(clusters, budget, first_page=considered[0])
    return TemplateSurvey(page_count=page_count, considered=considered,
                          clusters=tuple(clusters), sample=tuple(sample))


def grid_features(shape: PageShape) -> list[float]:
    """
    The page's grid as a short vector on a 0-1 scale.

    Everything here is a count the survey already read. What it is *for* is
    telling apart pages whose ink maps agree: reference pages 40 and 95 are
    both a column of evenly spread ink, and one is seven row bands while the
    other is a row of four cards.
    """
    return [
        min(1.0, shape.rows / GRID_MAX_ROWS),
        min(1.0, shape.columns / GRID_MAX_COLUMNS),
        min(1.0, shape.blocks / GRID_MAX_BLOCKS),
        min(1.0, shape.exhibits / GRID_MAX_EXHIBITS),
        1.0 if shape.has_ground_split else 0.0,
        1.0 if shape.dark_ground else 0.0,
    ]


def page_distance(a: "_Point", b: "_Point") -> float:
    """
    How unlike two pages are: where their ink sits, and how they are set.

    The grid half is half its average disagreement and half its *largest*
    one, because two pages that differ sharply on a single structural axis
    are different kinds of page even when they agree about the rest. Averaging
    alone put reference page 40 (seven row bands, one column) in the same
    group as page 95 (four cards in a row) - their ink maps are near-identical
    and it is only the row and column counts that tell them apart.
    """
    ink = signature_distance(a.signature, b.signature)
    if not a.grid or len(a.grid) != len(b.grid):
        return ink
    gaps = [abs(x - y) for x, y in zip(a.grid, b.grid)]
    grid = 0.5 * (sum(gaps) / len(gaps)) + 0.5 * max(gaps)
    return INK_WEIGHT * ink + GRID_WEIGHT * grid


def cluster_pages(shapes: Sequence[PageShape]) -> list[PageCluster]:
    """
    Group pages that look alike.

    Leader clustering - walk the pages, put each into the first group it is
    close enough to, start a group when none is - then merge the groups that
    ended up adjacent and re-assign everything to its nearest centroid. It is
    deterministic, it needs no library, and every step of it can be explained
    to whoever hits a grouping they disagree with, which matters more here
    than the last few percent of a better algorithm would.
    """
    points = [_Point.of(shape) for shape in shapes if shape.signature]
    if not points:
        return []

    groups: list[list[_Point]] = []
    centroids: list[_Point] = []
    for point in points:
        for index, centroid in enumerate(centroids):
            if page_distance(point, centroid) <= CLUSTER_RADIUS:
                groups[index].append(point)
                centroids[index] = _centroid(groups[index])
                break
        else:
            groups.append([point])
            centroids.append(point)

    groups, centroids = _merge_close(groups, centroids)
    for _ in range(REFINE_PASSES):
        groups, centroids = _reassign(points, centroids)
        groups, centroids = _merge_close(groups, centroids)

    ordered = sorted(
        ((group, centroid) for group, centroid in zip(groups, centroids) if group),
        key=lambda pair: (-len(pair[0]), pair[0][0].shape.page_number),
    )
    return [
        PageCluster(
            id=index,
            label=label_for([point.shape for point in group]),
            pages=tuple(sorted(point.shape.page_number for point in group)),
            representative=_representative(group, centroid),
            centroid=tuple(centroid.signature),
        )
        for index, (group, centroid) in enumerate(ordered)
    ]


def choose_sample(clusters: Sequence[PageCluster], budget: int = DEFAULT_SAMPLE,
                  first_page: Optional[int] = None) -> list[int]:
    """
    Which pages to actually look at.

    One from every cluster before a second from any, so a layout that appears
    once is still seen; then the remainder shared out in proportion to how
    much of the deck each cluster is. The first page is kept regardless - it
    is the cover, and it carries the brand treatment nothing else does.
    """
    if not clusters or budget <= 0:
        return []

    chosen: list[int] = []
    if first_page is not None:
        chosen.append(first_page)

    for cluster in clusters:
        if len(chosen) >= budget:
            break
        if cluster.representative not in chosen:
            chosen.append(cluster.representative)

    total = sum(cluster.size for cluster in clusters) or 1
    remaining = budget - len(chosen)
    if remaining > 0:
        for cluster in clusters:
            share = max(0, round(remaining * cluster.size / total))
            for page in _spread(cluster.pages, share + 1):
                if len(chosen) >= budget:
                    break
                if page not in chosen:
                    chosen.append(page)
            if len(chosen) >= budget:
                break

    # Still short - a deck of few clusters and many pages. Fill from the
    # largest clusters rather than leaving the budget unspent.
    for cluster in clusters:
        for page in cluster.pages:
            if len(chosen) >= budget:
                break
            if page not in chosen:
                chosen.append(page)

    return sorted(chosen[:budget])


# ── Naming a group ────────────────────────────────────────────────────

COVER = "cover"
DIVIDER = "divider"
SPLIT_CANVAS = "split canvas"
EXHIBIT = "exhibit"
CARD_ROW = "card row"
MATRIX = "matrix"
ROW_BANDS = "row bands"
TEXT_PAGE = "text page"


def label_for(group: Sequence[PageShape]) -> str:
    """
    A provisional name for what a group of pages is.

    Read off the same measurements the grouping used, and deliberately
    shallow: it is here so a survey is legible, and so #121 has something to
    name properly. It is not meant to be the deck's archetype vocabulary.

    The order is the rule. The first page of a document is its cover whatever
    else it resembles - a cover and a chapter divider are the same shape, and
    only position tells them apart. A split canvas that also carries a chart
    is a split canvas, because the ground is the louder decision. And a page
    of four wide columns is a card row whether or not those columns have
    several rows in them.

    Calibrated against the reference's design set: pages 40 (seven bands), 95
    (four cards), 12 (a 5 x 3 matrix), 110 (two charts), 22 and 66 (split
    canvases) and 4 (prose) each land on their own name.
    """
    if not group:
        return "unknown"
    words = _median(shape.word_count for shape in group)
    blocks = _median(shape.blocks for shape in group)
    rows = _median(shape.rows for shape in group)
    columns = _median(shape.columns for shape in group)
    exhibits = _median(shape.exhibits for shape in group)
    split = sum(1 for s in group if s.has_ground_split) / len(group)

    # Sparseness first, because a chapter divider often carries a decorative
    # shape the exhibit finder cannot tell from a chart, and a real exhibit
    # page is never this quiet - it has axis labels, values, a caption and a
    # source line. A sparse page and the deck's cover are the same shape, and
    # only position separates them.
    if words < SPARSE_WORDS:
        return COVER if any(s.page_number == 1 for s in group) else DIVIDER
    if exhibits >= 1 and blocks <= EXHIBIT_MAX_BLOCKS:
        return EXHIBIT
    if split >= 0.5:
        return SPLIT_CANVAS
    if columns >= CARD_ROW_MIN_COLUMNS:
        return CARD_ROW
    if rows >= GRID_MIN_ROWS and columns >= MATRIX_MIN_COLUMNS:
        return MATRIX
    if rows >= GRID_MIN_ROWS:
        return ROW_BANDS
    return TEXT_PAGE


def _median(values) -> float:
    ordered = sorted(values)
    return float(ordered[len(ordered) // 2]) if ordered else 0.0


# ── Internals ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Point:
    """A page as the two things clustering compares: its ink and its grid."""
    signature: tuple[float, ...]
    grid: tuple[float, ...]
    shape: Optional[PageShape] = None

    @classmethod
    def of(cls, shape: PageShape) -> "_Point":
        return cls(tuple(shape.signature), tuple(grid_features(shape)), shape)


def _centroid(group: Sequence[_Point]) -> _Point:
    count = len(group)
    signature = tuple(sum(p.signature[i] for p in group) / count
                      for i in range(len(group[0].signature)))
    grid = tuple(sum(p.grid[i] for p in group) / count
                 for i in range(len(group[0].grid)))
    return _Point(signature=signature, grid=grid)


def _merge_close(groups: list[list[_Point]],
                 centroids: list[_Point]) -> tuple[list, list]:
    """Fold together groups whose centroids ended up on top of each other."""
    merged_groups: list[list[_Point]] = []
    merged_centroids: list[_Point] = []
    for group, centroid in zip(groups, centroids):
        if not group:
            continue
        for index, existing in enumerate(merged_centroids):
            if page_distance(centroid, existing) <= MERGE_RADIUS:
                merged_groups[index] = merged_groups[index] + group
                merged_centroids[index] = _centroid(merged_groups[index])
                break
        else:
            merged_groups.append(list(group))
            merged_centroids.append(centroid)
    return merged_groups, merged_centroids


def _reassign(points: Sequence[_Point],
              centroids: list[_Point]) -> tuple[list, list]:
    """Put every page in its nearest group, then move the centroids to match."""
    groups: list[list[_Point]] = [[] for _ in centroids]
    for point in points:
        distances = [page_distance(point, c) for c in centroids]
        groups[distances.index(min(distances))].append(point)
    kept = [(group, _centroid(group)) for group in groups if group]
    return [g for g, _ in kept], [c for _, c in kept]


def _representative(group: Sequence[_Point], centroid: _Point) -> int:
    """The page of a group that looks most like the group."""
    return min(group,
               key=lambda point: (page_distance(point, centroid),
                                  point.shape.page_number)).shape.page_number


def _spread(pages: Sequence[int], count: int) -> list[int]:
    """``count`` pages spread across a cluster rather than its first few."""
    pages = list(pages)
    if count <= 0 or not pages:
        return []
    if count >= len(pages):
        return pages
    step = (len(pages) - 1) / max(1, count - 1) if count > 1 else 0
    return [pages[min(len(pages) - 1, round(i * step))] for i in range(count)]
