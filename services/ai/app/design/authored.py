"""
A design language the agent reasons out for one deck.

Directions were a point in a 4x4 enum - four grounds, four structures - and
the prompt required each of three to use a different one of each. So three
directions were three picks from sixteen cells, and the only things that
varied with the subject were a hex and a two-word name. Deck to deck they
could not help but look the same, and no wording of the prompt could fix a
vocabulary that did not contain the difference being asked for.

A catalogue of packs would have been the same failure at a different size:
still "pick one of N".

So the language is **authored per deck**. What the agent decides is the
design - the measure, the ramp and its ratios, the colour roles, what kinds of
page this deck needs and what each carries, how dense a page is, how imagery
is treated. Reasoned from the subject, in the terms a designer would use.

What the agent does **not** decide is geometry. The model never emits
coordinates - that is what removed the whole class of guessed-rectangle
failures - so no frame, span, baseline or slot capacity is authored here. All
of those are derived, by the same code that derives them when a language is
measured from a PDF instead of authored.

The result is a `Pack`, which means every gate that already exists still
applies to it: no slot too small for the type it is set in, no ambiguous
overlaps, a legibility floor, and a repair loop that can only ratchet upward.
An invented language that cannot pass those is rejected exactly as a badly
recovered one is.
"""

from __future__ import annotations

from typing import Literal, NamedTuple, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.design.pack import (
    ColorRoles,
    Confidence,
    Illustration,
    LegibilityPolicy,
    Pack,
    PageArchetype,
    Rhythm,
    SlotSpec,
    SpacingScale,
    TypeRole,
)
from app.design.schema import (Canvas, FracPlace, Frame, GridPlace, GridSpec,
                               Insets, Scrim)

# What a page can carry. Open in combination and count - a page may hold any
# number of these in any order - and closed in kind, because a slot's kind is
# what decides the type it is set in and what material may go there. This is
# the level at which a design decision stops being a decision and starts being
# a rectangle.
SlotKind = Literal["heading", "standfirst", "prose", "figure", "statistic",
                   "quote", "caption", "footnote"]

# The type role each slot kind is set in.
ROLE_FOR_SLOT: dict[str, str] = {
    "heading": "headline",
    "standfirst": "standfirst",
    "prose": "body",
    "statistic": "headline",
    "quote": "pullquote",
    "caption": "caption",
    "footnote": "footnote",
    "figure": "body",
}

FRAME_FOR_SLOT: dict[str, str] = {
    "heading": "headline",
    "standfirst": "standfirst",
    "prose": "column",
    "statistic": "statRow",
    "quote": "pullquote",
    "caption": "caption",
    "footnote": "footnotes",
    "figure": "figure",
}

# Slot kinds that carry an exhibit rather than copy.
#
# A language may declare one `required`, and a model reasonably writes that:
# it means "this page is about its chart". `SlotSpec.required` means something
# else - "reject this archetype when the slot is empty" - and honouring the
# second reading of the first sentence made an authored language unusable.
#
# The deck's cover carries a heading and a subtitle and no chart, so the only
# content archetype was rejected, `_place` had nothing left to fall back to,
# and `compose` raised `Unplaceable` on page one. Every text-only page failed
# the same way. The direction then rendered no previews at all, and the user
# chose a design by looking at three grey rectangles.
#
# So a required exhibit slot is honoured as prominence, not as a precondition.
# An empty one costs nothing: `bind._compact` drops a frame nothing was bound
# to and closes the gap above it.
EXHIBIT_SLOTS = frozenset({"figure", "statistic"})

ACCEPTS_FOR_SLOT: dict[str, tuple[str, ...]] = {
    "figure": ("figure", "chart", "table", "diagram", "metrics"),
    # A stat row is where a row of figures goes - that is the whole reason a
    # language declares one - so it takes figures and nothing else. It used to
    # accept text, which meant a page with no metrics put a paragraph there
    # and the paragraph was set at headline size, competing with the heading
    # two inches above it. An empty stat row is a page that had no figures;
    # a sentence in one is a page that looks broken.
    "statistic": ("metrics",),
}

# How tall one slot is, as multiples of the lines its role sets. A heading is
# two lines because a headline that fits on one is a headline that could have
# been longer.
LINES_FOR_SLOT: dict[str, int] = {
    "heading": 2, "standfirst": 3, "prose": 6, "statistic": 2,
    "quote": 3, "caption": 2, "footnote": 2, "figure": 0,
}

# What a density does to how much copy a page carries.
DENSITY_SCALE = {"airy": 0.7, "balanced": 1.0, "dense": 1.45}

