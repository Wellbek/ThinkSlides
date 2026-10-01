"""
How copy set in a type role is measured: the one place that decides it.

Every question the engine asks about whether copy fits - the selector's
strain, the repair loop's overflow findings, the writer's per-field budgets,
and every slot's `max_chars` - is a question about the text as it will be
*drawn*. `compile._text_primitive` draws a role in the pack's face for it,
bold when the role is semibold or bold, in capitals when its case says so,
and with the role's tracking. A measurement that leaves any of those out
answers a different question.

They were left out, one site at a time:

- Authored capacities were measured in Arial whatever the slot's face
  (#361), so a language set in a wider face was promised room it did not
  have.
- Every typeset check measured headlines at regular weight, though they are
  drawn bold - five to ten percent narrower than the page.
- Capacity was characters-per-line times lines. Words do not pack a line
  solid, so a two-line headline slot advertised about half a line more than
  two lines hold, and drafting was told that number as the longest headline
  the design sets at full size.

So the measurement lives here, and capacity is found by setting real prose
in the slot rather than by dividing its width by an average advance.
"""
from __future__ import annotations

import functools
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.design.pack import TypeRole
    from app.renderer.typeset.shape import TextLayout

# Ordinary expository prose, the register slides are written in: mixed word
# lengths, a few long words, punctuation. A representative mix rather than a
# worst case - capping at a line of capital Ws would forbid every real
# headline. Long enough to fill the largest slot a page has at caption size.
SAMPLE_PROSE = (
    "The quick brown fox jumps over the lazy dog and keeps going until the "
    "line is comfortably longer than any measure. Most of what a reader "
    "takes from a page is decided before the first sentence is finished: "
    "where the eye lands, how long the lines run, and whether the type is "
    "large enough to read without effort. A column that is too wide loses "
    "the reader at every return; one that is too narrow breaks phrases "
    "apart. Between the two lies a comfortable measure, and the copy written "
    "for it should respect the room it has been given rather than spill "
    "past the frame that holds it. "
)


def is_bold(role: "TypeRole") -> bool:
    """Whether the role is drawn bold - `compile`'s rule, stated once."""
    return role.weight in ("semibold", "bold")


def as_drawn(text: str, role: "TypeRole") -> str:
    """The characters the renderer actually draws for this text.

    Small capitals are measured as full capitals. The typesetter has no
    notion of a reduced capital, and erring wide is the safe direction for
    a measurement whose job is to say whether copy fits.
    """
    if role.case in ("upper", "small"):
        return text.upper()
    return text


def set_copy(text: str, role: "TypeRole", family: str, width_pt: float,
             height_pt: float, *, columns: int = 1,
             column_gap_pt: float = 12.0) -> "TextLayout":
    """Set this copy in a box exactly as the renderer will draw it."""
    from app.renderer.typeset.shape import layout

    return layout(
        as_drawn(text, role), width_pt, height_pt, family, role.size_pt,
        line_height_pt=role.line_height_pt, bold=is_bold(role),
        tracking_per_mille=role.tracking_per_mille,
        columns=columns, column_gap_pt=column_gap_pt,
    )


def capacity(role: "TypeRole", family: str, width_pt: float,
             lines: int) -> int:
    """How many characters of ordinary prose `lines` lines of this role hold.

    Found by setting `SAMPLE_PROSE` at this width and counting the
    characters on the first `lines` lines, the spaces between them
    included, so word-wrap loss is in the number and not assumed away.

    Where the lines happen to break moves that count by up to a word a line,
    which at display sizes - three or four words to a line - is a quarter
    of it. The prose is set from several starting words and the fewest
    characters any of them fits is the capacity, because drafting is told
    this number as a limit: copy written to it should fit whichever words
    it happens to be made of.
    """
    if lines <= 0 or width_pt <= 0:
        return 0
    return _capacity(family, role.size_pt, is_bold(role),
                     role.case in ("upper", "small"),
                     role.tracking_per_mille, round(width_pt, 2), int(lines))


# How many starting words the sample is set from.
CAPACITY_STARTS = 12


@functools.lru_cache(maxsize=4096)
def _capacity(family: str, size_pt: float, bold: bool, capitals: bool,
              tracking_per_mille: int, width_pt: float, lines: int) -> int:
    from app.renderer.typeset.shape import wrap

    words = SAMPLE_PROSE.split()
    step = max(1, len(words) // CAPACITY_STARTS)
    fewest = None
    for start in range(0, step * CAPACITY_STARTS, step):
        sample = " ".join(words[start:] + words[:start]) + " "
        while True:
            text = sample.upper() if capitals else sample
            set_lines = wrap(text, width_pt, family, size_pt, bold=bold,
                             tracking_per_mille=tracking_per_mille)
            # The sample's last line is short only because the sample
            # ended, so it never counts: more lines must be set than kept.
            if len(set_lines) > lines:
                break
            sample += sample
        held = set_lines[:lines]
        count = sum(len(line) for line in held) + len(held) - 1
        fewest = count if fewest is None else min(fewest, count)
    return max(1, fewest or 1)
