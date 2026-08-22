"""
How much a template puts on a page, and at what sizes.

An extracted design system carried a palette, three font sizes, layouts,
spacing tokens and chart colours. It had no field for *how much goes on a
slide*. So a user could upload a dense analyst deck as their template and we
would learn its colours and lose its density entirely - and density is a large
part of what makes that deck read the way it does.

The reference is stark about it. Page 40 carries seven row bands, each with an
icon, a label and a full sentence. Page 12 is a 5 x 3 matrix where every cell
is a complete sentence. A generated deck putting four bullets on a slide is not
a slightly sparser version of that; it is a different genre.

The type scale had the same problem. ``typography`` held titleFont, bodyFont
and captionFont, all defaulting to Arial 36/20/12, with no way to express a
serif display over a sans body - which is exactly the pairing the reference
uses, Georgia at 25pt over Arial at 12-14pt.

**Everything here is measured, not judged.** No model call: these are counts
and sizes read off rendered pages, which is what makes a template's density
something to hold a planner to rather than an impression to argue with.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from app.measure.page_metrics import (
    APPARATUS_MAX_PT,
    PageMetrics,
    _is_serif,
    measure_pdf,
)
from app.measure.structure import PageStructure, count_blocks_in_pdf

# A page carrying less than this is a cover or a divider - a real page, but not
# evidence about how dense the deck's *content* pages are.
CONTENT_MIN_WORDS = 25
CONTENT_MIN_INK = 0.12

# Font stacks by family, so a typeface named in a template still renders when
# it is not installed where we render. A template naming Helvetica Neue on a
# Linux container is the normal case, not the exception.
FALLBACK_CHAINS: dict[str, tuple[str, ...]] = {
    "serif": ("Georgia", "Times New Roman", "Liberation Serif", "DejaVu Serif",
              "serif"),
    "sans": ("Arial", "Helvetica", "Liberation Sans", "DejaVu Sans", "sans-serif"),
    "mono": ("Consolas", "Courier New", "Liberation Mono", "DejaVu Sans Mono",
             "monospace"),
}

# Sizes within this many points of each other are one rung of the ramp.
RAMP_TOLERANCE_PT = 0.75
# A rung carrying less than this share of the deck's characters is noise - a
# stray superscript or a single oversized figure, not a step in the scale.
RAMP_MIN_SHARE = 0.01

# How many pages of a template to measure. Enough to see its range, few
# enough that uploading a long report does not stall the run.
MAX_PROFILE_PAGES = 24

# Families a Linux render host substitutes in for fonts it does not have.
# A page reporting one of these is reporting this container's font set, not
# the template's - see ``looks_substituted``.
SUBSTITUTE_FAMILIES: tuple[str, ...] = (
    "liberation", "dejavu", "nimbus", "noto", "urw", "free", "droid",
    "carlito", "caladea", "cantarell", "adwaita", "c059", "p052", "z003",
    "d050000l", "standard symbols",
)

# "LiberationSans" is a PostScript name, "Liberation Sans" is the family a
# font picker will match. The words are put back at every lower-to-upper
# boundary.
_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")


@dataclass
class DensityProfile:
    """How much a deck puts on a content page."""
    pages_measured: int = 0

    words_median: float = 0.0
    words_p25: float = 0.0
    words_p75: float = 0.0
    blocks_median: float = 0.0
    blocks_p25: float = 0.0
    blocks_p75: float = 0.0
    ink_median: float = 0.0

    # The shape of a dense page, where there is one: how many rows and columns
    # its grid runs to, and how many words a cell carries.
    grid_rows_median: float = 0.0
    grid_columns_median: float = 0.0
    words_per_block_median: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "pagesMeasured": self.pages_measured,
            "wordsPerSlide": {"median": self.words_median,
                              "p25": self.words_p25, "p75": self.words_p75},
            "blocksPerSlide": {"median": self.blocks_median,
                               "p25": self.blocks_p25, "p75": self.blocks_p75},
            "inkCoverageMedian": self.ink_median,
            "gridRowsMedian": self.grid_rows_median,
            "gridColumnsMedian": self.grid_columns_median,
            "wordsPerBlockMedian": self.words_per_block_median,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "DensityProfile":
        data = data or {}
        words = data.get("wordsPerSlide") or {}
        blocks = data.get("blocksPerSlide") or {}
        return cls(
            pages_measured=int(data.get("pagesMeasured") or 0),
            words_median=_number(words.get("median")),
            words_p25=_number(words.get("p25")),
            words_p75=_number(words.get("p75")),
            blocks_median=_number(blocks.get("median")),
            blocks_p25=_number(blocks.get("p25")),
            blocks_p75=_number(blocks.get("p75")),
            ink_median=_number(data.get("inkCoverageMedian")),
            grid_rows_median=_number(data.get("gridRowsMedian")),
            grid_columns_median=_number(data.get("gridColumnsMedian")),
            words_per_block_median=_number(data.get("wordsPerBlockMedian")),
        )

    def describe(self) -> str:
        """
        The budget, in a sentence a planner can be held to.

        A range rather than a number: the reference's own pages run from 138 to
        293 words, and a target of "208" would be a spurious precision that
        turns a design into a quota.

        The opening clause says where the figures came from, because the two
        cases carry different authority - measured pages are what the user's
        own template does, and the built-in numbers are a house style.
        """
        if not self.words_p75:
            return ""
        opening = (
            f"This template's own pages (measured, {self.pages_measured} of them)"
            if self.pages_measured
            else "Pages in this design"
        )
        return (
            f"{opening} carry {self.words_p25:.0f}-{self.words_p75:.0f} words "
            f"and {self.blocks_p25:.0f}-{self.blocks_p75:.0f} blocks each "
            f"(median {self.words_median:.0f} words, "
            f"{_plural(self.blocks_median, 'block')}), at about "
            f"{self.words_per_block_median:.0f} words per block"
        )


@dataclass
class TypeProfile:
    """A deck's full type ramp and the pairing it is set in."""
    ramp_pt: list[float] = field(default_factory=list)
    display_pt: Optional[float] = None
    body_pt: Optional[float] = None
    caption_pt: Optional[float] = None
    display_face: Optional[str] = None
    body_face: Optional[str] = None
    display_is_serif: bool = False
    display_fallbacks: list[str] = field(default_factory=list)
    body_fallbacks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rampPt": self.ramp_pt,
            "displayPt": self.display_pt,
            "bodyPt": self.body_pt,
            "captionPt": self.caption_pt,
            "displayFace": self.display_face,
            "bodyFace": self.body_face,
            "displayIsSerif": self.display_is_serif,
            "displayFallbacks": self.display_fallbacks,
            "bodyFallbacks": self.body_fallbacks,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "TypeProfile":
        data = data or {}
        return cls(
            ramp_pt=[_number(v) for v in data.get("rampPt") or []],
            display_pt=_optional(data.get("displayPt")),
            body_pt=_optional(data.get("bodyPt")),
            caption_pt=_optional(data.get("captionPt")),
            display_face=data.get("displayFace") or None,
            body_face=data.get("bodyFace") or None,
            display_is_serif=bool(data.get("displayIsSerif")),
            display_fallbacks=list(data.get("displayFallbacks") or []),
            body_fallbacks=list(data.get("bodyFallbacks") or []),
        )

    def as_typography(self) -> dict:
        """
        The three-role ``typography`` block, filled from the measured ramp.

        The bundle's existing shape is kept and populated rather than replaced:
        every consumer already reads titleFont/bodyFont/captionFont, and a
        template's real pairing is expressible in it once the *faces* differ.
        """
        return {
            "titleFont": {
                "typeface": self.display_face or "Arial",
                "sizePt": self.display_pt or 36,
                "bold": True,
                "fallbacks": self.display_fallbacks,
            },
            "bodyFont": {
                "typeface": self.body_face or "Arial",
                "sizePt": self.body_pt or 20,
                "bold": False,
                "fallbacks": self.body_fallbacks,
            },
            "captionFont": {
                "typeface": self.body_face or "Arial",
                "sizePt": self.caption_pt or 12,
                "bold": False,
                "fallbacks": self.body_fallbacks,
            },
            "rampPt": self.ramp_pt,
            "displayIsSerif": self.display_is_serif,
        }


