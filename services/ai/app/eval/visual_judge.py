"""
Judge a rendered deck by looking at it.

The scorecard measures properties: ink coverage, blocks per page, how close the
type ramp is to an exemplar's. Those are cheap, deterministic and diffable
between commits, which is exactly what an iteration loop needs - and they are
not what "publication grade" means. A deck can match every number and still be
obviously amateur, and a deck can miss most of them and be excellent, because
the exemplars themselves disagree by 3x on the numbers.

So the numbers are the regression signal and the eye is the arbiter. This
module renders the deck's pages, puts them in front of a vision model next to
exemplar pages, and asks the question a metric cannot: *does this look like
something a professional firm published?*

Two rules follow from that, and they are the whole design:

* **The judge outranks the scorecard.** When the judge is confident a deck is
  publication grade and the score is low, the deck is accepted and the
  *metric* is recorded as suspect. A number that disagrees with a clear-eyed
  look at the artefact is a broken number, and optimising against it makes the
  output worse while the dashboard improves.
* **The images never reach the agent loop.** They are read here, sent on one
  bounded call, and dropped. Feeding a rendered page back into the run's own
  conversation is what killed a run with a 767k-token request (#237), and this
  module exists next to that failure, not in ignorance of it.
"""

from __future__ import annotations

import base64
import os
import shutil
import tempfile
from dataclasses import dataclass, field, asdict
from typing import Any, Optional, Sequence

import structlog

from app.eval import corpus
from app.agent.tools._json_extract import extract_json_object

logger = structlog.get_logger()

# How many pages of the deck go in front of the judge. A verdict on a whole
# deck does not get better past the first handful of pages, and every extra
# image is cost and latency on a call that runs many times over an iteration.
MAX_DECK_PAGES = 6
# ...and how many exemplar pages ride along as the comparison. Fewer, because
# they are there to set the bar, not to be assessed.
MAX_EXEMPLAR_PAGES = 3
# Rasterisation width. Enough to read a subhead and see whether a chart is
# labelled; not enough to make the payload the problem.
RENDER_DPI = 90
# JPEG rather than PNG, and quality tuned down: a deck page is mostly flat
# colour, and the judge is looking at composition and hierarchy rather than
# at compression artefacts.
JPEG_QUALITY = 70

GRADES = ("publication", "competent", "amateur")


@dataclass
class PageVerdict:
    page: int
    grade: str = "competent"
    note: str = ""


@dataclass
class Verdict:
    """What the judge thought, and how sure it was."""

    grade: str = "competent"
    confident: bool = False
    summary: str = ""
    strengths: list[str] = field(default_factory=list)
    faults: list[str] = field(default_factory=list)
    pages: list[PageVerdict] = field(default_factory=list)
    compared_against: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def is_publication_grade(self) -> bool:
        return self.grade == "publication"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Adjudication:
    """The judge's verdict set beside the scorecard, and what to do about it."""

    verdict: Verdict
    style_score: Optional[float]
    accepted: bool
    metric_is_suspect: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.to_dict(),
            "styleScore": self.style_score,
            "accepted": self.accepted,
            "metricIsSuspect": self.metric_is_suspect,
            "reason": self.reason,
        }


# A score below this would, on the numbers alone, mean "not close to the
# exemplars". It is the threshold the judge is allowed to overrule.
LOW_STYLE_SCORE = 0.55


