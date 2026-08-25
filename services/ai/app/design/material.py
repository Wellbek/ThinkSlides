"""
What a page actually has to say, before anything decides how it looks.

The order this inverts is the whole point. Today ``select_layout`` runs
before a word of copy exists - the loop's own directive says "call
select_layout, then any build_* tool, then design_slide_content" - so the
geometry is chosen from a seven-value content-type enum and the material is
poured into whatever came back. A page cannot be composed for material that
has not been gathered yet.

Here the material is gathered first and described in terms a layout decision
can be taken *from*: how many things there are to say, how long each one is,
what kind of evidence stands behind them, whether there is a quotable
sentence, whether the points are a comparison or a sequence. The archetype
is then chosen by fit against that description.

Nothing in this module asks a model to rate its own material. Every field is
counted from content that already exists, for the same reason ``budget.py``
gives: a model asked whether it has enough to fill a page will say yes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional, Sequence

MaterialKind = Literal["text", "figure", "chart", "table", "diagram",
                       "metrics", "quote", "caption", "footnote"]

PageRole = Literal["cover", "section", "content", "exhibit", "closing",
                   "appendix"]

# A sentence shorter than this is a fragment, not a quotable line.
MIN_QUOTE_CHARS = 40
MAX_QUOTE_CHARS = 220
# Below this much body copy, a page has nothing to pull a quote out of.
QUOTE_MIN_BODY_CHARS = 700
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_HAS_FIGURE = re.compile(r"\d")
# Words that say a set of points is being weighed against another set, or
# stepped through in order. Used only to *describe* the material; nothing
# here decides anything on its own.
_COMPARISON = ("versus", " vs", "compared", "against", "whereas", "while",
               "unlike", "outperform", "behind", "ahead of", "trade-off")
_SEQUENCE = ("first", "then", "next", "finally", "phase", "step", "stage",
             "quarter", "roadmap", "timeline", "followed by")


@dataclass(frozen=True)
class MaterialItem:
    """One thing that has to go on the page."""

    id: str
    kind: MaterialKind
    text: str = ""
    payload: Optional[dict] = None
    # Set for figures and exhibits that have a shape of their own. A slot
    # whose aspect is nothing like the exhibit's will crop or letterbox it,
    # and that is a cost worth counting rather than discovering at render.
    aspect: Optional[float] = None
    importance: float = 0.5
    source_ref: Optional[int] = None
    # What this item is for, as opposed to how big it happens to be. Without
    # it the assignment matches on size alone, and a paragraph that fits a
    # pull-quote frame is set as a pull-quote - which is how a page ends up
    # shouting one of its body paragraphs while its actual heading sits in a
    # column halfway down.
    role_hint: str = "body"

    @property
    def chars(self) -> int:
        return len(self.text)

    @property
    def carries_figures(self) -> bool:
        return bool(_HAS_FIGURE.search(self.text))


@dataclass(frozen=True)
class MaterialShape:
    """The shape of a page's material, as a layout decision can read it."""

    role: PageRole = "content"
    items: tuple[MaterialItem, ...] = ()
    heading: str = ""
    quote: str = ""
    is_comparison: bool = False
    is_sequence: bool = False
    citations: int = 0
    research_findings: int = 0
    source_words: int = 0

    # ── counts a fit cost reads directly ──────────────────────────────────

    @property
    def text_items(self) -> tuple[MaterialItem, ...]:
        return tuple(i for i in self.items if i.kind == "text")

    @property
    def exhibits(self) -> tuple[MaterialItem, ...]:
        return tuple(
            i for i in self.items
            if i.kind in ("chart", "table", "diagram", "metrics")
        )

    @property
    def figures(self) -> tuple[MaterialItem, ...]:
        return tuple(i for i in self.items if i.kind == "figure")

    @property
    def total_chars(self) -> int:
        return sum(i.chars for i in self.text_items)

    @property
    def longest_chars(self) -> int:
        return max((i.chars for i in self.text_items), default=0)

    @property
    def figure_count(self) -> int:
        """How many of the text items carry a number that could be stated."""
        return sum(1 for i in self.text_items if i.carries_figures)

    @property
    def is_thin(self) -> bool:
        """Whether there is genuinely not much here.

        Named rather than inferred at each call site, because the right
        response to thin material is a quieter archetype, not a dense one
        filled with padding.
        """
        return len(self.text_items) <= 1 and not self.exhibits \
            and self.total_chars < 200

    def counts(self) -> dict[str, int]:
        return {
            "text": len(self.text_items),
            "exhibit": len(self.exhibits),
            "figure": len(self.figures),
            "quote": 1 if self.quote else 0,
            "citations": self.citations,
        }