def density_brief(design_system: Optional[dict]) -> str:
    """
    The measured density of the run's template, in one sentence, or "".

    Read by the planner and the content designer so both write against the
    same figure. It is deliberately a measurement rather than an instruction:
    a planner told "be dense" argues, and a planner told "this template's own
    pages carry 178-235 words" has something to aim at.
    """
    profile = DensityProfile.from_dict((design_system or {}).get("density"))
    return profile.describe()


def fallback_chain(face: Optional[str]) -> list[str]:
    """
    The stack to try for a face, starting with the face itself.

    A typeface named in a template is routinely not installed where we render,
    and a chain is the difference between "the deck's serif, or a serif" and
    "whatever the renderer defaults to".
    """
    name = (face or "").strip()
    lowered = name.lower()
    chain = [name] if name else []
    chain += [candidate for candidate in FALLBACK_CHAINS[family_of(name)]
              if candidate.lower() != lowered]
    return chain


def family_of(face: Optional[str]) -> str:
    """Which of the three stacks a face belongs to."""
    lowered = (face or "").strip().lower()
    if any(key in lowered for key in ("mono", "courier", "consolas")):
        return "mono"
    return "serif" if _is_serif(lowered) else "sans"


def preferred_face(face: Optional[str]) -> str:
    """
    The name worth writing into a generated deck, for a face we measured.

    A substituted name is this container's font, and putting it in the PPTX
    hands the user a deck naming a font their machine does not have either.
    The head of the substituted face's own family is a font that ships with
    Windows and macOS, which is the point of naming one at all.
    """
    name = (face or "").strip()
    if not name or not looks_substituted(name):
        return name
    return FALLBACK_CHAINS[family_of(name)][0]