PROMPT = """\
You are judging whether a slide deck looks like something a professional \
firm published, or like something generated.

The first {deck_count} image(s) are consecutive pages of the deck under \
review. The remaining {exemplar_count} image(s) are pages from published \
reference decks, included so you can see the standard - NOT a template to \
match. The references are deliberately in different registers from each \
other; a deck that resembles none of them can still be excellent.

Judge craft, not subject matter, and not whether you agree with the content:

- Typographic hierarchy. Are there distinct, deliberate levels - eyebrow, \
headline, deck, body, caption - or is everything two sizes of the same thing?
- Composition. Does each page have a structure the eye can follow, with \
intentional alignment and margins, or is it a title with a list under it?
- Figures. Are charts and tables labelled, scaled and titled so they can be \
read without the speaker, or are they decoration?
- Restraint. One accent used deliberately, or colour scattered around?
- Apparatus. Are figures attributed, and are footnotes and page furniture \
handled consistently?
- Emptiness and crowding. Is the whitespace doing work, or is the page \
padded out or jammed full?

Grade the deck as a whole:
- "publication" - would not look out of place next to the references.
- "competent" - clean and readable, but visibly a generated deck.
- "amateur" - obvious defects: overflow, collisions, unlabelled figures, \
default styling.

Say you are confident ONLY if you would stand behind the grade in front of \
the person who asked for the deck. If the pages are ambiguous, or you cannot \
see enough to tell, say confident is false.

Reply with JSON and nothing else:

{{
  "grade": "<one of publication, competent, amateur>",
  "confident": <true or false>,
  "summary": "<one sentence on what decides the grade>",
  "strengths": ["<what genuinely works, as specifics>"],
  "faults": ["<what holds it back, each naming the page and the problem>"],
  "pages": [{{"page": <1-based page number>, "grade": "<grade>", "note": "<what stands out>"}}]
}}
"""


def _encode(path: str) -> str:
    with open(path, "rb") as handle:
        return "data:image/jpeg;base64," + base64.b64encode(handle.read()).decode()


def _render_pdf_pages(pdf_path: str, out_dir: str, pages: Sequence[int],
                      prefix: str) -> list[str]:
    """Rasterise the given 1-based pages to JPEGs, in order."""
    import fitz

    written: list[str] = []
    doc = fitz.open(pdf_path)
    try:
        for page_number in pages:
            if not 1 <= page_number <= doc.page_count:
                continue
            pixmap = doc[page_number - 1].get_pixmap(dpi=RENDER_DPI)
            path = os.path.join(out_dir, f"{prefix}-{page_number:03d}.jpg")
            pixmap.pil_save(path, format="JPEG", quality=JPEG_QUALITY)
            written.append(path)
    finally:
        doc.close()
    return written


def _exemplar_images(out_dir: str) -> tuple[list[str], list[str]]:
    """
    Exemplar pages to set the bar, when their PDFs are on this machine.

    The PDFs are third-party and deliberately not committed, so this is
    best-effort: without them the judge still works, it just has nothing to
    compare against and says so. **Design pages only** - showing a held-out
    validation page to a model is looking at it, and `assert_exemplar_design_page`
    is what makes that a failure rather than a quiet habit.
    """
    images: list[str] = []
    used: list[str] = []
    for entry in corpus.EXEMPLARS:
        if len(images) >= MAX_EXEMPLAR_PAGES:
            break
        pdf_path = corpus.find_exemplar_pdf(entry.id)
        if not pdf_path:
            continue
        page = entry.design_pages[0]
        corpus.assert_exemplar_design_page(entry.id, page)
        rendered = _render_pdf_pages(pdf_path, out_dir, [page], entry.id)
        if rendered:
            images.extend(rendered)
            used.append(entry.id)
    return images, used


def _parse(reply: str) -> Verdict:
    data = extract_json_object(reply)
    if not isinstance(data, dict):
        return Verdict(error="the judge did not return a JSON object")

    grade = str(data.get("grade") or "").strip().lower()
    if grade not in GRADES:
        # An unrecognised grade is not a pass. Falling through to "competent"
        # keeps a malformed reply from being read as an endorsement.
        grade = "competent"

    def _lines(key: str) -> list[str]:
        value = data.get(key)
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    pages = []
    for entry in data.get("pages") or []:
        if not isinstance(entry, dict):
            continue
        try:
            number = int(entry.get("page"))
        except (TypeError, ValueError):
            continue
        page_grade = str(entry.get("grade") or "").strip().lower()
        pages.append(PageVerdict(
            page=number,
            grade=page_grade if page_grade in GRADES else "competent",
            note=str(entry.get("note") or "").strip(),
        ))

    return Verdict(
        grade=grade,
        confident=bool(data.get("confident")),
        summary=str(data.get("summary") or "").strip(),
        strengths=_lines("strengths"),
        faults=_lines("faults"),
        pages=pages,
    )


