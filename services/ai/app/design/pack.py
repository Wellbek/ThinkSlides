"""
The Design Language Pack: a publication's visual grammar, as data.

A pack is what "make it look like that report" resolves to. It holds the
grid the publication is set on, the rhythm its text sits to, the ladder its
spacing steps along, the roles its type ramp divides into, the roles its
palette divides into, and a library of the page archetypes it actually uses.

Two rules keep a pack honest.

**A pack states its confidence.** Grid recovery, baseline recovery and
spacing quantisation are all period-estimation problems, and every one of
them returns a confident answer even when the deck has no grid at all. So a
pack carries the residual alongside the reading, and a pack that could not
find a grid says so rather than inventing one. An invented grid is worse
than an admitted absence: it teaches the engine a fiction and then measures
conformance to it.

**Legibility is a property of the pack, not a global constant.** The
renderer used to enforce ``MIN_BODY_PT = 16`` on every deck, with a comment
that a template specifying 11pt body copy "is describing a document, not a
slide". Measured as a share of canvas height, the four reference
publications set body copy at 2.6%, 2.2%, 1.5% and 1.3% - and that floor is
3.0%, above every one of them. It did not enforce legibility; it forbade
the registers we are trying to reach. Each pack carries its own floors,
measured from the publication it came from.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.design.schema import Canvas, Frame, GridSpec


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Confidence(_Model):
    """How much of the evidence a recovered reading actually explains."""

    value: float = Field(default=0.0, ge=0.0, le=1.0)
    residual: float = Field(default=0.0, ge=0.0)
    note: str = ""

    @property
    def trustworthy(self) -> bool:
        return self.value >= 0.6


class Rhythm(_Model):
    """The baseline grid: a unit, a phase, and how well the page keeps to it."""

    unit_pt: float = Field(default=0.0, ge=0.0)
    phase_pt: float = Field(default=0.0, ge=0.0)
    confidence: Confidence = Confidence()

    @property
    def present(self) -> bool:
        return self.unit_pt > 0 and self.confidence.trustworthy


class SpacingScale(_Model):
    """A ladder of gaps, in points.

    Spacing is referenced by index everywhere else in the system. That is
    what stops a deck from acquiring as many gap sizes as it has pages, and
    it is what turns "tighten that" into a step rather than a guess.
    """

    base_pt: float = Field(default=6.0, gt=0)
    steps: tuple[float, ...] = (0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0)
    confidence: Confidence = Confidence()

    def pt(self, index: Optional[int]) -> float:
        if index is None:
            return 0.0
        if index < 0 or index >= len(self.steps):
            raise IndexError(
                f"spacing step {index} is outside this pack's ladder of "
                f"{len(self.steps)} steps"
            )
        return self.base_pt * self.steps[index]

    def inches(self, index: Optional[int]) -> float:
        return self.pt(index) / 72.0


class TypeRole(_Model):
    """One rung of the type ramp, named by the job it does.

    ``line_height_pt`` is absolute rather than a multiple, because a baseline
    rhythm is a fixed distance down the page. ``measure_chars_target`` is the
    line length the role wants - the single strongest determinant of whether
    body copy reads as a publication or as a slide, and something no part of
    the previous system had any opinion about.
    """

    size_pt: float = Field(gt=0)
    line_height_pt: float = Field(gt=0)
    weight: Literal["light", "regular", "medium", "semibold", "bold"] = "regular"
    face: Literal["display", "body", "mono"] = "body"
    case: Literal["none", "upper", "small"] = "none"
    tracking_per_mille: int = Field(default=0, ge=-200, le=400)
    color_role: str = "ink"
    space_before: Optional[int] = Field(default=None, ge=0)
    space_after: Optional[int] = Field(default=None, ge=0)
    min_pt: float = Field(default=6.0, gt=0)
    measure_chars_target: Optional[int] = Field(default=None, ge=10, le=200)
    # What was actually measured in the source publication, kept so a pack
    # can be audited against the thing it claims to describe.
    measured_min_pt: Optional[float] = None
    measured_median_pt: Optional[float] = None
    measured_max_pt: Optional[float] = None


class ColorRoles(_Model):
    """The palette, by job rather than by hex."""

    ground: str = "#FFFFFF"
    ground_alt: str = "#F4F4F6"
    ground_dark: str = "#101820"
    ink: str = "#1A1A1A"
    ink_muted: str = "#6B6B76"
    ink_reversed: str = "#FFFFFF"
    accent: str = "#0F62FE"
    accent_alt: str = "#FFE600"
    rule: str = "#D6D6DE"
    scrim: str = "#000000"

    def get(self, role: str) -> str:
        value = getattr(self, role, None)
        if not isinstance(value, str):
            raise KeyError(
                f"unknown colour role {role!r}; this pack defines "
                + ", ".join(sorted(self.model_fields))
            )
        return value


class SlotSpec(_Model):
    """What one frame of an archetype will accept, and how much of it."""

    frame_id: str
    accepts: tuple[str, ...] = ()
    required: bool = False
    prominence: float = Field(default=0.5, ge=0.0, le=1.0)
    min_chars: Optional[int] = Field(default=None, ge=0)
    max_chars: Optional[int] = Field(default=None, ge=0)
    # What this slot typically *carries*, as against what it could hold.
    #
    # For a measured pack the two are the same thing: `max_chars` comes from
    # the page the archetype was read off, so it is already an observation.
    # For an authored one it is not - the capacity there is derived from the
    # rectangle, so it says how much text would fit, which is a ceiling and
    # not a habit. Selecting on the ceiling penalised every generous slot for
    # being generous: an archetype with a full-width prose column scored far
    # worse than a cramped one for the same material, because the roomy one
    # looked more underfilled. Left unset it falls back to `max_chars`, which
    # is what every measured pack wants.
    typical_chars: Optional[int] = Field(default=None, ge=0)

    @property
    def expected_chars(self) -> int:
        return int(self.typical_chars or self.max_chars or 0)
    max_lines: Optional[int] = Field(default=None, ge=1)
    aspect_min: Optional[float] = Field(default=None, gt=0)
    aspect_max: Optional[float] = Field(default=None, gt=0)


class PageArchetype(_Model):
    """One page pattern the publication actually uses.

    ``frequency`` matters as much as the geometry: reproducing a house style
    means reproducing its *mix* of pages, not picking its most interesting
    page every time.
    """

    id: str = Field(min_length=1)
    label: str = ""
    role: Literal["cover", "section", "content", "exhibit", "closing", "appendix"] = "content"
    frequency: float = Field(default=0.0, ge=0.0, le=1.0)
    # An archetype's own grid, when it has one the pack's does not describe.
    # Measured across all five of EY's design pages, no single grid explains
    # them: an executive-summary spread is on four columns, a chart page is
    # on something else, and pooling the two recovers nothing. The grid is a
    # property of the page pattern, not of the publication.
    grid: Optional[GridSpec] = None
    grid_confidence: Confidence = Confidence()
    frames: tuple[Frame, ...] = ()
    slots: tuple[SlotSpec, ...] = ()
    example_pages: tuple[int, ...] = ()
    note: str = ""


class LegibilityPolicy(_Model):
    """The floors this pack considers readable, as a share of canvas height.

    Stated as a ratio because point sizes are not comparable across canvases:
    WTI sets 10pt body on a 792pt-tall page and BXSL sets 12pt on a 540pt
    page, and the second is the larger type.
    """

    body_min_ratio: float = Field(default=0.020, gt=0, le=0.2)
    caption_min_ratio: float = Field(default=0.013, gt=0, le=0.2)
    display_min_ratio: float = Field(default=0.035, gt=0, le=0.5)
    absolute_floor_pt: float = Field(default=6.0, gt=0)

    def floor_pt(self, kind: str, canvas: Canvas) -> float:
        ratio = {
            "body": self.body_min_ratio,
            "caption": self.caption_min_ratio,
            "display": self.display_min_ratio,
        }.get(kind, self.caption_min_ratio)
        return max(self.absolute_floor_pt, ratio * canvas.h_in * 72.0)


class Illustration(_Model):
    """How a publication treats its imagery, as one statement for a whole deck.

    Illustration was decided per slide: `generate_image` composed a prompt
    from the run's design direction and every other image path decided for
    itself. Two pages of one deck could come back in unrelated treatments,
    and nothing said they should not - a deck's imagery is as much part of how
    it reads as its palette, and the palette has lived in the pack since the
    beginning.

    `confidence` is why this is a measurement rather than a preference. A
    publication whose pages carry two photographs tells us very little, and a
    treatment asserted from that is a treatment invented. Below the threshold
    the pack says so and the default is used, in the open.
    """

    # What imagery *is* here: a photograph, a flat illustration, a diagram.
    kind: Literal["photographic", "illustrated", "diagrammatic", "none"] = "photographic"
    # How it is finished. `duotone` and `mono` are the two a publication most
    # often uses to make stock imagery look like its own.
    treatment: Literal["full", "duotone", "mono", "muted"] = "full"
    # Where it sits: bleeding off the page, or inset within the grid.
    placement: Literal["bleed", "inset"] = "inset"
    # The one sentence handed to an image provider, so every generated image
    # in a deck is asked for in the same terms.
    style_note: str = ""
    # Which colour roles imagery leans on, in order.
    palette_roles: tuple[str, ...] = ("accent", "ink")
    confidence: Confidence = Confidence()


class Pack(_Model):
    """A publication's visual grammar, ready to generate against."""

    id: str = Field(min_length=1)
    version: str = "1"
    label: str = ""
    source_note: str = ""
    canvas: Canvas
    grid: GridSpec
    grid_confidence: Confidence = Confidence()
    rhythm: Rhythm = Rhythm()
    spacing: SpacingScale = SpacingScale()
    type_roles: dict[str, TypeRole] = Field(default_factory=dict)
    colors: ColorRoles = ColorRoles()
    archetypes: tuple[PageArchetype, ...] = ()
    # Kinds of page this publication never showed us, named rather than
    # silently absent. A pack that is missing a divider should say so, so a
    # designer reading it knows the gap is in the source and not in the
    # reading - and so nothing downstream mistakes "not recovered" for
    # "not used".
    vocabulary_gaps: tuple[str, ...] = ()
    illustration: Illustration = Illustration()
    policy: LegibilityPolicy = LegibilityPolicy()
    body_face: str = "Arial"
    display_face: str = "Arial"
    mono_face: str = "Consolas"

    def role(self, name: str) -> TypeRole:
        try:
            return self.type_roles[name]
        except KeyError:
            raise KeyError(
                f"pack {self.id!r} has no type role {name!r}; it defines "
                + ", ".join(sorted(self.type_roles))
            ) from None

    def archetype(self, archetype_id: str) -> PageArchetype:
        for entry in self.archetypes:
            if entry.id == archetype_id:
                return entry
        raise KeyError(
            f"pack {self.id!r} has no archetype {archetype_id!r}; it defines "
            + ", ".join(a.id for a in self.archetypes)
        )

    def face_for(self, role: TypeRole) -> str:
        return {
            "display": self.display_face,
            "body": self.body_face,
            "mono": self.mono_face,
        }.get(role.face, self.body_face)