# What share of a slot's geometric capacity a page of this density actually
# carries. A derived capacity is a ceiling - the characters that would fit if
# the slot were set solid - and no designed page is set solid. Selecting
# against the ceiling made every archetype look underfilled and picked the
# meanest one available; selecting against the habit picks the one whose shape
# the material suits.
TYPICAL_FILL = {"airy": 0.45, "balanced": 0.62, "dense": 0.82}

# The ramp, as multiples of the body size. Authored as ratios rather than
# points because a ratio is the decision - a display three times the body is a
# loud deck at any canvas size, and 42pt is only loud on some.
DEFAULT_RATIOS = {
    "display": 3.6, "headline": 2.4, "standfirst": 1.4,
    "body": 1.0, "caption": 0.85, "footnote": 0.7, "eyebrow": 0.7,
}

# Leading, as a multiple of the size it sets. Display type is set tighter than
# body type in every publication anyone would want to look like.
LEADING = {"display": 1.05, "headline": 1.15, "standfirst": 1.25,
           "body": 1.4, "caption": 1.3, "footnote": 1.25, "eyebrow": 1.2}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Slot(_Model):
    """One thing a page carries."""

    kind: SlotKind
    # How many columns of the grid it spans. Clamped to the grid on
    # derivation, so an agent asking for eight of six gets six.
    #
    # Consecutive slots whose spans fit the grid share a row - that is the
    # whole of the horizontal vocabulary, and it needs nothing new to author.
    # Before rows existed a span narrowed a frame without moving anything up
    # beside it, so `figure span 3` was a half-width box with dead space to
    # its right and the next slot went underneath it. Every page an authored
    # language produced was one column down the left of the slide.
    span: int = Field(default=1, ge=1, le=48)
    # Whether the page is unusable without it.
    required: bool = False
    # A short id, so two prose columns on one page are distinguishable.
    name: str = ""
    # Whether this runs to the edge of the page rather than stopping at the
    # margin. Which edges is derived from where the slot lands - a frame in
    # the first row bleeds off the top, one at column zero off the left - so
    # a language says that a figure bleeds and never says where.
    bleed: bool = False
    # Whether this sits *behind* the rest of the page: the full content box,
    # below everything but the page's own ground, with the type laid over it. This is how the reference decks open -
    # a full-page photograph carrying the title - and it was the one shape the
    # schema could already express that no authored language could ask for.
    #
    # A page built this way is not compacted when a frame drops out.
    # `bind._compact` refuses to close gaps on a page whose frames overlap,
    # deliberately: "the row below" is not a thing such a page has. So a
    # behind slot must be authored complete rather than relying on that pass.
    behind: bool = False


class PageKind(_Model):
    """One kind of page this deck needs, and what it carries."""

    name: str = Field(min_length=1)
    role: Literal["cover", "section", "content", "exhibit", "closing"] = "content"
    slots: tuple[Slot, ...] = ()
    # Roughly what share of the deck is this kind of page. Reproducing a look
    # means reproducing its mix, not its single most interesting page.
    frequency: float = Field(default=0.0, ge=0.0, le=1.0)


class Language(_Model):
    """A design language, as the decisions behind one rather than as geometry."""

    name: str = Field(min_length=1)
    rationale: str = ""

    # The measure. Everything horizontal derives from these.
    columns: int = Field(default=6, ge=2, le=16)
    margin_ratio: float = Field(default=0.06, gt=0.0, lt=0.25)
    gutter_ratio: float = Field(default=0.02, gt=0.0, lt=0.1)

    # The ramp. Body in points, everything else as a multiple of it.
    # The default is a slide's body size, not a document's: 14.0 sat below
    # every canvas-relative floor, so the default itself needed scaling.
    body_pt: float = Field(default=18.0, gt=4.0, lt=48.0)
    ratios: dict[str, float] = Field(default_factory=dict)
    display_face: str = "Arial"
    body_face: str = "Arial"

    # The palette, by role rather than by position.
    ground: str = "#FFFFFF"
    ink: str = "#1A1A1A"
    accent: str = "#1F4E78"
    accent_alt: str = ""

    density: Literal["airy", "balanced", "dense"] = "balanced"
    illustration: Illustration = Illustration()

    pages: tuple[PageKind, ...] = ()

    def ratio(self, role: str) -> float:
        return float(self.ratios.get(role) or DEFAULT_RATIOS.get(role, 1.0))