async def judge_pdf(pdf_path: str, *, max_pages: int = MAX_DECK_PAGES) -> Verdict:
    """Put a rendered deck in front of the vision model and ask how it looks."""
    from app.llm.provider import get_vision_llm_provider

    work_dir = tempfile.mkdtemp(prefix="thinkslides_judge_")
    try:
        import fitz

        doc = fitz.open(pdf_path)
        page_count = doc.page_count
        doc.close()
        pages = list(range(1, min(page_count, max_pages) + 1))
        deck_images = _render_pdf_pages(pdf_path, work_dir, pages, "deck")
        if not deck_images:
            return Verdict(error=f"nothing to look at in {pdf_path}")

        exemplar_images, used = _exemplar_images(work_dir)
        prompt = PROMPT.format(
            deck_count=len(deck_images), exemplar_count=len(exemplar_images)
        )
        payload = [_encode(path) for path in (*deck_images, *exemplar_images)]

        try:
            reply = await get_vision_llm_provider().chat_with_images(
                prompt=prompt, images=payload, temperature=0.2, max_tokens=2048
            )
        except Exception as exc:                                # noqa: BLE001
            logger.warning("visual_judge_failed", error=str(exc))
            return Verdict(error=str(exc), compared_against=used)

        verdict = _parse(reply)
        verdict.compared_against = used
        logger.info(
            "visual_judge_verdict",
            grade=verdict.grade, confident=verdict.confident,
            deck_pages=len(deck_images), exemplars=used,
        )
        return verdict
    finally:
        # The images do not outlive the call, and they never enter the agent
        # loop's conversation - see the module docstring.
        shutil.rmtree(work_dir, ignore_errors=True)


def adjudicate(verdict: Verdict, style_score: Optional[float]) -> Adjudication:
    """
    Reconcile the eye and the numbers, with the eye on top.

    The case this exists for is the third one: a deck the judge is confident is
    publication grade, scoring badly. That combination is evidence about the
    *metric*, not about the deck - so the deck is accepted and the metric is
    flagged for repair. Optimising against a number that disagrees with the
    artefact makes the output worse while the dashboard improves, which is the
    failure this whole harness is supposed to prevent.
    """
    if verdict.error:
        return Adjudication(
            verdict=verdict, style_score=style_score,
            accepted=(style_score or 0.0) >= LOW_STYLE_SCORE,
            metric_is_suspect=False,
            reason=f"the judge could not look at the deck ({verdict.error}); "
                   "falling back to the score alone",
        )

    scored_low = style_score is not None and style_score < LOW_STYLE_SCORE

    if verdict.is_publication_grade and verdict.confident:
        return Adjudication(
            verdict=verdict, style_score=style_score, accepted=True,
            metric_is_suspect=scored_low,
            reason=("the judge is confident this is publication grade; the "
                    "low style score is evidence against the metric, not "
                    "against the deck")
            if scored_low else "the judge and the score agree this is good",
        )

    if verdict.grade == "amateur":
        return Adjudication(
            verdict=verdict, style_score=style_score, accepted=False,
            metric_is_suspect=not scored_low,
            reason=("the judge can see defects the score missed"
                    if not scored_low else
                    "the judge and the score agree this is not good enough"),
        )

    # "competent", or a grade the judge would not stand behind. Not an
    # endorsement, so the numbers decide - which is the right default for the
    # case where the eye has nothing decisive to say.
    return Adjudication(
        verdict=verdict, style_score=style_score, accepted=not scored_low,
        metric_is_suspect=False,
        reason="the judge was not confident enough to overrule the score",
    )
