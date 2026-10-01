"""
The pages a publication needs that its design pages do not show.

A pack recovered from a PDF describes the pages that were sampled, and those
samples are design pages: spreads, chart pages, executive summaries. Every
archetype in every shipped pack has role ``content``. So a deck's opener, its
section dividers and its closing page have no archetype at all, and ``fit``
refuses them - correctly, because an archetype for a role that does not exist
cannot be conjured by relaxing a check.

It can be *composed*, though, and that is what this does. A cover built from
the pack's own grid, type ramp, spacing ladder and colour roles is in the
publication's language even though no cover page was ever sampled: it uses the
same measure, the same display face, the same margins and the same accent.
What it is not is a *trace* of a real cover, and the distinction matters -
these carry ``frequency = 0.0`` and a note saying they were composed, so
nothing downstream mistakes them for something measured.

Every dimension here is measured rather than chosen. The first version fixed
the frame heights as fractions of the page, and a three-line headline set at
display size ran straight through the standfirst beneath it and off the bottom
of the slide. A frame's height is now what its own type needs, and how much
copy a slot will take is asked of the real font at the real measure.

This is the minimum vocabulary, not the goal. Recovering real patterns by
clustering pages rather than tracing single ones is issue #268; this is what
makes a whole deck renderable in the meantime, and it stays useful afterwards
for the roles a publication genuinely never shows us.
"""

from __future__ import annotations

from typing import Optional

from app.design.geometry import content_box
from app.design.pack import Pack, PageArchetype, SlotSpec, TypeRole
from app.design.schema import Frame, FracPlace, GridPlace

# The roles a deck cannot be built without. `content` is excluded: a pack with
# no content archetype has nothing to compose from and is a broken pack, not
# one with a gap to fill.
REQUIRED_ROLES = ("cover", "section", "closing")

COMPOSED_NOTE = (
    "Composed from this pack's grid, type ramp and colour roles. Not traced "
    "from a sampled page - see app/design/vocabulary.py."
)

# An opener's type sits low on the page: it is what BXSL does at 0.66, and it
# leaves the upper two thirds for the ground to carry the image or the flat
# colour. Stated as where the block *ends*, because it is built upward from
# there once its height is known.
COVER_BASELINE = 0.93
SECTION_BASELINE = 0.62

# How many lines each opener slot may take before its copy is too long for the
# page it is on. Enforced through `max_chars`, measured at the face and size
# the slot is actually set in.
COVER_HEADLINE_LINES = 3
COVER_STANDFIRST_LINES = 3
EYEBROW_LINES = 1
SECTION_HEADLINE_LINES = 2

# The gap between an opener's slots, in multiples of the pack's own baseline
# so the spacing belongs to the publication rather than to this file.
OPENER_GAP_BASELINES = 1.0

# An opener's headline is set larger than a page's headline in every
# publication here. How much larger is taken from the pack's own ramp - one
# more rung of the same ratio it already climbs - rather than from a number
# chosen here, then held inside a band so a pack with a distorted ramp cannot
# produce a cover line taller than the page. WTI's headline/standfirst ratio
# is 2.94, which unclamped would set a cover at 147pt on an 11in page.
DISPLAY_RATIO_MIN = 1.15
DISPLAY_RATIO_MAX = 2.0
DISPLAY_RATIO_FALLBACK = 1.5
# As a share of canvas height, which is the only way sizes compare across
# canvases: 10pt on WTI's 792pt page is larger type than 12pt on BXSL's 540pt.
DISPLAY_MIN_SHARE = 0.055
DISPLAY_MAX_SHARE = 0.100

DISPLAY_ROLE = "display"


def display_role(pack: Pack) -> TypeRole:
    """The rung above this pack's headline, for an opener.

    Derived from the pack's own type ramp so a publication that climbs
    gently gets a gentle cover, then clamped to a band of canvas height so a
    distorted ramp cannot produce something absurd.
    """
    headline = pack.type_roles.get("headline")
    if headline is None:
        headline = next(iter(pack.type_roles.values()))
    standfirst = pack.type_roles.get("standfirst")
    ratio = (headline.size_pt / standfirst.size_pt
             if standfirst and standfirst.size_pt > 0 else DISPLAY_RATIO_FALLBACK)
    ratio = min(DISPLAY_RATIO_MAX, max(DISPLAY_RATIO_MIN, ratio))

    canvas_pt = pack.canvas.h_in * 72.0
    size = headline.size_pt * ratio
    size = min(canvas_pt * DISPLAY_MAX_SHARE,
               max(canvas_pt * DISPLAY_MIN_SHARE, size))
    leading = headline.line_height_pt / max(1.0, headline.size_pt)
    return headline.model_copy(update={
        "size_pt": round(size, 1),
        "line_height_pt": round(size * leading, 1),
    })


