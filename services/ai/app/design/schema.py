"""
The DesignDoc: one deck, described as structure rather than as coordinates.

Three properties separate this from the slide dict it replaces, and each one
exists because of a specific failure the old shape allowed.

**No authored coordinates.** A page names an archetype from its pack and fills
that archetype's frames. Where those frames sit is the pack's business, and
the pack was measured from a real publication. A model asked for a rectangle
returns a plausible rectangle; a model asked which frame the pull-quote goes
in returns an answer that is either right or rejected.

**Content hangs off the frame that holds it.** The old shape kept a flat list
of content items each carrying a ``regionId`` string, re-joined at render
time and type-checked by nothing. Here a content item is *inside* its frame,
so "bound to a frame that does not exist" cannot be written down.

**Spacing and colour are references, never literals.** A gap is a step on the
pack's spacing ladder and a colour is a role in its palette. This is what
makes a deck read as one document instead of a set of locally reasonable
pages, and it is what turns a repair into a move along a ladder rather than
an invented number.

Siblings may overlap when they declare different ``z``. The old region
validator refused all overlap, which forbade the single most common
publication device there is: type set over an image or a colour panel.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "designdoc/1"


class _Model(BaseModel):
    """Shared configuration: reject unknown keys, and stay immutable.

    ``extra="forbid"`` is the point. A misspelled field that silently does
    nothing is how the previous representation drifted apart from the three
    places that read it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


# ── geometry vocabulary ────────────────────────────────────────────────────


class Insets(_Model):
    """Four edges, in inches."""

    top: float = 0.0
    right: float = 0.0
    bottom: float = 0.0
    left: float = 0.0


class Canvas(_Model):
    """The page itself.

    ``bleed_in`` is the margin outside the trim that a full-bleed element
    runs into. It is zero for a deck that will only ever be projected and
    non-zero for one that will be printed, and it belongs to the pack rather
    than to any page.
    """

    w_in: float = Field(gt=0)
    h_in: float = Field(gt=0)
    bleed_in: float = Field(default=0.0, ge=0)
    safe_in: float = Field(default=0.0, ge=0)

    @property
    def aspect(self) -> float:
        return self.w_in / self.h_in


class GridSpec(_Model):
    """The column grid, and the vertical rhythm set on it.

    ``baseline_in`` is the unit every text baseline snaps to. A deck whose
    body text sits on a consistent rhythm reads as typeset; one whose text
    lands wherever the box put it reads as a slide. ``rows`` is optional
    because most publication grids are columnar only.
    """

    columns: int = Field(default=12, ge=1, le=48)
    gutter_in: float = Field(default=0.15, ge=0)
    margin: Insets = Insets()
    baseline_in: float = Field(default=0.0, ge=0)
    rows: Optional[int] = Field(default=None, ge=1, le=48)
    row_gutter_in: float = Field(default=0.0, ge=0)

    def column_width_in(self, canvas: Canvas) -> float:
        """One column's width, given the canvas this grid is set on."""
        usable = canvas.w_in - self.margin.left - self.margin.right
        total_gutter = self.gutter_in * (self.columns - 1)
        return max(0.0, (usable - total_gutter) / self.columns)


# ── placement ──────────────────────────────────────────────────────────────


class GridPlace(_Model):
    """A frame placed on the grid. The normal case."""

    kind: Literal["grid"] = "grid"
    col: int = Field(ge=0)
    span: int = Field(default=1, ge=1)
    row: Optional[int] = Field(default=None, ge=0)
    row_span: Optional[int] = Field(default=None, ge=1)
    # When the grid has no row system, a frame still has to say where it
    # starts and how tall it is. These are fractions of the content box.
    y: Optional[float] = Field(default=None, ge=-1.0, le=2.0)
    h: Optional[float] = Field(default=None, gt=0.0, le=2.0)


