"""
What a slide states, and the span it came from.

A deck can be beautiful and unsourced. This is the other axis, and the one a
source-grounded generator is actually defined by: a reader should be able to
take a figure off a page and find the sentence it came from.

Nothing tied the two together. `analyze_document` and `web_search` both
existed, pgvector was in the stack, and none of it survived into a slide - so
"which sentence of which source does this number come from" was unanswerable,
and a pass-3 deck stated Gartner and IDC figures with no attribution anywhere.

Two decisions worth stating.

**A figure is checked mechanically, not judged.** Whether "61%" appears in a
source span is a fact, and asking a model to grade its own sourcing is exactly
the kind of check that passes when it should fail. What is left to judgement -
whether the span *means* what the slide says it means - is not claimed here.

**A year is not a claim.** "in 2026" is a number and needs no source, and a
checker that demanded one would flag every deck ever written and be switched
off within a day. What needs a source is a measurement: a percentage, an
amount of money, a multiple, a count large enough that nobody would assert it
from memory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

# What counts as a figure that has to be sourced. Deliberately narrow: the
# cost of a false positive is a checker nobody trusts.
_PERCENT = re.compile(r"\d+(?:\.\d+)?\s?%")
_MONEY = re.compile(
    r"[$£€¥]\s?\d+(?:[\d,.]*)\s?(?:bn|billion|m|million|k|thousand|tn|trillion)?",
    re.IGNORECASE,
)
_MULTIPLE = re.compile(r"\b\d+(?:\.\d+)?\s?x\b", re.IGNORECASE)
_SCALED = re.compile(
    r"\b\d+(?:\.\d+)?\s?(?:bn|billion|m|million|k|thousand|tn|trillion)\b",
    re.IGNORECASE,
)
# A bare number big enough that nobody asserts it from memory. Four digits and
# up, which lets a year through - handled below - and catches 12,400.
_LARGE = re.compile(r"\b\d{1,3}(?:,\d{3})+\b|\b\d{4,}\b")

_YEAR = re.compile(r"^(?:19|20)\d{2}$")

# How much of a figure's surrounding words a span has to share before it is
# read as supporting it rather than merely containing the same number.
CONTEXT_WORDS = 4
MIN_CONTEXT_OVERLAP = 0.25


@dataclass(frozen=True)
class Span:
    """A passage of a source, locatable by a human."""

    text: str
    source_id: str
    source_title: str = ""
    # Where in the document it is. Absent for a web result, whose locator is
    # its url.
    start: Optional[int] = None
    end: Optional[int] = None

    @property
    def locator(self) -> str:
        if self.start is not None and self.end is not None:
            return f"{self.source_id}#{self.start}-{self.end}"
        return self.source_id


@dataclass(frozen=True)
class Claim:
    """One stated figure, and the span behind it."""

    figure: str
    statement: str
    span: Span
    # How the support was established. `exact` means the figure appears in the
    # span verbatim; nothing here claims the span *means* what the slide says.
    basis: str = "exact"

    def to_dict(self) -> dict:
        return {
            "figure": self.figure,
            "statement": self.statement,
            "quote": self.span.text,
            "sourceId": self.span.source_id,
            "sourceTitle": self.span.source_title,
            "locator": self.span.locator,
            "basis": self.basis,
        }


@dataclass
class Evidence:
    """Every span a run has to support its claims with."""

    spans: tuple[Span, ...] = ()

    @classmethod
    def from_research(cls, findings: Optional[Sequence[dict]]) -> "Evidence":
        """Spans from what the run looked up.

        A search result's snippet is the span: it is the passage the backend
        returned as its evidence, and it is what a reader following the url
        will look for.
        """
        spans = []
        for finding in findings or ():
            if not isinstance(finding, dict):
                continue
            url = str(finding.get("url") or "").strip()
            snippet = str(finding.get("snippet") or "").strip()
            if not url or not snippet:
                continue
            spans.append(Span(
                text=snippet, source_id=url,
                source_title=str(finding.get("title") or "").strip() or url,
            ))
        return cls(spans=tuple(spans))

    @classmethod
    def from_document(cls, text: str, *, source_id: str, title: str = "",
                      window: int = 400) -> "Evidence":
        """Spans from an uploaded document, each one locatable by offset.

        Split on sentences and grown to a window, so a figure and the words
        that qualify it stay together: a span of "61" is not evidence of
        anything.
        """
        spans = []
        for match in re.finditer(r"[^.!?\n]+[.!?]?", text):
            sentence = match.group().strip()
            if not sentence:
                continue
            start = max(0, match.start() - window // 2)
            end = min(len(text), match.end() + window // 2)
            spans.append(Span(
                text=text[start:end].strip(), source_id=source_id,
                source_title=title or source_id, start=start, end=end,
            ))
        return cls(spans=tuple(spans))

    def __add__(self, other: "Evidence") -> "Evidence":
        return Evidence(spans=self.spans + other.spans)


def _normalise(figure: str) -> str:
    return re.sub(r"[\s,]", "", figure).lower().rstrip(".")


def figures_in(text: str) -> list[str]:
    """Every figure in this text that a reader would expect a source for."""
    found: list[str] = []
    taken: list[tuple[int, int]] = []
    # Most specific first, so "$4.2bn" is claimed before the plain-scaled
    # pattern can offer "4.2bn" for the same characters. A figure overlapping
    # one already found is the same figure read less completely.
    for pattern in (_MONEY, _PERCENT, _MULTIPLE, _SCALED, _LARGE):
        for match in pattern.finditer(text or ""):
            figure = match.group().strip()
            bare = re.sub(r"[^\d]", "", figure)
            if not bare:
                continue
            if _YEAR.match(figure.strip()) and pattern is _LARGE:
                # A year is a number and not a measurement. Demanding a source
                # for "in 2026" would flag every deck ever written.
                continue
            if any(match.start() < end and start < match.end()
                   for start, end in taken):
                continue
            taken.append((match.start(), match.end()))
            found.append(figure)
    return found


def _context(text: str, figure: str) -> set[str]:
    """The words either side of a figure, lowercased."""
    index = text.lower().find(figure.lower())
    if index < 0:
        return set()
    before = re.findall(r"[a-z]{3,}", text[:index].lower())[-CONTEXT_WORDS:]
    after = re.findall(r"[a-z]{3,}", text[index + len(figure):].lower())[:CONTEXT_WORDS]
    return set(before) | set(after)


def support_for(figure: str, statement: str,
                evidence: Evidence) -> Optional[Claim]:
    """The span behind this figure, or None when nothing carries it.

    A span has to contain the figure *and* share some of the words around it.
    Without the second half, "61%" on a slide about cyber risk is "supported"
    by a span about 61% of something else entirely, which is worse than no
    check at all - it puts a source line under a number the source does not
    make.
    """
    wanted = _normalise(figure)
    context = _context(statement, figure)
    best: Optional[tuple[float, Span]] = None
    for span in evidence.spans:
        haystack = _normalise(span.text)
        if wanted not in haystack:
            continue
        if not context:
            overlap = 1.0
        else:
            shared = context & set(re.findall(r"[a-z]{3,}", span.text.lower()))
            overlap = len(shared) / len(context)
        if overlap < MIN_CONTEXT_OVERLAP:
            continue
        if best is None or overlap > best[0]:
            best = (overlap, span)
    if best is None:
        return None
    return Claim(figure=figure, statement=statement, span=best[1])


def claims_for(statement: str, evidence: Evidence) -> tuple[list[Claim], list[str]]:
    """Every figure in this statement, split into supported and not."""
    supported: list[Claim] = []
    unsupported: list[str] = []
    for figure in figures_in(statement):
        claim = support_for(figure, statement, evidence)
        if claim is None:
            unsupported.append(figure)
        else:
            supported.append(claim)
    return supported, unsupported