def with_display(pack: Pack) -> Pack:
    """This pack, carrying a display rung. Never replaces one it already has."""
    if DISPLAY_ROLE in pack.type_roles:
        return pack
    roles = dict(pack.type_roles)
    roles[DISPLAY_ROLE] = display_role(pack)
    return pack.model_copy(update={"type_roles": roles})


def _span(columns: int, share: float) -> int:
    """A column span covering roughly this share of the grid, at least one."""
    return max(1, min(columns, round(columns * share)))


def _width_in(pack: Pack, span: int) -> float:
    """How wide a frame of this many columns is, in inches."""
    column = pack.grid.column_width_in(pack.canvas)
    return span * column + max(0, span - 1) * pack.grid.gutter_in


def fits_in(pack: Pack, role_name: str, width_in: float, lines: int) -> int:
    """How many characters of this role fit `lines` lines at this width.

    Asked of the real font, set as it is drawn, rather than estimated: a
    chars-per-line constant is wrong by nearly a factor of two between
    narrow and wide copy, and the cap it produces is what decides whether a
    headline overruns its frame.
    """
    role = pack.type_roles.get(role_name)
    if role is None or width_in <= 0:
        return 0
    from app.design.setting import capacity

    return capacity(role, pack.face_for(role), width_in * 72.0, lines)


def _height_fraction(pack: Pack, role_name: str, lines: int) -> float:
    """The share of the content box `lines` lines of this role need."""
    role = pack.type_roles.get(role_name)
    box = content_box(pack.canvas, pack.grid)
    if role is None or box.height <= 0:
        return 0.1
    return (lines * role.line_height_pt / 72.0) / box.height


def _gap_fraction(pack: Pack) -> float:
    box = content_box(pack.canvas, pack.grid)
    baseline = pack.grid.baseline_in or (pack.canvas.h_in * 0.02)
    return (baseline * OPENER_GAP_BASELINES) / max(0.01, box.height)


def _opener(pack: Pack, role: str, ground: str) -> PageArchetype:
    """An opener or a closer: ground, an eyebrow, a headline, a standfirst.

    Laid out upward from `COVER_BASELINE` so each block is exactly as tall as
    its own type, and the one beneath it starts where the one above ends.
    """
    columns = pack.grid.columns
    wide = _span(columns, 0.85)
    narrow = _span(columns, 0.70)
    gap = _gap_fraction(pack)

    standfirst_h = _height_fraction(pack, "standfirst", COVER_STANDFIRST_LINES)
    headline_h = _height_fraction(pack, DISPLAY_ROLE, COVER_HEADLINE_LINES)
    eyebrow_h = _height_fraction(pack, "eyebrow", EYEBROW_LINES)

    standfirst_y = COVER_BASELINE - standfirst_h
    headline_y = standfirst_y - gap - headline_h
    eyebrow_y = headline_y - gap - eyebrow_h

    frames = [
        Frame(id=f"{role}-ground", role="panel",
              place=FracPlace(x=0.0, y=0.0, w=1.0, h=1.0),
              ground=ground, z=0,
              bleed=("top", "right", "bottom", "left")),
        Frame(id=f"{role}-headline", role="headline",
              type_role=DISPLAY_ROLE,
              place=GridPlace(col=0, span=wide, y=headline_y, h=headline_h),
              z=1),
    ]
    slots = [
        SlotSpec(frame_id=f"{role}-headline", accepts=("text",), required=True,
                 prominence=1.0, max_lines=COVER_HEADLINE_LINES,
                 max_chars=fits_in(pack, DISPLAY_ROLE, _width_in(pack, wide),
                                   COVER_HEADLINE_LINES)),
    ]
    # Only the rungs this pack actually distinguishes. Two of the three
    # shipped packs have no eyebrow role at all, and inventing the slot gave
    # it a capacity of zero characters, so it rejected everything offered to
    # it and stood empty on every opener.
    if "eyebrow" in pack.type_roles:
        frames.append(Frame(
            id=f"{role}-eyebrow", role="eyebrow",
            place=GridPlace(col=0, span=narrow, y=eyebrow_y, h=eyebrow_h),
            z=1))
        slots.append(SlotSpec(
            frame_id=f"{role}-eyebrow", accepts=("text",), prominence=0.3,
            max_lines=EYEBROW_LINES,
            max_chars=fits_in(pack, "eyebrow", _width_in(pack, narrow),
                              EYEBROW_LINES)))
    if "standfirst" in pack.type_roles:
        frames.append(Frame(
            id=f"{role}-standfirst", role="standfirst",
            place=GridPlace(col=0, span=narrow, y=standfirst_y,
                            h=standfirst_h),
            z=1))
        slots.append(SlotSpec(
            frame_id=f"{role}-standfirst", accepts=("text",), prominence=0.6,
            max_lines=COVER_STANDFIRST_LINES,
            max_chars=fits_in(pack, "standfirst", _width_in(pack, narrow),
                              COVER_STANDFIRST_LINES)))
    frames = tuple(frames)
    slots = tuple(slots)
    return PageArchetype(
        id=f"composed-{role}", label=role.title(), role=role,
        frequency=0.0, frames=frames, slots=slots, note=COMPOSED_NOTE,
    )


