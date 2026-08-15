"""
A deck's archetypes, and the chapter it repeats them in.

The reference is not 184 individually designed pages. It is **a chapter
repeated fourteen times**: each technology trend gets the same slide series,
with the same interrogative titles, verbatim -

    What is the trend about?
    Why should leaders pay attention?
    What industries are most affected by the trend?
    What should leaders consider when engaging with the trend?
    Additional resources

That repetition is most of what makes the deck read as one document rather
than a pile of slides, and no amount of per-slide polish substitutes for it.
The planner had no way to express "this deck is a pattern instantiated once
per subject", so a fourteen-chapter deck came out as fourteen unrelated runs
of invention.

Two things are read here, and neither needs a model:

**Archetypes** are the survey's clusters (#117) with everything else the epic
measures attached - the region grammar of their representative page (#118),
what their pages carry (#119), and what register their titles are in.

**The chapter** is a repeating series, found by sequence mining over the
pages' *titles* rather than their archetype labels. Titles turned out to be
much the stronger signal: the reference asks "Why should leaders pay
attention?" in every chapter, but answers it on a card row in one chapter and
a split canvas in the next, so a series keyed on layout alone would find
fourteen different chapters.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from app import titles as title_model
from app.measure.clusters import TemplateSurvey
from app.measure.grammar import LayoutGrammar, TemplateGrammar

# A title form has to recur this often to be part of a pattern rather than a
# coincidence. Three is the fewest that can establish a rhythm.
MIN_REPEATS = 3
# Two titles sharing this much of their vocabulary are the same question.
# The reference asks "What is the trend about?", "What is this trend about?"
# and "What is the tech trend about?" and means one thing by all three.
TITLE_SIMILARITY = 0.6
# A chapter shorter than this is not a chapter, it is a pair of pages that
# happen to recur.
MIN_CHAPTER_PAGES = 3
# A position has to appear in this share of the chapters to be part of the
# pattern. Below it, it is something one chapter did once.
POSITION_SUPPORT = 0.5
# A segment carrying less of the pattern than this opened with the same page a
# chapter does and then went its own way - a deck's front matter, usually.
INSTANCE_SUPPORT = 0.4
# Words that carry no meaning in a title and only make two unlike titles look
# alike.
_STOPWORDS = frozenset("""
a an the and or of for to in on at by with from is are was were be been this
that these those it its as
""".split())
_CONTINUED_RE = re.compile(r"\(\s*continued\s*\)", re.I)
_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Archetype:
    """One kind of page a template has, with everything measured about it."""
    name: str
    label: str
    pages: tuple[int, ...] = ()
    representative: int = 0
    regions: tuple[dict, ...] = ()
    features: tuple[str, ...] = ()
    words_median: float = 0.0
    blocks_median: float = 0.0
    title_register: str = title_model.LABEL

    @property
    def size(self) -> int:
        return len(self.pages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "pages": list(self.pages),
            "representative": self.representative,
            "regions": [dict(region) for region in self.regions],
            "features": list(self.features),
            "wordsMedian": self.words_median,
            "blocksMedian": self.blocks_median,
            "titleRegister": self.title_register,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Archetype":
        data = data or {}
        return cls(
            name=str(data.get("name") or ""),
            label=str(data.get("label") or ""),
            pages=tuple(data.get("pages") or ()),
            representative=int(data.get("representative") or 0),
            regions=tuple(r for r in (data.get("regions") or [])
                          if isinstance(r, dict)),
            features=tuple(str(f) for f in (data.get("features") or [])),
            words_median=float(data.get("wordsMedian") or 0.0),
            blocks_median=float(data.get("blocksMedian") or 0.0),
            title_register=str(data.get("titleRegister") or title_model.LABEL),
        )


@dataclass(frozen=True)
class ChapterPosition:
    """One slide of the repeating series."""
    index: int
    title: str
    archetype: str
    support: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "title": self.title,
                "archetype": self.archetype, "support": self.support}

    @classmethod
    def from_dict(cls, data: dict) -> "ChapterPosition":
        data = data or {}
        return cls(index=int(data.get("index") or 0),
                   title=str(data.get("title") or ""),
                   archetype=str(data.get("archetype") or ""),
                   support=int(data.get("support") or 0))


@dataclass(frozen=True)
class Chapter:
    """The series a deck repeats, once per subject."""
    positions: tuple[ChapterPosition, ...] = ()
    occurrences: tuple[int, ...] = ()

    @property
    def count(self) -> int:
        return len(self.occurrences)

    @property
    def length(self) -> int:
        return len(self.positions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "positions": [p.to_dict() for p in self.positions],
            "occurrences": list(self.occurrences),
            "count": self.count,
            "length": self.length,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> Optional["Chapter"]:
        if not data or not data.get("positions"):
            return None
        return cls(
            positions=tuple(ChapterPosition.from_dict(p)
                            for p in data.get("positions") or []),
            occurrences=tuple(data.get("occurrences") or ()),
        )

    def describe(self) -> str:
        """The pattern, in the terms a planner writes an outline in."""
        if not self.positions:
            return ""
        lines = [
            f"This template repeats one chapter {self.count} times, "
            f"{self.length} slides each:"
        ]
        for position in self.positions:
            lines.append(
                f"  {position.index + 1}. \"{position.title}\" "
                f"- {position.archetype}"
            )
        return "\n".join(lines)


@dataclass
class TemplateArchetypes:
    """Every archetype a template has, and the chapter it repeats."""
    archetypes: tuple[Archetype, ...] = ()
    chapter: Optional[Chapter] = None

    def named(self, name: str) -> Optional[Archetype]:
        return next((a for a in self.archetypes if a.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "archetypes": [a.to_dict() for a in self.archetypes],
            "chapter": self.chapter.to_dict() if self.chapter else None,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "TemplateArchetypes":
        data = data or {}
        return cls(
            archetypes=tuple(Archetype.from_dict(a)
                             for a in (data.get("archetypes") or [])),
            chapter=Chapter.from_dict(data.get("chapter")),
        )


# ── Titles ────────────────────────────────────────────────────────────

def page_headline(page) -> str:
    """The page's headline: every line set in its largest type."""
    from app.measure.page_metrics import _collect_spans

    spans = _collect_spans(page)
    if not spans:
        return ""
    largest = max(span["size"] for span in spans)
    lines = [span for span in spans if span["size"] >= largest - 0.6]
    ordered = sorted(lines, key=lambda s: (round(s["bbox"][1], 1), s["bbox"][0]))
    return " ".join(span["text"].strip() for span in ordered).strip()


