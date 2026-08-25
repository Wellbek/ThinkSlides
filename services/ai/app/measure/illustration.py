"""
How a publication treats its imagery, read from the imagery.

A deck's illustration is as much a part of how it reads as its palette, and
the palette has been measured from the reference since the beginning while
illustration was decided per slide. Two pages of one deck could come back in
unrelated treatments and nothing said they should not.

What can honestly be read off a page is narrow, and that is the point.

**Is it colour, or is it not.** A page whose photographs are all near-grey is
a publication that treats its imagery, and the two treatments a publication
most often uses to make stock imagery look like its own are exactly the ones
that show up in saturation: `mono` and `duotone`.

**Does it bleed.** An image touching the trim is a different visual system
from one inset in the grid, and which one a publication uses is a decision it
makes on every page.

Neither is asserted from two photographs. A publication that shows us almost
no imagery is telling us it has no illustration system worth reproducing, and
a treatment recovered from three pixels is a treatment invented.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Sequence

# Below this many images across the sample there is nothing to generalise
# from, and the reading is reported as unconfident rather than as a finding.
MIN_IMAGES = 4

# Saturation, 0..1, below which an image reads as treated rather than as a
# full-colour photograph. Mid-grey sits at 0.
MONO_MAX_SATURATION = 0.10
DUOTONE_MAX_SATURATION = 0.22
MUTED_MAX_SATURATION = 0.38

# How close to the trim an image has to reach before it is bleeding, as a
# fraction of the page.
BLEED_TOLERANCE = 0.02
# What share of a publication's images have to bleed before bleeding is the
# house rule rather than an exception on one page.
BLEED_SHARE = 0.5

# How many hues an image may cluster into and still read as a duotone.
DUOTONE_MAX_HUES = 2


@dataclass(frozen=True)
class ImageReading:
    """One image on one page, as the two things worth measuring."""

    saturation: float
    hues: int
    bleeds: bool


@dataclass(frozen=True)
class IllustrationReading:
    """What a publication does with its imagery, and how sure we are."""

    treatment: str = "full"
    placement: str = "inset"
    images: int = 0
    confidence: float = 0.0
    note: str = ""


def _saturation(r: int, g: int, b: int) -> float:
    high, low = max(r, g, b), min(r, g, b)
    return 0.0 if high == 0 else (high - low) / high


def read_image(pixels: Sequence[tuple[int, int, int]], *,
               bleeds: bool) -> ImageReading:
    """One image, sampled. Pixels are however many the caller could afford."""
    if not pixels:
        return ImageReading(saturation=0.0, hues=0, bleeds=bleeds)
    saturations = [_saturation(*pixel) for pixel in pixels]
    # Hue buckets, coarse: the question is "one colour, two, or many", not
    # which colours they are.
    buckets = set()
    for r, g, b in pixels:
        if _saturation(r, g, b) < MONO_MAX_SATURATION:
            continue
        high = max(r, g, b)
        if high == r:
            buckets.add("r" if g >= b else "m")
        elif high == g:
            buckets.add("g" if b >= r else "y")
        else:
            buckets.add("b" if r >= g else "c")
    return ImageReading(
        saturation=statistics.median(saturations),
        hues=len(buckets),
        bleeds=bleeds,
    )


def recover(readings: Sequence[ImageReading]) -> IllustrationReading:
    """The publication's treatment, or an honest shrug.

    Refusing below a threshold rather than answering anyway is the same rule
    the grid, the rhythm and the spacing ladder follow: a pack that quietly
    invents a reading teaches every later stage to converge on a fiction.
    """
    usable = list(readings)
    if len(usable) < MIN_IMAGES:
        return IllustrationReading(
            images=len(usable), confidence=0.0,
            note=(f"only {len(usable)} image(s) across the sampled pages; too "
                  f"few to read a treatment from"),
        )

    saturation = statistics.median(r.saturation for r in usable)
    hues = statistics.median(r.hues for r in usable)
    if saturation <= MONO_MAX_SATURATION:
        treatment = "mono"
    elif saturation <= DUOTONE_MAX_SATURATION and hues <= DUOTONE_MAX_HUES:
        treatment = "duotone"
    elif saturation <= MUTED_MAX_SATURATION:
        treatment = "muted"
    else:
        treatment = "full"

    bleeding = sum(1 for r in usable if r.bleeds) / len(usable)
    placement = "bleed" if bleeding >= BLEED_SHARE else "inset"

    # Agreement, not sample size: a publication whose images scatter across
    # every treatment has no treatment, however many of them there are.
    spread = (statistics.pstdev([r.saturation for r in usable])
              if len(usable) > 1 else 0.0)
    confidence = max(0.0, min(1.0, 1.0 - spread * 2.5))
    return IllustrationReading(
        treatment=treatment, placement=placement, images=len(usable),
        confidence=round(confidence, 3),
        note=(f"{len(usable)} images, median saturation {saturation:.2f}, "
              f"{bleeding:.0%} bleeding"),
    )