def _section(pack: Pack, ground: str) -> PageArchetype:
    """A divider: one line, and the accent doing the work."""
    columns = pack.grid.columns
    wide = _span(columns, 0.85)
    narrow = _span(columns, 0.5)
    gap = _gap_fraction(pack)

    headline_h = _height_fraction(pack, DISPLAY_ROLE, SECTION_HEADLINE_LINES)
    eyebrow_h = _height_fraction(pack, "eyebrow", EYEBROW_LINES)
    headline_y = SECTION_BASELINE - headline_h
    eyebrow_y = headline_y - gap - eyebrow_h

    frames = [
        Frame(id="section-ground", role="panel",
              place=FracPlace(x=0.0, y=0.0, w=1.0, h=1.0),
              ground=ground, z=0,
              bleed=("top", "right", "bottom", "left")),
        Frame(id="section-headline", role="headline",
              type_role=DISPLAY_ROLE,
              place=GridPlace(col=0, span=wide, y=headline_y, h=headline_h),
              z=1),
    ]
    slots = [
        SlotSpec(frame_id="section-headline", accepts=("text",), required=True,
                 prominence=1.0, max_lines=SECTION_HEADLINE_LINES,
                 max_chars=fits_in(pack, DISPLAY_ROLE, _width_in(pack, wide),
                                   SECTION_HEADLINE_LINES)),
    ]
    if "eyebrow" in pack.type_roles:
        frames.append(Frame(
            id="section-eyebrow", role="eyebrow",
            place=GridPlace(col=0, span=narrow, y=eyebrow_y, h=eyebrow_h),
            z=1))
        slots.append(SlotSpec(
            frame_id="section-eyebrow", accepts=("text",), prominence=0.3,
            max_lines=EYEBROW_LINES,
            max_chars=fits_in(pack, "eyebrow", _width_in(pack, narrow),
                              EYEBROW_LINES)))
    frames = tuple(frames)
    slots = tuple(slots)
    return PageArchetype(
        id="composed-section", label="Section", role="section",
        frequency=0.0, frames=frames, slots=slots, note=COMPOSED_NOTE,
    )


# The page a deck falls back to, one page at a time, when none of its own
# arrangements can carry that page's material (#322). A headline, one column
# the width of the grid, and a figure frame beneath it the width of the grid:
# the arrangement with the fewest ways to fail, in the pack's own grid, type
# and colours. It is never offered to the selector alongside the pack's
# archetypes - it would win on capacity and flatten every deck - only after
# they have all refused a page.
PLAIN_ID = "composed-plain"
PLAIN_HEADLINE_LINES = 2
# The column's share of the height left below the headline; the figure takes
# the rest. An empty figure frame is dropped at bind and the column closes up.
PLAIN_COLUMN_SHARE = 0.34


def plain_content(pack: Pack) -> PageArchetype:
    """The plainest content page this pack can set, built from its tokens."""
    columns = pack.grid.columns
    width = _width_in(pack, columns)
    gap = _gap_fraction(pack)
    headline_role = "headline" if "headline" in pack.type_roles else "body"
    headline_h = _height_fraction(pack, headline_role, PLAIN_HEADLINE_LINES)
    rest = max(0.0, 1.0 - headline_h - 2 * gap)
    column_y = headline_h + gap
    column_h = rest * PLAIN_COLUMN_SHARE
    figure_y = column_y + column_h + gap
    figure_h = max(0.05, 1.0 - figure_y)

    body = pack.type_roles.get("body")
    box = content_box(pack.canvas, pack.grid)
    column_lines = max(1, int(column_h * box.height * 72.0
                              / max(1.0, body.line_height_pt if body else 14.0)))
    frames = (
        Frame(id="plain-headline", role="headline", type_role=headline_role,
              place=GridPlace(col=0, span=columns, y=0.0, h=headline_h), z=1),
        Frame(id="plain-column", role="column", type_role="body",
              place=GridPlace(col=0, span=columns, y=column_y, h=column_h), z=1),
        Frame(id="plain-figure", role="figure",
              place=GridPlace(col=0, span=columns, y=figure_y, h=figure_h), z=1),
    )
    slots = (
        SlotSpec(frame_id="plain-headline", accepts=("text",), required=True,
                 prominence=1.0, max_lines=PLAIN_HEADLINE_LINES,
                 max_chars=fits_in(pack, headline_role, width,
                                   PLAIN_HEADLINE_LINES)),
        SlotSpec(frame_id="plain-column", accepts=("text",), prominence=0.5,
                 max_chars=fits_in(pack, "body", width, column_lines)),
        SlotSpec(frame_id="plain-figure",
                 accepts=("figure", "chart", "table", "diagram", "metrics"),
                 prominence=0.7),
    )
    return PageArchetype(
        id=PLAIN_ID, label="Plain page", role="content",
        frequency=0.0, frames=frames, slots=slots, note=COMPOSED_NOTE,
    )


