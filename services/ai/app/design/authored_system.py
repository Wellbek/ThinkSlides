"""
The design system an authored language compiles to.

A `Language` says what the deck's design *is* - the measure, the ramp, the
colour roles, the kinds of page. A `Pack` is what composes a page from it. But
almost everything downstream of the design pause reads neither: the chart
renderer wants a palette in the design-system shape, the block builders want
`dataViz` tokens, the planner reads `density`, and `contrast_problems` takes a
design system and nothing else.

So a language compiles to one too, exactly as a `DirectionSpec` does through
`compile_direction`. Built on the default bundle for the same reason that one
is - every token the renderer relies on is present and correct, and what the
language changes is the palette's deployment, the type and the density.

The contrast floor comes with it. `directions.validate` holds a designed
direction to WCAG AA 4.5:1 and repairs it where it fails; a design the agent
invented is held to the same floor, because "the agent chose it" is not a
reason a reader can see the text.
"""

from __future__ import annotations

from typing import Optional

from app.design.authored import DEFAULT_RATIOS, Language
from app.design_system import (
    derive_brand_palette,
    derive_data_viz_config,
    get_default_design_system,
)
from app.observability.logging import get_logger
from app.renderer import table_style
from app import colors

logger = get_logger(__name__)

# How far a colour is moved per attempt when it fails the contrast floor.
# Walked away from the ground in small steps rather than snapped to black or
# white, so a language that misses by a little keeps most of the colour it
# chose.
REPAIR_STEP = 0.04


def system_for(language: Language, base: Optional[dict] = None) -> dict:
    """The design system this language's deck is rendered against."""
    system = dict(base or get_default_design_system())

    palette = [dict(entry) for entry in
               derive_brand_palette(language.accent,
                                    language.accent_alt or None)]
    _set(palette, "white", language.ground)
    _set(palette, "text", language.ink)
    system["palette"] = {"colors": palette,
                         "schemeName": f"authored-{_slug(language.name)}"}

    system["typography"] = _typography(language)
    system["density"] = dict(system.get("density") or {},
                             posture=language.density)
    system["colorTheme"] = _slug(language.name)
    system["name"] = language.name
    # The two the specimen renderer and the drafting phase read off a
    # direction. An authored language has no enum `ground` or `structure`, and
    # neither is consulted on this path - the pack decides both - but leaving
    # them absent would make an authored direction the only one in the system
    # missing keys every other reader expects.
    system["ground"] = "dark" if _is_dark(language.ground) else "light"
    system["structure"] = "authored"
    system["dataViz"] = table_style.apply_chart_family(
        derive_data_viz_config(palette, body_font=language.body_face),
        "labelled", wins=True,
    )
    system["dataViz"]["chartFamily"] = "labelled"
    return system


def _typography(language: Language) -> dict:
    from app.measure.density import fallback_chain

    display_pt = round(language.body_pt * language.ratio("display"), 1)
    body_pt = round(language.body_pt, 1)
    return {
        "titleFont": {"typeface": language.display_face, "sizePt": display_pt,
                      "bold": True,
                      "fallbacks": fallback_chain(language.display_face)},
        "bodyFont": {"typeface": language.body_face, "sizePt": body_pt,
                     "bold": False,
                     "fallbacks": fallback_chain(language.body_face)},
        "captionFont": {
            "typeface": language.body_face,
            "sizePt": round(body_pt * language.ratio("caption"), 1),
            "bold": False, "fallbacks": fallback_chain(language.body_face)},
        "rampPt": [display_pt,
                   round(body_pt * language.ratio("headline"), 1),
                   body_pt,
                   round(body_pt * language.ratio("caption"), 1)],
        "displayIsSerif": _is_serif(language.display_face),
    }


