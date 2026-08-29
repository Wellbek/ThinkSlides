"""
How much a slide should carry, and whether the one we designed carries it.

#119 measures what a template puts on a page. That number was published to the
planner and the content designer as a target, and a target nothing checks is a
suggestion. This is the check: a per-archetype budget in blocks and words, a
way to count what a designed slide actually carries, and a verdict.

The budget is not the measured figure applied flatly. Two things move it:

* **The archetype.** A cover carries a title. A divider carries two words. An
  exhibit slide's blocks are mostly the exhibit, and a dashboard's are four of
  them. Holding all of them to a content page's budget would produce a deck of
  uniformly cluttered pages, which is a different failure from the one this
  fixes.

* **The material.** This is the risk #129 owns and states plainly: telling a
  model to fill seven bands is a reliable way to get seven bands of filler.
  Density is only a virtue when the material supports it. So the budget scales
  between a floor and the measured target according to what the slide actually
  has to say - key points, figures, how many exhibits, research behind it - and
  a slide with one figure and no research is *allowed* to be sparse. A thin
  slide is a legitimate outcome; a thin slide inflated to look full is not.

  The dashboard archetype is where that risk bites hardest, because it asks for
  four exhibits rather than a few more bullets, and a fabricated exhibit is
  worse than a fabricated sentence - a chart makes a number look sourced. So a
  dashboard is not a shape a slide can simply declare: ``dashboard_is_earned``
  gates it on material *and* on actually having several things to show, and an
  unearned one is demoted to EXHIBIT with a note saying why.

The counting deliberately mirrors ``measure.structure``, which counts blocks on
a *rendered page*: a bulleted list of five points is five blocks there, so it
is five blocks here. If the two drifted apart, a deck would be written to one
number and scored on another. ``tests/test_budget.py`` renders fixture decks
and checks the two agree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from app.measure.density import DensityProfile

# ── Archetypes ────────────────────────────────────────────────────────

COVER = "cover"
DIVIDER = "divider"
CONTENT = "content"
EXHIBIT = "exhibit"
DASHBOARD = "dashboard"
CLOSING = "closing"
ARCHETYPES = frozenset({COVER, DIVIDER, CONTENT, EXHIBIT, DASHBOARD, CLOSING})

# What each archetype carries, as a share of the template's measured content
# page. A content page is the thing that was measured, so it is 1.0 by
# definition; the rest are read off the reference's own pages of each kind.
#
#   cover     page 1   - a title, a standfirst and nothing else
#   divider   page 30  - a chapter number and its name
#   exhibit   page 22  - one chart plus the sentences framing it
#   dashboard page 41  - four exhibits and their captions, and little else
#   closing   page 168 - a recap, set more openly than a content page
ARCHETYPE_SHARE: dict[str, tuple[float, float]] = {
    # archetype: (share of blocks, share of words)
    COVER: (0.10, 0.06),
    DIVIDER: (0.12, 0.05),
    CONTENT: (1.00, 1.00),
    EXHIBIT: (0.55, 0.55),
    # More blocks than a content page and fewer words: a dashboard is mostly
    # exhibit, and its prose is captions and interpretation rather than
    # argument. Reading it as a content page that lost its text is how a
    # perfectly good board-pack page gets sent back for being thin.
    DASHBOARD: (1.15, 0.70),
    CLOSING: (0.55, 0.60),
}

# How much material a slide must have behind it before it may be a dashboard.
# This is the whole guard, and it is deliberately high.
#
# budget.py's own reasoning applies with more force here than anywhere else:
# telling a model to fill seven bands is a reliable way to get seven bands of
# filler, and a dashboard asks for four exhibits. A slide with one figure and
# no research cannot honestly carry four, and the outcome of pretending
# otherwise is four fabricated ones - which is worse than the sparse page it
# replaced, because a chart makes a number look sourced.
DASHBOARD_MATERIAL_FLOOR = 0.62
# ...and it must genuinely have several things to show. Composition alone is
# not the test: one chart in a two-panel layout is an exhibit page that used
# the wrong archetype.
DASHBOARD_MIN_EXHIBITS = 2

# How a plan's contentType maps onto an archetype. Everything a planner can
# emit has to land somewhere, so anything unlisted is a content page.
CONTENT_TYPE_ARCHETYPE: dict[str, str] = {
    "title": COVER,
    "section": DIVIDER,
    "divider": DIVIDER,
    "chart": EXHIBIT,
    "table": EXHIBIT,
    "image": EXHIBIT,
    "dashboard": DASHBOARD,
    "conclusion": CLOSING,
}

# What counts as an exhibit for the dashboard gate. A diagram is one: a
# five-step flow is the same kind of claim on the page as a chart, and it is
# the same kind of work for a reader.
EXHIBIT_CONTENT_TYPES = frozenset({"chart", "table", "image", "diagram"})

# ── The budget's shape ────────────────────────────────────────────────

# A slide may come in this far under its target before it is under budget, and
# this far over before it is crowded. Wider below than above because a good
# slide often has less to say than the template's median page, and because the
# cost of the two failures is not symmetric: a sparse slide is thin, and an
# overfull one is unreadable.
UNDER_TOLERANCE = 0.55
OVER_TOLERANCE = 1.45

# Where the budget lands when a slide has nothing behind it. Not zero: a slide
# in a deck still carries a headline and something under it.
THIN_FLOOR = 0.35

# A slide is never held to more than this many blocks however dense the
# template, because past it the page stops being read and starts being
# scanned - and the reference's own densest page is 17.
MAX_BLOCKS = 18
# ...except a dashboard, which is read differently. Its blocks are mostly
# exhibits and figure tiles rather than sentences, and a reader scans it on
# purpose. The reference's own dashboard pages reach the mid twenties.
MAX_BLOCKS_BY_ARCHETYPE: dict[str, int] = {DASHBOARD: 26}

# How a deck's *format* - not a slide's archetype - moves the budget. Most
# formats carry as much as today's default and are left at 1.0: the format
# already earns its keep through the page vocabulary it asks
# `plan_presentation_structure` for, and scaling the per-slide numbers on top
# of that would double-count it.
#
# The two that get a real number both break that default in a way nothing
# else already captures. A pitch slide is read across a room in the seconds
# it is on screen - "one claim a page" means the ordinary target is still too
# much, not merely differently shaped. A poster page is not a slide at all:
# it is the whole deck's material on one sheet, and MAX_BLOCKS was
# calibrated against the reference's densest single *slide*, reaching 17 -
# a poster page routinely carries several times that.
FORMAT_BUDGET_SCALE: dict[str, float] = {
    "pitch": 0.55,
    "poster": 3.2,
}


def _format_scale(format_id: str) -> float:
    return FORMAT_BUDGET_SCALE.get(str(format_id or "").strip().lower(), 1.0)

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'%$.,-]*")


@dataclass(frozen=True)
class SlideBudget:
    """What one slide is expected to carry, and what it may not fall below."""
    archetype: str
    material: float

    blocks_target: float
    blocks_min: float
    blocks_max: float
    words_target: float
    words_min: float
    words_max: float
    words_per_block: float

    ON = "on"
    UNDER = "under"
    OVER = "over"

    def verdict(self, substance: "SlideSubstance") -> str:
        """Whether what was designed meets the budget."""
        if substance.blocks < self.blocks_min or substance.words < self.words_min:
            return self.UNDER
        if substance.blocks > self.blocks_max or substance.words > self.words_max:
            return self.OVER
        return self.ON

    def describe(self) -> str:
        """The budget, in the terms whoever writes the slide works in."""
        if self.archetype in (COVER, DIVIDER):
            return (
                f"This is a {self.archetype}: it carries its headline and at "
                f"most {self.blocks_max:.0f} other things. Do not fill it."
            )
        return (
            f"Aim for about {self.blocks_target:.0f} blocks and "
            f"{self.words_target:.0f} words on this slide "
            f"(at least {self.blocks_min:.0f} blocks and {self.words_min:.0f} "
            f"words; no more than {self.blocks_max:.0f} blocks), at roughly "
            f"{self.words_per_block:.0f} words a block"
        )

    def shortfall(self, substance: "SlideSubstance") -> str:
        """Why a slide is under budget, in terms its author can act on."""
        parts = []
        if substance.blocks < self.blocks_min:
            parts.append(
                f"{substance.blocks} blocks where this slide should carry at "
                f"least {self.blocks_min:.0f}"
            )
        if substance.words < self.words_min:
            parts.append(
                f"{substance.words} words where it should carry at least "
                f"{self.words_min:.0f}"
            )
        return " and ".join(parts)


@dataclass(frozen=True)
class SlideSubstance:
    """What a designed slide actually carries."""
    blocks: int
    words: int
    exhibits: int = 0

    @property
    def words_per_block(self) -> float:
        return self.words / self.blocks if self.blocks else 0.0


# ── Building a budget ─────────────────────────────────────────────────

def density_brief_available(design_system: Optional[dict]) -> bool:
    """
    Whether this run has a density to hold anything to.

    A design system with no ``density`` was never measured - a bundle saved
    before the column existed, or a template that rendered to nothing. Stating
    a budget derived from zeros would be inventing the number the whole
    mechanism exists to avoid inventing.
    """
    profile = DensityProfile.from_dict((design_system or {}).get("density"))
    return bool(profile.words_median and profile.blocks_median)


def archetype_for(slide_or_outline: dict) -> str:
    """
    Which kind of page this is.

    Read from the plan's ``contentType`` where there is one, and otherwise
    from the register: a headline that only names its topic, on a slide with
    nothing under it, is a divider whatever it was labelled.
    """
    data = slide_or_outline or {}
    declared = str(data.get("archetype") or "").strip().lower()
    if declared in ARCHETYPES:
        return declared
    content_type = str(data.get("contentType") or "").strip()
    return CONTENT_TYPE_ARCHETYPE.get(content_type, CONTENT)


def material_score(
    key_points: Optional[list] = None,
    *,
    has_exhibit: bool = False,
    exhibits: int = 0,
    figures: int = 0,
    research_findings: int = 0,
    source_words: int = 0,
) -> float:
    """
    How much this slide has to say, from 0 (nothing) to 1 (plenty).

    Every signal here is something the run *already knows* before a word is
    written - how many points the plan gave the slide, how many charts or
    tables were built for it, how many figures the analysed content supplies,
    whether any research stands behind it. None of it asks a model to rate
    itself, because a model asked whether it has enough material to fill a page
    will say yes.

    ``exhibits`` is accepted so callers that know the count can say so, but it
    is deliberately worth exactly what ``has_exhibit`` was worth: having an
    exhibit is 0.20, and a second one adds nothing here. Weighting the count
    would have quietly *lowered* the score of every existing single-exhibit
    slide, and there is no headroom to give it - the terms already sum to 1.0,
    so paying for a count means taking it from points, figures or research,
    none of which deserve to matter less.

    Nothing is lost by that: how many exhibits a slide has is read directly,
    where it actually decides something, by ``dashboard_is_earned``.
    """
    points = len([p for p in (key_points or []) if str(p or "").strip()])
    has_exhibit = has_exhibit or int(exhibits or 0) > 0
    signals = (
        min(1.0, points / 5.0) * 0.40
        + (0.20 if has_exhibit else 0.0)
        + min(1.0, figures / 4.0) * 0.20
        + min(1.0, research_findings / 3.0) * 0.10
        + min(1.0, source_words / 400.0) * 0.10
    )
    return round(min(1.0, signals), 3)


def dashboard_is_earned(material: float, exhibits: int) -> bool:
    """
    Whether this slide may be a dashboard at all.

    The gate, and the reason the archetype is safe to have. A dashboard asks
    for four exhibits, and a slide with one figure and no research cannot
    honestly carry four - the outcome of pretending otherwise is four
    fabricated exhibits, which is worse than the sparse page it replaced
    because a chart makes a number look sourced.

    Two conditions, and both are needed. Material, because that is what says
    there is something to show. Exhibit count, because composition alone is not
    the test: one chart in a two-panel layout is an exhibit page that reached
    for the wrong archetype.
    """
    return (
        float(material or 0.0) >= DASHBOARD_MATERIAL_FLOOR
        and int(exhibits or 0) >= DASHBOARD_MIN_EXHIBITS
    )


def resolve_archetype(slide_or_outline: dict, material: Optional[float] = None,
                      exhibits: Optional[int] = None) -> str:
    """
    The archetype a slide actually gets, after the dashboard gate.

    ``archetype_for`` reports what a slide *claims*. This reports what it has
    earned, and demotes an unearned dashboard to EXHIBIT rather than refusing
    it: the page still has a chart on it, and judging it as a dashboard would
    hold a thin slide to the densest budget in the table.
    """
    claimed = archetype_for(slide_or_outline)
    if claimed != DASHBOARD:
        return claimed
    if material is None and exhibits is None:
        return claimed
    if dashboard_is_earned(material or 0.0, exhibits or 0):
        return DASHBOARD
    return EXHIBIT


def budget_for(
    design_system: Optional[dict],
    archetype: str = CONTENT,
    material: float = 1.0,
    format_id: str = "",
) -> SlideBudget:
    """
    The budget for one slide of one kind, given what it has to work with.

    ``material`` scales the target between a floor and what the template's own
    pages carry. It is the whole guard against this becoming a filler quota:
    the deck is asked to be as dense as its template *where the material goes
    that far*, and no denser.

    ``format_id`` scales the result again, afterward, for the two formats
    whose numbers genuinely differ from a boardroom deck's rather than only
    their page vocabulary - see ``FORMAT_BUDGET_SCALE``.
    """
    profile = DensityProfile.from_dict((design_system or {}).get("density"))
    kind = archetype if archetype in ARCHETYPES else CONTENT
    block_share, word_share = ARCHETYPE_SHARE[kind]
    scale = _format_scale(format_id)
    ceiling = max(2, round(MAX_BLOCKS_BY_ARCHETYPE.get(kind, MAX_BLOCKS) * scale))

    material = max(0.0, min(1.0, float(material)))
    reach = THIN_FLOOR + (1.0 - THIN_FLOOR) * material

    # The floor constants scale too, not only the measured target: a design
    # system with no density profile of its own (material_score's median
    # comes back zero) must not fall through the scale entirely and leave a
    # poster with the same six-word minimum a boardroom slide has.
    blocks = min(ceiling, max(1.0 * scale, profile.blocks_median * block_share * reach * scale))
    words = max(6.0 * scale, profile.words_median * word_share * reach * scale)
    per_block = profile.words_per_block_median or (words / blocks if blocks else 0.0)

    return SlideBudget(
        archetype=kind,
        material=material,
        blocks_target=round(blocks, 1),
        blocks_min=round(max(1.0, blocks * UNDER_TOLERANCE), 1),
        blocks_max=round(min(ceiling, max(2.0, blocks * OVER_TOLERANCE)), 1),
        words_target=round(words),
        words_min=round(max(4.0, words * UNDER_TOLERANCE)),
        words_max=round(max(12.0, words * OVER_TOLERANCE)),
        words_per_block=round(per_block, 1),
    )


# ── Counting what a slide carries ─────────────────────────────────────

def measure_slide(slide: Optional[dict],
                  design_system: Optional[dict] = None) -> SlideSubstance:
    """
    Count a designed slide the way the rendered page will be counted.

    The title is not a block, for the same reason ``measure.structure`` drops
    it: every page has one, so counting it reports every page as carrying one
    more than it does.

    ``design_system`` is read for one thing, and it is not cosmetic: a table
    drawn as filled bands renders as a single exhibit and a table drawn as
    hairlines renders as a grid of cells, and the page counter sees exactly
    that difference. Reference page 12 is fifteen blocks because it is a
    hairline table; the same rows in filled bands are one.
    """
    contents = (slide or {}).get("contents") or []
    title_index = _title_index(contents)
    hairline = _table_is_hairline(design_system)

    blocks = 0
    words = 0
    exhibits = 0
    for index, item in enumerate(contents):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "")
        counted, said = _count_item(kind, item, is_title=index == title_index,
                                    hairline_tables=hairline)
        blocks += counted
        words += said
        if kind in EXHIBIT_CONTENT_TYPES:
            exhibits += 1

    for item in (slide or {}).get("primitives") or []:
        if isinstance(item, dict) and str(item.get("kind") or "") == "text":
            words += _words(_primitive_text(item))
            blocks += 1

    return SlideSubstance(blocks=blocks, words=words, exhibits=exhibits)


def _count_item(kind: str, item: dict, is_title: bool,
                hairline_tables: bool = False) -> tuple[int, int]:
    """One content item as (blocks, words)."""
    from app.renderer.blocks import BLOCK_PAYLOAD_KEY, block_entry_count

    if kind == "text":
        runs = [r for r in (item.get("runs") or []) if isinstance(r, dict)]
        said = sum(_words(r.get("text")) for r in runs)
        if is_title:
            return 0, said
        # One block per top-level run, matching what the page counter sees: a
        # bulleted list renders as one row per point, and each row is a block.
        # A sub-bullet belongs to the point above it.
        return sum(1 for r in runs if _level(r) == 0 and _words(r.get("text"))), said

    if kind == "chart":
        chart = item.get("chart") or {}
        return 1, _words(chart.get("title")) + _words(chart.get("unitLine"))

    if kind == "image":
        image = item.get("image") or {}
        return 1, _words(image.get("caption"))

    if kind == "table":
        return _count_table(item.get("table") or {}, hairline_tables)

    if kind == "metrics":
        items = [m for m in ((item.get("metrics") or {}).get("items") or [])
                 if isinstance(m, dict)]
        said = sum(_words(m.get("value")) + _words(m.get("label")) for m in items)
        return len(items), said

    if kind in BLOCK_PAYLOAD_KEY:
        return block_entry_count(item), _block_words(kind, item)

    return 0, 0


def _count_table(table: dict, hairline: bool) -> tuple[int, int]:
    """
    A table as blocks and words.

    Filled bands read as one object on the page - the rendered rows sit inside
    a single ruled shape, and the page counter finds an exhibit, not a grid.
    Hairlines read as the grid they are: the header row is furniture and the
    first column is usually row labels, both of which ``measure.structure``
    drops, leaving the data cells - which is what reference page 12's fifteen
    sentence-cells are.
    """
    rows = [r for r in (table.get("rows") or []) if isinstance(r, list)]
    words = sum(_words(cell) for row in rows for cell in row)
    if not rows:
        return 0, 0
    if not hairline:
        return 1, words
    has_header = bool(table.get("headerRow", table.get("hasHeader", True)))
    data_rows = max(0, len(rows) - (1 if has_header else 0))
    columns = max((len(row) for row in rows), default=0)
    return max(1, data_rows * max(1, columns - 1)), words


def _table_is_hairline(design_system: Optional[dict]) -> bool:
    """Whether this deck rules its tables rather than filling them."""
    data_viz = (design_system or {}).get("dataViz") or {}
    return str(data_viz.get("tableTreatment") or "").strip().lower() == "hairline"


def _block_words(kind: str, item: dict) -> int:
    """Every word inside a typed block's payload, whatever its shape."""
    from app.renderer.blocks import BLOCK_PAYLOAD_KEY

    return _words_in(item.get(BLOCK_PAYLOAD_KEY.get(kind, "")))