def find_quote(paragraphs: Sequence[str]) -> str:
    """The most quotable sentence in this material, if there is one.

    A pull-quote is a sentence the page already contains, set larger - not a
    new sentence written to fill a frame. Choosing it here, from copy that
    exists, is what keeps it that way: an archetype with a pull-quote slot
    and no quotable sentence should score worse rather than invite one to be
    invented.
    """
    best, best_score = "", 0.0
    for paragraph in paragraphs:
        for sentence in _SENTENCE.split(paragraph.strip()):
            sentence = sentence.strip()
            if not (MIN_QUOTE_CHARS <= len(sentence) <= MAX_QUOTE_CHARS):
                continue
            # Prefer a sentence that asserts something: a figure, or a verb
            # of change. A definition set at 24pt is a definition nobody
            # asked to read twice.
            score = 1.0
            if _HAS_FIGURE.search(sentence):
                score += 1.0
            if any(word in sentence.lower() for word in
                   ("rose", "fell", "grew", "shrank", "leads", "outpaced",
                    "doubled", "halved", "expects", "must", "cannot")):
                score += 0.6
            score -= abs(len(sentence) - 120) / 400.0
            if score > best_score:
                best, best_score = sentence, score
    return best


def describe(paragraphs: Sequence[str]) -> tuple[bool, bool]:
    """Whether this copy reads as a comparison, and whether as a sequence."""
    joined = " ".join(paragraphs).lower()
    return (
        any(word in joined for word in _COMPARISON),
        any(word in joined for word in _SEQUENCE),
    )


def shape_from(role: PageRole, heading: str, paragraphs: Sequence[str],
               exhibits: Sequence[dict] = (), figures: Sequence[dict] = (),
               *, citations: int = 0, research_findings: int = 0,
               source_words: int = 0) -> MaterialShape:
    """Gather a slide's material into the description a layout is chosen from."""
    items: list[MaterialItem] = []
    # The heading is material like anything else, and has to take part in the
    # assignment rather than being posted into a frame afterwards: an
    # archetype whose headline slot is required is otherwise rejected for
    # every page, because the one item that could fill it was held back.
    if str(heading or "").strip():
        items.append(MaterialItem(id="heading", kind="text",
                                  text=str(heading).strip(), importance=1.0,
                                  role_hint="heading"))
    for index, text in enumerate(paragraphs):
        if str(text or "").strip():
            items.append(MaterialItem(id=f"text-{index}", kind="text",
                                      text=str(text).strip()))
    for index, exhibit in enumerate(exhibits):
        kind = str(exhibit.get("kind") or "chart")
        items.append(MaterialItem(
            id=f"exhibit-{index}",
            kind=kind if kind in ("chart", "table", "diagram", "metrics") else "chart",
            payload=dict(exhibit),
            aspect=exhibit.get("aspect"),
            importance=float(exhibit.get("importance", 0.7)),
        ))
    for index, figure in enumerate(figures):
        items.append(MaterialItem(
            id=f"figure-{index}", kind="figure", payload=dict(figure),
            aspect=figure.get("aspect"),
            importance=float(figure.get("importance", 0.5)),
        ))

    comparison, sequence = describe(paragraphs)
    # A pull-quote earns its place only when there is enough copy for a
    # highlight to be a highlight. Offered on a page with two paragraphs, it
    # sets one of them twice - once as body and once at pull-quote size - and
    # reads as a mistake rather than as a device.
    body_chars = sum(len(str(t or "")) for t in paragraphs)
    quote = find_quote(paragraphs) if body_chars >= QUOTE_MIN_BODY_CHARS else ""
    return MaterialShape(
        role=role,
        items=tuple(items),
        heading=str(heading or "").strip(),
        quote=quote,
        is_comparison=comparison,
        is_sequence=sequence,
        citations=citations,
        research_findings=research_findings,
        source_words=source_words,
    )