def profile_pdf(pdf_path: str,
                pages: Optional[Sequence[int]] = None) -> tuple[DensityProfile,
                                                                TypeProfile]:
    """Measure a rendered template: how dense it is and what it is set in."""
    if pages is None:
        pages = sample_pages(_page_count(pdf_path))
    measured = measure_pdf(pdf_path, pages)
    structures = count_blocks_in_pdf(pdf_path, [m.page_number for m in measured])
    return build_profiles(measured, structures)


def profile_template(template_path: str) -> Optional[tuple[DensityProfile,
                                                           TypeProfile]]:
    """
    Measure whatever pages a template actually has.

    A PDF is already rendered. A PPTX is put through the same LibreOffice
    conversion the preview path uses, so what gets measured is what the
    template *looks like*, not what its XML claims - the two differ whenever a
    placeholder's size is inherited rather than declared.

    Returns None when there is nothing to learn: a .potx carrying layouts and
    no slides renders to a blank page or to nothing at all, and inventing a
    density for it would be worse than falling back to the default.
    """
    from app.measure.rendered import as_pdf

    with as_pdf(template_path) as pdf_path:
        if not pdf_path:
            return None
        return _learned(profile_pdf(pdf_path))


def _learned(profile: tuple[DensityProfile, TypeProfile]):
    """The profile, or None when the pages held neither copy nor type."""
    density, types = profile
    if not density.pages_measured and not types.ramp_pt:
        return None
    return profile


def sample_pages(total: int, limit: int = MAX_PROFILE_PAGES) -> list[int]:
    """
    Up to ``limit`` pages spread across a deck, as 1-based page numbers.

    Measuring every page of a 180-page report to learn its density is work
    spent on precision the answer does not have; an even spread sees the
    same range of layouts.
    """
    if total <= 0:
        return []
    if total <= limit:
        return list(range(1, total + 1))
    step = (total - 1) / (limit - 1)
    return sorted({round(index * step) + 1 for index in range(limit)})


def _page_count(pdf_path: str) -> int:
    from app.measure.rendered import page_count

    return page_count(pdf_path)


