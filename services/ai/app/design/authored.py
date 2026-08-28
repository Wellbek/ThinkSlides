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

from typing import Literal, Optional

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
from app.design.schema import Canvas, FracPlace, Frame, GridPlace, GridSpec, Insets

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
    span: int = Field(default=1, ge=1, le=48)
    # Whether the page is unusable without it.
    required: bool = False
    # A short id, so two prose columns on one page are distinguishable.
    name: str = ""


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
    body_pt: float = Field(default=14.0, gt=4.0, lt=48.0)
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


def _archetype(kind: PageKind, language: Language, canvas: Canvas,
               grid: GridSpec, roles: dict[str, TypeRole]) -> PageArchetype:
    """One authored page kind, laid out.

    This is where a decision becomes a rectangle, and the agent is not in the
    room for it. Slots are stacked down the content box in the order they were
    declared, each as tall as the type it is set in needs, and a slot that
    spans fewer columns than the grid sits at the left edge of its own run.
    """
    from app.design.geometry import content_box

    box = content_box(canvas, grid)
    scale = DENSITY_SCALE[language.density]
    frames: list[Frame] = []
    slots: list[SlotSpec] = []
    cursor = 0.0
    gap = (grid.baseline_in or canvas.h_in * 0.012) / max(0.01, box.height)

    ground = "accent" if kind.role in ("cover", "section") else None
    if ground:
        frames.append(Frame(
            id=f"{kind.name}-ground", role="panel",
            place=FracPlace(x=0.0, y=0.0, w=1.0, h=1.0),
            ground=ground, z=0, bleed=("top", "right", "bottom", "left"),
        ))

    heights = _heights(kind, language, roles, box.height, scale, gap)

    for index, slot in enumerate(kind.slots):
        role_name = ROLE_FOR_SLOT[slot.kind]
        role = roles.get(role_name) or roles["body"]
        span = max(1, min(grid.columns, slot.span))
        height = heights[index]
        identifier = f"{kind.name}-{slot.name or slot.kind}-{index}"
        frames.append(Frame(
            id=identifier, role=FRAME_FOR_SLOT[slot.kind],
            # The rung this slot is set in, stated on the frame rather than
            # only used to size it. Left off, a statistic slot was measured
            # as a headline and then *rendered* at body size, because the
            # renderer reads the frame and the frame said nothing - so a
            # language's stat row came out the same size as its prose.
            type_role=("display" if slot.kind == "heading"
                       and kind.role in ("cover", "section") else role_name),
            place=GridPlace(col=0, span=span, y=round(cursor, 4),
                            h=round(height, 4)),
            z=1,
        ))
        capacity = _capacity(role, span, grid, canvas, height, box.height)
        slots.append(SlotSpec(
            frame_id=identifier,
            accepts=ACCEPTS_FOR_SLOT.get(slot.kind, ("text",)),
            required=slot.required,
            prominence=1.0 if slot.kind == "heading" else 0.5,
            max_chars=capacity,
            typical_chars=max(1, round(
                capacity * TYPICAL_FILL[language.density])),
        ))
        cursor += height + gap

    return PageArchetype(
        id=kind.name, label=kind.name.replace("-", " "), role=kind.role,
        frequency=kind.frequency, frames=tuple(frames), slots=tuple(slots),
        note="authored for this deck, not measured from a publication",
    )


# Slot kinds whose height is decided by the type they are set in. A headline
# is two lines tall because that is what two lines of it measures; giving it
# the room left over at the bottom of the page would set one line of it
# floating in the middle of nowhere. Everything else flows.
FIXED_HEIGHT_SLOTS = frozenset({
    "heading", "standfirst", "statistic", "quote", "caption", "footnote",
})


def _heights(kind: PageKind, language: Language, roles: dict[str, TypeRole],
             box_height: float, scale: float, gap: float) -> list[float]:
    """Each slot's height as a fraction of the content box, filling the page.

    The natural heights come from the type. They rarely add up to a page: a
    heading, a stat row and a column of prose measure about a third of the
    box, and the stack used to be laid from the top and simply stop - so
    every page carried its material in the top third and two thirds of white
    below it, whatever the language said its density was. That is not an airy
    layout, it is an unfinished one.

    So what is left over is given to the slots that can use it - prose and
    figures, the two that flow - in proportion to what they already have. A
    page with neither keeps its natural heights and its white space, which is
    right: a section divider carrying one line is meant to be mostly empty.
    """
    natural: list[float] = []
    for slot in kind.slots:
        role = roles.get(ROLE_FOR_SLOT[slot.kind]) or roles["body"]
        if slot.kind == "figure":
            natural.append(0.34)
            continue
        lines = LINES_FOR_SLOT[slot.kind]
        wanted = max(1, round(lines * (scale if slot.kind == "prose" else 1.0)))
        natural.append((wanted * role.line_height_pt / 72.0)
                       / max(0.01, box_height))

    used = sum(natural) + gap * max(0, len(natural) - 1)
    spare = 1.0 - used
    flexible = [index for index, slot in enumerate(kind.slots)
                if slot.kind not in FIXED_HEIGHT_SLOTS]
    if abs(spare) < 1e-9 or not flexible:
        return natural

    share = sum(natural[index] for index in flexible)
    if share <= 0:
        return natural

    if spare > 0:
        for index in flexible:
            natural[index] += spare * natural[index] / share
        return natural

    # Too tall, not too short. A language that declares a heading, a stat row,
    # a column and a figure asks for more than a page: the stack was laid from
    # the top regardless and the last slot ran off the bottom of the canvas,
    # where nothing renders. The flexible slots give the room back first,
    # since they are the ones that flow.
    wanted = -spare
    if wanted < share:
        for index in flexible:
            natural[index] -= wanted * natural[index] / share
        return natural

    # Even emptying the flexible slots is not enough, so everything shrinks
    # together and the ramp is preserved as a ratio. A page cannot be taller
    # than the page.
    total = sum(natural)
    room = max(0.0, 1.0 - gap * max(0, len(natural) - 1))
    if total <= 0 or room <= 0:
        return natural
    return [height * room / total for height in natural]


def _capacity(role: TypeRole, span: int, grid: GridSpec, canvas: Canvas,
              height_fraction: float, box_height: float) -> int:
    """How much copy this slot holds, measured at the face it is set in."""
    from app.renderer.typeset.fonts import width_pt

    column = grid.column_width_in(canvas)
    width_in = span * column + max(0, span - 1) * grid.gutter_in
    sample = ("The quick brown fox jumps over the lazy dog and keeps going "
              "until the line is comfortably longer than any measure")
    per_char = width_pt(sample, "Arial", role.size_pt) / len(sample)
    if per_char <= 0 or width_in <= 0:
        return 1
    lines = max(1, int((height_fraction * box_height * 72.0)
                       / max(1.0, role.line_height_pt)))
    return max(1, int(width_in * 72.0 / per_char) * lines)


def to_pack(language: Language, canvas: Canvas, *,
            pack_id: Optional[str] = None) -> Pack:
    """The language, derived into the representation everything else reads.

    Deriving rather than accepting is the whole point: what comes back has a
    measured grid, a real ramp and archetypes whose slots can hold the type
    they are set in, because all of that was computed here rather than
    asserted by a model.
    """
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
            body_min_ratio=(language.body_pt * 0.75) / (canvas.h_in * 72.0),
        ),
    )
    archetypes = tuple(_archetype(kind, language, canvas, pack.grid, roles)
                       for kind in language.pages)
    return pack.model_copy(update={"archetypes": archetypes})


def _slug(name: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in name)
    return "-".join(part for part in cleaned.split("-") if part) or "authored"