class FracPlace(_Model):
    """A frame placed by fraction of its parent. The escape hatch.

    Kept deliberately. Grid absolutism produces worse pages than free
    placement on the minority of layouts that genuinely are not on a grid -
    a cover, a full-bleed opener, a diagram that wants the space it wants.
    What makes it safe is that it is *counted*: the share of frames placed
    this way is a reported metric, so the escape hatch cannot quietly become
    the default without showing up as a number.
    """

    kind: Literal["frac"] = "frac"
    x: float = Field(ge=-1.0, le=2.0)
    y: float = Field(ge=-1.0, le=2.0)
    w: float = Field(gt=0.0, le=3.0)
    h: float = Field(gt=0.0, le=3.0)


Place = Annotated[Union[GridPlace, FracPlace], Field(discriminator="kind")]

BleedEdge = Literal["top", "right", "bottom", "left"]


# ── text ───────────────────────────────────────────────────────────────────


class Run(_Model):
    """One stretch of text with uniform formatting.

    Deliberately carries no size or colour of its own by default: those come
    from the block's type role, so a deck cannot end up with as many type
    scales as it has runs. The overrides exist for the cases that genuinely
    differ - a figure set in the accent colour inside a sentence - and are
    the exception the role system is measured against.
    """

    text: str
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    underline: bool = False
    smallcaps: bool = False
    superscript: bool = False
    # Per-mille of the em, the unit OOXML's a:rPr/@spc actually wants.
    tracking_per_mille: Optional[int] = Field(default=None, ge=-200, le=400)
    color_role: Optional[str] = None
    size_pt: Optional[float] = Field(default=None, gt=0)
    # A footnote marker points at Footnotes[n-1] on the same page.
    footnote_ref: Optional[int] = Field(default=None, ge=1)
    # A citation marker points at the deck's reference list.
    source_ref: Optional[int] = Field(default=None, ge=1)


class Paragraph(_Model):
    """One paragraph: runs, plus the list level it sits at."""

    runs: tuple[Run, ...] = ()
    level: int = Field(default=0, ge=0, le=4)
    bullet: bool = False

    @property
    def text(self) -> str:
        return "".join(run.text for run in self.runs)


# ── content ────────────────────────────────────────────────────────────────


class TextBlock(_Model):
    """Text set in one type role, possibly flowing into a later frame."""

    kind: Literal["text"] = "text"
    type_role: str
    paragraphs: tuple[Paragraph, ...] = ()
    columns: int = Field(default=1, ge=1, le=6)
    column_gap: Optional[int] = Field(default=None, ge=0)
    # When set, this block is one part of a story continued across the
    # frames named by the Flow with this id.
    flow_id: Optional[str] = None
    align: Literal["left", "center", "right", "justify"] = "left"
    anchor: Literal["top", "middle", "bottom"] = "top"
    baseline_snap: bool = True
    first_line_indent: Optional[int] = Field(default=None, ge=0)
    optical_align: bool = False
    max_lines: Optional[int] = Field(default=None, ge=1)

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.paragraphs)


class Figure(_Model):
    """An image, placed with a fit rule rather than stretched.

    ``fit`` and ``crop`` are the reason this is not just an image path. The
    previous renderer stretched every picture into whatever rectangle it was
    given, which distorts any photograph whose aspect does not match its
    slot - and no slot's aspect is chosen to match a photograph. ``focal``
    drives the crop under ``cover`` so the subject survives the cropping.
    """

    kind: Literal["figure"] = "figure"
    source: dict
    fit: Literal["cover", "contain", "fill"] = "cover"
    crop: Optional[Insets] = None
    focal_x: float = Field(default=0.5, ge=0.0, le=1.0)
    focal_y: float = Field(default=0.5, ge=0.0, le=1.0)
    aspect_lock: bool = True
    alt_text: str = ""