def _words_in(payload: Any) -> int:
    if isinstance(payload, str):
        return _words(payload)
    if isinstance(payload, dict):
        return sum(_words_in(value) for value in payload.values())
    if isinstance(payload, list):
        return sum(_words_in(value) for value in payload)
    return 0


def _primitive_text(item: dict) -> str:
    runs = item.get("runs")
    if isinstance(runs, list):
        return " ".join(str(r.get("text") or "") for r in runs if isinstance(r, dict))
    return str(item.get("text") or "")


def _title_index(contents: list) -> int:
    """Which content item is the slide's headline."""
    for index, item in enumerate(contents):
        if not isinstance(item, dict) or str(item.get("type") or "") != "text":
            continue
        if item.get("placeholderIdx") in (0, "0"):
            return index
    for index, item in enumerate(contents):
        if isinstance(item, dict) and str(item.get("type") or "") == "text":
            return index
    return -1


def _level(run: dict) -> int:
    try:
        return int(run.get("level") or 0)
    except (TypeError, ValueError):
        return 0


def _words(text: Any) -> int:
    return len(_WORD_RE.findall(str(text or "")))


# ── The check ─────────────────────────────────────────────────────────

def validate_density(
    slide: Optional[dict],
    design_system: Optional[dict] = None,
    material: Optional[float] = None,
) -> tuple[list[str], list[str]]:
    """
    Reject a slide that came in far under what its material supports.

    Deliberately an error rather than a render-time fix. The module this issue
    was opened against grew four bullets by 1.15x to fill the page, which makes
    a sparse slide a *large* sparse slide - the answer is more content, and the
    only place more content can come from is the step that wrote it.

    Equally deliberately, it only fires when the material is there. A slide
    backed by one figure and no research cannot honestly carry seven blocks,
    and the right outcome there is a sparse slide.

    ``material`` of None means nothing measured how much this slide had to
    work with - a slide validated outside a run, or written before the content
    designer recorded it. There is no check to make in that case, and guessing
    would be the invention this exists to prevent.
    """
    if material is None:
        return [], []
    if not isinstance(slide, dict) or not density_brief_available(design_system):
        return [], []

    substance = measure_slide(slide, design_system)
    archetype = resolve_archetype(slide, material, substance.exhibits)
    budget = budget_for(design_system, archetype, material)
    if budget.archetype in (COVER, DIVIDER):
        return [], []

    demoted = []
    if archetype_for(slide) == DASHBOARD and budget.archetype != DASHBOARD:
        demoted = [
            "This slide was planned as a dashboard but carries "
            f"{substance.exhibits} exhibit(s) with a material score of "
            f"{material:.2f}, so it is judged as a single-exhibit page. Either "
            "give it the exhibits the plan expected, from figures the material "
            "genuinely supports, or replan it as an ordinary exhibit slide. Do "
            "not add exhibits to reach a count."
        ]

    verdict = budget.verdict(substance)
    if verdict == SlideBudget.ON:
        return [], demoted

    if verdict == SlideBudget.OVER:
        return [], demoted + [
            f"This slide carries {substance.blocks} blocks and "
            f"{substance.words} words, past the {budget.blocks_max:.0f} blocks "
            f"/ {budget.words_max:.0f} words this template's pages hold "
            "comfortably - it will be set small to fit"
        ]

    # Under budget with thin material behind it is a legitimate slide.
    if budget.material < 0.5:
        return [], demoted

    return [
        f"This slide is under its density budget: {budget.shortfall(substance)}. "
        f"{budget.describe()}. Redesign it with more on it - more points, a "
        "table, a chart, or the figures behind the claim set as a metric row - "
        "rather than shortening the ones it has. If the material genuinely "
        "does not go that far, say so and merge it with another slide."
    ], []
