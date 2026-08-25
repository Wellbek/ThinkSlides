"""
Checking a deck against what it actually has evidence for.

In code, after drafting, never as a tool. The validator this pattern replaces
was a tool the model could decline to call, and `validate_slide_json` was
invoked zero times across every observed run - which left density, title
register and apparatus checking inert in practice. A correctness check that
can be skipped is not a correctness mechanism.

What it reports is deliberately narrow. A figure on a page either appears in a
span the run actually retrieved or it does not, and that is a fact rather than
a judgement. Whether the span *means* what the page says it means is not
decided here, and is not claimed to be.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.grounding.claim import Claim, Evidence, claims_for


@dataclass(frozen=True)
class Unsupported:
    """A figure a slide states with nothing behind it."""

    slide_index: int
    figure: str
    statement: str

    def describe(self) -> str:
        return (f"slide {self.slide_index + 1} states {self.figure!r} and no "
                f"source this run retrieved carries it")


def _statements(slide: dict) -> list[str]:
    """Every line of copy on a slide, as written."""
    out: list[str] = []
    for block in slide.get("contents") or ():
        if not isinstance(block, dict):
            continue
        for run in block.get("runs") or ():
            if isinstance(run, dict):
                text = str(run.get("text") or "").strip()
                if text:
                    out.append(text)
    return out


def ground(slides: list[dict], evidence: Evidence) -> tuple[list[Claim],
                                                            list[Unsupported]]:
    """Attach a claim to every figure that has one, and name those that do not.

    The claims are written onto the slide, so what a page can prove travels
    with the page rather than being recomputed by whatever needs to know.
    """
    all_claims: list[Claim] = []
    gaps: list[Unsupported] = []
    for slide in slides:
        if not isinstance(slide, dict):
            continue
        index = int(slide.get("slideIndex") or 0)
        claims: list[Claim] = []
        for statement in _statements(slide):
            supported, unsupported = claims_for(statement, evidence)
            claims.extend(supported)
            gaps.extend(Unsupported(index, figure, statement)
                        for figure in unsupported)
        if claims:
            slide["claims"] = [claim.to_dict() for claim in claims]
        all_claims.extend(claims)
    return all_claims, gaps


def states_researched_figures(slides: list[dict]) -> bool:
    """Whether any page carries a claim, and so owes the reader attribution.

    This is what the on-page source line should fire on. Whether the deck also
    gets a numbered reference trail and a Sources appendix stays the user's
    preference - the two were conflated, and the result was a deck citing
    Gartner and IDC with no attribution anywhere on any page.
    """
    return any(slide.get("claims") for slide in slides
               if isinstance(slide, dict))