def normalise_title(text: str) -> str:
    """
    A headline as the question it asks.

    Case, punctuation, "(continued)" and the small words all vary between two
    printings of the same question, and none of them changes what is being
    asked.
    """
    cleaned = _CONTINUED_RE.sub(" ", str(text or ""))
    words = [w for w in _WORD_RE.findall(cleaned.lower()) if w not in _STOPWORDS]
    return " ".join(words)


def titles_match(a: str, b: str) -> bool:
    """Whether two normalised titles are the same question."""
    left, right = set(a.split()), set(b.split())
    if not left or not right:
        return False
    overlap = len(left & right) / len(left | right)
    return overlap >= TITLE_SIMILARITY


def _title_forms(normalised: Sequence[str]) -> dict[str, str]:
    """Map every normalised title onto the form it belongs to."""
    forms: list[tuple[str, int]] = []
    counts: dict[str, int] = {}
    for title in normalised:
        if title:
            counts[title] = counts.get(title, 0) + 1
    for title, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        for index, (leader, _) in enumerate(forms):
            if titles_match(title, leader):
                forms[index] = (leader, forms[index][1] + count)
                counts[title] = -index - 1
                break
        else:
            forms.append((title, count))
    mapping: dict[str, str] = {}
    for title, count in counts.items():
        mapping[title] = forms[-count - 1][0] if count < 0 else title
    return mapping


# ── Building the archetypes ───────────────────────────────────────────

def build(survey: TemplateSurvey, grammar: Optional[TemplateGrammar] = None,
          headlines: Optional[dict[int, str]] = None,
          shapes: Optional[dict[int, Any]] = None) -> TemplateArchetypes:
    """
    Name a template's clusters and find the chapter they repeat in.

    Everything here is assembly: the clusters come from #117, the grammar from
    #118, the counts from the same page reading #119 uses. Nothing calls a
    model, which is what the issue asks for and also what makes running it on
    every upload affordable.
    """
    headlines = headlines or {}
    shapes = shapes or {}
    grammar_by_page = {
        layout.page: layout for layout in (grammar.layouts if grammar else [])
    }

    archetypes: list[Archetype] = []
    used: set[str] = set()
    for cluster in survey.clusters:
        name = _unique(cluster.label, used)
        layout: Optional[LayoutGrammar] = grammar_by_page.get(cluster.representative)
        pages = [shapes[p] for p in cluster.pages if p in shapes]
        archetypes.append(Archetype(
            name=name,
            label=cluster.label,
            pages=cluster.pages,
            representative=cluster.representative,
            regions=layout.regions if layout else (),
            features=layout.features if layout else (),
            words_median=_median(p.word_count for p in pages),
            blocks_median=_median(p.blocks for p in pages),
            title_register=_register_of(
                [headlines.get(p, "") for p in cluster.pages]
            ),
        ))

    chapter = detect_chapter(survey, headlines,
                             {a.representative: a.name for a in archetypes},
                             {p: a.name for a in archetypes for p in a.pages})
    return TemplateArchetypes(archetypes=tuple(archetypes), chapter=chapter)


