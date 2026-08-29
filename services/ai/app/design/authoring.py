"""
Asking the agent to design, and reading back what it decided.

The prompt here is the whole of the feature: everything downstream derives
geometry from these decisions, so what matters is that the decisions are
*about this deck* rather than three arrangements of the same furniture.

Two things it has to do that the enum could not.

**Make the languages differ structurally.** Three languages that differ only
in hue is the failure this replaces. So the prompt asks for differences in the
measure, the ramp and the page vocabulary, and the reader can say when a set
came back with the same column count and the same page kinds - checked rather
than merely requested, because a prompt asking for variety and a reader
accepting sameness teaches the model that the ask was decorative.

**Ground the choice in the subject.** A deck about film receipts and a deck
about clinical trials should not arrive at the same measure by coincidence.
The rationale is part of the contract: a language whose rationale would fit
any deck is a language that was not reasoned.
"""

from __future__ import annotations

import json
from typing import Optional, Sequence

from app.agent.tools._today import today_line
from app.design.authored import DEFAULT_RATIOS, Language, PageKind, Slot
from app.design.deck_format import DeckFormat
from app.design import deck_format as deck_format_module
from app.observability.logging import get_logger

logger = get_logger(__name__)

WANTED = 3

MIN_DISTINCT_MEASURES = 2
MIN_DISTINCT_PAGE_SETS = 2

SLOT_KINDS: tuple[str, ...] = (
    "heading", "standfirst", "prose", "figure", "statistic", "quote",
    "caption", "footnote",
)

PAGE_ROLES = ("cover", "section", "content", "exhibit", "closing")