class Exhibit(_Model):
    """A chart, table or diagram, carried as the payload its builder emits.

    The payload shape is owned by the existing builders rather than restated
    here: they already validate it, and a second declaration of the same
    contract is a second thing to keep in step.
    """

    kind: Literal["exhibit"] = "exhibit"
    exhibit_kind: Literal["chart", "table", "diagram", "metrics", "block"]
    payload: dict
    caption: Optional[TextBlock] = None
    # The shape the exhibit itself wants, declared before it is placed. A
    # drawn graph knows its own aspect from its viewBox, and a slot nothing
    # like that shape crops it or letterboxes it - a cost worth counting while
    # a slot is being chosen rather than discovering at render.
    aspect: Optional[float] = Field(default=None, gt=0)
    # What to do when the slot is the wrong shape anyway: letterbox, crop, or
    # stretch. The repair loop's one move for a figure that does not fit.
    fit: Literal["cover", "contain", "fill"] = "contain"


class Rule(_Model):
    """A drawn line. Publication decks use these structurally, not as decor."""

    kind: Literal["rule"] = "rule"
    orientation: Literal["horizontal", "vertical"] = "horizontal"
    weight_pt: float = Field(default=0.75, gt=0)
    color_role: str = "rule"
    inset: Optional[int] = Field(default=None, ge=0)


class Mark(_Model):
    """A raw primitive. The lowest escape hatch, and counted like FracPlace."""

    kind: Literal["mark"] = "mark"
    primitive: Literal["text", "rect", "line", "ellipse", "image"]
    payload: dict
    rotation_deg: float = Field(default=0.0, ge=-360.0, le=360.0)


ContentItem = Annotated[
    Union[TextBlock, Figure, Exhibit, Rule, Mark],
    Field(discriminator="kind"),
]


# ── frames ─────────────────────────────────────────────────────────────────

FRAME_ROLES = (
    "root", "rail", "panel", "column", "band", "bandRow", "banner", "grid",
    "figure", "caption", "pullquote", "marginNote", "standfirst", "kicker",
    "statRow", "footnotes", "sourceLine", "folio", "eyebrow", "headline",
)


class Scrim(_Model):
    """A wash over a frame's ground so type on top of it stays legible.

    The alternative - hoping the image is dark enough - is how white text
    ends up on a pale sky.
    """

    color_role: str = "scrim"
    opacity: float = Field(default=0.45, ge=0.0, le=1.0)


class Frame(_Model):
    """One area of a page, and what sits in it.

    Frames nest, and siblings may overlap **when their ``z`` differs**. That
    single allowance is what makes the reference pages representable: type
    over a full-bleed image, a pull-quote sitting across a panel edge, a
    figure bleeding under a caption. Overlap at equal ``z`` is still an
    error, because that is ambiguity rather than intent.
    """

    id: str = Field(min_length=1)
    role: str = "panel"
    place: Place
    # Which rung of the pack's type ramp this frame's text is set in, when the
    # frame's role does not imply the right one. A cover's headline is set
    # larger than a page's headline in every publication, and without this the
    # only ways to say so are to scale the pack (which moves every content
    # page too) or to invent a frame role for a size (which confuses what a
    # frame *is* with how big its type is).
    type_role: Optional[str] = None
    z: int = 0
    bleed: tuple[BleedEdge, ...] = ()
    pad: Optional[int] = Field(default=None, ge=0)
    ground: Optional[str] = None
    scrim: Optional[Scrim] = None
    # A ruled outline around the frame, in a colour role. The reference
    # publications set their pull-out statements in exactly this - a box in
    # the accent colour with nothing inside it but the sentence - and a frame
    # could previously only be filled, never outlined, so the one shape that
    # says "this is the point of the page" had to be faked as a filled panel
    # or left out. `Primitive` has carried `stroke`/`stroke_pt` all along;
    # this is what lets a design ask for one.
    border: Optional[str] = None
    border_pt: float = Field(default=1.0, ge=0.0, le=12.0)
    clip: bool = False
    children: tuple["Frame", ...] = ()
    content: tuple[ContentItem, ...] = ()

    @model_validator(mode="after")
    def _known_role(self) -> "Frame":
        if self.role not in FRAME_ROLES:
            raise ValueError(
                f"frame {self.id!r} has role {self.role!r}; "
                f"known roles are {', '.join(sorted(FRAME_ROLES))}"
            )
        return self

    def walk(self):
        """This frame and every descendant, depth first."""
        yield self
        for child in self.children:
            yield from child.walk()