def build_profiles(pages: Sequence[PageMetrics],
                   structures: Sequence[PageStructure]) -> tuple[DensityProfile,
                                                                 TypeProfile]:
    density = DensityProfile()
    types = TypeProfile()
    if not pages:
        return density, types

    paired = list(zip(pages, list(structures) + [None] * len(pages)))
    content = [
        (page, structure) for page, structure in paired
        if page.word_count >= CONTENT_MIN_WORDS and page.ink_coverage >= CONTENT_MIN_INK
    ]
    if content:
        _measure_density(density, content)
    _measure_type(types, pages)
    return density, types


def _measure_density(profile: DensityProfile, content) -> None:
    words = sorted(page.word_count for page, _ in content)
    blocks = sorted(
        (structure.block_count if structure else page.block_count)
        for page, structure in content
    )
    inks = sorted(page.ink_coverage for page, _ in content)

    profile.pages_measured = len(content)
    profile.words_median = round(statistics.median(words), 1)
    profile.words_p25 = round(_percentile(words, 0.25), 1)
    profile.words_p75 = round(_percentile(words, 0.75), 1)
    profile.blocks_median = round(statistics.median(blocks), 1)
    profile.blocks_p25 = round(_percentile(blocks, 0.25), 1)
    profile.blocks_p75 = round(_percentile(blocks, 0.75), 1)
    profile.ink_median = round(statistics.median(inks), 4)

    rows = sorted(s.rows for _, s in content if s and s.rows)
    columns = sorted(s.columns for _, s in content if s and s.columns)
    if rows:
        profile.grid_rows_median = round(statistics.median(rows), 1)
    if columns:
        profile.grid_columns_median = round(statistics.median(columns), 1)

    per_block = sorted(
        page.word_count / structure.block_count
        for page, structure in content
        if structure and structure.block_count
    )
    if per_block:
        profile.words_per_block_median = round(statistics.median(per_block), 1)


def _measure_type(profile: TypeProfile, pages: Sequence[PageMetrics]) -> None:
    """
    The deck's full ramp, rather than three fixed roles.

    Rungs are the sizes that actually carry copy, weighted by how much of it
    they carry - a stray 5pt superscript is not a step in a type scale, and a
    ramp that includes it is a ramp nothing can be held to.
    """
    weights: dict[float, int] = {}
    for page in pages:
        for size, count in page.type_ramp.items():
            weights[float(size)] = weights.get(float(size), 0) + count
    total = sum(weights.values()) or 1

    # A rung is named by the size carrying most of its characters, not by
    # whichever member happened to be seen first: a body set overwhelmingly at
    # 14.0 with a little 14.4 rounding noise is a 14pt deck, and reporting it
    # as 14.4 puts the ramp out of step with the body size measured per page.
    rungs: list[list[float]] = []  # [dominant size, dominant count, total count]
    for size in sorted(weights, reverse=True):
        count = weights[size]
        if count / total < RAMP_MIN_SHARE:
            continue
        for rung in rungs:
            if abs(rung[0] - size) <= RAMP_TOLERANCE_PT:
                if count > rung[1]:
                    rung[0], rung[1] = size, count
                rung[2] += count
                break
        else:
            rungs.append([size, count, count])

    profile.ramp_pt = sorted((round(rung[0], 1) for rung in rungs), reverse=True)

    display_sizes = [p.display_size_pt for p in pages if p.display_size_pt]
    body_sizes = [p.body_size_pt for p in pages if p.body_size_pt]
    if display_sizes:
        profile.display_pt = round(statistics.median(display_sizes), 1)
    if body_sizes:
        profile.body_pt = round(statistics.median(body_sizes), 1)
    # The caption rung is the smallest one that is still *copy*. Below the
    # apparatus ceiling a rung is a footnote or a source line, which the
    # apparatus tokens size on their own - reading the reference's 7pt
    # footnotes as its caption size would set every caption at 7pt.
    body = profile.body_pt or 0.0
    copy_rungs = [size for size in profile.ramp_pt
                  if size > APPARATUS_MAX_PT and (not body or size < body)]
    if copy_rungs:
        profile.caption_pt = copy_rungs[-1]
    elif body:
        profile.caption_pt = round(body * 0.85, 1)
    elif profile.ramp_pt:
        profile.caption_pt = profile.ramp_pt[-1]

    display_faces = [p.display_face for p in pages if p.display_face]
    body_faces = [p.body_face for p in pages if p.body_face]
    if display_faces:
        profile.display_face = _clean_face(statistics.mode(display_faces))
    if body_faces:
        profile.body_face = _clean_face(statistics.mode(body_faces))
    profile.display_is_serif = _is_serif(profile.display_face or "")
    profile.display_fallbacks = fallback_chain(profile.display_face)
    profile.body_fallbacks = fallback_chain(profile.body_face)