def readable(language: Language) -> Language:
    """The same language, with its colours moved until its copy can be read.

    The floor is `directions.MIN_CONTRAST`, and it applies here for the same
    reason it applies there: a deck whose body copy sits at 2.8:1 on its own
    ground is not a design decision, it is an unreadable deck. Repaired
    rather than rejected, because the language's measure, ramp and page
    vocabulary are all still good - one colour is not.

    This is not the same check `directions.contrast_problems` runs, and it
    cannot be. That one compiles a design system and asks `DeckTheme` what
    colour the text will be - and `DeckTheme` derives a readable ink from the
    ground itself, through `colors.readable_on`, so it never reports a
    failure it has already fixed. The design-language path does not go
    through it: `render_doc` sets type in the pack's own `ink` on the pack's
    own `ground`, exactly as authored. Whatever the agent wrote is what a
    reader gets, which is why the floor has to be applied to the language.
    """
    update = {}
    for field in ("ink", "accent"):
        repaired = _lift(getattr(language, field), language.ground,
                         language.name, field)
        if repaired is not None:
            update[field] = repaired
    return language.model_copy(update=update) if update else language


def _lift(colour: str, ground: str, name: str, field: str) -> Optional[str]:
    """``colour`` moved off ``ground`` until it clears the floor, or None.

    None means it already cleared, so the caller keeps exactly what was
    authored - which is the common case and the one worth not disturbing.
    """
    from app.directions import MIN_CONTRAST

    if colors.contrast_ratio(colour, ground) >= MIN_CONTRAST:
        return None

    # Away from the ground, not toward black: on a near-black ground the
    # readable colour is a light one, and stepping an already-dark one darker
    # would walk away from the floor rather than toward it.
    target = "#FFFFFF" if _is_dark(ground) else "#000000"
    shift = REPAIR_STEP
    while shift <= 1.0:
        candidate = colors.mix(colour, target, shift)
        if colors.contrast_ratio(candidate, ground) >= MIN_CONTRAST:
            logger.info("authored_colour_repaired", name=name, field=field,
                        was=colour, now=candidate,
                        ratio=round(colors.contrast_ratio(candidate, ground), 2))
            return candidate
        shift += REPAIR_STEP

    # Not even the extreme reads against this ground, so the ground is what is
    # wrong. Take the extreme anyway: a page whose ink is blacker than its
    # author wanted is a compromise, and one nobody can read is not a page.
    logger.warning("authored_colour_unrepairable", name=name, field=field,
                   ground=ground, colour=colour)
    return target


def contrast_problems(language: Language) -> list[str]:
    """Every pair this language sets type in that fails WCAG AA.

    Measured on the language rather than on the design system it compiles to,
    for the reason `readable` gives: the compiled system is read through
    `DeckTheme`, which repairs contrast on the way past and so reports none.
    """
    from app.directions import MIN_CONTRAST

    problems = []
    for name, colour in (("body and heading", language.ink),
                         ("accent", language.accent)):
        ratio = colors.contrast_ratio(colour, language.ground)
        if ratio < MIN_CONTRAST:
            problems.append(
                f"{language.name} sets {name} {colour} on {language.ground}, "
                f"a contrast of {ratio:.1f}:1 against the {MIN_CONTRAST}:1 "
                "WCAG AA needs"
            )
    return problems


def _is_dark(hex_value: str) -> bool:
    return colors.contrast_ratio(hex_value, "#FFFFFF") > \
        colors.contrast_ratio(hex_value, "#000000")


# Face names that carry serifs, for the one downstream flag that asks. Matched
# on substrings because a fallback chain names families rather than files.
_SERIF_MARKERS = ("serif", "georgia", "garamond", "times", "baskerville",
                  "caslon", "didot", "bodoni", "minion", "playfair",
                  "merriweather", "lora", "cambria", "book")


def _is_serif(face: str) -> bool:
    lowered = (face or "").lower()
    if "sans" in lowered:
        return False
    return any(marker in lowered for marker in _SERIF_MARKERS)


def _slug(name: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in name)
    return "-".join(part for part in cleaned.split("-") if part) or "language"


def _set(palette: list[dict], name: str, value: str) -> None:
    for entry in palette:
        if entry.get("name") == name:
            entry["value"] = value
            return
    palette.append({"name": name, "value": value})


__all__ = ["system_for", "readable", "contrast_problems", "DEFAULT_RATIOS"]
