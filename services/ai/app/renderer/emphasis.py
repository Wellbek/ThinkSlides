"""
Markdown emphasis in copy a model wrote, and what to do about it.

A model asked for prose writes markdown sooner or later. Nothing anywhere
stripped it, so a real deck rendered `*Cars 2* leads in worldwide earnings` -
asterisks and all - on a slide a customer opened.

Rendering the markers is the one option that is always wrong. The other two
are both defensible: strip them, or honour them. This honours them where the
whole run is emphasised, because that is what the model meant, and strips them
otherwise - a run is one style, so half a run in italics is not expressible
without splitting it, and splitting a run to chase an asterisk is a worse
trade than losing the emphasis.

Deliberately narrow. This is not a markdown parser: a deck is not markdown,
and a renderer that starts interpreting `#` and `-` and `>` in slide copy will
eat a heading that was meant to be a hash.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Paired emphasis only, and never across a line break: `*` on its own is a
# bullet or a footnote marker in half the decks ever written.
_STRONG = re.compile(r"\*\*(?!\s)([^*\n]+?)(?<!\s)\*\*")
_EMPH = re.compile(r"(?<!\*)\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)")
_STRONG_U = re.compile(r"__(?!\s)([^_\n]+?)(?<!\s)__")

_WHOLE_STRONG = re.compile(r"^\*\*(?!\s)([^*\n]+?)(?<!\s)\*\*$")
_WHOLE_EMPH = re.compile(r"^\*(?!\s)([^*\n]+?)(?<!\s)\*$")


@dataclass(frozen=True)
class Emphasised:
    """One run's text, with any emphasis the markers asked for."""

    text: str
    bold: bool = False
    italic: bool = False


def read(text: str) -> Emphasised:
    """This run's copy, with the markers resolved rather than rendered."""
    raw = text or ""
    stripped = raw.strip()

    whole_strong = _WHOLE_STRONG.fullmatch(stripped)
    if whole_strong:
        return Emphasised(text=raw.replace(stripped, whole_strong.group(1)),
                          bold=True)
    whole_emph = _WHOLE_EMPH.fullmatch(stripped)
    if whole_emph:
        return Emphasised(text=raw.replace(stripped, whole_emph.group(1)),
                          italic=True)

    # Emphasis inside a longer run: the markers go, the styling cannot follow
    # without splitting the run, and a reader loses less by that than by
    # reading asterisks.
    out = _STRONG.sub(r"\1", raw)
    out = _STRONG_U.sub(r"\1", out)
    out = _EMPH.sub(r"\1", out)
    return Emphasised(text=out)


def strip(text: str) -> str:
    """Just the copy, for a caller that cannot carry the emphasis."""
    return read(text).text
