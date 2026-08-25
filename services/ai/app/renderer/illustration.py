"""
One illustration style, read by every path that puts an image on a page.

Illustration was decided per slide. `generate_image` composed a prompt from
the run's design direction, `select_source_image` chose from a catalogue with
no style at all, and an MCP server returned whatever it returned. Two pages of
one deck could come back in unrelated treatments and nothing said they should
not.

This is the single place that answers "what does imagery look like in this
deck". A pack that measured a treatment from its source supplies it; a pack
that could not says so, and the deck's chosen design direction is used
instead. Nothing here invents a treatment, because a house style asserted from
one photograph is not a house style.

Consistency is *measured*, not asserted. Whether a deck's imagery reads as one
hand is a number - how far its images sit from each other in saturation and
from the deck's own palette - and a number is a thing that can fail.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Optional, Sequence

# How the four treatments read to an image provider, and to a renderer that
# has to apply one to a picture it did not generate.
TREATMENT_WORDS = {
    "mono": "monochrome, near-greyscale",
    "duotone": "duotone, two colours only",
    "muted": "desaturated, muted colour",
    "full": "full colour",
}

# Saturation each treatment implies, used to check a deck against its own
# declared style rather than against a taste.
TREATMENT_SATURATION = {
    "mono": 0.05, "duotone": 0.16, "muted": 0.30, "full": 0.60,
}

# How far a deck's images may sit from its declared treatment, and from each
# other, before they stop reading as one hand.
MAX_DRIFT = 0.22
MAX_SPREAD = 0.28


@dataclass(frozen=True)
class Style:
    """What imagery looks like in this deck, and where that came from."""

    treatment: str = "full"
    placement: str = "inset"
    note: str = ""
    palette: tuple[str, ...] = ()
    # `measured` is the difference between a house style and a default. A
    # caller that wants to know whether to trust this has to be able to ask.
    measured: bool = False

    def for_provider(self) -> str:
        """The one sentence an image provider is given.

        Composed here so every generated image in a deck is asked for in the
        same terms, rather than each call assembling its own from whatever it
        had to hand.
        """
        parts = [TREATMENT_WORDS.get(self.treatment, TREATMENT_WORDS["full"])]
        parts.append("bleeding off the edge of the page"
                     if self.placement == "bleed"
                     else "inset within the page's margins")
        if self.note:
            parts.append(self.note)
        if self.palette:
            parts.append("a palette close to " + ", ".join(self.palette[:3]))
        return ", ".join(parts)


def _palette_of(design_system: Optional[dict]) -> tuple[str, ...]:
    entries = ((design_system or {}).get("palette") or {}).get("colors") or []
    return tuple(
        str(entry.get("value")) for entry in entries
        if isinstance(entry, dict) and str(entry.get("value") or "").startswith("#")
    )


def style_for(pack, design_system: Optional[dict] = None) -> Style:
    """The deck's illustration style: measured where it could be, else chosen.

    A pack whose source showed almost no imagery reports no confidence, and
    this falls back to the direction the user picked rather than to a
    treatment nobody measured or asked for.
    """
    palette = _palette_of(design_system)
    declared = getattr(pack, "illustration", None)
    if declared is not None and declared.confidence.value > 0:
        return Style(
            treatment=declared.treatment,
            placement=declared.placement,
            note=declared.style_note,
            palette=palette or tuple(
                getattr(pack.colors, role, "")
                for role in declared.palette_roles
            ),
            measured=True,
        )
    direction = (design_system or {}).get("direction") or {}
    ground = str(direction.get("ground") or "").strip().lower()
    return Style(
        treatment="mono" if ground in ("dark", "ink") else "full",
        placement="inset",
        palette=palette,
        measured=False,
    )


@dataclass(frozen=True)
class Consistency:
    """Whether a deck's imagery reads as one hand, as a number."""

    images: int
    drift: float
    spread: float
    consistent: bool
    note: str = ""


def measure(saturations: Sequence[float], style: Style) -> Consistency:
    """How far this deck's images sit from its style, and from each other.

    Two numbers because two things go wrong. *Drift* is a deck whose images
    are consistently not what it said they would be. *Spread* is a deck whose
    images disagree with each other, which is the failure this whole thing
    exists to catch: one page duotone and the next full colour reads as two
    decks stapled together.
    """
    values = [float(v) for v in saturations]
    if not values:
        return Consistency(images=0, drift=0.0, spread=0.0, consistent=True,
                           note="this deck has no imagery to be consistent about")
    target = TREATMENT_SATURATION.get(style.treatment, 0.6)
    drift = abs(statistics.median(values) - target)
    spread = statistics.pstdev(values) if len(values) > 1 else 0.0
    consistent = drift <= MAX_DRIFT and spread <= MAX_SPREAD
    return Consistency(
        images=len(values), drift=round(drift, 3), spread=round(spread, 3),
        consistent=consistent,
        note=(f"{len(values)} images, {drift:.2f} from the deck's "
              f"{style.treatment} treatment, {spread:.2f} apart from each "
              f"other"),
    )
