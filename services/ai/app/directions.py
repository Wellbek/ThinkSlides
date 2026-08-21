"""
Three genuinely different design systems for one brief.

The agent asked about visual style and the user picked by reading the words
"professional", "bold", "monochrome". Those mapped onto five hardcoded
palettes differing in seven hex values each; every one rendered the identical
eight layouts, the identical Arial 36/20/12 type scale and the identical
single accent rule. A user who picked "bold" and a user who picked "nature"
got the same deck in different colours. There was no design choice being made.

A **direction** is a whole design system, and three of them have to differ on
things a reader would notice:

* **where the dark tone goes** - a dark ground, a dark rail beside white, a
  split canvas, or white throughout. Not the hue: the deployment.
* **the type pairing** - a serif display over a sans body is a different deck
  from sans over sans.
* **the structural motif** - where the title sits and what sits under it.
* **the density posture** - dense analyst against airy executive, which sets
  the planner's word and block targets as well as the look.
* **the structure it leads with** - whether a set of parallel points comes out
  as bullets, as a row of figures, as accent cards or as ruled bands. Colour
  and type are what a thumbnail shows first; *shape* is what the reader
  actually navigates by, and three directions that shape the same content
  identically are three colourways.

The model authors a **spec** on those five axes, and this module compiles it
into a full bundle. That split is deliberate: a model asked to emit a whole
DesignSystemBundle has to invent layout catalogues in EMU, and it will, badly.
Asked for five decisions and a palette seed it can actually make them, and
what comes back is guaranteed to render because the code built it.

Two checks then run before a user is shown anything, because nothing else
enforces a floor once the model is authoring outright:

* **distinctness** - three directions differing only in hue are a failed set;
* **contrast** - every text-on-ground pair must clear WCAG AA, or the
  direction is rejected rather than rendered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Optional, Sequence

from app import colors
from app.colors import _to_hls
from app.renderer import table_style
from app.design_system import (
    COLOR_THEMES, derive_brand_palette, derive_data_viz_config,
    get_default_design_system,
)

# Where the dark tone goes. The axis the reference deck varies most and the
# one a thumbnail shows at a glance.
GROUNDS = ("light", "dark", "rail", "split")
# How much goes on a page, as the multiplier on the built-in density.
DENSITIES = {"airy": 0.6, "balanced": 1.0, "dense": 1.7}
# What sits under the title.
MOTIFS = ("rule", "band", "none")
# How a set of parallel points is shaped. The names are the renderer's own
# block vocabulary, so a direction that previews row bands is a direction the
# deck can actually build in - see ``specimen.ARGUMENT_BUILDERS``, which the
# test suite holds to this same tuple.
STRUCTURES = ("list", "stats", "cards", "bands")

# How this direction draws a chart. The tokens have existed since #120 and
# nothing authored them, so three visibly different directions all drew the
# same matplotlib chart - which is the one element of a deck a reader is most
# likely to be looking at.
#
# Paired with the structure it belongs beside rather than left free: a deck
# whose points are oversized figures and whose charts are gridded analyst
# plots is two decks. The model may still name one explicitly.
CHART_FAMILIES_BY_DENSITY = {"airy": "minimal", "balanced": "labelled",
                             "dense": "gridded"}

# WCAG AA for normal text. Enforced before a direction is rendered, not after
# a user has chosen it.
MIN_CONTRAST = 4.5

# Two directions this alike on the four axes are one direction shown twice.
MIN_DISTINCT_AXES = 2
# How many directions a set has.
DIRECTION_COUNT = 3

_ID_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class DirectionSpec:
    """The five decisions that make one direction, plus what to call it."""
    id: str
    name: str
    rationale: str
    ground: str = "light"
    display_face: str = "Arial"
    body_face: str = "Arial"
    display_pt: float = 36.0
    body_pt: float = 20.0
    density: str = "balanced"
    motif: str = "rule"
    structure: str = "list"
    # Empty means "take it from the density", which pairs a chart treatment
    # with the deck's own register rather than leaving three directions to
    # draw the same plot.
    chart_family: str = ""
    # The hue this direction proposes for itself, when the run has not settled
    # one. Before it existed every direction resolved to the house navy, so a
    # deck about anything at all was previewed in the same blue three times.
    palette_hex: str = ""

    @property
    def display_is_serif(self) -> bool:
        from app.measure.page_metrics import _is_serif

        return _is_serif(self.display_face)

    @property
    def pairing(self) -> str:
        """The type axis, as the thing that actually differs: serif over sans."""
        from app.measure.page_metrics import _is_serif

        return f"{'serif' if _is_serif(self.display_face) else 'sans'}-over-" \
               f"{'serif' if _is_serif(self.body_face) else 'sans'}"

    @property
    def resolved_chart_family(self) -> str:
        """The family this direction draws its charts in."""
        return self.chart_family or CHART_FAMILIES_BY_DENSITY.get(
            self.density, "labelled"
        )

    def axes(self) -> tuple[str, str, str, str, str]:
        return (self.ground, self.pairing, self.motif, self.density,
                self.structure)

    @classmethod
    def from_dict(cls, data: dict, index: int = 0) -> "DirectionSpec":
        data = data or {}
        name = str(data.get("name") or f"Direction {index + 1}").strip()
        return cls(
            id=_slug(data.get("id") or name) or f"direction-{index + 1}",
            name=name,
            rationale=str(data.get("rationale") or "").strip(),
            ground=_one_of(data.get("ground"), GROUNDS, "light"),
            display_face=str(data.get("displayFace") or "Arial").strip() or "Arial",
            body_face=str(data.get("bodyFace") or "Arial").strip() or "Arial",
            display_pt=_number(data.get("displaySizePt"), 36.0),
            body_pt=_number(data.get("bodySizePt"), 20.0),
            density=_one_of(data.get("density"), tuple(DENSITIES), "balanced"),
            motif=_one_of(data.get("motif"), MOTIFS, "rule"),
            structure=_one_of(data.get("structure"), STRUCTURES, "list"),
            chart_family=_one_of(data.get("chartFamily"),
                                 table_style.CHART_FAMILY_NAMES, ""),
            palette_hex=_hex_or_blank(data.get("paletteHex")),
        )

    def to_dict(self) -> dict:
        """
        The spec as the model wrote it, so it can be recompiled later.

        A direction carries this with it because the colour is answered in the
        same pause the direction is chosen in: the answer lands after the
        specimens were drawn, and recolouring the chosen direction means
        compiling its axes again against a different seed.
        """
        return {
            "id": self.id, "name": self.name, "rationale": self.rationale,
            "ground": self.ground, "displayFace": self.display_face,
            "bodyFace": self.body_face, "displaySizePt": self.display_pt,
            "bodySizePt": self.body_pt, "density": self.density,
            "motif": self.motif, "structure": self.structure,
            "chartFamily": self.chart_family,
            "paletteHex": self.palette_hex,
        }


def compile_direction(spec: DirectionSpec, brand_hex: Optional[str] = None,
                      base: Optional[dict] = None) -> dict:
    """
    A spec as a full design system the renderer can build a deck from.

    Built from the built-in bundle rather than from nothing, so every layout,
    every spacing token and every chrome default the renderer relies on is
    present and correct. What the direction changes is the palette's
    deployment, the type, the motif and the density - which is the whole list
    of things three directions are supposed to differ on.
    """
    system = dict(base or get_default_design_system())
    palette = _palette_for(spec, brand_hex, system)
    system["palette"] = {"colors": palette,
                         "schemeName": f"direction-{spec.id}"}
    grounds = _grounds_for(spec, palette)
    system["grounds"] = {
        role: value for role, value in grounds.items() if not role.startswith("_")
    }
    system["typography"] = _typography_for(spec)
    system["density"] = _density_for(spec, system)
    system["spacing"] = _spacing_for(spec, system)
    system["chrome"] = _chrome_for(spec, system)
    system["dataViz"] = table_style.apply_chart_family(
        derive_data_viz_config(palette, body_font=spec.body_face),
        spec.resolved_chart_family, wins=True,
    )
    system["dataViz"]["chartFamily"] = spec.resolved_chart_family
    system["colorTheme"] = spec.id
    system["name"] = spec.name
    system["ground"] = spec.ground
    # Read by the specimen deck, which shapes the preview's points with it,
    # and by the drafting phase, which tells the model which block this deck
    # leads with. A preview showing ruled bands over a deck that then writes
    # bullets is a preview that flattered a direction the deck did not honour.
    system["structure"] = spec.structure
    return system


def _palette_for(spec: DirectionSpec, brand_hex: Optional[str],
                 base: dict) -> list[dict]:
    """
    The palette, with the direction's ground deployed into it.

    A hue the user has already settled wins outright: a run that answered
    "green" gets three green directions, and a coral one here would be the
    tool ignoring an answer it already has. Where nothing is settled the
    direction's own proposed hue is used, because the alternative - which is
    what this did - resolves every direction of every deck to the same house
    navy.
    """
    seed = brand_hex or spec.palette_hex
    if seed and colors.parse_hex(seed):
        palette = [dict(entry) for entry in derive_brand_palette(seed)]
    else:
        base_colors = (base.get("palette") or {}).get("colors")
        palette = [dict(entry) for entry in
                   (base_colors or COLOR_THEMES["professional"])]

    slots = {entry.get("name"): entry.get("value") for entry in palette}
    dark = slots.get("dark") or "#15334F"
    light = slots.get("light") or "#F0F4F8"

    # ``DeckTheme`` reads the "white" slot as the ground a content slide sits
    # on, so that is where a direction's content deployment lands - not a new
    # slot the renderer would have to learn about.
    #
    # Lifted off the cover's own dark rather than equal to it: a dark direction
    # that sets both to the same value gives every page one background, and a
    # deck of identical pages reads as a set of unrelated documents.
    content = {
        "light": "#FFFFFF",
        "dark": colors.mix(dark, "#FFFFFF", 0.12),
        "rail": "#FFFFFF",
        "split": light,
    }
    _set(palette, "white", content.get(spec.ground, "#FFFFFF"))
    return palette


def _grounds_for(spec: DirectionSpec, palette: list[dict]) -> dict:
    """Which ground each kind of slide sits on, for this direction.

    Every deck used to put its cover on the dark tone, whatever else it did -
    so three directions rendered three identical covers, and the only thing a
    user could actually see a difference in was the content background. The
    cover is the first thing they look at, so it is the first thing a direction
    has to change.

    Four deployments, and no two of them share a cover:

    * ``light`` - a tinted cover over white pages. The title carries the brand
      colour and there is no dark page in the deck.
    * ``dark``  - dark cover, content lifted slightly off it. One tone, two
      values.
    * ``rail``  - dark cover, white content. The classic report shape.
    * ``split`` - the brand colour itself as the cover, content on a tint of
      it. The loudest of the four.
    """
    slots = {entry.get("name"): entry.get("value") for entry in palette}
    dark = slots.get("dark") or "#15334F"
    primary = slots.get("primary") or dark
    light = slots.get("light") or "#F0F4F8"
    white = slots.get("white") or "#FFFFFF"

    covers = {
        # Not pure white: a cover indistinguishable from the page after it
        # gives the deck no opening, and the invariant everywhere else here is
        # that a cover reads as one. The tint is the whole difference, which is
        # what makes this the minimal direction rather than the absent one.
        "light": light,
        "dark": dark,
        "rail": dark,
        "split": primary,
    }
    cover = covers.get(spec.ground, dark)
    return {
        "cover": cover,
        "content": white,
        # Section dividers echo the cover, so a deck reads as one document.
        "section": cover,
        "closing": cover,
        # Kept for anything reading the axis by name rather than by colour.
        "_ground": spec.ground,
        "_light": light,
    }


def _typography_for(spec: DirectionSpec) -> dict:
    from app.measure.density import fallback_chain

    return {
        "titleFont": {"typeface": spec.display_face, "sizePt": spec.display_pt,
                      "bold": True, "fallbacks": fallback_chain(spec.display_face)},
        "bodyFont": {"typeface": spec.body_face, "sizePt": spec.body_pt,
                     "bold": False, "fallbacks": fallback_chain(spec.body_face)},
        "captionFont": {"typeface": spec.body_face,
                        "sizePt": round(spec.body_pt * 0.7, 1), "bold": False,
                        "fallbacks": fallback_chain(spec.body_face)},
        "rampPt": [spec.display_pt, spec.body_pt,
                   round(spec.body_pt * 0.85, 1), round(spec.body_pt * 0.7, 1)],
        "displayIsSerif": spec.display_is_serif,
    }


def _density_for(spec: DirectionSpec, base: dict) -> dict:
    """
    The direction's density posture, as the budget the planner is held to.

    This is the axis that is not only visual: a dense analyst direction and an
    airy executive one differ in how much the planner is asked to put on a
    slide, which #129 turns into a target and a rejection.
    """
    from app.measure.density import DensityProfile

    profile = DensityProfile.from_dict(base.get("density"))
    scale = DENSITIES.get(spec.density, 1.0)
    return DensityProfile(
        pages_measured=0,
        words_median=round(profile.words_median * scale),
        words_p25=round(profile.words_p25 * scale),
        words_p75=round(profile.words_p75 * scale),
        blocks_median=round(profile.blocks_median * scale),
        blocks_p25=round(profile.blocks_p25 * scale),
        blocks_p75=round(profile.blocks_p75 * scale),
        ink_median=profile.ink_median,
        grid_rows_median=profile.grid_rows_median,
        grid_columns_median=profile.grid_columns_median,
        words_per_block_median=profile.words_per_block_median,
    ).to_dict()


def _spacing_for(spec: DirectionSpec, base: dict) -> dict:
    """The motif, as the tokens the renderer actually draws it from.

    ``spacing.accentRule`` is declared rather than hardcoded precisely so a
    direction can move it or drop it - and until this existed the motif axis
    changed nothing at all, so a "no motif" direction still drew the same rule
    under the same title as the other two. Three directions that differ only
    in background colour is what that produced.
    """
    spacing = dict(base.get("spacing") or {})
    if spec.motif == "none":
        spacing.pop("accentRule", None)
        spacing.pop("heroAccentRule", None)
        return spacing
    if spec.motif == "band":
        # A band rather than a rule: the full title measure, thick enough to
        # read as a filled shape instead of a line. Measured off the canvas
        # and the rule's own left inset rather than guessed at as a multiple
        # of the rule - four times a short rule lands at 45% of the page,
        # which reads as an empty highlight box, not as a band.
        canvas_emu = int((base.get("slideDimensions") or {}).get("widthEmu") or 0)
        for key, width_key in (("accentRule", "titleWidthEmu"),
                               ("heroAccentRule", "heroWidthEmu")):
            rule = dict(spacing.get(key) or {})
            if not rule:
                continue
            measure = canvas_emu - 2 * int(rule.get("xEmu") or 0)
            rule["cxEmu"] = int(spacing.get(width_key) or max(measure, 0)
                                or rule.get("cxEmu", 0) * 4)
            rule["cyEmu"] = int(rule.get("cyEmu", 0) * 3) or rule.get("cyEmu", 0)
            spacing[key] = rule
    return spacing


def _chrome_for(spec: DirectionSpec, base: dict) -> dict:
    """Where the title sits and what sits under it.

    ``enabled`` is the key ``ChromeSpec`` actually reads. This used to declare
    ``{"show": "never"}``, which reads as an element declared with no opinion -
    so it defaulted to *on*, and the one direction that asked for no rule was
    the only one that drew one.
    """
    chrome = dict(base.get("chrome") or {})
    if spec.motif == "none":
        chrome["footerRule"] = {"enabled": False}
    elif spec.motif == "band":
        chrome["footerRule"] = {"enabled": True}
    return chrome


# ── The two checks ────────────────────────────────────────────────────

def contrast_problems(design_system: dict) -> list[str]:
    """
    Every text-on-ground pair in a direction that fails WCAG AA.

    Checked before rendering rather than after choosing: a direction whose
    body copy cannot be read is not a direction, and offering it as one of
    three is offering a choice with a trap in it.
    """
    from app.renderer import deck_theme

    problems: list[str] = []
    try:
        theme = deck_theme.DeckTheme.from_design_system(design_system)
    except Exception as e:  # pragma: no cover - a palette that will not parse
        return [f"the palette could not be resolved ({e})"]

    for role in (deck_theme.ROLE_COVER, deck_theme.ROLE_CONTENT,
                 deck_theme.ROLE_SECTION):
        style = theme.style_for(role)
        for name, colour in (("body", style.body_color),
                             ("title", style.title_color)):
            ratio = colors.contrast_ratio(colour, style.background)
            if ratio < MIN_CONTRAST:
                problems.append(
                    f"{role} slides set {name} text {colour} on "
                    f"{style.background}, a contrast of {ratio:.1f}:1 "
                    f"against the {MIN_CONTRAST}:1 WCAG AA needs"
                )
    return problems


def distinctness_problems(specs: Sequence[DirectionSpec]) -> list[str]:
    """
    Why a set of directions is not a set.

    Three directions differing only in hue are a failed set - which is exactly
    what the five colour themes this replaces were. Two directions have to
    differ on at least two of the five axes to be two directions.
    """
    problems: list[str] = []
    if len(specs) < 2:
        return problems

    seen_ids = [spec.id for spec in specs]
    if len(set(seen_ids)) != len(seen_ids):
        problems.append("two directions share an id")

    # The ground decides the cover, and the cover is the first thing a user
    # looks at. Two directions sharing one is two directions they cannot tell
    # apart in the thumbnail, whatever else differs - which is what "I have
    # 2/3 the same designs" meant.
    grounds = [spec.ground for spec in specs]
    for ground in sorted({g for g in grounds if grounds.count(g) > 1}):
        sharing = [spec.name for spec in specs if spec.ground == ground]
        problems.append(
            f"{' and '.join(repr(n) for n in sharing)} both sit on the "
            f"{ground!r} ground, so they render the same cover - give each "
            "direction a different one"
        )

    # Ground, type and colour are what a thumbnail shows; structure is what
    # the reader navigates by. A set that shapes its points identically
    # everywhere is a set of colourways however different the covers look,
    # which is the whole complaint in #151.
    structures = {spec.structure for spec in specs}
    if len(specs) >= DIRECTION_COUNT and len(structures) < 2:
        problems.append(
            f"all {len(specs)} directions shape their points as "
            f"{next(iter(structures))!r}, so every preview shows the same "
            "slide - give them different structures"
        )

    for index, first in enumerate(specs):
        for second in specs[index + 1:]:
            differing = [
                axis for axis, (a, b) in zip(
                    ("ground", "type pairing", "motif", "density", "structure"),
                    zip(first.axes(), second.axes()),
                ) if a != b
            ]
            if len(differing) < MIN_DISTINCT_AXES:
                problems.append(
                    f"{first.name!r} and {second.name!r} are the same "
                    f"direction: they differ only on "
                    f"{', '.join(differing) or 'nothing'}"
                )
    return problems


# How far a hue may be moved to make a direction readable. Lightness only:
# the hue the model picked for this subject is the thing worth keeping, and
# moving it along one axis is a repair rather than a substitution.
_REPAIR_STEP = 0.04
_REPAIR_MAX_SHIFT = 0.40
# Past these a colour is effectively black or white, and no longer the hue
# it started as.
_REPAIR_FLOOR, _REPAIR_CEILING = 0.08, 0.92


def _repair_lightnesses(seed: str) -> list[float]:
    """Lightnesses to try, nearest to the seed's own first.

    Both directions, alternating, so a colour that is too light for white
    text is as likely to be darkened as a colour that is too dark for black
    text is to be lightened - the fix is whichever is closer.
    """
    current = _to_hls(seed)
    start = current[1] if current else 0.5
    out: list[float] = []
    steps = int(_REPAIR_MAX_SHIFT / _REPAIR_STEP)
    for step in range(1, steps + 1):
        for delta in (-step * _REPAIR_STEP, step * _REPAIR_STEP):
            value = round(start + delta, 4)
            if _REPAIR_FLOOR <= value <= _REPAIR_CEILING:
                out.append(value)
    return out


def repair_contrast(spec: DirectionSpec, brand_hex: Optional[str] = None,
                    base: Optional[dict] = None) -> tuple[Optional[DirectionSpec],
                                                          Optional[dict]]:
    """
    The same direction in the nearest readable version of its own hue.

    Contrast is a property that can be repaired rather than a verdict. A
    direction that missed WCAG AA by 1.3:1 used to take the whole set down
    with it - the two that passed, the specimen copy written about this
    deck, and the palettes - and the user was shown the built-in three in
    the house navy instead. Darkening the coral the model chose keeps the
    direction it chose it for.

    Returns ``(None, None)`` when no lightness clears the floor, which is
    the case a caller should still reject.
    """
    seed = brand_hex or spec.palette_hex
    if not seed or not colors.parse_hex(seed):
        # Nothing to move: the direction is drawn from the built-in palette,
        # so an unreadable one is a bug in the defaults, not in the model.
        return None, None
    for lightness in _repair_lightnesses(seed):
        candidate = colors.with_lightness(seed, lightness)
        repaired = replace(spec, palette_hex=candidate)
        # Compiled against the repaired hue explicitly rather than against
        # ``brand_hex``, which would win over it in ``_palette_for``.
        system = compile_direction(repaired, None, base)
        if not contrast_problems(system):
            return repaired, system
    return None, None


def validate(specs: Sequence[DirectionSpec], brand_hex: Optional[str] = None,
             base: Optional[dict] = None) -> tuple[list[dict], list[str]]:
    """
    Compile a proposed set, and say what is wrong with it.

    Returns the compiled directions and the problems. A caller with problems
    should re-ask rather than show them: the point of authoring outright is
    that nothing enforces a floor, so this is the floor.

    A direction whose text cannot be read on its ground is repaired first -
    see :func:`repair_contrast` - and only reported as a problem when no
    version of its own hue clears WCAG AA.
    """
    problems = distinctness_problems(specs)
    compiled: list[dict] = []
    for proposed in specs:
        spec = proposed
        system = compile_direction(spec, brand_hex, base)
        if contrast_problems(system):
            repaired, repaired_system = repair_contrast(spec, brand_hex, base)
            if repaired_system is None:
                problems.append(
                    f"{proposed.name!r} is unreadable, and no lightness of "
                    "its own hue clears WCAG AA"
                )
                continue
            spec, system = repaired, repaired_system
        compiled.append({
            "id": spec.id,
            "name": spec.name,
            "rationale": spec.rationale,
            "designSystem": system,
            # Carried so the direction can be compiled again against another
            # hue when the colour answer arrives - see ``recolour``.
            "spec": spec.to_dict(),
            "previewUrls": [],
        })
    if len(compiled) < DIRECTION_COUNT:
        problems.append(
            f"only {len(compiled)} of {DIRECTION_COUNT} directions survived"
        )
    return compiled, problems


def top_up(compiled: list[dict], wanted: int,
           brand_hex: Optional[str] = None,
           base: Optional[dict] = None,
           seeds: Sequence[str] = ()) -> list[dict]:
    """
    Fill a short set from the defaults, keeping what was authored.

    A set that came back one direction short used to be discarded whole,
    so a single unusable direction cost the user the two good ones as
    well. The authored ones lead; the built-ins make up the number, and
    only where they do not collide with a ground or an id already in use.

    ``seeds`` are hues the model proposed for this subject. A built-in
    filling a gap takes one rather than the house navy, so a topped-up
    card is still a card about this deck rather than half a stock one.
    """
    # Deduplicated first: an authored set can reach here still carrying two
    # directions on the same ground, which is two directions a user cannot
    # tell apart in a thumbnail. Keeping the first of each is the same floor
    # ``distinctness_problems`` states, applied instead of merely reported.
    taken_ids: set = set()
    taken_grounds: set = set()
    out: list[dict] = []
    for entry in compiled:
        ground = (entry.get("spec") or {}).get("ground")
        if entry.get("id") in taken_ids or (ground and ground in taken_grounds):
            continue
        taken_ids.add(entry.get("id"))
        if ground:
            taken_grounds.add(ground)
        out.append(entry)
        if len(out) >= wanted:
            return out

    unused_seeds = [seed for seed in seeds if colors.parse_hex(seed)]
    for spec in default_specs():
        if len(out) >= wanted:
            break
        if spec.id in taken_ids or spec.ground in taken_grounds:
            continue
        if not brand_hex and unused_seeds:
            spec = replace(spec, palette_hex=unused_seeds.pop(0))
        system = compile_direction(spec, brand_hex, base)
        if contrast_problems(system):
            spec, system = repair_contrast(spec, brand_hex, base)
            if system is None:
                continue
        taken_ids.add(spec.id)
        taken_grounds.add(spec.ground)
        out.append({
            "id": spec.id,
            "name": spec.name,
            "rationale": spec.rationale,
            "designSystem": system,
            "spec": spec.to_dict(),
            "previewUrls": [],
        })
    return out[:wanted]


# ── The fallback set ──────────────────────────────────────────────────

def default_specs() -> list[DirectionSpec]:
    """
    Three directions to fall back on, differing on every axis.

    Used when the model's own set cannot be salvaged, and as the shape the
    prompt is written against. Not COLOR_THEMES: the point of this feature is
    that a direction is a design rather than a palette.
    """
    return [
        DirectionSpec(
            id="clean-minimal", name="Clean minimal",
            rationale="White throughout, sans over sans, a rule under the "
                      "title. Points come out as big figures.",
            ground="light", display_face="Arial", body_face="Arial",
            display_pt=36.0, body_pt=20.0, density="airy", motif="rule",
            structure="stats",
        ),
        DirectionSpec(
            id="dark-analyst", name="Dark analyst",
            rationale="Dark ground, sans over sans, a filled band under the "
                      "title. Points come out as ruled rows beside a table.",
            ground="dark", display_face="Arial", body_face="Arial",
            display_pt=30.0, body_pt=15.0, density="dense", motif="band",
            structure="bands",
        ),
        DirectionSpec(
            id="editorial-serif", name="Editorial serif",
            rationale="A serif display over a sans body on a split canvas. "
                      "Points come out as written prose.",
            ground="split", display_face="Georgia", body_face="Arial",
            display_pt=30.0, body_pt=15.0, density="balanced", motif="none",
            structure="list",
        ),
    ]


def default_directions(brand_hex: Optional[str] = None,
                       base: Optional[dict] = None) -> list[dict]:
    """The fallback set, compiled."""
    compiled, _ = validate(default_specs(), brand_hex, base)
    return compiled


def recolour(direction: dict, brand_hex: str,
             base: Optional[dict] = None) -> dict:
    """
    The same direction, compiled again in another hue.

    The colour is answered in the same pause the direction is chosen in -
    one card, two questions - so the answer necessarily lands after the
    specimens were drawn. What the previews are honest about is ground, type,
    motif and structure; the hue is applied here, afterwards, to the one
    direction that was actually picked.

    A recolour that cannot be read is not applied. A user who asked for a
    yellow deck and got unreadable body copy has been given a worse deck than
    the one they were looking at, and silently is the worst way to do it.
    """
    spec_data = (direction or {}).get("spec")
    if not isinstance(spec_data, dict) or not colors.parse_hex(brand_hex):
        # No spec means an uploaded template, whose palette is the user's own
        # and is not ours to overwrite.
        return direction
    system = compile_direction(DirectionSpec.from_dict(spec_data), brand_hex, base)
    if contrast_problems(system):
        return direction
    return {**direction, "designSystem": system}


def template_direction(design_system: dict, name: str = "Your template") -> dict:
    """
    The uploaded template itself, as direction one.

    A user who handed us a template has already made the choice this tool
    exists to offer; the other two directions are variations on it, and the
    template must be the one they can keep.
    """
    return {
        "id": "your-template",
        "name": name,
        "rationale": "Your uploaded template's own design system, unchanged.",
        "designSystem": design_system,
        "previewUrls": [],
    }


# ── Internals ─────────────────────────────────────────────────────────

def _set(palette: list[dict], name: str, value: str) -> None:
    for entry in palette:
        if entry.get("name") == name:
            entry["value"] = value
            return
    palette.append({"name": name, "value": value, "type": "hex"})


def _slug(value: Any) -> str:
    return _ID_RE.sub("-", str(value or "").strip().lower()).strip("-")


def _one_of(value: Any, allowed: Sequence[str], default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else default


def _hex_or_blank(value: Any) -> str:
    text = str(value or "").strip()
    if text and not text.startswith("#"):
        text = f"#{text}"
    return text.upper() if colors.parse_hex(text) else ""


def _number(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default