class Flow(_Model):
    """One story continued across several frames, in reading order.

    PPTX has no linked text boxes, so a flow is broken into independent
    boxes at solve time - the solver knows where the lines fall, so it knows
    where to cut. That flattening is one-way and is recorded here so the
    reason a page has three text boxes instead of one is legible.
    """

    id: str = Field(min_length=1)
    frame_ids: tuple[str, ...] = Field(min_length=2)


# ── pages and deck ─────────────────────────────────────────────────────────

PAGE_ROLES = ("cover", "section", "content", "exhibit", "closing", "appendix")


class PatchOp(_Model):
    """One recorded repair, so a page can say how it came to look like this."""

    op: str
    args: dict = Field(default_factory=dict)
    reason: str = ""


class Provenance(_Model):
    """Why this page is the page it is.

    A bad page should be attributable to a bad fit cost or a bad patch,
    rather than to nothing at all.
    """

    selected_by: str = ""
    fit_cost: Optional[float] = None
    considered: tuple[str, ...] = ()
    patched_by: tuple[PatchOp, ...] = ()
    # Which designed slide this page came from. One slide can become several
    # pages when its material has to be split, so this is not the page's own
    # position - it is what the page is attributable *to*, which is what a
    # caller needs to say "place that slide again, differently".
    source_slide: Optional[int] = None
    # The frame the page's heading was bound to. Recorded because a measured
    # pack sets its assertion wherever the house does - EY in a pull-quote -
    # so the frame's role does not say which frame is the headline.
    heading_frame: str = ""


class Page(_Model):
    """One page: an archetype from the pack, with its frames filled."""

    id: str = Field(min_length=1)
    archetype: str = Field(min_length=1)
    role: Literal["cover", "section", "content", "exhibit", "closing", "appendix"] = "content"
    grid: Optional[GridSpec] = None
    ground: Optional[str] = None
    chrome: Optional[str] = "inherit"
    frames: tuple[Frame, ...] = ()
    flows: tuple[Flow, ...] = ()
    notes: str = ""
    provenance: Provenance = Provenance()

    def all_frames(self):
        for frame in self.frames:
            yield from frame.walk()

    def frame_by_id(self, frame_id: str) -> Optional[Frame]:
        for frame in self.all_frames():
            if frame.id == frame_id:
                return frame
        return None

    @model_validator(mode="after")
    def _frames_are_coherent(self) -> "Page":
        seen: set[str] = set()
        for frame in self.all_frames():
            if frame.id in seen:
                raise ValueError(f"page {self.id!r} repeats frame id {frame.id!r}")
            seen.add(frame.id)
        for flow in self.flows:
            missing = [fid for fid in flow.frame_ids if fid not in seen]
            if missing:
                raise ValueError(
                    f"flow {flow.id!r} names frames not on page {self.id!r}: "
                    + ", ".join(missing)
                )
        return self


class PackRef(_Model):
    """Which design language this deck is set in."""

    id: str = Field(min_length=1)
    version: str = "1"


class DesignDoc(_Model):
    """A whole deck, in the representation the renderer and the eval share."""

    schema_version: Literal["designdoc/1"] = SCHEMA_VERSION
    pack: PackRef
    canvas: Canvas
    grid: GridSpec
    pages: tuple[Page, ...] = ()
    title: str = ""

    @model_validator(mode="after")
    def _page_ids_are_unique(self) -> "DesignDoc":
        seen: set[str] = set()
        for page in self.pages:
            if page.id in seen:
                raise ValueError(f"deck repeats page id {page.id!r}")
            seen.add(page.id)
        return self

    def grid_for(self, page: Page) -> GridSpec:
        return page.grid or self.grid