def _register_of(headlines: Sequence[str]) -> str:
    """Which register an archetype's headlines are usually in."""
    registers = [title_model.classify(h) for h in headlines if str(h or "").strip()]
    if not registers:
        return title_model.LABEL
    return max(set(registers), key=registers.count)


def _unique(label: str, used: set[str]) -> str:
    name, suffix = label, 2
    while name in used:
        name = f"{label} {suffix}"
        suffix += 1
    used.add(name)
    return name


def _median(values) -> float:
    ordered = sorted(values)
    return round(float(statistics.median(ordered)), 1) if ordered else 0.0


# ── Finding the chapter ───────────────────────────────────────────────

def detect_chapter(survey: TemplateSurvey, headlines: dict[int, str],
                   by_representative: dict[int, str],
                   by_page: dict[int, str]) -> Optional[Chapter]:
    """
    The series this template repeats, if it repeats one.

    Anchored on the title that recurs most evenly across the document - a
    chapter opener is the one page every chapter has in the same place - and
    then read as the modal series of the segments between anchors. Deviation
    is expected and survives: a position kept in the pattern is one that most
    chapters have, not one every chapter has.
    """
    pages = sorted(survey.considered)
    if len(pages) < MIN_CHAPTER_PAGES * 2:
        return None

    normalised = {page: normalise_title(headlines.get(page, "")) for page in pages}
    forms = _title_forms([normalised[page] for page in pages])
    form_of = {page: forms.get(normalised[page], normalised[page]) for page in pages}

    anchor = _anchor(pages, form_of)
    if anchor is None:
        return None

    starts = [page for page in pages if form_of.get(page) == anchor]
    segments: list[list[int]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else pages[-1] + 1
        segment = [page for page in pages if start <= page < end]
        if len(segment) >= MIN_CHAPTER_PAGES:
            segments.append(segment)
    if len(segments) < MIN_REPEATS:
        return None

    length = int(statistics.median([len(s) for s in segments]))
    positions: list[ChapterPosition] = []
    for index in range(length):
        at = [segment[index] for segment in segments if index < len(segment)]
        if len(at) < max(2, int(len(segments) * POSITION_SUPPORT)):
            continue
        title_form = _modal([form_of.get(page, "") for page in at])
        positions.append(ChapterPosition(
            index=len(positions),
            title=_prettiest(
                [headlines.get(page, "") for page in at
                 if form_of.get(page) == title_form]
            ) or _prettiest([headlines.get(page, "") for page in at]),
            archetype=_modal([by_page.get(page, "") for page in at]),
            support=sum(1 for page in at if form_of.get(page) == title_form),
        ))
    if len(positions) < MIN_CHAPTER_PAGES:
        return None

    # Which segments are actually instances of the pattern the rest of them
    # described. The front matter of a deck opens with the same title page a
    # chapter does and then goes its own way; counting it as a chapter
    # overstates how often the pattern is used.
    wanted = [normalise_title(position.title) for position in positions]
    instances = tuple(
        segment[0] for segment in segments
        if _matches(segment, form_of, wanted) >= INSTANCE_SUPPORT
    )
    return Chapter(positions=tuple(positions),
                   occurrences=instances or tuple(starts))


def _matches(segment: Sequence[int], form_of: dict[int, str],
             wanted: Sequence[str]) -> float:
    """How much of the pattern a segment actually carries."""
    present = {form_of.get(page, "") for page in segment}
    hits = sum(1 for title in wanted
               if any(title and titles_match(title, form) for form in present))
    return hits / max(1, len(wanted))


def _anchor(pages: Sequence[int], form_of: dict[int, str]) -> Optional[str]:
    """
    The title form that best divides the document into chapters.

    Best meaning: it recurs often, its occurrences are evenly spaced, and the
    gaps between them are long enough to be chapters. A form appearing twice
    at either end of the deck divides nothing.
    """
    candidates: dict[str, list[int]] = {}
    for page in pages:
        form = form_of.get(page)
        if form:
            candidates.setdefault(form, []).append(page)

    best, best_score = None, 0.0
    for form, at in candidates.items():
        if len(at) < MIN_REPEATS:
            continue
        gaps = [b - a for a, b in zip(at, at[1:])]
        if not gaps or min(gaps) < MIN_CHAPTER_PAGES:
            continue
        # Whole chapters, not occurrences. A form appearing at the *end* of
        # every chapter recurs exactly as often as one appearing at the start,
        # and divides the deck one chapter worse: its last occurrence opens a
        # segment with nothing after it.
        complete = sum(1 for length in _segment_lengths(pages, at)
                       if length >= MIN_CHAPTER_PAGES)
        if complete < MIN_REPEATS:
            continue
        spread = (at[-1] - at[0]) / max(1, pages[-1] - pages[0])
        evenness = 1.0 / (1.0 + statistics.pstdev(gaps)
                          / max(1e-6, statistics.fmean(gaps)))
        score = complete * evenness * spread
        if score > best_score:
            best, best_score = form, score
    return best


def _segment_lengths(pages: Sequence[int], starts: Sequence[int]) -> list[int]:
    lengths = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else pages[-1] + 1
        lengths.append(sum(1 for page in pages if start <= page < end))
    return lengths


def _modal(values: Sequence[str]) -> str:
    present = [v for v in values if v]
    return max(set(present), key=present.count) if present else ""


def _prettiest(headlines: Sequence[str]) -> str:
    """
    The printed form of a title to reuse, out of the ones that were printed.

    The shortest, because the reference's longer variants are the ones that
    ran a second question onto the same line - "What is the trend about, and
    what are the most noteworthy technologies" is two positions printed on
    one page, and the pattern wants the question, not the collision.
    """
    cleaned = [
        " ".join(_CONTINUED_RE.sub(" ", str(h or "")).split())
        for h in headlines if str(h or "").strip()
    ]
    # A headline of nothing but figures is a page whose largest type is a
    # statistic, not a title. Prefer one made of words where there is one.
    worded = [h for h in cleaned if any(c.isalpha() for c in h)]
    return min(worded or cleaned, key=lambda h: (len(h), h)) if cleaned else ""


# ── Holding a plan to the pattern ─────────────────────────────────────

def chapter_of(design_system: Optional[dict]) -> Optional[Chapter]:
    """The chapter this run's template repeats, if it has one."""
    return TemplateArchetypes.from_dict(
        (design_system or {}).get("archetypes")
    ).chapter


def check_consistency(slides: Sequence[dict]) -> list[str]:
    """
    Whether a planned deck instantiated its chapter the same way each time.

    The point of a pattern is that a reader can tell where they are without
    reading: the third slide of every chapter answers the same question. A
    deck that asks it differently each time has the cost of a pattern and
    none of the benefit, and this is what makes that automatically visible
    rather than a thing someone notices on slide forty.
    """
    chapters: dict[str, list[dict]] = {}
    for slide in slides or []:
        if not isinstance(slide, dict):
            continue
        subject = str(slide.get("chapterOf") or "").strip()
        if subject:
            chapters.setdefault(subject, []).append(slide)
    if len(chapters) < 2:
        return []

    problems: list[str] = []
    lengths = {subject: len(group) for subject, group in chapters.items()}
    if len(set(lengths.values())) > 1:
        problems.append(
            "chapters are different lengths: "
            + ", ".join(f"{s} has {n}" for s, n in sorted(lengths.items()))
        )

    ordered = list(chapters.items())
    reference_subject, reference_slides = ordered[0]
    for position in range(len(reference_slides)):
        wanted = normalise_title(reference_slides[position].get("title", ""))
        for subject, group in ordered[1:]:
            if position >= len(group):
                continue
            got = normalise_title(group[position].get("title", ""))
            if wanted and got and not titles_match(wanted, got):
                problems.append(
                    f"slide {position + 1} of each chapter asks different "
                    f"questions: {reference_subject!r} has "
                    f"{reference_slides[position].get('title')!r}, "
                    f"{subject!r} has {group[position].get('title')!r}"
                )
                break
    return problems


# ── Reading a document ────────────────────────────────────────────────

def read_pdf(pdf_path: str, survey: TemplateSurvey,
             grammar: Optional[TemplateGrammar] = None,
             shapes: Optional[dict[int, Any]] = None) -> TemplateArchetypes:
    """Read every considered page's headline, then build the archetypes."""
    import fitz

    from app.measure.page_metrics import page_shape

    doc = fitz.open(pdf_path)
    try:
        headlines = {
            page: page_headline(doc[page - 1])
            for page in survey.considered if 1 <= page <= len(doc)
        }
        if shapes is None:
            # One page per archetype rather than all of them. A cluster is
            # defined by its pages looking alike, so its representative is a
            # fair statement about what they carry - and reading two hundred
            # pages again to average what the survey already grouped would
            # double the cost of an upload for a second decimal place.
            shapes = {
                cluster.representative: page_shape(doc[cluster.representative - 1],
                                                   cluster.representative)
                for cluster in survey.clusters
                if 1 <= cluster.representative <= len(doc)
            }
    finally:
        doc.close()
    return build(survey, grammar, headlines, shapes)
