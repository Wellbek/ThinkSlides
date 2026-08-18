"""
Named ways to divide a page, and the geometry each one resolves to.

``regions`` can resolve and validate an arbitrary tree, and has been able to
since #118. What was missing was any way to *author* one. The grammar offers
the compositions measured off the template's own pages, to be copied verbatim,
which is right when one of them fits and leaves nothing when none does - a
deck whose template contains no two-exhibit page could never make one, however
much the material wanted it.

The obvious fix is to let the model write the fractions. That reintroduces
exactly the estimation the grammar exists to avoid: a model asked for
coordinates produces a rail of 0.31 and a panel of 0.71, and the two overlap
by a hundredth nobody notices until content is drawn over content.

So the geometry lives here and the model states intent - which composition, in
what proportion. The same split that made the ``build_*`` tools reliable:
the model says what it wants, the code says what that is in numbers.

Two bands are reserved on every composition and are not negotiable:

* a **title band** at the top, because a composed slide still puts its
  headline in the template's own title placeholder, and a region drawn under
  that placeholder is content drawn over content;
* a **footnote band** at the bottom, because an exhibit page with no room for
  its source line is how a chart ends up making a claim with nothing standing
  behind it. The apparatus (#125) needs somewhere to put one.

What is left between them is the body box, and a composition divides that.
"""

from __future__ import annotations

from dataclasses import dataclass

# The bands, as fractions of the canvas height. Measured against the
# reference deck's own content pages rather than chosen: its title sits in
# the top fifth and its source lines in the bottom fifteenth.
TITLE_BAND_H = 0.20
FOOTNOTE_BAND_H = 0.07

# Side margins, and the gap between two regions that sit beside each other.
# The gutter is what keeps siblings from touching: validate_regions tolerates
# a hairline of shared edge, but a composition that relies on that tolerance
# is one rounding away from being rejected.
MARGIN_X = 0.06
GUTTER = 0.025

# A full-bleed rail runs the whole height and carries its own ground, so it
# ignores the bands - it is the page's edge, not part of its body.
FULL_BLEED = frozenset({"rail"})

# Bands every composition reserves rather than divides. They are part of the
# page's furniture, not slots an exhibit competes for, which is why they are
# excluded both from the ids a caller may bind to and from the minimum-size
# check an exhibit slot has to pass.
RESERVED = frozenset({"footnote"})

BODY_TOP = TITLE_BAND_H
BODY_BOTTOM = 1.0 - FOOTNOTE_BAND_H
BODY_H = BODY_BOTTOM - BODY_TOP
BODY_LEFT = MARGIN_X
BODY_W = 1.0 - 2 * MARGIN_X

# How short a band or column may get before it stops being able to hold
# anything. A composition that resolves below this is a composition the model
# asked the wrong question of, and it is told so rather than rendered.
MIN_PART_W = 0.12
MIN_PART_H = 0.08


@dataclass(frozen=True)
class Composition:
    """One named division of a page."""
    name: str
    # What the parts are called, in reading order. These become region ids,
    # so they are what a caller binds an exhibit to.
    parts: tuple[str, ...]
    # What ``proportion`` means here, said in the terms of this composition -
    # it is a different quantity in each one, and a single generic
    # description would be a description of nothing.
    proportion_means: str
    default_proportion: float
    min_proportion: float
    max_proportion: float
    # Whether the caller chooses how many parts there are.
    variable_parts: bool = False
    summary: str = ""


COMPOSITIONS: dict[str, Composition] = {
    "split": Composition(
        name="split",
        parts=("left", "right"),
        proportion_means="the share of the width taken by the left half",
        default_proportion=0.5, min_proportion=0.3, max_proportion=0.7,
        summary="Two panels side by side. The workhorse: an exhibit and its reading.",
    ),
    "railAndPanel": Composition(
        name="railAndPanel",
        parts=("rail", "panel"),
        proportion_means="the share of the width taken by the rail",
        default_proportion=0.28, min_proportion=0.15, max_proportion=0.4,
        summary=(
            "A narrow full-height rail with its own ground, and the body beside "
            "it. The rail carries the section, the takeaway or the question; the "
            "panel carries the evidence."
        ),
    ),
    "heroAndSidebar": Composition(
        name="heroAndSidebar",
        parts=("hero", "sidebar"),
        proportion_means="the share of the width taken by the hero",
        default_proportion=0.64, min_proportion=0.5, max_proportion=0.78,
        summary=(
            "One large exhibit with a stack of smaller things beside it. Use when "
            "one chart is the argument and the rest is support."
        ),
    ),
    "stripAndGrid": Composition(
        name="stripAndGrid",
        parts=("strip", "gridLeft", "gridRight"),
        proportion_means="the share of the body height taken by the strip",
        default_proportion=0.24, min_proportion=0.15, max_proportion=0.4,
        summary=(
            "A figure strip across the top and two exhibits under it. The "
            "board-pack page: the numbers a reader quotes, then what explains them."
        ),
    ),
    "exhibitGrid": Composition(
        name="exhibitGrid",
        parts=("topLeft", "topRight", "bottomLeft", "bottomRight"),
        proportion_means="the share of the body height taken by the top row",
        default_proportion=0.5, min_proportion=0.35, max_proportion=0.65,
        summary=(
            "Four quadrants. Only when there are genuinely four different "
            "questions to answer - four exhibits saying the same thing is worse "
            "than one saying it once."
        ),
    ),
    "bandStack": Composition(
        name="bandStack",
        parts=("band1", "band2", "band3"),
        proportion_means="unused; band heights are equal",
        default_proportion=0.5, min_proportion=0.0, max_proportion=1.0,
        variable_parts=True,
        summary=(
            "Full-width horizontal bands, each a row of the argument. Two to six."
        ),
    ),
}