def _clean_face(face: str) -> str:
    """
    A PDF's internal font name as the family a renderer can ask for.

    PDFs carry names like "ABCDEF+Georgia-Bold" or "LiberationSans"; a design
    system that records either has recorded something no font picker will
    match. The words are put back so the result is the family's real name -
    "LiberationSans" is not installed anywhere, "Liberation Sans" is.
    """
    name = str(face or "")
    if "+" in name:
        name = name.split("+", 1)[1]
    for suffix in ("-BoldItalic", "-BoldMT", "-Bold", "-ItalicMT", "-Italic",
                   "-Regular", "MT", "PS", "-Roman"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    name = name.replace("-", " ")
    if " " not in name:
        name = _CAMEL_BOUNDARY.sub(r"\1 \2", name)
    return " ".join(name.split()) or "Arial"


def looks_substituted(face: Optional[str]) -> bool:
    """
    Whether a measured face is one the render host swapped in.

    LibreOffice renders a deck with whatever fonts this container has, so a
    template set in Calibri comes back saying Liberation Sans and a template
    set in Georgia comes back saying Noto Serif. The *name* on such a page is
    evidence about this machine, not about the template - unlike the point
    size, which is not substituted, and unlike whether the face is a serif,
    which substitution preserves.
    """
    lowered = str(face or "").strip().lower()
    return any(lowered.startswith(family) for family in SUBSTITUTE_FAMILIES)


def resolve_face(declared: Optional[str], measured: Optional[str]) -> str:
    """
    The typeface to record, from what a template declared and what it renders.

    A theme's declared font is not always what its slides are set in - a deck
    can override it on every page - so a real name read off the rendered page
    wins. When the page only shows a substitute, the declaration wins instead,
    unless the two disagree about being a serif: substitution never changes
    that, so a disagreement means the declaration is not what the deck uses.
    """
    declared, measured = (declared or "").strip(), (measured or "").strip()
    if not measured:
        return declared
    if not declared:
        return measured
    if not looks_substituted(measured):
        return measured
    if _is_serif(declared) == _is_serif(measured):
        return declared
    return measured


def _plural(count: float, noun: str) -> str:
    return f"{count:.0f} {noun}" + ("" if round(count) == 1 else "s")


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    position = q * (len(values) - 1)
    low, high = int(position), min(len(values) - 1, int(position) + 1)
    if low == high:
        return float(values[low])
    return float(values[low] + (values[high] - values[low]) * (position - low))


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def default_density() -> dict:
    """
    The density the built-in design system targets.

    These are the *floor of the library*: the density of the least dense of
    the four decks the exemplar corpus calls excellent (McKinsey Technology
    Trends, design split - 208 words and 10 blocks a page at 20 words a
    block). A deck built with no template is not forced to any one house, but
    it is not allowed to sit below every house either.

    The numbers this replaced - 70 words in 4 blocks - said in their own
    docstring that they were "not the four-bullet slide either", and were
    exactly the four-bullet slide: a run against them produced 58 words in 5
    blocks, hit its budget, and was measured at 0.15 on blocks and 0.19 on
    words against an editorial-consulting register. The budget, not the
    model's willingness to write, was what held the page empty.

    A single column at this word count is a wall of text, so the grid widens
    with the budget: the reference sets three columns over three rows.
    """
    return DensityProfile(
        pages_measured=0,
        words_median=208, words_p25=178, words_p75=235,
        blocks_median=10, blocks_p25=7, blocks_p75=13,
        ink_median=0.497,
        grid_rows_median=3, grid_columns_median=3,
        words_per_block_median=20,
    ).to_dict()
