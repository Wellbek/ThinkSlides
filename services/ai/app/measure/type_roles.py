"""
Naming the rungs of a publication's type ramp.

A ramp on its own - "this deck sets 52, 26, 23.5, 21, 16 and 11 point" - is
not usable for generation. What a page needs to know is which of those is
the body, which is the headline, which is the small-caps line running across
the top, and which is the number hung in the margin. That is a question
about how each size is *used*, and use is measurable: where on the page it
appears, how often, how much of it there is, whether it is set in capitals,
whether it sits alone on its line.

Rule-based rather than a model call, for the same reason `grammar.py` gives:
a model asked to read measurements returns plausible measurements. Every
assignment here is a statement about counted evidence, and every rung keeps
the evidence that named it so a wrong reading can be argued with.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Sequence

_FOLIO_RE = re.compile(r"^\s*\d{1,3}\s*$")
_SERIF_HINTS = ("serif", "georgia", "times", "garamond", "minion", "caslon",
                "baskerville", "playfair", "merriweather", "cambria", "book")
# A rung carrying less of the page's text than this is furniture or an
# accident, not a role worth naming.
MIN_SHARE = 0.001
TOP_BAND = 0.14
BOTTOM_BAND = 0.84
# Body copy appears on nearly every page. Stated as a share rather than as
# "within one page of the most", which is far too generous on a short deck:
# a methodology appendix sets 42% of a magazine's characters on one page out
# of three, and a slack of one page is enough to read that as its body.
BODY_PAGE_SHARE = 0.8


@dataclass
class RungEvidence:
    """Everything counted about one size on the ramp."""

    size_pt: float
    chars: int = 0
    occurrences: int = 0
    lines: int = 0
    pages: set = field(default_factory=set)
    upper_chars: int = 0
    y_positions: list = field(default_factory=list)
    x_positions: list = field(default_factory=list)
    line_gaps: list = field(default_factory=list)
    fonts: dict = field(default_factory=lambda: defaultdict(int))
    colors: dict = field(default_factory=lambda: defaultdict(int))
    folio_like: int = 0

    @property
    def median_y(self) -> float:
        return _median(self.y_positions)

    @property
    def median_x(self) -> float:
        return _median(self.x_positions)

    @property
    def upper_share(self) -> float:
        return self.upper_chars / self.chars if self.chars else 0.0

    @property
    def lines_per_occurrence(self) -> float:
        return self.lines / self.occurrences if self.occurrences else 0.0

    @property
    def per_page(self) -> float:
        return self.occurrences / len(self.pages) if self.pages else 0.0

    @property
    def face(self) -> str:
        return max(self.fonts, key=lambda f: self.fonts[f]) if self.fonts else ""

    @property
    def color(self) -> str:
        return max(self.colors, key=lambda c: self.colors[c]) if self.colors else ""

    @property
    def line_height_pt(self) -> float:
        gap = _median(self.line_gaps)
        return gap if gap > 0 else round(self.size_pt * 1.3, 1)

    @property
    def is_serif(self) -> bool:
        lowered = self.face.lower()
        return any(hint in lowered for hint in _SERIF_HINTS)


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def read_rungs(pages) -> dict[float, RungEvidence]:
    """Count how every size on the ramp is used, across the given pages.

    ``pages`` is a sequence of ``(page_number, fitz_page)``.
    """
    rungs: dict[float, RungEvidence] = {}
    for number, page in pages:
        width = float(page.rect.width)
        height = float(page.rect.height)
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            by_size: dict[float, list] = defaultdict(list)
            for line in block.get("lines", []):
                spans = [s for s in line["spans"] if s["text"].strip()]
                if not spans:
                    continue
                size = round(max(s["size"] for s in spans), 1)
                by_size[size].append((line, spans))

            for size, lines in by_size.items():
                rung = rungs.setdefault(size, RungEvidence(size_pt=size))
                rung.occurrences += 1
                rung.pages.add(number)
                rung.lines += len(lines)
                baselines = []
                for line, spans in lines:
                    text = "".join(s["text"] for s in spans).strip()
                    letters = [c for c in text if c.isalpha()]
                    rung.chars += len(text)
                    rung.upper_chars += sum(1 for c in letters if c.isupper())
                    if _FOLIO_RE.match(text):
                        rung.folio_like += 1
                    x0, y0, x1, y1 = line["bbox"]
                    rung.y_positions.append(y0 / height)
                    rung.x_positions.append(x0 / width)
                    baselines.append(y1)
                    for span in spans:
                        rung.fonts[span["font"]] += len(span["text"])
                        rung.colors[f"#{span['color']:06X}"] += len(span["text"])
                baselines.sort()
                for first, second in zip(baselines, baselines[1:]):
                    gap = round(second - first, 1)
                    if 0 < gap < size * 3:
                        rung.line_gaps.append(gap)
    return rungs


def assign_roles(rungs: dict[float, RungEvidence]) -> dict[str, RungEvidence]:
    """Name each rung by how the publication uses it.

    The order matters: body is settled first because every other rule is
    stated relative to it, and a ramp read without knowing which rung is the
    body has no scale to reason on.
    """
    total_chars = sum(r.chars for r in rungs.values()) or 1
    usable = [r for r in rungs.values() if r.chars / total_chars >= MIN_SHARE]
    if not usable:
        return {}

    roles: dict[str, RungEvidence] = {}

    # Body: the rung the deck is mostly made of - but "most characters" on
    # its own is wrong, and wrong on two of the three references.
    #
    # An investor deck's legal disclaimers are 77% of its characters at 7pt,
    # sitting in a block at the foot of every page; a magazine's methodology
    # appendix is 42% of its characters at 7pt on one page out of three.
    # Reading either as body copy sets the whole pack's scale to a footnote.
    #
    # So: nothing in the apparatus band can be the body, and the body has to
    # appear on nearly every page. What survives both is read as usual, by
    # how much of the deck it sets.
    candidates = [r for r in usable if r.median_y < BOTTOM_BAND] or usable
    seen = set()
    for rung in usable:
        seen |= rung.pages
    needed = max(1, math.ceil(BODY_PAGE_SHARE * len(seen)))
    steady = [r for r in candidates if len(r.pages) >= needed] or candidates
    body = max(steady, key=lambda r: r.chars)
    roles["body"] = body

    remaining = [r for r in usable if r is not body]

    # Folio: a number, alone, in a margin. Named before anything else can
    # claim it, because a big number in a corner otherwise reads as a
    # headline.
    folio = next(
        (r for r in remaining
         if r.folio_like >= max(1, r.occurrences * 0.6)
         and (r.median_y <= TOP_BAND or r.median_y >= BOTTOM_BAND)),
        None,
    )
    if folio is not None:
        roles["folio"] = folio
        remaining.remove(folio)

    # Headline: the largest rung that is set in short bursts. Size alone is
    # not enough - a deck with one enormous cover word would otherwise name
    # that its headline role for every page.
    headline = max(
        (r for r in remaining
         if r.size_pt > body.size_pt * 1.3 and r.lines_per_occurrence <= 4.0),
        key=lambda r: r.size_pt,
        default=None,
    )
    if headline is not None:
        roles["headline"] = headline
        remaining.remove(headline)

    # Eyebrow: small, capitalised, at the top of the page, a line at a time.
    eyebrow = next(
        (r for r in remaining
         if r.size_pt <= body.size_pt * 1.05
         and r.upper_share >= 0.6
         and r.median_y <= TOP_BAND),
        None,
    )
    if eyebrow is not None:
        roles["eyebrow"] = eyebrow
        remaining.remove(eyebrow)

    # Pull-quote: bigger than body, short, and usually set in the other face.
    pullquote = next(
        (r for r in sorted(remaining, key=lambda r: -r.size_pt)
         if body.size_pt * 1.25 <= r.size_pt <= body.size_pt * 2.6
         and r.lines_per_occurrence <= 8.0
         and (r.is_serif != body.is_serif or r.chars < total_chars * 0.06)),
        None,
    )
    if pullquote is not None:
        roles["pullquote"] = pullquote
        remaining.remove(pullquote)

    # Standfirst: between body and headline, longer than a pull-quote.
    standfirst = next(
        (r for r in sorted(remaining, key=lambda r: -r.size_pt)
         if body.size_pt * 1.1 < r.size_pt < body.size_pt * 2.2),
        None,
    )
    if standfirst is not None:
        roles["standfirst"] = standfirst
        remaining.remove(standfirst)

    # Footnote: small, and low on the page.
    footnote = next(
        (r for r in sorted(remaining, key=lambda r: r.size_pt)
         if r.size_pt <= body.size_pt * 0.8 and r.median_y >= BOTTOM_BAND),
        None,
    )
    if footnote is not None:
        roles["footnote"] = footnote
        remaining.remove(footnote)

    # Caption: whatever small rung is left.
    caption = next(
        (r for r in sorted(remaining, key=lambda r: -r.chars)
         if r.size_pt <= body.size_pt),
        None,
    )
    if caption is not None:
        roles["caption"] = caption

    return roles


def describe(roles: dict[str, RungEvidence]) -> str:
    """The reading, with the evidence that produced it, for auditing."""
    lines = ["| role | size | line height | share | upper | y | per page | face |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    total = sum(r.chars for r in roles.values()) or 1
    for name, rung in sorted(roles.items(), key=lambda kv: -kv[1].size_pt):
        lines.append(
            f"| {name} | {rung.size_pt:.1f} | {rung.line_height_pt:.1f} "
            f"| {rung.chars / total:.1%} | {rung.upper_share:.0%} "
            f"| {rung.median_y:.2f} | {rung.per_page:.1f} | {rung.face} |"
        )
    return "\n".join(lines)


def faces_for(roles: dict[str, RungEvidence]) -> tuple[str, str]:
    """The body face and the display face, taken from the roles themselves.

    Derived from the assigned roles rather than guessed from the ramp: a
    publication whose headline is set in the same family as its body has one
    face and should say so, and the serif it reserves for pull-quotes is a
    pairing rather than a display face.
    """
    body = roles.get("body")
    body_face = body.face if body else ""
    display = roles.get("headline") or roles.get("standfirst")
    return (body_face, display.face if display else body_face)


def pairing_face(roles: dict[str, RungEvidence]) -> str:
    """The face a deck reserves for quotes, when it reserves one.

    EY sets Georgia for its pull-quotes and its standfirst and EY Interstate
    for everything else. That pairing is a large part of how the page reads,
    and it is invisible to a reading that only asks for "the display face".
    """
    body = roles.get("body")
    if body is None:
        return ""
    for name in ("pullquote", "standfirst"):
        rung = roles.get(name)
        if rung and rung.face and clean_face(rung.face) != clean_face(body.face):
            return rung.face
    return ""


def clean_face(name: str) -> str:
    """A PostScript font name reduced to the family a PPTX should ask for."""
    if not name:
        return ""
    family = name.split("+")[-1]
    family = re.sub(r"[-,](Light|Regular|Book|Medium|Semibold|Bold|Italic|"
                    r"Oblique|Roman|Pro|MT|LightBold)+$", "", family)
    family = re.sub(r"(Light|Regular|Book|Medium|Semibold|Bold|Italic|Pro)$",
                    "", family)
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", family).strip() or name