MIN_BANDS = 2
MAX_BANDS = 6


class CompositionError(ValueError):
    """A composition that cannot be resolved, stated so a caller can act."""


def compose(name: str, proportion: float | None = None,
            parts: int | None = None,
            grounds: dict[str, str] | None = None) -> list[dict]:
    """
    Resolve a named composition to a region tree.

    Returns the list a slide's ``regions`` field takes: fractional geometry
    against the canvas, one entry per part plus the reserved footnote band.
    """
    composition = COMPOSITIONS.get(name)
    if composition is None:
        raise CompositionError(
            f"{name!r} is not a composition. Available: "
            f"{', '.join(sorted(COMPOSITIONS))}."
        )

    share = composition.default_proportion if proportion is None else float(proportion)
    if not composition.variable_parts and not (
        composition.min_proportion <= share <= composition.max_proportion
    ):
        raise CompositionError(
            f"proportion {share} is outside what {name!r} can carry "
            f"({composition.min_proportion} to {composition.max_proportion}). "
            f"Here it is {composition.proportion_means}; past that range one "
            "part is too small to hold anything."
        )

    builder = _BUILDERS[name]
    regions = builder(share, parts)

    for region in regions:
        _check_part(name, region)

    if grounds:
        by_id = {region["id"]: region for region in regions}
        for region_id, colour in grounds.items():
            target = by_id.get(str(region_id))
            if target is None:
                raise CompositionError(
                    f"cannot ground {region_id!r}: {name!r} has no such part. "
                    f"Its parts are {', '.join(r['id'] for r in regions)}."
                )
            target["ground"] = str(colour)

    return regions


def part_names(name: str, parts: int | None = None) -> list[str]:
    """The region ids a composition will produce, for a caller to bind against."""
    return [region["id"] for region in compose(name, parts=parts)]


def describe() -> str:
    """The catalogue, as the tool's own parameter description."""
    lines = []
    for composition in COMPOSITIONS.values():
        names = ", ".join(composition.parts)
        if composition.variable_parts:
            names += ", ... (parts chooses how many)"
        lines.append(f"{composition.name} [{names}] - {composition.summary}")
    return " | ".join(lines)


# ── Builders ──────────────────────────────────────────────────────────
#
# Each returns top-level regions only. Nesting is available - Region.from_dict
# reads children at any depth - but a composition that nests is a composition
# whose parts cannot be bound to independently, which is the one thing every
# caller needs.


def _footnote() -> dict:
    return {"id": "footnote", "kind": "banner",
            "x": BODY_LEFT, "y": BODY_BOTTOM + GUTTER,
            "w": BODY_W, "h": FOOTNOTE_BAND_H - GUTTER}


def _columns(share: float, ids: tuple[str, str]) -> list[dict]:
    left_w = BODY_W * share - GUTTER / 2
    right_w = BODY_W * (1 - share) - GUTTER / 2
    return [
        {"id": ids[0], "kind": "panel", "x": BODY_LEFT, "y": BODY_TOP,
         "w": left_w, "h": BODY_H},
        {"id": ids[1], "kind": "panel", "x": BODY_LEFT + left_w + GUTTER,
         "y": BODY_TOP, "w": right_w, "h": BODY_H},
    ]


def _build_split(share: float, _parts: int | None) -> list[dict]:
    return [*_columns(share, ("left", "right")), _footnote()]