def _type_roles(language: Language) -> dict[str, TypeRole]:
    """The ramp in points, from the ratios the language decided."""
    out: dict[str, TypeRole] = {}
    for role in DEFAULT_RATIOS:
        size = round(language.body_pt * language.ratio(role), 1)
        out[role] = TypeRole(
            size_pt=size,
            line_height_pt=round(size * LEADING.get(role, 1.3), 1),
            weight="bold" if role in ("headline", "display") else "regular",
            face="display" if role in ("display", "headline", "standfirst",
                                       "pullquote") else "body",
            color_role="ink",
            measured_min_pt=size, measured_median_pt=size, measured_max_pt=size,
        )
    out["pullquote"] = out["standfirst"].model_copy(update={
        "size_pt": round(language.body_pt * language.ratio("standfirst") * 1.3, 1),
        "line_height_pt": round(
            language.body_pt * language.ratio("standfirst") * 1.3 * 1.2, 1),
    })
    return out


def _grid(language: Language, canvas: Canvas, baseline_in: float) -> GridSpec:
    margin = canvas.w_in * language.margin_ratio
    return GridSpec(
        columns=language.columns,
        gutter_in=round(canvas.w_in * language.gutter_ratio, 4),
        margin=Insets(left=round(margin, 4), right=round(margin, 4),
                      top=round(margin, 4), bottom=round(margin, 4)),
        baseline_in=round(baseline_in, 4),
    )


# The slot kinds that flow, and so can be set beside one another. A heading
# spans its page by nature; a figure and the prose reading it are the pair a
# designed page actually puts side by side.
FLOWING_SLOTS = frozenset({"figure", "prose"})