def prompt(brief: str, answers: str = "", wanted: int = WANTED,
          *, fmt: "DeckFormat | None" = None) -> str:
    """What to ask for. The decisions, never the geometry.

    ``fmt`` says what kind of presentation this is, and it changes more of
    the prompt than any other single thing does - a "scientific journal"
    language authored for a pitch and one authored for an academic seminar
    should not come back the same, and until #284 nothing told the model
    which one it was designing.
    """
    fmt = fmt or deck_format_module.by_id(None)
    example = {
        "languages": [{
            "name": "<two or three words a non-designer would recognise>",
            "rationale": "<one line on why THIS subject wants THIS design>",
            "columns": 6,
            "marginRatio": 0.06,
            "gutterRatio": 0.02,
            "bodyPt": 20.0,
            "ratios": {"display": 4.2, "headline": 2.8, "standfirst": 1.4},
            "displayFace": "<a real typeface>",
            "bodyFace": "<a real typeface>",
            "ground": "#RRGGBB",
            "ink": "#RRGGBB",
            "accent": "#RRGGBB",
            "accentAlt": "#RRGGBB",
            "density": "airy|balanced|dense",
            "pages": [{
                "name": "<a slug for this kind of page>",
                "role": "cover|section|content|exhibit|closing",
                "frequency": 0.4,
                "slots": [
                    {"kind": "figure", "span": 6, "behind": True},
                    {"kind": "heading", "span": 6, "required": True},
                    {"kind": "prose", "span": 3, "name": "left"},
                ],
            }],
        }],
    }
    lines = [
        today_line(),
        "",
        f"Design {wanted} genuinely different design languages for this deck.",
        "",
        f"The deck: {brief.strip() or 'a business presentation'}",
        "",
        f"Format: {fmt.label} - {fmt.description}. This deck wants "
        f"{fmt.vocabulary}.",
    ]
    if str(answers or "").strip():
        lines += ["", "Settled with the user already - every language must "
                  "respect these:", str(answers).strip()[:1200]]
    lines += [
        "",
        "You are deciding the DESIGN, not drawing the pages. State the "
        "measure, the type ramp, the palette by role, and what kinds of page "
        "this deck needs. Where each slot lands, how tall it is and how much "
        "it holds are worked out from your decisions - do not give positions, "
        "sizes in inches, or rectangles.",
        "",
        "Return JSON only, in this shape. Every <angle bracket> is a "
        "description of what belongs there, not a value to copy:",
        json.dumps(example, indent=2),
        "",
        "How to decide:",
        "- `columns` is the measure. A wide measure with few columns reads as "
        "a report; many narrow columns read as a broadsheet or a dashboard. "
        "Choose from what this deck has to carry.",
        f"- You are designing for {fmt.description}. Body type below "
        f"{fmt.floor_pt():.0f}pt on this canvas will be scaled up regardless "
        "of what you set, so choose a number you actually mean: a journal or "
        "a report sets 10 or 11pt on A4 and is right to, but that number "
        "describes a page held at arm's length, not this format.",
        "- `bodyPt` and `ratios` are the ramp. A ratio is a multiple of the "
        "body, so a display of 4.2 is four times the body copy - a loud deck. "
        "A ratio near 2 is a quiet one. Set the rungs you care about; the "
        f"rest default to {json.dumps(DEFAULT_RATIOS)}.",
        "- The palette is by role. `ground` is the paper, `ink` the type, "
        "`accent` the one colour carrying emphasis, `accentAlt` a second if "
        "the deck needs one. Choose hues the subject suggests rather than a "
        "default corporate blue.",
        "- `pages` is the vocabulary this deck needs. A page kind is a list "
        f"of slots in reading order, each one of: {', '.join(SLOT_KINDS)}. "
        "`span` is how many of your columns it takes. Give the deck the kinds "
        "of page it actually needs: if it argues from figures it wants a page "
        "built round a statistic row; if it compares, two prose columns side "
        "by side.",
        "- `required` on a slot means the page is UNUSABLE without it, so use "
        "it only for the heading. A cover also needs somewhere for its "
        "subtitle to go - a `standfirst` or a `prose` slot - or it can carry "
        "only a title and nothing else.",
        "- At least one content page MUST carry a `figure` slot. A deck's "
        "charts, tables and diagrams go there and nowhere else, so a "
        "vocabulary without one cannot hold the deck's own evidence and is "
        "rejected.",
        "- A slot may also set `behind: true` or `bleed: true`, and this is "
        "what separates a designed page from a form with boxes on it. "
        "`behind` puts the slot UNDER everything else on the page, filling "
        "it edge to edge - a photograph with the heading, a quote and a "
        "statistic set over it, the way a report opens a section. `bleed` "
        "runs a slot off whichever edges its row puts it against, so a "
        "figure reaches the paper's edge instead of sitting in a polite "
        "margin. Text over a behind slot is reversed out automatically; you "
        "do not set its colour.",
        "- Give at least one page kind a `behind` figure with real material "
        "over it. A vocabulary in which nothing ever overlaps anything is a "
        "vocabulary of rectangles in rows, and it will read as a template "
        "whatever its type and colour are.",
        "- `frequency` is roughly what share of the deck each kind is, and "
        "they should sum to about 1.",
        "",
        "What makes a set good:",
        f"- They must differ in MEASURE and in PAGE VOCABULARY, not only in "
        f"colour: at least {MIN_DISTINCT_MEASURES} different column counts "
        f"and {MIN_DISTINCT_PAGE_SETS} different sets of page kinds across "
        f"the {wanted}. A set differing only in hue is rejected.",
        "- Each rationale says why THIS subject wants THIS design. One that "
        "would fit any deck is one that was not reasoned.",
        "- Name each for what it looks like, in words a non-designer would "
        "recognise. Never lettered A/B/C.",
        "- Use real typefaces (Georgia, Arial, Helvetica, Times New Roman, "
        "Verdana). A serif display over a sans body is a different deck from "
        "sans over sans.",
    ]
    return "\n".join(lines)


def _slot(raw) -> Optional[Slot]:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip()
    if kind not in SLOT_KINDS:
        return None
    try:
        span = max(1, int(raw.get("span") or 1))
    except (TypeError, ValueError):
        span = 1
    return Slot(
        kind=kind,  # type: ignore[arg-type]
        span=min(48, span),
        required=bool(raw.get("required")),
        name=str(raw.get("name") or "").strip()[:24],
        # `Slot` has carried these since the packing work, and `to_pack`
        # already derives real geometry for both - a behind slot becomes the
        # whole content box at z=0, a bleeding one runs off the edges its row
        # puts it against. Nothing ever set them: this parser dropped the
        # keys and the prompt never asked for them, so no authored language
        # could produce a page with an image behind its text. That is the
        # single most recognisable move in the reference publications (a
        # full-bleed photograph with the headline, a numeral and a quote set
        # over it), and it was unreachable by construction.
        bleed=bool(raw.get("bleed")),
        behind=bool(raw.get("behind")),
    )


