"""
What kind of presentation this is - the one thing nothing in the system used
to know.

Every run assumed the same shape: a 16:9 deck of ten to eighteen slides, in a
boardroom register. A "scientific journal" design language authored at 10pt
body (issue #282) was not a bad answer - it was the right answer to a
question nobody had asked, because nothing told the model whether it was
designing a slide read across a room or a page held at arm's length.

A format is not a style. It is a small set of hard facts a deck's shape
follows from: the canvas, the type floor, roughly how many pages, and what
kinds of page it needs. Everything else - the palette, the ramp, the
imagery - stays the author's decision, exactly as it is for any language.

Two floor conventions meet here. A projected deck's floor is a share of its
canvas, because ten points means something different on a 540pt slide than
on a 792pt one (see ``authored.CANVAS_BODY_FLOOR_RATIO``). A printed poster
is read at a fixed physical distance regardless of how large the sheet is,
so its floor is an absolute point size. ``floor_pt`` is the one place that
distinction is resolved, and everything downstream reads a point size
either way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.design.schema import Canvas

# The canvas every projected format shares. Only a printed format changes it.
_SLIDE_CANVAS = Canvas(w_in=13.333, h_in=7.5)


@dataclass(frozen=True)
class DeckFormat:
    """One kind of presentation, and the hard facts that follow from it."""

    id: str
    label: str
    canvas: Canvas
    # Roughly how many pages a deck of this format runs. Advisory, not
    # enforced - a plan that has less to say than the floor should say less.
    pages_min: int
    pages_max: int
    # Exactly one of these is set. See the module docstring.
    body_floor_ratio: Optional[float]
    body_floor_pt: Optional[float]
    density: str  # airy | balanced | dense - the authoring prompt's hint
    # What this format's page vocabulary tends to need, folded into the
    # authoring prompt verbatim.
    vocabulary: str
    # What the format is, for a reader deciding at a glance - the viewing
    # distance and register, not a restatement of the label.
    description: str
    # Multiplies the ordinary per-slide budget (see app/budget.py). 1.0 for
    # a format that carries as much as today's default.
    budget_scale: float = 1.0
    # True only for a format whose whole deck is a single page.
    single_page: bool = False

    def floor_pt(self, canvas: Optional[Canvas] = None) -> float:
        """The smallest body type this format allows, in points."""
        target = canvas or self.canvas
        if self.body_floor_pt is not None:
            return self.body_floor_pt
        return (self.body_floor_ratio or 0.0) * target.h_in * 72.0


BOARDROOM = DeckFormat(
    id="boardroom", label="Boardroom deck",
    canvas=_SLIDE_CANVAS, pages_min=10, pages_max=18,
    body_floor_ratio=0.030, body_floor_pt=None, density="balanced",
    vocabulary=("mixed content and exhibit pages, section dividers, a "
                "closing recap - the shape a board pack or a client report "
                "already has"),
    description="read on a screen, presented and left behind as a document",
    budget_scale=1.0,
)

PITCH = DeckFormat(
    id="pitch", label="Pitch",
    canvas=_SLIDE_CANVAS, pages_min=15, pages_max=30,
    body_floor_ratio=0.045, body_floor_pt=None, density="airy",
    vocabulary=("one claim a page, carried by a large image or a single "
                "number - almost no running prose, and few columns"),
    description="projected and spoken over, not read - the room reads the "
                "speaker, the slide carries one idea",
    budget_scale=0.55,
)

ACADEMIC = DeckFormat(
    id="academic", label="Academic / seminar",
    canvas=_SLIDE_CANVAS, pages_min=12, pages_max=25,
    body_floor_ratio=0.033, body_floor_pt=None, density="balanced",
    vocabulary=("prose-forward pages, a figure with its own caption, and a "
                "real footnote slot for citations"),
    description="a seminar or a lecture, closest to how a deck is built by "
                "default today",
    budget_scale=1.0,
)

DASHBOARD = DeckFormat(
    id="dashboard", label="Data dashboard",
    canvas=_SLIDE_CANVAS, pages_min=3, pages_max=8,
    body_floor_ratio=0.026, body_floor_pt=None, density="dense",
    vocabulary=("statistic rows and figure grids almost exclusively - "
                "prose limited to captions and a short interpretation"),
    description="almost entirely charts, tables and numbers, read at a "
                "glance rather than followed as an argument",
    budget_scale=1.0,
)

POSTER = DeckFormat(
    id="poster", label="Conference poster",
    canvas=Canvas(w_in=33.1, h_in=46.8), pages_min=1, pages_max=1,
    body_floor_ratio=None, body_floor_pt=24.0, density="dense",
    vocabulary=("every section a paper would have - an abstract, a method, "
                "results, a conclusion, references - on one page, in three "
                "or four real columns"),
    description="printed and read standing up, at arm's length, with "
                "nobody speaking over it",
    budget_scale=3.2,
    single_page=True,
)

FORMATS: dict[str, DeckFormat] = {
    f.id: f for f in (BOARDROOM, PITCH, ACADEMIC, DASHBOARD, POSTER)
}

DEFAULT_FORMAT = BOARDROOM.id

# The order offered to the user - default first, poster last since it is the
# one that changes the shape of the run rather than only its numbers.
ORDER = (BOARDROOM.id, PITCH.id, ACADEMIC.id, DASHBOARD.id, POSTER.id)


def by_id(format_id: Optional[str]) -> DeckFormat:
    """The format this id names, or the default when it names nothing real.

    A run must always have a concrete format to build against - an unknown
    or absent id is not an error, it is every run before this existed.
    """
    return FORMATS.get(str(format_id or "").strip().lower(), FORMATS[DEFAULT_FORMAT])


def _option_text(fmt: DeckFormat) -> str:
    return f"{fmt.label} - {fmt.description}"


# The fixed question every consulted run asks, in the same words every time -
# the same convention as loop.AUTONOMY_QUESTION. Reserved ahead of the
# ambiguity truncation: what kind of presentation this is decides the
# design pause that follows it, and an ambiguity crowding it out would
# leave every run defaulting to a boardroom deck by starvation rather than
# by choice.
QUESTION = {
    "question": "What kind of presentation is this?",
    "options": [_option_text(FORMATS[fid]) for fid in ORDER],
}

# Matched against the option's own label text, which rides along in the
# reply verbatim - the card sends back "{question} {option text}", and the
# option text is the label followed by its description. A label is chosen
# specifically to be a word unlikely to appear in an unrelated sentence.
_LABEL_PATTERNS: dict[str, "re.Pattern[str]"] = {
    fmt.id: re.compile(re.escape(fmt.label), re.IGNORECASE)
    for fmt in FORMATS.values()
}


def resolve_from_text(text: str) -> Optional[str]:
    """The format named in this text, or None if none is."""
    content = text or ""
    for format_id, pattern in _LABEL_PATTERNS.items():
        if pattern.search(content):
            return format_id
    return None


def resolve_from_chat(chat_messages: list[dict]) -> Optional[str]:
    """The format named anywhere in this conversation, most recent first.

    Scanned the same way ``pipeline.user_confirmed_autonomy`` scans for its
    own answer: a user's reply to this question rides in a chat message
    alongside answers to others, so this keeps looking further back until one
    of them actually names a format, rather than stopping at the first user
    turn that does not. Returns None rather than a default when nothing in
    the whole conversation ever has, so a caller can tell "nobody has
    answered yet" apart from "the answer was the default".
    """
    for message in chat_messages[::-1] if chat_messages else []:
        if message.get("role") != "user":
            continue
        content = message.get("content") or ""
        if not content.strip():
            continue
        found = resolve_from_text(content)
        if found:
            return found
    return None


def resolve(chat_messages: list[dict]) -> str:
    """The format the user chose, or the default when nobody has."""
    return resolve_from_chat(chat_messages) or DEFAULT_FORMAT
