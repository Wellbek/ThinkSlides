"""
Choosing a page archetype by how well it fits the material.

What this replaces: ``select_layout`` scores a seven-value ``content_type``
enum against layout *names* by substring match - "+10 if the layout is called
something containing 'chart'" - with no model call and no knowledge of what
is going on the page. It is a 2010-era heuristic sitting in the middle of an
LLM pipeline, and with no uploaded template it chooses between eight
hardcoded layouts, which is why every generated deck looks the same.

The cost here is deterministic and every term is measured. The model's job
moves to saying what the page is *about*; which archetype carries that is
arithmetic, and arithmetic that can be audited when a page comes out wrong.

The one term worth arguing about is ``capacity_strain``, which is two-sided.
Penalising only overrun makes the roomiest archetype always the safest
choice, and a deck of half-empty pages is exactly what the previous system
produced. Underfill is a fault too.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

from app.design.assign import INFEASIBLE, solve, total_cost
from app.design.geometry import content_box
from app.design.material import MaterialItem, MaterialShape
from app.design.pack import Pack, PageArchetype, SlotSpec
from app.design.roles import type_role_for
from app.design.schema import FracPlace, GridPlace, GridSpec

# What each term of the cost is worth. Stated here rather than spread through
# the function so the trade-off is arguable in one place.
W_SLOT = 3.0
W_CAPACITY = 2.0
# An exhibit set in the wrong shape is not a small fault, and this was the
# cheapest thing on the board to get wrong. Two reasons compounded. It is a
# *pair* cost, averaged over every placement, so on a three-item page an
# aspect penalty of 1.0 reached the total as 0.33 - against `W_DENSITY` and
# `W_SLOT`, which are page-level and undiluted. And once an authored language
# offers several arrangements of one page kind, the exhibit's shape is exactly
# the thing that should decide between them: at 1.0 the arrangement did not
# respond to the chart at all, and a portrait chart and a letterbox one got
# the same page.
#
# Measured against the shipped packs at 1, 2, 4, 6, 8 and 12: their choices do
# not change at any of them, because their archetypes differ in far more than
# the shape of one slot. 6.0 is the smallest weight at which the arrangement
# tracks the exhibit.
W_ASPECT = 6.0
W_DENSITY = 2.5
W_RHYTHM = 0.8
# Material has an order, and the assignment did not know it. Minimising each
# pairing independently put paragraph one in a caption slot at the top of the
# page, paragraph two in the right-hand column and paragraph three at the
# bottom left, which reads as three fragments rather than as a page. This
# prefers the k-th paragraph to land in the k-th text slot in reading order -
# the one sequencing statement that is still pairwise, and so still solvable
# exactly by the assignment.
W_SEQUENCE = 2.2
# Overflow is not one cost among several. A page whose copy runs off the
# bottom is not a slightly worse page, it is a broken one, and averaging its
# strain in with everything else lets an archetype win on the strength of
# its other slots while one of them is unreadable.
W_OVERFLOW = 6.0
# An exhibit too short to read is broken, the way overflowing copy is. Tables
# and diagrams declare no natural aspect, so the aspect term said nothing about
# them, and a five-row table was set in a slot a quarter of an inch tall with
# its rows printed over each other (#351). Weighted like overflow, and as a
# pair cost - so it is doubled to stay decisive once averaged over a page.
W_LEGIBLE = 12.0
# The shortest slot each kind of exhibit stays readable in, in inches. A table
# is measured instead: one row per line of the pack's body type, with the
# leading a ruled table needs.
MIN_EXHIBIT_IN = {"chart": 1.6, "diagram": 1.0, "metrics": 0.6}
TABLE_ROW_LEADING = 1.8
# Past this shortfall an exhibit is not tight, it is unreadable, and the slot
# is refused rather than charged: as a cost it was outbid by the text overflow
# of every other arrangement, and the table lost. A page with no slot its
# exhibit can be read in is split instead, which gives the exhibit a page.
LEGIBLE_TOLERANCE = 0.25


def legible_tolerance(item: MaterialItem) -> float:
    """How far short of its floor an exhibit may fall before it is refused.

    Zero for a table: its floor is an exact measurement - the table itself,
    wrapped at the slot's width, at the smallest size it is read at - so any
    shortfall means the table is set smaller than that. With the 25% allowed
    to the estimated floors, half-width tables came out at 8 and 9pt (#357).
    """
    return 0.0 if item.kind == "table" else LEGIBLE_TOLERANCE
# How strongly a required slot pulls material into itself. The assignment
# minimises total cost and has no concept of "this column must be used", so
# without a bias the cheapest solution routinely leaves the headline slot
# empty and the archetype is then rejected for a reason that was never about
# the material.
REQUIRED_BONUS = 5.0

# A slot filled below this share of its height is underfilled; above 1.0 it
# overruns. Both are costs. Taken from `composition.COMFORTABLE_FILL`, which
# reached the same number from the other direction.
COMFORTABLE_FILL = 0.72

_TEXT_KINDS = frozenset({"text", "quote", "caption", "footnote"})
_EXHIBIT_KINDS = frozenset({"chart", "table", "diagram", "metrics"})


@dataclass(frozen=True)
class Placement:
    """One material item, bound to one slot of an archetype."""

    item: MaterialItem
    slot: SlotSpec
    cost: float


@dataclass(frozen=True)
class Selection:
    """An archetype, the binding it implies, and why it was chosen."""

    archetype: PageArchetype
    placements: tuple[Placement, ...]
    unplaced: tuple[MaterialItem, ...]
    unfilled: tuple[SlotSpec, ...]
    cost: float
    terms: dict[str, float] = field(default_factory=dict)

    @property
    def slot_for(self) -> dict[str, str]:
        return {p.item.id: p.slot.frame_id for p in self.placements}


# Frame roles that carry display type. Only material meant for them may go
# in them: a body paragraph in a headline frame is not a tight fit, it is
# the wrong kind of thing, and the difference has to be infeasible rather
# than expensive or the solver will trade it against a rounding error.
DISPLAY_FRAME_ROLES = frozenset({"headline", "pullquote", "standfirst"})

# The two frames that refuse ordinary prose. A standfirst is deliberately not
# among them: it is a *lead paragraph*, so body copy is exactly what belongs
# in it. Grouping it with the other two meant a cover's subtitle had nowhere
# to go, and the slot sat empty on every page that had one.
DISPLAY_ONLY_FRAME_ROLES = frozenset({"headline", "pullquote"})

# The page's apparatus. These are filled by the apparatus system from the
# deck's own sources and folios, never by the material the deck is about.
# Without this a paragraph is bound into a footnote frame and set at seven
# points: BXSL's page 20 is an endnotes page whose only text frames are
# captions and footnotes, and every paragraph of a generated page landed in
# one of them.
APPARATUS_FRAME_ROLES = frozenset({"footnotes", "sourceLine", "folio"})

RESTRICTED_FRAME_ROLES = DISPLAY_ONLY_FRAME_ROLES | APPARATUS_FRAME_ROLES

_HINT_FOR_FRAME = {"headline": "heading", "pullquote": "quote",
                   "standfirst": "body"}


def _role_compatible(frame_role: str, item: MaterialItem) -> bool:
    if frame_role in APPARATUS_FRAME_ROLES:
        return False
    if frame_role not in DISPLAY_ONLY_FRAME_ROLES:
        # A heading may sit in a plain column when the archetype has no
        # headline frame; that is a compromise, not an error.
        return True
    return item.role_hint in ("heading", "quote")


def _accepts(slot: SlotSpec, item: MaterialItem) -> bool:
    if not slot.accepts:
        return True
    if item.kind in _TEXT_KINDS:
        return "text" in slot.accepts
    if item.kind == "figure":
        return "figure" in slot.accepts
    # The exhibit's own kind, not merely "some exhibit". Any-of matching sent
    # a chart into a stat row that named only `metrics`, because the row
    # accepted one exhibit kind and that was taken as accepting all of them -
    # so a graph landed in a slot two lines tall. A slot that says `figure`
    # still takes any of them: that is the general-purpose exhibit slot, and
    # every measured pack declares its figure slots that way.
    return item.kind in slot.accepts or "figure" in slot.accepts


def _slot_frame(archetype: PageArchetype, slot: SlotSpec):
    return next((f for f in _walk(archetype.frames)
                 if f.id == slot.frame_id), None)


def _slot_rect(archetype: PageArchetype, slot: SlotSpec, pack: Pack,
               grid: GridSpec) -> Optional[tuple[float, float]]:
    """The slot's width and height in inches, or None if it has no frame."""
    frame = _slot_frame(archetype, slot)
    if frame is None:
        return None
    canvas = pack.canvas
    if isinstance(frame.place, GridPlace):
        box = content_box(canvas, grid)
        column = grid.column_width_in(canvas)
        width = (frame.place.span * column
                 + (frame.place.span - 1) * grid.gutter_in)
        height = (frame.place.h or 1.0) * box.height
        return (width, height)
    place: FracPlace = frame.place
    return (place.w * canvas.w_in, place.h * canvas.h_in)


def _walk(frames):
    for frame in frames:
        yield from frame.walk()


def _slot_origin(archetype: PageArchetype, slot: SlotSpec, pack: Pack,
                 grid: GridSpec) -> Optional[tuple[float, float]]:
    """Where this slot starts on the page, as (top, left) in inches."""
    frame = _slot_frame(archetype, slot)
    if frame is None:
        return None
    canvas = pack.canvas
    if isinstance(frame.place, GridPlace):
        box = content_box(canvas, grid)
        column = grid.column_width_in(canvas)
        left = box.left + frame.place.col * (column + grid.gutter_in)
        top = box.top + (frame.place.y or 0.0) * box.height
        return (top, left)
    place: FracPlace = frame.place
    return (place.y * canvas.h_in, place.x * canvas.w_in)


def reading_order(archetype: PageArchetype, pack: Pack,
                  grid: GridSpec) -> dict[str, float]:
    """Each text slot's place in the page's reading order, normalised to 0..1.

    Down and then across, which is how a column grid is read. A slot whose
    frame cannot be located is left out rather than guessed at.
    """
    located = []
    for slot in archetype.slots:
        if slot.accepts and "text" not in slot.accepts:
            continue
        origin = _slot_origin(archetype, slot, pack, grid)
        if origin is None:
            continue
        located.append((origin, slot.frame_id))
    if len(located) < 2:
        return {frame_id: 0.0 for _, frame_id in located}
    # Banded by row before being read across, so two slots a hair apart
    # vertically are not ordered by that hair.
    band = max(0.02 * pack.canvas.h_in, grid.baseline_in * 2 or 0.0)
    located.sort(key=lambda entry: (round(entry[0][0] / band) if band else
                                    entry[0][0], entry[0][1]))
    last = len(located) - 1
    return {frame_id: index / last for index, (_, frame_id) in enumerate(located)}


def overflow_of(item: MaterialItem, pack: Pack,
                size: Optional[tuple[float, float]],
                frame_role: str = "column",
                type_role: Optional[str] = None) -> float:
    """How far past its slot this item's copy runs, as a share of the slot."""
    if size is None or item.kind not in _TEXT_KINDS:
        return 0.0
    width_in, height_in = size
    if width_in <= 0 or height_in <= 0:
        return 1.0
    from app.design.setting import set_copy

    role = pack.type_roles.get(
        type_role_for(frame_role, item.kind, pack, type_role))
    if role is None:
        return 0.0
    result = set_copy(item.text, role, pack.face_for(role),
                      width_in * 72.0, height_in * 72.0)
    return result.overflow_pt / (height_in * 72.0)


def capacity_strain(item: MaterialItem, slot: SlotSpec, pack: Pack,
                    size: Optional[tuple[float, float]],
                    frame_role: str = "column",
                    type_role: Optional[str] = None) -> float:
    """How badly this item over- or under-fills this slot.

    Measured with the real typesetter rather than by character count, so the
    answer is about the page rather than about the string length.
    """
    if size is None:
        return 1.0
    width_in, height_in = size
    if width_in <= 0 or height_in <= 0:
        return 1.0

    if item.kind not in _TEXT_KINDS:
        # An exhibit fills whatever it is given; what matters for it is
        # aspect and legibility, both counted in `pair_cost`.
        return 0.0

    from app.design.setting import set_copy

    role = pack.type_roles.get(
        type_role_for(frame_role, item.kind, pack, type_role))
    if role is None:
        return 0.5
    result = set_copy(item.text, role, pack.face_for(role),
                      width_in * 72.0, height_in * 72.0)
    if height_in <= 0:
        return 1.0
    fill = result.height_pt / (height_in * 72.0)
    if fill > 1.0:
        return min(4.0, (fill - 1.0) ** 2 * 4.0 + (fill - 1.0))
    if fill < COMFORTABLE_FILL:
        return (COMFORTABLE_FILL - fill) ** 2
    return 0.0


# The mismatch at which one shape is simply the wrong shape, past which
# nothing is gained by measuring further.
#
# It was two, and a factor of two is a smaller difference than the page
# offers: a half-width slot beside a column measures about 1.4, a full-width
# band under a heading about 6.3, and those are four and a half apart. So
# every portrait chart saturated against both and the term could not say which
# was closer - it reported "maximally wrong" for the slot that fitted and for
# the slot that did not. Widening it to four leaves the shipped packs choosing
# exactly the pages they chose before, and lets a tall exhibit find the tall
# slot.
ASPECT_SATURATION = 4.0


def aspect_penalty(item: MaterialItem,
                   size: Optional[tuple[float, float]]) -> float:
    """How far this slot's shape is from the shape the item wants."""
    if size is None or not item.aspect or item.aspect <= 0:
        return 0.0
    width_in, height_in = size
    if height_in <= 0:
        return 1.0
    slot_aspect = width_in / height_in
    if slot_aspect <= 0:
        return 1.0
    return min(1.0, abs(math.log(item.aspect / slot_aspect))
               / math.log(ASPECT_SATURATION))


def exhibit_floor_in(item: MaterialItem, pack: Pack,
                     width_in: Optional[float] = None) -> float:
    """The shortest slot this exhibit can be read in, in inches.

    A table is measured at the slot's width, wrapped the way it will be
    drawn: a row count alone said nothing about a six-column table whose
    cells each break onto three lines in a half-width column.
    """
    if item.kind == "table":
        table = (item.payload or {}).get("table") or {}
        rows = [[str(c) for c in row] for row in (table.get("rows") or [])]
        if width_in and rows:
            from app.renderer import text_fit
            from app.renderer.table_layout import needed_height_in

            # The table is drawn in the design's data-viz face, which the
            # pack does not carry: measured in both it and the pack's body
            # face, and the taller kept, the floor is never the optimistic one.
            faces = {text_fit.DEFAULT_MEASURE_FAMILY, pack.body_face or ""} - {""}
            header = bool(table.get("headerRow"))
            return max(needed_height_in(rows, header, width_in, family=face)
                       for face in faces)
        body = pack.type_roles.get("body")
        size_pt = body.size_pt if body else 12.0
        return max(1, len(rows)) * size_pt * TABLE_ROW_LEADING / 72.0
    return MIN_EXHIBIT_IN.get(item.kind, 0.0)


def legibility_shortfall(item: MaterialItem, pack: Pack,
                         size: Optional[tuple[float, float]]) -> float:
    """How far short of readable this slot leaves an exhibit, from 0 to 1."""
    if item.kind not in _EXHIBIT_KINDS or size is None:
        return 0.0
    floor = exhibit_floor_in(item, pack, size[0])
    if floor <= 0:
        return 0.0
    return min(1.0, max(0.0, floor - size[1]) / floor)


def readable_somewhere(item: MaterialItem, pack: Pack,
                       candidates: Optional[Sequence[PageArchetype]] = None) -> bool:
    """Whether any slot of these archetypes can show this exhibit legibly.

    Legibility depends on the slot, not on the prose around the exhibit, so
    when this is False no amount of splitting a page's prose will help.
    """
    if item.kind not in _EXHIBIT_KINDS:
        return True
    for archetype in candidates or pack.archetypes:
        grid = archetype.grid or pack.grid
        for slot in archetype.slots:
            if not _accepts(slot, item):
                continue
            size = _slot_rect(archetype, slot, pack, grid)
            if size and legibility_shortfall(item, pack, size) <= legible_tolerance(item):
                return True
    return False


def pair_cost(item: MaterialItem, slot: SlotSpec, pack: Pack,
              archetype: PageArchetype, grid: GridSpec, *,
              item_order: Optional[float] = None,
              slot_order: Optional[float] = None,
              legible: bool = True) -> float:
    """What it costs to put this item in this slot.

    ``legible`` refuses a slot an exhibit cannot be read in. Relaxed only as a
    last resort, for material that cannot be divided any further.
    """
    if not _accepts(slot, item):
        return INFEASIBLE
    frame = _slot_frame(archetype, slot)
    frame_role = frame.role if frame else "column"
    if not _role_compatible(frame_role, item):
        return INFEASIBLE
    size = _slot_rect(archetype, slot, pack, grid)
    shortfall = legibility_shortfall(item, pack, size)
    if legible and shortfall > legible_tolerance(item):
        return INFEASIBLE
    cost = (
        W_CAPACITY * capacity_strain(item, slot, pack, size, frame_role,
                                     frame.type_role if frame else None)
        + W_ASPECT * aspect_penalty(item, size)
        + W_LEGIBLE * shortfall
    )
    # Prominent material belongs in prominent slots. Stated as a preference
    # rather than a rule, so it bends when the geometry disagrees.
    cost += abs(item.importance - slot.prominence) * 0.4
    # ...and material meant for this kind of frame belongs in it.
    if _HINT_FOR_FRAME.get(frame_role) == item.role_hint:
        cost -= 2.0
    elif item.role_hint == "heading" and frame_role not in DISPLAY_FRAME_ROLES:
        # A page whose assertion is set as a body paragraph has buried its
        # own argument. Allowed, because an archetype without a headline
        # frame is still usable, but it should lose to one that has a place
        # to put the heading.
        cost += 2.0
    if slot.max_chars and item.chars > slot.max_chars:
        cost += min(2.0, (item.chars - slot.max_chars) / max(1, slot.max_chars))
    if slot.min_chars and item.chars < slot.min_chars:
        cost += 0.3
    if item_order is not None and slot_order is not None:
        cost += W_SEQUENCE * abs(item_order - slot_order)
    if slot.required and _suits(slot, item):
        cost -= REQUIRED_BONUS
    return cost


def _suits(slot: SlotSpec, item: MaterialItem) -> bool:
    """Whether this item is the kind of thing the slot was made for.

    The required-slot bonus has to be gated on this. Applied flatly it is
    larger than every other term put together, so *any* item makes a
    required slot the cheapest place to be - and a three-hundred-character
    paragraph wins the headline slot and gets set at headline size across
    the page. A required slot pulls in material it can hold, not whatever
    the solver reached first.
    """
    if slot.max_chars and item.chars > slot.max_chars * 1.5:
        return False
    return True


def _body_slots(slots: Sequence[SlotSpec], archetype: PageArchetype,
                slot_order: dict) -> list[SlotSpec]:
    """This archetype's slots a paragraph could actually sit in, in reading order.

    A display-only frame - a headline, an eyebrow - is not one of them, even
    though it accepts text: it exists for the one line that names the page.
    """
    usable = []
    for slot in slots:
        if slot.accepts and "text" not in slot.accepts:
            continue
        frame = _slot_frame(archetype, slot)
        if frame is not None and frame.role in DISPLAY_ONLY_FRAME_ROLES:
            continue
        if frame is not None and frame.role in APPARATUS_FRAME_ROLES:
            continue
        usable.append(slot)
    return sorted(usable, key=lambda s: slot_order.get(s.frame_id, 0.0))


def _grouped(items: list[MaterialItem], slots: Sequence[SlotSpec],
             archetype: PageArchetype, slot_order: dict) -> list[MaterialItem]:
    """Paragraphs gathered into as many runs as this archetype has room for.

    The assignment below is one item to one slot, which read as "a prose
    column holds one paragraph". A prose column holds a column of prose - it
    is what the word means, it is what `max_chars` measures, and `_fill`
    already puts several items in one frame. Without this, an archetype with
    two body slots could not carry a three-paragraph page at all: the third
    paragraph went unplaced, `fit` refused the archetype, and the page was
    split into two that were four-fifths empty. Every content page of a
    three-point deck came out that way.

    The groups are sized in proportion to what each slot holds, not evenly.
    An even split put a hundred characters into a stat row that holds fifty
    and fifty into a column that holds four hundred - which is a worse page
    than the split it replaced. Order is preserved, so the page still reads
    top to bottom in the order it was written.
    """
    body = [item for item in items
            if item.kind == "text" and item.role_hint == "body"]
    usable = _body_slots(slots, archetype, slot_order)
    room = len(usable)
    if room < 1 or len(body) <= room:
        return items

    # A slot that states no capacity is taken at the median of those that do,
    # so it is neither starved nor handed the whole page.
    stated = [float(s.max_chars) for s in usable if s.max_chars]
    fallback = (sorted(stated)[len(stated) // 2] if stated else 1.0)
    weights = [float(s.max_chars) if s.max_chars else fallback for s in usable]
    weight_total = sum(weights) or float(room)

    total = float(sum(item.chars for item in body) or len(body))
    # Where each group ends, as a running character count.
    bounds = []
    running = 0.0
    for weight in weights:
        running += total * weight / weight_total
        bounds.append(running)

    groups: list[list[MaterialItem]] = [[] for _ in range(room)]
    index = 0
    carried = 0
    for item in body:
        while index < room - 1 and carried >= bounds[index]:
            index += 1
        groups[index].append(item)
        carried += item.chars

    merged: dict[str, MaterialItem] = {}
    dropped: set[str] = set()
    for group in groups:
        if len(group) < 2:
            continue
        first = group[0]
        merged[first.id] = MaterialItem(
            id=first.id,
            kind="text",
            text="\n".join(item.text for item in group),
            # The group is as important as its most important paragraph:
            # averaging would let one aside dilute the point beside it.
            importance=max(item.importance for item in group),
            role_hint="body",
        )
        dropped.update(item.id for item in group[1:])

    return [merged.get(item.id, item) for item in items
            if item.id not in dropped]


def fit(shape: MaterialShape, archetype: PageArchetype, pack: Pack, *,
        previous_archetype: Optional[str] = None,
        legible: bool = True) -> Optional[Selection]:
    """Bind this material into this archetype, and say what it cost.

    Returns None when the archetype cannot carry the material at all - a
    required slot left empty, or an exhibit with nowhere to go.
    """
    if archetype.role != shape.role and shape.role in ("cover", "section",
                                                       "closing"):
        return None
    # A page of endnotes is a real page of the publication and a useless
    # archetype: it has no column, headline or standfirst, so binding a deck's
    # material to it sets every paragraph at footnote size. Such pages are
    # recorded as `appendix` when the pack is built, and content does not go
    # on them.
    if shape.role == "content" and archetype.role not in ("content", "exhibit"):
        return None

    grid = archetype.grid or pack.grid
    items = list(shape.items)
    if shape.quote:
        items.append(MaterialItem(id="quote", kind="quote", text=shape.quote,
                                  importance=0.75, role_hint="quote"))
    slots = list(archetype.slots)
    if not slots or not items:
        return None

    slot_order = reading_order(archetype, pack, grid)
    items = _grouped(items, slots, archetype, slot_order)
    # Only the body prose is sequenced. A heading, a quote or an exhibit is
    # placed by what it *is*, and ordering it against the paragraphs would
    # drag the heading toward the middle of the page.
    body = [item for item in items
            if item.kind == "text" and item.role_hint == "body"]
    item_order = {
        item.id: (index / (len(body) - 1) if len(body) > 1 else 0.0)
        for index, item in enumerate(body)
    }
    cost = [
        [
            pair_cost(item, slot, pack, archetype, grid,
                      item_order=item_order.get(item.id),
                      slot_order=slot_order.get(slot.frame_id),
                      legible=legible)
            for slot in slots
        ]
        for item in items
    ]
    assignment = solve(cost)

    placements = []
    unplaced = []
    for index, item in enumerate(items):
        column = assignment[index]
        if column is None:
            unplaced.append(item)
        else:
            placements.append(Placement(item=item, slot=slots[column],
                                        cost=cost[index][column]))
    filled = {p.slot.frame_id for p in placements}
    unfilled = tuple(s for s in slots if s.frame_id not in filled)

    if any(s.required for s in unfilled):
        return None
    if unplaced:
        # An archetype that cannot carry all of the material is not a
        # cheaper option, it is not an option. Costing it and letting it
        # compete meant a page that silently dropped five of its seven
        # paragraphs scored *better* than one that placed all of them,
        # because dropping material costs nothing to measure and a full page
        # costs density and overflow. Losing the content is the worst
        # outcome available, so it is not on the menu; a caller with more
        # material than any archetype holds has to split the page, and
        # `unplaced_report` says so.
        return None

    # Every item is placed and every required slot is filled by the time we
    # get here, so what remains to penalise is optional slots left standing
    # empty - an archetype used at a fraction of its shape.
    optional = [s for s in slots if not s.required]
    slot_mismatch = (
        sum(1 for s in unfilled if not s.required) / len(optional)
        if optional else 0.0
    )
    capacity = max(
        0.0,
        (total_cost(cost, assignment)
         + REQUIRED_BONUS * sum(1 for p in placements if p.slot.required))
        / max(1, len(placements)),
    )
    density = _density_deviation(shape, archetype)
    rhythm = _rhythm_penalty(archetype, previous_archetype)

    overflow = sum(
        overflow_of(
            p.item, pack, _slot_rect(archetype, p.slot, pack, grid),
            (_slot_frame(archetype, p.slot).role
             if _slot_frame(archetype, p.slot) else "column"),
            (_slot_frame(archetype, p.slot).type_role
             if _slot_frame(archetype, p.slot) else None),
        )
        for p in placements
    )
    terms = {
        "slot_mismatch": W_SLOT * slot_mismatch,
        "capacity": capacity,
        "overflow": W_OVERFLOW * overflow,
        "density": W_DENSITY * density,
        "rhythm": W_RHYTHM * rhythm,
    }
    return Selection(
        archetype=archetype,
        placements=tuple(placements),
        unplaced=tuple(unplaced),
        unfilled=unfilled,
        cost=sum(terms.values()),
        terms=terms,
    )


def _density_deviation(shape: MaterialShape,
                       archetype: PageArchetype) -> float:
    """How far this material is from the volume the archetype was built for.

    Measured in characters against what the archetype's text slots actually
    held on the page they were read from, not in item counts. Three
    paragraphs and eight paragraphs are both "several items"; one of them
    fills an EY spread and the other leaves two thirds of it white, and a
    count cannot tell them apart. This is the term that should stop a
    six-hundred-word archetype being chosen for a hundred and fifty words.
    """
    expected = sum(
        slot.expected_chars for slot in archetype.slots
        if "text" in (slot.accepts or ())
    )
    actual = shape.total_chars + len(shape.heading)
    if expected <= 0:
        return 0.0
    ratio = actual / expected
    if ratio >= 1.0:
        # More copy than the archetype held is a smaller problem than less:
        # it shows up as overflow, which has its own term and is measured
        # against the real typesetter.
        return min(1.5, (ratio - 1.0) * 0.5)
    # Underfill is quadratic, because a page at a third of its intended
    # volume is far worse than one at two thirds - it reads as unfinished
    # rather than as airy.
    return min(3.0, (1.0 - ratio) ** 2 * 3.0)


def _rhythm_penalty(archetype: PageArchetype,
                    previous_archetype: Optional[str]) -> float:
    """Discourage running the same page twice.

    A deck that picks its single best-fitting archetype every time is a deck
    of identical pages, which is the failure this whole layer exists to end.
    Reproducing a house style means reproducing its *mix*.
    """
    if previous_archetype and archetype.id == previous_archetype:
        return 1.0
    return 0.0


def choose(shape: MaterialShape, pack: Pack, *,
           previous_archetype: Optional[str] = None,
           candidates: Optional[Sequence[PageArchetype]] = None,
           legible: bool = True) -> Optional[Selection]:
    """The archetype from this pack that best carries this material."""
    considered = list(candidates if candidates is not None else pack.archetypes)
    scored = [
        selection for selection in (
            fit(shape, archetype, pack, previous_archetype=previous_archetype,
                legible=legible)
            for archetype in considered
        )
        if selection is not None
    ]
    if not scored:
        return None
    # Ties break toward the archetype the publication uses more often, so a
    # generated deck drifts toward the house's common page rather than its
    # most interesting one.
    scored.sort(key=lambda s: (round(s.cost, 6), -s.archetype.frequency))
    return scored[0]