def _arrangements(kind: PageKind, columns: int) -> list[tuple[str, PageKind]]:
    """The same page kind, arranged every way its own slots allow.

    An authored page kind used to derive to exactly one geometry, which meant
    a content-driven selector was choosing from a set of size one: a page of
    three paragraphs and a page carrying a wide chart got the same shape,
    because the shape was decided before either was looked at.

    `select.choose` already scores every archetype against the real material -
    how many items, how many characters, an exhibit's natural aspect, how far
    the page sits from the archetype's density habit. So what was missing was
    never a decision procedure. It was something to decide between.

    These are the language's own slots at the two ends of the one axis that is
    genuinely free, with the arrangement it authored kept as its preference.
    Nothing here invents a page kind the language did not ask for.
    """
    flowing = [index for index, slot in enumerate(kind.slots)
               if slot.kind in FLOWING_SLOTS and not slot.behind]

    def respan(spans: dict[int, int]) -> PageKind:
        return kind.model_copy(update={"slots": tuple(
            slot.model_copy(update={"span": spans.get(index, slot.span)})
            for index, slot in enumerate(kind.slots))})

    every = max(1, columns)
    candidates = [
        ("", kind),
        ("stacked", respan({i: every for i in range(len(kind.slots))})),
    ]
    if len(flowing) >= 2:
        half = max(1, columns // 2)
        spans = {i: every for i in range(len(kind.slots))}
        spans.update({i: half for i in flowing})
        candidates.append(("paired", respan(spans)))

    # Two arrangements that come out the same shape are one archetype. A
    # language whose slots are already full width *is* the stack, and offering
    # the selector two identical options only costs it time.
    seen: set[tuple] = set()
    unique: list[tuple[str, PageKind]] = []
    for suffix, variant in candidates:
        key = tuple((slot.span, slot.behind) for slot in variant.slots)
        if key in seen:
            continue
        seen.add(key)
        unique.append((suffix, variant))
    return unique


def _archetype(kind: PageKind, language: Language, canvas: Canvas,
               grid: GridSpec, roles: dict[str, TypeRole], *,
               suffix: str = "",
               frequency: Optional[float] = None) -> PageArchetype:
    """One authored page kind, laid out.

    This is where a decision becomes a rectangle, and the agent is not in the
    room for it. `_stack` decides where every slot sits - which row it shares,
    which columns it takes, how tall its band is - and this turns those
    fractions into frames and the slot specs that bind material to them.
    """
    from app.design.geometry import content_box

    box = content_box(canvas, grid)
    scale = DENSITY_SCALE[language.density]
    frames: list[Frame] = []
    slots: list[SlotSpec] = []
    gap = (grid.baseline_in or canvas.h_in * 0.012) / max(0.01, box.height)

    ground = "accent" if kind.role in ("cover", "section") else None
    # Paint order, stated rather than tied: the page's ground, then a figure
    # that sits behind, then everything laid over it. A cover carries a
    # full-page ground, so a behind figure at the ground's own z overlapped
    # it completely and check_doc refused the page - and with it every render
    # of any deck on that language.
    behind_z = 1 if ground else 0
    if ground:
        frames.append(Frame(
            id=f"{kind.name}-ground", role="panel",
            place=FracPlace(x=0.0, y=0.0, w=1.0, h=1.0),
            ground=ground, z=0, bleed=("top", "right", "bottom", "left"),
        ))

    stack = _stack(kind, language, roles, box.height, scale, gap, grid.columns)

    archetype_id = f"{kind.name}-{suffix}" if suffix else kind.name

    for index, slot in enumerate(kind.slots):
        role_name = _rung_for(slot, kind)
        role = roles.get(role_name) or roles["body"]
        # Prefixed with the arrangement, not just the page kind: two
        # arrangements of one kind are two archetypes, and a frame id repeated
        # across them makes a composed page ambiguous about where it came from.
        identifier = f"{archetype_id}-{slot.name or slot.kind}-{index}"
        cell = stack[index]
        frames.append(Frame(
            id=identifier, role=FRAME_FOR_SLOT[slot.kind],
            # The rung this slot is set in, stated on the frame rather than
            # only used to size it. Left off, a statistic slot was measured
            # as a headline and then *rendered* at body size, because the
            # renderer reads the frame and the frame said nothing - so a
            # language's stat row came out the same size as its prose.
            type_role=role_name,
            place=GridPlace(col=cell.col, span=cell.span,
                            y=round(cell.y, 4), h=round(cell.h, 4)),
            # A slot that sits behind goes under everything else. Overlap is
            # an error only between siblings at equal z, so this is the one
            # declaration that makes type-over-image legal geometry rather
            # than an ambiguity the gate refuses.
            z=behind_z if cell.behind else behind_z + 1,
            bleed=cell.bleed,
            # A photograph with type over it needs two things the geometry
            # alone does not give it: something to darken the image so the
            # type is legible whatever the picture happens to be, and a
            # ground dark enough that everything above it reverses out to
            # white. Without the scrim this is white text hoping for a dark
            # sky; without the ground, `designdoc_renderer._ground_under`
            # finds no colour under the type, falls back to the page's own
            # white, and paints dark ink onto the photograph.
            **({"scrim": Scrim(color_role="ink", opacity=0.55),
                "ground": "ink"}
               if cell.behind and slot.kind == "figure" else {}),
            # A quote that is not sitting over a picture is the page's
            # pull-out statement, and the reference publications rule a box
            # round it in the accent colour. Only when it stands on the
            # page's own ground: outlining one laid over a photograph draws
            # a rectangle round part of the image, which is a different and
            # much worse thing.
            **({"border": "accent", "border_pt": 1.5}
               if slot.kind == "quote"
               and not _over_panel(cell, kind, grid.columns)
               else {}),
        ))
        capacity = _capacity(role, _face_for(language, role), cell.span,
                             grid, canvas, cell.h, box.height)
        slots.append(SlotSpec(
            frame_id=identifier,
            accepts=ACCEPTS_FOR_SLOT.get(slot.kind, ("text",)),
            required=slot.required and slot.kind not in EXHIBIT_SLOTS,
            prominence=1.0 if slot.kind == "heading" else 0.5,
            max_chars=capacity,
            typical_chars=max(1, round(
                capacity * TYPICAL_FILL[language.density])),
        ))

    return PageArchetype(
        id=archetype_id, label=archetype_id.replace("-", " "), role=kind.role,
        frequency=kind.frequency if frequency is None else frequency,
        frames=tuple(frames), slots=tuple(slots),
        note="authored for this deck, not measured from a publication",
    )


class _Cell(NamedTuple):
    """Where one slot ends up, in fractions of the content box."""

    col: int
    span: int
    y: float
    h: float
    bleed: tuple[str, ...]
    behind: bool


def _rows(kind: PageKind, columns: int) -> list[list[int]]:
    """Slot indices grouped into the rows they share.

    Consecutive slots whose spans sum to the grid width belong beside each
    other, and the next row starts when the sum would overflow. That single
    rule turns `heading 6 / figure 3 / prose 3` from three stacked boxes into
    a headline over a figure and its commentary side by side, and it adds no
    authoring vocabulary at all: `Slot.span` already says how wide a thing is
    and `PageKind.slots` already has an order.

    A slot that sits behind is not in any row. It is the whole page.
    """
    rows: list[list[int]] = []
    current: list[int] = []
    used = 0
    for index, slot in enumerate(kind.slots):
        if slot.behind:
            continue
        span = max(1, min(columns, slot.span))
        if current and used + span > columns:
            rows.append(current)
            current, used = [], 0
        current.append(index)
        used += span
    if current:
        rows.append(current)
    return rows


def _natural(kind: PageKind, roles: dict[str, TypeRole],
             box_height: float, scale: float) -> list[float]:
    """How tall each slot wants to be, from the type it is set in."""
    out: list[float] = []
    for slot in kind.slots:
        role = roles.get(_rung_for(slot, kind)) or roles["body"]
        if slot.kind == "figure":
            out.append(0.34)
            continue
        lines = LINES_FOR_SLOT[slot.kind]
        wanted = max(1, round(lines * (scale if slot.kind == "prose" else 1.0)))
        out.append((wanted * role.line_height_pt / 72.0)
                   / max(0.01, box_height))
    return out


def _over_panel(cell: "_Cell", kind: PageKind, columns: int) -> bool:
    """Whether this cell is laid over a behind slot rather than beside it.

    A full-page behind slot puts everything over it; a side panel puts over
    it only what starts inside the panel's own columns.
    """
    panel = _panel_span(kind, columns)
    if any(slot.behind for slot in kind.slots) and not panel:
        return True
    return bool(panel) and cell.col < panel


def _panel_span(kind: PageKind, columns: int) -> int:
    """How many columns a side panel reserves, or 0 when there is none.

    A behind slot is one of two things, and its own declared width is what
    says which. Naming a width narrower than the grid asks for a panel down
    one side - the reference publications' section opener, a full-height
    photograph beside the reading matter. Naming none (the default span of
    1) or the whole grid asks for a wash under the entire page, with the
    type laid over it.

    Only the first is honoured. Two panels would be two sides of a page with
    nothing left between them, and a page whose whole width is spoken for
    has nowhere to put what it is about.
    """
    for slot in kind.slots:
        if not slot.behind:
            continue
        span = int(slot.span)
        if 1 < span < columns:
            # Never so wide that the remaining columns cannot hold a
            # sentence: a panel of eleven columns in twelve leaves a
            # one-column measure, which sets two words to a line.
            return min(span, max(1, columns - MIN_READING_COLUMNS))
        return 0
    return 0


# The narrowest measure worth setting body copy in, in columns of the page's
# own grid. Below this a "column" is a stack of hyphenated fragments.
MIN_READING_COLUMNS = 3


def _stack(kind: PageKind, language: Language, roles: dict[str, TypeRole],
           box_height: float, scale: float, gap: float,
           columns: int) -> list[_Cell]:
    """Where every slot sits, row by row.

    Rows are the unit of vertical rhythm once rows exist. Distributing the
    page's spare height per *slot* - which is what this did when every slot
    was its own band - over-counts a row carrying two things and leaves the
    page short: a figure and its commentary side by side measured as two
    stacked bands, so the stack came to about half the box and the rest was
    white. That is the same unfinished page `_fill` was written to prevent,
    reached through the side door.
    """
    natural = _natural(kind, roles, box_height, scale)
    # A behind slot that names its own width is a *panel* down one side of the
    # page rather than a wash under the whole of it - the shape the reference
    # publications open a section with: a full-height photograph holding the
    # left half, the reading matter set in the columns beside it. Reserving
    # its columns here is what keeps the two from being laid out on top of
    # each other; a behind slot that names no width (span 1, or the whole
    # grid) still becomes the full page, with the type over it.
    panel = _panel_span(kind, columns)
    rows = _rows(kind, columns - panel)

    # A row is as tall as the tallest thing in it, and can grow if anything
    # in it flows.
    row_natural = [max(natural[i] for i in row) for row in rows]
    row_flexible = [
        any(kind.slots[i].kind not in FIXED_HEIGHT_SLOTS for i in row)
        for row in rows
    ]
    row_figure = [any(kind.slots[i].kind == "figure" for i in row) for row in rows]
    row_height = _fill(row_natural, row_flexible, gap, protected=row_figure)

    cells: list[Optional[_Cell]] = [None] * len(kind.slots)
    y = 0.0
    for position, (row, height) in enumerate(zip(rows, row_height)):
        col = panel
        for index in row:
            slot = kind.slots[index]
            span = max(1, min(columns - panel, slot.span))
            # A fixed-height slot keeps its own height and sits at the top of
            # its row. Stretching a heading to match the figure beside it sets
            # one line of display type floating in the middle of a tall box.
            own = (height if slot.kind not in FIXED_HEIGHT_SLOTS
                   else min(natural[index], height))
            cells[index] = _Cell(
                col=col, span=span, y=y, h=max(MIN_SLOT_HEIGHT, own),
                bleed=_bleed_edges(slot, col, span, columns,
                                   position, len(rows)),
                behind=False,
            )
            col += span
        y += height + gap

    for index, slot in enumerate(kind.slots):
        if not slot.behind:
            continue
        if panel:
            # The panel: its own columns, the full height of the page, and
            # bleeding off every edge it actually touches. Not the right
            # edge - that is where the reading matter begins, and a
            # photograph running under the text would put white type on
            # whatever the picture happened to be there.
            cells[index] = _Cell(
                col=0, span=panel, y=0.0, h=1.0,
                bleed=("top", "bottom", "left"), behind=True,
            )
        else:
            cells[index] = _Cell(
                col=0, span=columns, y=0.0, h=1.0,
                bleed=("top", "right", "bottom", "left"), behind=True,
            )

    settled = [cell for cell in cells if cell is not None]
    return _seated(settled, kind.role)


def _seated(cells: list[_Cell], role: str) -> list[_Cell]:
    """An opener's type sits low on the page rather than at the top of it.

    `vocabulary.py` already builds a composed cover this way - it is what BXSL
    does, and it leaves the upper two thirds for the ground to carry - but an
    authored opener stacked from the top like any other page. So a language's
    own cover was a title and a standfirst in the top quarter with a flat
    field of accent under them, while the cover composed *for* a language that
    forgot to declare one was set properly. Two covers, two rules, and the
    authored one looked unfinished.

    Only a stack with room to move is moved. A cover carrying a column of
    prose has already filled the page, and pushing it down would run it off
    the bottom.
    """
    from app.design.vocabulary import COVER_BASELINE, SECTION_BASELINE

    baseline = {"cover": COVER_BASELINE, "section": SECTION_BASELINE}.get(role)
    if baseline is None:
        return cells
    movable = [cell for cell in cells if not cell.behind]
    if not movable:
        return cells
    bottom = max(cell.y + cell.h for cell in movable)
    shift = baseline - bottom
    if shift <= 0:
        return cells
    return [cell if cell.behind else cell._replace(y=cell.y + shift)
            for cell in cells]


def _bleed_edges(slot: Slot, col: int, span: int, columns: int,
                 row: int, rows: int) -> tuple[str, ...]:
    """Which edges a bleeding slot runs off, from where it landed.

    Derived rather than authored. A language says that a figure bleeds; where
    it bleeds is a consequence of the row it is in and the columns it takes,
    and asking a model for it would be asking for geometry again.
    """
    if not slot.bleed:
        return ()
    edges = []
    if row == 0:
        edges.append("top")
    if row == rows - 1:
        edges.append("bottom")
    if col == 0:
        edges.append("left")
    if col + span >= columns:
        edges.append("right")
    return tuple(edges)


# Slot kinds whose height is decided by the type they are set in. A headline
# is two lines tall because that is what two lines of it measures; giving it
# the room left over at the bottom of the page would set one line of it
# floating in the middle of nowhere. Everything else flows.
FIXED_HEIGHT_SLOTS = frozenset({
    "heading", "standfirst", "statistic", "quote", "caption", "footnote",
})

# The smallest a slot may be squeezed to and still be a rectangle. `GridPlace`
# refuses a height of zero, and a page whose bands have all been shrunk to
# nothing is a page that should have been split rather than one that should
# fail to construct.
MIN_SLOT_HEIGHT = 0.01


# The share of its natural height a prose band keeps when a page is too tall
# and a figure on it is kept at its own height instead.
PROSE_SHRINK_FLOOR = 0.35


def _fill(natural: list[float], flexible: list[bool],
          gap: float, protected: Optional[list[bool]] = None) -> list[float]:
    """Bands sized to fill the page, given what each one naturally wants.

    The natural heights come from the type. They rarely add up to a page: a
    heading, a stat row and a column of prose measure about a third of the
    box, and the stack used to be laid from the top and simply stop - so
    every page carried its material in the top third and two thirds of white
    below it, whatever the language said its density was. That is not an airy
    layout, it is an unfinished one.

    So what is left over is given to the bands that can use it - the ones
    carrying something that flows, prose and figures - in proportion to what
    they already have. A page with neither keeps its natural heights and its
    white space, which is right: a section divider carrying one line is meant
    to be mostly empty.

    A band is a row, not a slot, so two things side by side are measured once
    rather than twice.
    """
    heights = list(natural)
    used = sum(heights) + gap * max(0, len(heights) - 1)
    spare = 1.0 - used
    growable = [index for index, can in enumerate(flexible) if can]
    if abs(spare) < 1e-9 or not growable:
        return heights

    share = sum(heights[index] for index in growable)
    if share <= 0:
        return heights

    if spare > 0:
        for index in growable:
            heights[index] += spare * heights[index] / share
        return heights

    # Too tall, not too short. A language that declares a heading, a stat row,
    # a column and a figure asks for more than a page: the stack was laid from
    # the top regardless and the last slot ran off the bottom of the canvas,
    # where nothing renders. The flexible bands give the room back first,
    # since they are the ones that flow.
    wanted = -spare
    # Prose before figures. Prose that loses room is measured, and a page
    # whose copy no longer fits is split or placed elsewhere; an exhibit that
    # loses room is simply drawn smaller. Shrinking both in proportion took a
    # stacked page's figure from a third of the page to a fifth of an inch,
    # and every table set in it came out as a sliver (#351, #350).
    guarded = [index for index in growable if protected and protected[index]]
    yielding = [index for index in growable if index not in guarded]
    if guarded and yielding:
        # Down to a floor, so a column keeps at least a few lines' height.
        give = sum(heights[index] * (1 - PROSE_SHRINK_FLOOR) for index in yielding)
        taken = min(wanted, give)
        for index in yielding:
            heights[index] -= taken * heights[index] * (1 - PROSE_SHRINK_FLOOR) / give
        wanted -= taken
        if wanted <= 1e-9:
            return heights
        share = sum(heights[index] for index in growable)
    if wanted < share:
        for index in growable:
            heights[index] -= wanted * heights[index] / share
        return heights

    # Even emptying the flexible bands is not enough, so everything shrinks
    # together and the ramp is preserved as a ratio. A page cannot be taller
    # than the page.
    total = sum(heights)
    room = max(0.0, 1.0 - gap * max(0, len(heights) - 1))
    if total <= 0 or room <= 0:
        return heights
    return [height * room / total for height in heights]


def _rung_for(slot: Slot, kind: PageKind) -> str:
    """The rung this slot is set in, which everything about it is sized by.

    An opener's heading is display type. That was stated on the frame, so it
    was *drawn* at display size, while the slot's band and its capacity were
    worked out at headline size, a third smaller: a cover advertised room
    for an eighty-character title in a band that holds one line of display
    type. One answer, read by the band, the capacity and the frame alike.
    """
    if slot.kind == "heading" and kind.role in OPENER_ROLES:
        return "display"
    return ROLE_FOR_SLOT[slot.kind]


def _face_for(language: Language, role: TypeRole) -> str:
    """The face this role is drawn in - `Pack.face_for`, before a pack exists."""
    from app.design.pack import face_of

    return face_of(role, display=language.display_face, body=language.body_face)


def _capacity(role: TypeRole, face: str, span: int, grid: GridSpec,
              canvas: Canvas, height_fraction: float, box_height: float) -> int:
    """How much copy this slot holds, set in the face and rung it is drawn in.

    It was measured in Arial whatever the language's faces, so a language
    set in a wider face was promised room it did not have (#361).
    """
    from app.design.setting import capacity

    column = grid.column_width_in(canvas)
    width_in = span * column + max(0, span - 1) * grid.gutter_in
    if width_in <= 0:
        return 1
    lines = max(1, int((height_fraction * box_height * 72.0)
                       / max(1.0, role.line_height_pt)))
    return max(1, capacity(role, face, width_in * 72.0, lines))


# Where an opener's subtitle goes, when the language did not say.
#
# A cover carrying a headline and nothing else is a page kind a model
# plausibly authors, and it is not wrong - it is incomplete. The deck's own
# cover material is a heading *and* a standfirst, so such an archetype can
# never carry it: `fit` leaves the subtitle unplaced, and an archetype that
# cannot hold all of the material is not an option.
#
# Composing the missing slot rather than refusing the page is the same
# judgement `vocabulary.py` already makes for a pack whose sampled pages never
# showed an opener. Without it the cover still composes - as *content*, on a
# content archetype - so the deck loses its cover silently, which is worse
# than the crash it replaces because nothing reports it.
OPENER_ROLES = frozenset({"cover", "section"})

# The slot kinds that can carry an opener's standfirst if one is present.
_STANDFIRST_KINDS = frozenset({"standfirst", "prose", "quote"})


def _with_opener_standfirst(language: Language) -> Language:
    """Every opener has somewhere for its subtitle to go."""
    pages = []
    changed = False
    for kind in language.pages:
        if (kind.role in OPENER_ROLES
                and not any(s.kind in _STANDFIRST_KINDS for s in kind.slots)):
            span = kind.slots[0].span if kind.slots else language.columns
            kind = kind.model_copy(update={
                "slots": kind.slots + (Slot(kind="standfirst", span=span,
                                            name="composed"),),
            })
            changed = True
        pages.append(kind)
    return language.model_copy(update={"pages": tuple(pages)}) \
        if changed else language


# The smallest body type an authored language may set, as a share of canvas
# height.
#
# Nothing used to relate `body_pt` to the canvas at all. A model asked for a
# "scientific journal" or a "traditional academic" design answers with the
# document sizes those references actually use - 10pt, 11pt - because that is
# what they are set in on A4. On a slide read across a room it is unreadable,
# and because every other rung is a ratio of the body, the whole page shrinks
# with it.
#
# PowerPoint's own default body is 18pt on a 7.5in canvas, which is 3.33%, and
# the conventional band for projected type is 18-24pt, or 3.3-4.4%. 3.0% is
# the floor rather than the target.
#
# This is a floor on *invention*, not on measurement. `typeset.policy` records
# why a global 3.0% is wrong for the packs recovered from real publications -
# they set body at 1.3% to 2.6% and are correct to - but those are documents
# with a real page behind them. A number a model produced for a slide has no
# such evidence, and 11pt on a 13.33in canvas is not a register, it is a
# category error.
CANVAS_BODY_FLOOR_RATIO = 0.030

# How far below the derived body the typesetter may still shrink before it has
# to say the composition is at fault rather than the type. Kept as the same
# three-quarters the first version used, but taken from the canvas instead of
# from `language.body_pt` - which made the policy a tautology, since a language
# setting 10pt body declared its own floor to be 7.5pt and passed.
POLICY_FLOOR_SHARE = 0.75


def _above_the_floor(language: Language, floor_pt: float) -> Language:
    """The language with its ramp scaled up to the floor, if it sits below it.

    Scaling rather than clamping: every rung is a ratio of the body, so
    raising the body raises the whole ramp and leaves the proportions the
    language was designed around exactly as authored. Clamping the body alone
    would flatten the ramp, and the ramp is the design.

    ``floor_pt`` is a point size rather than a canvas ratio, because the
    caller may be printed rather than projected - a poster's floor is fixed
    at physical size regardless of how large the sheet is (see
    ``app.design.deck_format``), and resolving that distinction is not this
    function's job.
    """
    if language.body_pt >= floor_pt:
        return language
    return language.model_copy(update={"body_pt": round(floor_pt, 1)})


def to_pack(language: Language, canvas: Canvas, *,
            pack_id: Optional[str] = None,
            floor_pt: Optional[float] = None) -> Pack:
    """The language, derived into the representation everything else reads.

    Deriving rather than accepting is the whole point: what comes back has a
    measured grid, a real ramp and archetypes whose slots can hold the type
    they are set in, because all of that was computed here rather than
    asserted by a model.

    ``floor_pt`` defaults to the ordinary projected-slide floor
    (``CANVAS_BODY_FLOOR_RATIO`` of this canvas) when the caller does not
    know better. A caller building against a specific deck format passes its
    own floor - see ``app.design.deck_format.DeckFormat.floor_pt``.
    """
    floor = (floor_pt if floor_pt is not None
             else CANVAS_BODY_FLOOR_RATIO * canvas.h_in * 72.0)
    language = _with_opener_standfirst(_above_the_floor(language, floor))
    roles = _type_roles(language)
    baseline_in = roles["body"].line_height_pt / 72.0
    grid = _grid(language, canvas, baseline_in)

    pack = Pack(
        id=pack_id or _slug(language.name),
        label=language.name,
        source_note=f"authored for this deck: {language.rationale}".strip(),
        canvas=canvas,
        grid=grid,
        grid_confidence=Confidence(
            value=1.0, note=f"{language.columns} columns, authored not measured"),
        rhythm=Rhythm(unit_pt=roles["body"].line_height_pt,
                      confidence=Confidence(value=1.0, note="set by the ramp")),
        spacing=SpacingScale(base_pt=round(roles["body"].line_height_pt / 4.0, 2)),
        type_roles=roles,
        colors=ColorRoles(
            ground=language.ground, ink=language.ink,
            accent=language.accent,
            accent_alt=language.accent_alt or language.accent,
            ink_reversed=language.ground,
        ),
        illustration=language.illustration,
        body_face=language.body_face,
        display_face=language.display_face,
        policy=LegibilityPolicy(
            body_min_ratio=(floor * POLICY_FLOOR_SHARE) / (canvas.h_in * 72.0),
        ),
    )
    archetypes: list[PageArchetype] = []
    for kind in language.pages:
        for suffix, variant in _arrangements(kind, pack.grid.columns):
            archetypes.append(_archetype(
                variant, language, canvas, pack.grid, roles, suffix=suffix,
                # Frequency only ever breaks a tie (`select.choose`), so
                # halving it for the arrangements the language did not write
                # says exactly one thing: when two arrangements cost the same,
                # the one it actually authored wins.
                frequency=(kind.frequency if not suffix
                           else kind.frequency * 0.5),
            ))
    return pack.model_copy(update={"archetypes": tuple(archetypes)})


def _slug(name: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in name)
    return "-".join(part for part in cleaned.split("-") if part) or "authored"
