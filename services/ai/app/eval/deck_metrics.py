"""
Aggregate a deck's pages into the profile the scorecard compares.

A profile is the deck-level shape of everything ``page_metrics`` measures per
page: how dense the pages are and how much that varies, what type ramp and
palette the deck actually rendered, how consistently it carries chrome and
apparatus, and how its charts are drawn. Reference decks and generated decks
produce the same structure, which is what makes a comparison meaningful.

The substance block is the part that filler cannot game, and it is deliberate
that it lives at deck level: whether a headline states a finding, and whether
two slides say different things, are not properties of a page in isolation.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Optional, Sequence

from app.eval.page_metrics import PageMetrics, measure_pdf

# A page carrying less than this much ink is a cover, a divider or a blank -
# real pages, but not evidence about how dense the deck's content pages are.
CONTENT_PAGE_MIN_INK = 0.12
# ...and one carrying fewer words than this is a section break.
CONTENT_PAGE_MIN_WORDS = 25

# Words that turn a topic label into a claim. Kept small and boring on
# purpose: the check has to be reproducible and explainable, and a large
# hand-tuned lexicon is neither.
_CLAIM_VERBS = frozenset("""
is are was were has have had will would can could should must does do did
grew grows growing rose rises rising fell falls falling drove drives driving
led leads leading gained gains lost loses cut cuts doubled tripled halved
increased increases decreased decreases outpaced outperformed shifted shifts
recorded reached reaches remains remained stayed accounts accounted expands
expanded contracted delivers delivered enables enabled requires required
depends depended exceeds exceeded lags lagged trails trailed adds added
""".split())
_COMPARATIVES = frozenset("""
more less higher lower faster slower bigger smaller greater fewer better worse
most least highest lowest fastest slowest largest smallest best worst than
versus vs while whereas despite although but
""".split())
_QUANTITY_RE = re.compile(r"(\d|\bhalf\b|\bthird\b|\bquarter\b|\bdouble\b|\btriple\b)",
                          re.IGNORECASE)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’\-]+")
_NUMBER_RE = re.compile(r"(?<![\w.])[$€£]?\d[\d,.]*\s?(%|bn|billion|million|m\b|k\b|x\b)?")

# Words too common to say anything about whether two slides differ.
_STOPWORDS = frozenset("""
the a an and or of to in for on with by from as at is are was were be been
this that these those it its their our your his her they we you i not no
than then so such more most can could will would should may might must
""".split())


@dataclass
class DeckProfile:
    """The deck-level shape of a rendered deck."""
    name: str
    page_count: int = 0
    content_page_count: int = 0

    ink_mean: float = 0.0
    ink_median: float = 0.0
    ink_p10: float = 0.0
    ink_p90: float = 0.0
    blocks_mean: float = 0.0
    blocks_median: float = 0.0
    words_mean: float = 0.0
    words_median: float = 0.0
    words_stdev: float = 0.0

    type_ramp: dict[str, int] = field(default_factory=dict)
    display_size_pt: Optional[float] = None
    body_size_pt: Optional[float] = None
    display_body_ratio: Optional[float] = None
    display_is_serif_fraction: float = 0.0
    faces: dict[str, int] = field(default_factory=dict)

    grounds: dict[str, float] = field(default_factory=dict)
    text_colors: dict[str, int] = field(default_factory=dict)
    ground_split_fraction: float = 0.0

    eyebrow_fraction: float = 0.0
    page_number_fraction: float = 0.0
    brand_mark_fraction: float = 0.0
    footnote_fraction: float = 0.0
    source_line_fraction: float = 0.0
    superscript_total: int = 0

    chart_count: int = 0
    figure_count: int = 0
    charts_per_page: float = 0.0
    chart_gridline_fraction: float = 0.0
    chart_vertical_axis_fraction: float = 0.0

    claim_title_fraction: float = 0.0
    question_title_fraction: float = 0.0
    label_title_fraction: float = 0.0
    informative_title_fraction: float = 0.0
    distinct_content: float = 0.0
    lexical_variety: float = 0.0
    sourced_figure_fraction: float = 0.0

    signatures: list[list[float]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeckProfile":
        known = {f for f in cls.__dataclass_fields__}          # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


def profile_pdf(pdf_path: str, name: str,
                pages: Optional[Iterable[int]] = None) -> DeckProfile:
    """Measure a PDF and reduce it to a deck profile."""
    measured = measure_pdf(pdf_path, pages)
    texts = _page_texts(pdf_path, [m.page_number for m in measured])
    return build_profile(name, measured, texts)


def build_profile(name: str, pages: Sequence[PageMetrics],
                  texts: Optional[dict[int, dict[str, Any]]] = None) -> DeckProfile:
    profile = DeckProfile(name=name, page_count=len(pages))
    if not pages:
        return profile

    content = [
        page for page in pages
        if page.ink_coverage >= CONTENT_PAGE_MIN_INK
        and page.word_count >= CONTENT_PAGE_MIN_WORDS
    ] or list(pages)
    profile.content_page_count = len(content)

    inks = sorted(page.ink_coverage for page in content)
    blocks = [page.block_count for page in content]
    words = [page.word_count for page in content]

    profile.ink_mean = round(statistics.fmean(inks), 4)
    profile.ink_median = round(statistics.median(inks), 4)
    profile.ink_p10 = round(_percentile(inks, 0.10), 4)
    profile.ink_p90 = round(_percentile(inks, 0.90), 4)
    profile.blocks_mean = round(statistics.fmean(blocks), 3)
    profile.blocks_median = round(statistics.median(blocks), 3)
    profile.words_mean = round(statistics.fmean(words), 2)
    profile.words_median = round(statistics.median(words), 2)
    profile.words_stdev = round(statistics.pstdev(words), 2)

    ramp: dict[str, int] = {}
    faces: dict[str, int] = {}
    text_colors: dict[str, int] = {}
    grounds: dict[str, float] = {}
    for page in pages:
        for size, count in page.type_ramp.items():
            ramp[size] = ramp.get(size, 0) + count
        for face, count in page.faces.items():
            faces[face] = faces.get(face, 0) + count
        for color, count in page.text_colors.items():
            text_colors[color] = text_colors.get(color, 0) + count
        for color, fraction in page.grounds:
            grounds[color] = grounds.get(color, 0.0) + fraction

    total_ground = sum(grounds.values()) or 1.0
    profile.type_ramp = dict(sorted(ramp.items(), key=lambda kv: float(kv[0])))
    profile.faces = dict(sorted(faces.items(), key=lambda kv: -kv[1])[:8])
    profile.text_colors = dict(sorted(text_colors.items(), key=lambda kv: -kv[1])[:8])
    profile.grounds = {
        color: round(weight / total_ground, 4)
        for color, weight in sorted(grounds.items(), key=lambda kv: -kv[1])[:6]
    }

    display_sizes = [p.display_size_pt for p in content if p.display_size_pt]
    body_sizes = [p.body_size_pt for p in content if p.body_size_pt]
    if display_sizes:
        profile.display_size_pt = round(statistics.median(display_sizes), 1)
    if body_sizes:
        profile.body_size_pt = round(statistics.median(body_sizes), 1)
    if profile.display_size_pt and profile.body_size_pt:
        profile.display_body_ratio = round(
            profile.display_size_pt / profile.body_size_pt, 3
        )
    profile.display_is_serif_fraction = round(
        _fraction(p.display_is_serif for p in content), 4
    )

    profile.ground_split_fraction = round(_fraction(p.has_ground_split for p in content), 4)
    profile.eyebrow_fraction = round(_fraction(p.has_eyebrow for p in content), 4)
    profile.page_number_fraction = round(_fraction(p.has_page_number for p in content), 4)
    profile.brand_mark_fraction = round(_fraction(p.has_brand_mark for p in content), 4)
    profile.footnote_fraction = round(_fraction(p.has_footnote_block for p in content), 4)
    profile.source_line_fraction = round(_fraction(p.has_source_line for p in content), 4)
    profile.superscript_total = sum(p.superscript_markers for p in pages)

    charts = [c for page in pages for c in page.charts if c.kind == "chart"]
    profile.chart_count = len(charts)
    profile.figure_count = sum(
        1 for page in pages for c in page.charts if c.kind == "figure"
    )
    profile.charts_per_page = round(len(charts) / max(1, len(pages)), 3)
    if charts:
        profile.chart_gridline_fraction = round(
            _fraction(c.has_horizontal_gridlines for c in charts), 4
        )
        profile.chart_vertical_axis_fraction = round(
            _fraction(c.has_vertical_axis for c in charts), 4
        )

    profile.signatures = [page.signature for page in pages]

    if texts:
        _measure_substance(profile, pages, texts)
    return profile


# --------------------------------------------------------------------------
# Substance - the measures density cannot fake
# --------------------------------------------------------------------------

def _page_texts(pdf_path: str, page_numbers: Sequence[int]) -> dict[int, dict[str, Any]]:
    """
    Per page: its headline (the largest type on the page) and its body copy.

    Read from the rendered PDF rather than from slide JSON on purpose - it is
    the only reading that applies equally to the reference deck and to our
    own output, and it is what a reader actually gets.
    """
    import fitz

    out: dict[int, dict[str, Any]] = {}
    doc = fitz.open(pdf_path)
    try:
        for page_number in page_numbers:
            if not 1 <= page_number <= len(doc):
                continue
            page = doc[page_number - 1]
            lines: list[tuple[float, float, str]] = []
            body: list[str] = []
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    spans = [s for s in line.get("spans", []) if (s.get("text") or "").strip()]
                    if not spans:
                        continue
                    text = "".join(s["text"] for s in spans).strip()
                    size = max(float(s.get("size", 0.0)) for s in spans)
                    top = float(line.get("bbox", (0, 0, 0, 0))[1])
                    lines.append((size, top, text))
                    body.append(text)
            headline = ""
            if lines:
                biggest = max(size for size, _, _ in lines)
                headline = " ".join(
                    text for size, _, text in sorted(lines, key=lambda t: t[1])
                    if size >= biggest - 0.6
                ).strip()
            out[page_number] = {"headline": headline, "body": " ".join(body)}
    finally:
        doc.close()
    return out


def title_states_a_claim(title: str) -> bool:
    """
    True when a headline states a finding rather than naming a topic.

    A claim needs a verb, and it needs something to compare or to count -
    "Revenue" is a topic, "Revenue grew 12% on price" is a claim. A question
    is neither: the reference deck's chapter grammar uses interrogatives
    deliberately, so they are not counted as claims and not counted against
    the deck either (see ``_measure_substance``).
    """
    words = [w.lower() for w in _WORD_RE.findall(title or "")]
    if len(words) < 3:
        return False
    has_verb = any(w in _CLAIM_VERBS for w in words)
    has_comparison = any(w in _COMPARATIVES for w in words)
    has_quantity = bool(_QUANTITY_RE.search(title or ""))
    return has_verb and (has_comparison or has_quantity)


def headline_register(title: str) -> str:
    """
    Which register a headline is in: ``claim``, ``question`` or ``label``.

    The reference deck uses two of these deliberately - a fixed interrogative
    series as its chapter grammar, and an action title on any page carrying a
    finding - and almost never the third. A bare topic label is what a deck
    falls back to when it has nothing to say, which is exactly why the share
    of *non-label* headlines is a substance measure rather than a style one:
    padding a slide with filler cannot turn "Revenue" into a headline that
    states something.
    """
    text = (title or "").strip()
    if not text:
        return "label"
    if text.rstrip().endswith("?"):
        return "question"
    return "claim" if title_states_a_claim(text) else "label"


def _measure_substance(profile: DeckProfile, pages: Sequence[PageMetrics],
                       texts: dict[int, dict[str, Any]]) -> None:
    content_numbers = [
        page.page_number for page in pages
        if page.ink_coverage >= CONTENT_PAGE_MIN_INK
        and page.word_count >= CONTENT_PAGE_MIN_WORDS
    ]
    if not content_numbers:
        return

    headlines = [
        (texts.get(n) or {}).get("headline", "") for n in content_numbers
    ]
    registers = [headline_register(h) for h in headlines if h]
    if registers:
        profile.claim_title_fraction = round(
            sum(1 for r in registers if r == "claim") / len(registers), 4
        )
        profile.question_title_fraction = round(
            sum(1 for r in registers if r == "question") / len(registers), 4
        )
        profile.label_title_fraction = round(
            sum(1 for r in registers if r == "label") / len(registers), 4
        )
        profile.informative_title_fraction = round(
            1.0 - profile.label_title_fraction, 4
        )

    word_sets: list[set[str]] = []
    all_words: list[str] = []
    for number in content_numbers:
        body = (texts.get(number) or {}).get("body", "")
        words = [w.lower() for w in _WORD_RE.findall(body) if w.lower() not in _STOPWORDS]
        all_words.extend(words)
        word_sets.append(set(words))

    overlaps: list[float] = []
    for i in range(len(word_sets)):
        for j in range(i + 1, len(word_sets)):
            union = word_sets[i] | word_sets[j]
            if not union:
                continue
            overlaps.append(len(word_sets[i] & word_sets[j]) / len(union))
    profile.distinct_content = round(1.0 - statistics.fmean(overlaps), 4) if overlaps else 1.0
    profile.lexical_variety = round(
        len(set(all_words)) / len(all_words), 4
    ) if all_words else 0.0

    pages_by_number = {page.page_number: page for page in pages}
    with_figures = 0
    sourced = 0
    for number in content_numbers:
        body = (texts.get(number) or {}).get("body", "")
        if not _NUMBER_RE.search(body):
            continue
        with_figures += 1
        page = pages_by_number.get(number)
        if page and (page.has_source_line or page.has_footnote_block
                     or page.superscript_markers):
            sourced += 1
    profile.sourced_figure_fraction = round(
        sourced / with_figures, 4
    ) if with_figures else 0.0


# --------------------------------------------------------------------------

def _fraction(flags: Iterable[bool]) -> float:
    values = list(flags)
    return sum(1 for v in values if v) / len(values) if values else 0.0


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = q * (len(sorted_values) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(sorted_values[int(position)])
    return float(
        sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)
    )