def _opener_ground(pack: Pack) -> str:
    """Which colour role an opener's ground takes.

    The accent, when the pack has one that is not the paper it prints on -
    an opener is where a publication is loudest. A pack whose accent is
    close to its ground falls back to the dark role rather than producing a
    white page with white type.
    """
    if pack.colors.accent and pack.colors.accent.lower() != pack.colors.ground.lower():
        return "accent"
    return "ground_dark"


def compose_for(pack: Pack, role: str) -> Optional[PageArchetype]:
    """The archetype this pack lacks for this role, composed from its tokens."""
    ground = _opener_ground(pack)
    if role == "cover":
        return _opener(pack, "cover", ground)
    if role == "closing":
        return _opener(pack, "closing", "ground_dark")
    if role == "section":
        return _section(pack, ground)
    return None


def ensure(pack: Pack) -> Pack:
    """This pack, with an archetype for every role a deck needs.

    Idempotent, and it never replaces a real one: a pack that did recover a
    cover from its source keeps it.
    """
    have = {archetype.role for archetype in pack.archetypes}
    missing = [role for role in REQUIRED_ROLES if role not in have]
    if not missing:
        return pack.model_copy(update={"vocabulary_gaps": gaps(pack)})
    pack = with_display(pack)
    composed = [compose_for(pack, role) for role in missing]
    filled = pack.model_copy(update={
        "archetypes": pack.archetypes + tuple(a for a in composed if a),
    })
    return filled.model_copy(update={"vocabulary_gaps": gaps(filled)})


# What a deck needs to be built at all, beyond the three page roles. Each is
# a question about capability rather than about a label: not "is there a page
# called a two-column page" but "is there anywhere two columns of copy could
# go".
CAPABILITIES = ("a page that states an assertion prominently",
                "a page that sets copy in more than one column",
                "a page that can carry an exhibit")


def _states_an_assertion(pack: Pack, archetype: PageArchetype) -> bool:
    """Whether this page has somewhere prominent to put its own claim.

    Deliberately not "has a headline frame". EY sets its assertion in a
    pull-quote beside a section label - that is the house, not a defect, and
    a check that insisted on the word `headline` would report a gap in a
    publication that has no such gap.
    """
    from app.design.select import DISPLAY_FRAME_ROLES

    frames = {f.id: f for frame in archetype.frames for f in frame.walk()}
    return any(
        frames[slot.frame_id].role in DISPLAY_FRAME_ROLES
        for slot in archetype.slots if slot.frame_id in frames
    )


def _sets_columns(pack: Pack, archetype: PageArchetype) -> bool:
    from app.design.pack_build import COPY_FRAME_ROLES

    frames = {f.id: f for frame in archetype.frames for f in frame.walk()}
    columns = [
        slot for slot in archetype.slots
        if slot.frame_id in frames
        and frames[slot.frame_id].role in COPY_FRAME_ROLES
        and (not slot.accepts or "text" in slot.accepts)
    ]
    return len(columns) >= 2


def _carries_an_exhibit(pack: Pack, archetype: PageArchetype) -> bool:
    return any(slot.accepts and "figure" in slot.accepts
               for slot in archetype.slots)


_CHECKS = (_states_an_assertion, _sets_columns, _carries_an_exhibit)


def gaps(pack: Pack) -> tuple[str, ...]:
    """What this pack cannot do, in the words of what it is for.

    Reported rather than papered over. A composed opener is a reasonable
    stand-in for one a publication never showed us; a publication that never
    shows a page carrying an exhibit is telling us something about itself,
    and inventing one would be inventing house style.
    """
    usable = [a for a in pack.archetypes if a.role in ("content", "exhibit")]
    missing = []
    for name, check in zip(CAPABILITIES, _CHECKS):
        if not any(check(pack, archetype) for archetype in usable):
            missing.append(name)
    for role in REQUIRED_ROLES:
        if not any(a.role == role for a in pack.archetypes):
            missing.append(f"a {role} page")
    return tuple(missing)


def composed_ids(pack: Pack) -> tuple[str, ...]:
    """Which of this pack's archetypes were composed rather than measured."""
    return tuple(a.id for a in pack.archetypes if a.note == COMPOSED_NOTE)
