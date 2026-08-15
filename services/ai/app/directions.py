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

The model authors a **spec** on those four axes, and this module compiles it
into a full bundle. That split is deliberate: a model asked to emit a whole
DesignSystemBundle has to invent layout catalogues in EMU, and it will, badly.
Asked for four decisions and a palette seed it can actually make them, and
what comes back is guaranteed to render because the code built it.

Two checks then run before a user is shown anything, because nothing else
enforces a floor once the model is authoring outright:

* **distinctness** - three directions differing only in hue are a failed set;
* **contrast** - every text-on-ground pair must clear WCAG AA, or the
  direction is rejected rather than rendered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from app import colors
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
    """The four decisions that make one direction, plus what to call it."""
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

    def axes(self) -> tuple[str, str, str, str]:
        return (self.ground, self.pairing, self.motif, self.density)

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
        )


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
    system["typography"] = _typography_for(spec)
    system["density"] = _density_for(spec, system)
    system["chrome"] = _chrome_for(spec, system)
    system["dataViz"] = derive_data_viz_config(palette, body_font=spec.body_face)
    system["colorTheme"] = spec.id
    system["name"] = spec.name
    system["ground"] = spec.ground
    return system


def _palette_for(spec: DirectionSpec, brand_hex: Optional[str],
                 base: dict) -> list[dict]:
    """
    The palette, with the direction's ground deployed into it.

    The *hue* is settled before this tool runs - a user who answered "green"
    gets three green directions, and a coral one here would be the tool
    ignoring an answer it already has. What varies is which slot the renderer
    ends up putting behind a content slide.
    """
    if brand_hex and colors.parse_hex(brand_hex):
        palette = [dict(entry) for entry in derive_brand_palette(brand_hex)]
    else:
        base_colors = (base.get("palette") or {}).get("colors")
        palette = [dict(entry) for entry in
                   (base_colors or COLOR_THEMES["professional"])]

    slots = {entry.get("name"): entry.get("value") for entry in palette}
    dark = slots.get("dark") or "#15334F"
    light = slots.get("light") or "#F0F4F8"

    # ``DeckTheme`` reads the "white" slot as the ground a content slide sits
    # on, so that is where a direction's deployment lands - not a new slot the
    # renderer would have to learn about. A "dark" direction is one whose
    # content pages sit on the deck's dark tone; "split" tints them; "rail"
    # and "light" keep the paper white and differ in the motif and the
    # composition the grammar puts on top of it.
    grounds = {
        "light": "#FFFFFF",
        # Lifted off the cover's own dark rather than equal to it. A dark
        # direction that sets both to the same value gives every page in the
        # deck one background, which is the defect #56 opened on with the
        # colours the other way round - a deck of identical pages reads as a
        # set of unrelated documents whichever tone they share.
        "dark": colors.mix(dark, "#FFFFFF", 0.12),
        "rail": "#FFFFFF",
        "split": light,
    }
    _set(palette, "white", grounds.get(spec.ground, "#FFFFFF"))
    return palette


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


def _chrome_for(spec: DirectionSpec, base: dict) -> dict:
    """Where the title sits and what sits under it."""
    chrome = dict(base.get("chrome") or {})
    if spec.motif == "none":
        chrome["footerRule"] = {"show": "never"}
    elif spec.motif == "band":
        chrome["footerRule"] = {"show": "always"}
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
    differ on at least two of the four axes to be two directions.
    """
    problems: list[str] = []
    if len(specs) < 2:
        return problems

    seen_ids = [spec.id for spec in specs]
    if len(set(seen_ids)) != len(seen_ids):
        problems.append("two directions share an id")

    for index, first in enumerate(specs):
        for second in specs[index + 1:]:
            differing = [
                axis for axis, (a, b) in zip(
                    ("ground", "type pairing", "motif", "density"),
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


def validate(specs: Sequence[DirectionSpec], brand_hex: Optional[str] = None,
             base: Optional[dict] = None) -> tuple[list[dict], list[str]]:
    """
    Compile a proposed set, and say what is wrong with it.

    Returns the compiled directions and the problems. A caller with problems
    should re-ask rather than show them: the point of authoring outright is
    that nothing enforces a floor, so this is the floor.
    """
    problems = distinctness_problems(specs)
    compiled: list[dict] = []
    for spec in specs:
        system = compile_direction(spec, brand_hex, base)
        failures = contrast_problems(system)
        if failures:
            problems.append(f"{spec.name!r} is unreadable: {failures[0]}")
            continue
        compiled.append({
            "id": spec.id,
            "name": spec.name,
            "rationale": spec.rationale,
            "designSystem": system,
            "previewUrls": [],
        })
    if len(compiled) < DIRECTION_COUNT:
        problems.append(
            f"only {len(compiled)} of {DIRECTION_COUNT} directions survived"
        )
    return compiled, problems


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
                      "title. Airy and executive.",
            ground="light", display_face="Arial", body_face="Arial",
            display_pt=36.0, body_pt=20.0, density="airy", motif="rule",
        ),
        DirectionSpec(
            id="dark-analyst", name="Dark analyst",
            rationale="Dark ground with banded rows, sans over sans, no rule. "
                      "Dense and data-forward.",
            ground="dark", display_face="Arial", body_face="Arial",
            display_pt=30.0, body_pt=15.0, density="dense", motif="band",
        ),
        DirectionSpec(
            id="editorial-serif", name="Editorial serif",
            rationale="A serif display over a sans body on a split canvas. "
                      "Reads like a written report.",
            ground="split", display_face="Georgia", body_face="Arial",
            display_pt=30.0, body_pt=15.0, density="balanced", motif="none",
        ),
    ]


def default_directions(brand_hex: Optional[str] = None,
                       base: Optional[dict] = None) -> list[dict]:
    """The fallback set, compiled."""
    compiled, _ = validate(default_specs(), brand_hex, base)
    return compiled


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


def _number(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default
