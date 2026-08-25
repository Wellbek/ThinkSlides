"""
Whether a deck's headlines add up to an argument.

`titles.py` decides what register one headline is in and whether it earns it.
Nothing looked at them together, so a deck could pass every per-page check and
still be a list of unrelated topics - which is what "Market Analysis
Presentation" followed by "Key Aspects of the Market" is.

The property this checks is the one `titles.py` already states and nothing
enforced: *a reader who reads only the headlines has read the argument*.

What is checked here is the **shape** of an argument, not its truth. Whether a
deck is right is not a thing code can decide, and pretending otherwise would
produce a number people would quote. What code can see is whether the deck
opens on something at stake, whether its pages carry findings rather than
topic labels, whether consecutive pages are about the same subject, and
whether it arrives somewhere. A deck failing those is not making an argument
whatever its contents; a deck passing them might still be wrong.

Each finding names the page, so the answer to "this deck does not argue
anything" is a page to rewrite rather than a grade.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from app import titles

# How much of a deck's body has to state a finding before the headlines read
# as an argument rather than as a table of contents. Not all of it: a divider
# and a cover are labels on purpose, and a deck of nothing but claims reads as
# a shouting match.
MIN_CLAIM_SHARE = 0.5

# What a page has to share with something the deck has already said. Any word
# at all, and that is where the evidence puts it rather than where it would be
# tidy to put it. Measured across a well-argued deck and a deck of unrelated
# topics, the best overlap per page came out at 0.27, 0.07, 0.08 against
# 0.00, 0.00, 0.00: the signal separating them is zero versus non-zero, and a
# bar set anywhere above that flags an argument for the crime of developing.
MIN_CONTINUITY = 0.0

# Above this, two headlines are not developing an argument, they are the same
# page written twice.
MAX_CONTINUITY = 0.85

_STOP = frozenset("""
a an and are as at be by for from has have how in into is it its of on or
that the their there these this to was were what when where which who why
will with we you our your
""".split())


@dataclass(frozen=True)
class Break:
    """One place the argument does not hold, and what would fix it."""

    slide_index: int
    property: str
    observed: str
    expected: str

    def describe(self) -> str:
        return (f"slide {self.slide_index + 1}: {self.observed}; "
                f"expected {self.expected}")


@dataclass(frozen=True)
class ArcReport:
    """What the deck's headlines do, read end to end."""

    headlines: tuple[str, ...]
    claim_share: float
    breaks: tuple[Break, ...] = ()

    @property
    def argues(self) -> bool:
        return not self.breaks

    def to_dict(self) -> dict:
        return {
            "argues": self.argues,
            "claimShare": round(self.claim_share, 3),
            "breaks": [
                {"slideIndex": b.slide_index, "property": b.property,
                 "observed": b.observed, "expected": b.expected}
                for b in self.breaks
            ],
        }


def _words(headline: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z'\-]+", (headline or "").lower())
            if w not in _STOP and len(w) > 2}


def continuity(one: str, two: str) -> float:
    """How much subject matter two headlines share, 0 to 1."""
    first, second = _words(one), _words(two)
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def _is_body(slide: dict) -> bool:
    """Whether this page is one the argument is made on.

    A cover, a divider and a closing page are labels by design, and holding
    them to the claim standard would report a defect in every well-made deck.
    """
    role = str(slide.get("role") or "").strip()
    if role in ("cover", "section", "closing", "appendix"):
        return False
    content_type = str(slide.get("contentType") or "").strip()
    return content_type not in ("title", "section", "conclusion")


def headline_of(slide: dict) -> str:
    """This page's headline, from a plan entry or a designed slide."""
    for key in ("title", "heading"):
        value = str(slide.get(key) or "").strip()
        if value:
            return value
    for block in slide.get("contents") or ():
        if isinstance(block, dict) and block.get("type") == "text":
            for run in block.get("runs") or ():
                if isinstance(run, dict) and str(run.get("text") or "").strip():
                    return str(run["text"]).strip()
    return ""


def read_arc(slides: Sequence[dict]) -> ArcReport:
    """Read a plan or a drafted deck as an argument, and say where it breaks."""
    pages = [s for s in slides if isinstance(s, dict)]
    headlines = tuple(headline_of(s) for s in pages)
    body = [(index, s) for index, s in enumerate(pages) if _is_body(s)]
    breaks: list[Break] = []

    if not body:
        return ArcReport(headlines=headlines, claim_share=0.0)

    claims = [index for index, slide in body
              if titles.states_a_claim(headline_of(slide))]
    share = len(claims) / len(body)
    if share < MIN_CLAIM_SHARE:
        first = body[0][0]
        breaks.append(Break(
            slide_index=first, property="claims",
            observed=f"{len(claims)} of {len(body)} pages state a finding",
            expected=f"at least {int(MIN_CLAIM_SHARE * 100)}% of them; a deck "
                     f"of topic labels is a table of contents",
        ))

    opening = body[0]
    if not (titles.states_a_claim(headline_of(opening[1]))
            or titles.is_question(headline_of(opening[1]))):
        breaks.append(Break(
            slide_index=opening[0], property="opening",
            observed=f"opens on {headline_of(opening[1])!r}",
            expected="a question or a finding, so the deck starts on "
                     "something at stake",
        ))

    # The deck's *last* page, not the last body page. A conclusion is excluded
    # from the body on purpose - it is not where the argument is made - and
    # checking the body's last page for a conclusion then contradicts that,
    # reporting a defect in a deck that does arrive somewhere.
    last_index = len(pages) - 1
    last = pages[last_index]
    if len(pages) > 1 and not titles.states_a_claim(headline_of(last)):
        breaks.append(Break(
            slide_index=last_index, property="closing",
            observed=f"ends on {headline_of(last)!r}",
            expected="a position, so the deck arrives somewhere",
        ))

    # Against everything before it, not against the page immediately before.
    # An argument *develops*: a deck that moves from cyber exposure to what
    # regulators now demand has changed subject deliberately, and a pairwise
    # check calls that a break while calling a deck that repeats itself
    # coherent. What is a genuine non-sequitur is a page connected to nothing
    # the deck has said so far.
    for position, (index, slide) in enumerate(body):
        if position == 0:
            continue
        headline = headline_of(slide)
        earlier = [headline_of(s) for _, s in body[:position]]
        best = max((continuity(headline, previous) for previous in earlier),
                   default=0.0)
        if best <= MIN_CONTINUITY:
            breaks.append(Break(
                slide_index=index, property="continuity",
                observed=f"{headline!r} connects to nothing the deck has "
                         f"said so far",
                expected="a page that follows from what came before",
            ))
        elif continuity(headline, earlier[-1]) > MAX_CONTINUITY:
            breaks.append(Break(
                slide_index=index, property="repetition",
                observed=f"{headline!r} restates the page before it",
                expected="a page that moves the argument on",
            ))

    return ArcReport(headlines=headlines, claim_share=share,
                     breaks=tuple(breaks))


def guidance(report: Optional[ArcReport]) -> str:
    """What to tell the planner about the arc it just produced.

    Written as the specific pages to fix rather than as a grade, because a
    grade is not something a model can act on.
    """
    if report is None or report.argues:
        return ""
    lines = ["The outline does not yet read as an argument:"]
    lines.extend(f"- {b.describe()}" for b in report.breaks[:5])
    lines.append(
        "Rewrite those headlines so a reader who reads only the headlines has "
        "read the argument. Each one should state what the page found, not "
        "what it is about."
    )
    return "\n".join(lines)