def _page(raw) -> Optional[PageKind]:
    if not isinstance(raw, dict):
        return None
    name = _slug(str(raw.get("name") or ""))
    slots = tuple(s for s in (_slot(x) for x in raw.get("slots") or []) if s)
    if not name or not slots:
        return None
    role = str(raw.get("role") or "content").strip()
    return PageKind(
        name=name,
        role=role if role in PAGE_ROLES else "content",  # type: ignore[arg-type]
        slots=slots,
        frequency=_clamp(raw.get("frequency"), 0.0, 0.0, 1.0),
    )


def read(payload: dict) -> list[Language]:
    """The languages in this response, skipping any that will not stand up.

    A language missing its pages is dropped rather than repaired: a repaired
    language is one nobody designed, and offering the user a thing invented by
    a parser is worse than offering them two things somebody chose.
    """
    out: list[Language] = []
    for raw in (payload or {}).get("languages") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        pages = tuple(p for p in (_page(x) for x in raw.get("pages") or []) if p)
        if not name or not pages:
            logger.info("authored_language_incomplete", name=name or "(unnamed)")
            continue
        try:
            out.append(Language(
                name=name[:60],
                rationale=str(raw.get("rationale") or "").strip()[:200],
                columns=int(_clamp(raw.get("columns"), 6, 2, 16)),
                margin_ratio=_clamp(raw.get("marginRatio"), 0.06, 0.01, 0.24),
                gutter_ratio=_clamp(raw.get("gutterRatio"), 0.02, 0.005, 0.09),
                # The low bound is a legibility floor of last resort, not a
                # target: `to_pack` scales anything below the canvas-relative
                # floor up. Left at 5.0 so a language is read as authored and
                # the scaling happens in one place.
                body_pt=_clamp(raw.get("bodyPt"), 18.0, 5.0, 40.0),
                ratios={
                    str(k): float(v)
                    for k, v in (raw.get("ratios") or {}).items()
                    if _is_number(v) and 0.2 <= float(v) <= 8.0
                },
                display_face=str(raw.get("displayFace") or "Arial")[:40],
                body_face=str(raw.get("bodyFace") or "Arial")[:40],
                ground=_hex(raw.get("ground"), "#FFFFFF"),
                ink=_hex(raw.get("ink"), "#1A1A1A"),
                accent=_hex(raw.get("accent"), "#1F4E78"),
                accent_alt=_hex(raw.get("accentAlt"), ""),
                density=(raw.get("density")
                         if raw.get("density") in ("airy", "balanced", "dense")
                         else "balanced"),
                pages=pages,
            ))
        except (TypeError, ValueError) as exc:
            logger.info("authored_language_rejected", name=name, error=str(exc))
    return out


def differs_enough(languages: Sequence[Language]) -> Optional[str]:
    """Why this set is three arrangements of one idea, or None if it is not."""
    if len(languages) < 2:
        return None
    measures = {language.columns for language in languages}
    if len(measures) < MIN_DISTINCT_MEASURES:
        return (f"every language uses {measures.pop()} columns; they differ "
                f"only in colour and type")
    vocabularies = {
        tuple(sorted(page.name for page in language.pages))
        for language in languages
    }
    if len(vocabularies) < MIN_DISTINCT_PAGE_SETS:
        return ("every language offers the same kinds of page; they differ "
                "only in colour and type")
    return None


def _clamp(value, default: float, low: float, high: float) -> float:
    if not _is_number(value):
        return default
    return max(low, min(high, float(value)))


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _hex(value, default: str) -> str:
    text = str(value or "").strip()
    if len(text) == 7 and text.startswith("#"):
        try:
            int(text[1:], 16)
            return text.upper()
        except ValueError:
            pass
    return default


def _slug(name: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in name)
    return "-".join(part for part in cleaned.split("-") if part)[:32]