def _build_rail_and_panel(share: float, _parts: int | None) -> list[dict]:
    # The rail is full-bleed: it runs the whole height, carries its own
    # ground, and is the page's edge rather than part of its body. So it
    # ignores the margins and the bands, and the panel starts clear of it.
    rail_w = share
    panel_x = rail_w + MARGIN_X
    return [
        {"id": "rail", "kind": "rail", "x": 0.0, "y": 0.0, "w": rail_w, "h": 1.0},
        {"id": "panel", "kind": "panel", "x": panel_x, "y": BODY_TOP,
         "w": 1.0 - panel_x - MARGIN_X, "h": BODY_H},
        _footnote_from(panel_x),
    ]


def _footnote_from(left: float) -> dict:
    return {"id": "footnote", "kind": "banner",
            "x": left, "y": BODY_BOTTOM + GUTTER,
            "w": 1.0 - left - MARGIN_X, "h": FOOTNOTE_BAND_H - GUTTER}


def _build_hero_and_sidebar(share: float, _parts: int | None) -> list[dict]:
    hero, sidebar = _columns(share, ("hero", "sidebar"))
    hero["kind"] = "panel"
    sidebar["kind"] = "grid"
    return [hero, sidebar, _footnote()]


def _build_strip_and_grid(share: float, _parts: int | None) -> list[dict]:
    strip_h = BODY_H * share - GUTTER / 2
    grid_y = BODY_TOP + strip_h + GUTTER
    grid_h = BODY_BOTTOM - grid_y
    half = (BODY_W - GUTTER) / 2
    return [
        {"id": "strip", "kind": "grid", "x": BODY_LEFT, "y": BODY_TOP,
         "w": BODY_W, "h": strip_h},
        {"id": "gridLeft", "kind": "panel", "x": BODY_LEFT, "y": grid_y,
         "w": half, "h": grid_h},
        {"id": "gridRight", "kind": "panel", "x": BODY_LEFT + half + GUTTER,
         "y": grid_y, "w": half, "h": grid_h},
        _footnote(),
    ]


def _build_exhibit_grid(share: float, _parts: int | None) -> list[dict]:
    top_h = BODY_H * share - GUTTER / 2
    bottom_y = BODY_TOP + top_h + GUTTER
    bottom_h = BODY_BOTTOM - bottom_y
    half = (BODY_W - GUTTER) / 2
    right_x = BODY_LEFT + half + GUTTER
    return [
        {"id": "topLeft", "kind": "panel", "x": BODY_LEFT, "y": BODY_TOP,
         "w": half, "h": top_h},
        {"id": "topRight", "kind": "panel", "x": right_x, "y": BODY_TOP,
         "w": half, "h": top_h},
        {"id": "bottomLeft", "kind": "panel", "x": BODY_LEFT, "y": bottom_y,
         "w": half, "h": bottom_h},
        {"id": "bottomRight", "kind": "panel", "x": right_x, "y": bottom_y,
         "w": half, "h": bottom_h},
        _footnote(),
    ]


def _build_band_stack(_share: float, parts: int | None) -> list[dict]:
    count = MIN_BANDS + 1 if parts is None else int(parts)
    if not MIN_BANDS <= count <= MAX_BANDS:
        raise CompositionError(
            f"bandStack takes {MIN_BANDS} to {MAX_BANDS} bands, not {count}. "
            "Two bands do not read as a set, and past six none of them can "
            "hold an implication."
        )
    band_h = (BODY_H - GUTTER * (count - 1)) / count
    regions = [
        {"id": f"band{n + 1}", "kind": "band", "x": BODY_LEFT,
         "y": BODY_TOP + n * (band_h + GUTTER), "w": BODY_W, "h": band_h}
        for n in range(count)
    ]
    return [*regions, _footnote()]


_BUILDERS = {
    "split": _build_split,
    "railAndPanel": _build_rail_and_panel,
    "heroAndSidebar": _build_hero_and_sidebar,
    "stripAndGrid": _build_strip_and_grid,
    "exhibitGrid": _build_exhibit_grid,
    "bandStack": _build_band_stack,
}


def _check_part(composition: str, region: dict) -> None:
    """
    Refuse a part too small to hold an exhibit.

    The proportion bounds already stop the obvious cases; this catches the
    ones that only appear once the margins and gutters are subtracted, which
    is not a calculation a caller should have to do.

    The reserved bands are exempt, and have to be: the footnote is a single
    line of source by construction, so holding it to an exhibit's minimum
    height would reject every composition ever written.
    """
    if region["id"] in RESERVED:
        return
    if region["w"] < MIN_PART_W or region["h"] < MIN_PART_H:
        raise CompositionError(
            f"{composition!r} resolves {region['id']!r} to "
            f"{region['w']:.2f} x {region['h']:.2f} of the page, which is too "
            "small to hold an exhibit. Choose a proportion closer to the middle "
            "of the range, or a composition with fewer parts."
        )
