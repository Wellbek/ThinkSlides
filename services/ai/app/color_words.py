"""
A colour the user typed, as something the deck can actually be built in.

The colour question offers a few swatches and a free-text box, and the
free-text box did nothing: ``_apply_colour_choice`` searched the reply for
``#RRGGBB`` and gave up otherwise, so "purple" was read as no answer at
all and the deck came back in whatever the direction proposed.

Two things have to work.

**A colour by name.** "purple", "forest green", "navy", "off-white" are
how people say colours; a hex is how the renderer wants them.

**A colour used as an accent.** "White with slight pink accents" is not a
request for a pink deck and not a request to drop the pink - it is a light
deck whose rules, glyphs and chart series are pink. The palette already
separates ``primary``/``dark``/``light`` from ``accent``, so a request that
names both roles can set them separately.

Deliberately conservative: a phrase this cannot read returns ``None``,
which is the same as answering nothing, rather than a guess that repaints
the deck in a colour nobody asked for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app import colors

# Colour words, longest first at match time so "forest green" wins over
# "green" and "off white" over "white". Design vocabulary alongside the CSS
# names, because people write "navy" and "charcoal" far more often than
# they write "midnightblue".
NAMED_COLOURS: dict[str, str] = {
    "black": "#000000", "white": "#FFFFFF", "off white": "#F7F5F0",
    "off-white": "#F7F5F0", "cream": "#F5EFE0", "ivory": "#FFFFF0",
    "bone": "#EFE8DC", "charcoal": "#36393D", "graphite": "#2B2F33",
    "grey": "#808080", "gray": "#808080", "silver": "#C0C0C0",
    "slate": "#54626F", "stone": "#A8A29E",
    "red": "#D62828", "crimson": "#B01B2E", "scarlet": "#D0342C",
    "burgundy": "#6E1423", "maroon": "#7B1E28", "brick": "#9C3B2E",
    "rust": "#B7410E", "terracotta": "#C56A4E",
    "orange": "#F26419", "amber": "#F0A202", "tangerine": "#F28500",
    "coral": "#FF7F50", "peach": "#F5B895", "apricot": "#F6A96A",
    "yellow": "#F2C511", "gold": "#D4AF37", "mustard": "#D0A215",
    "lemon": "#F4E04D", "sand": "#D9C7A3",
    "green": "#2E7D32", "forest green": "#1B4332", "forest": "#1B4332",
    "emerald": "#0F7B4F", "olive": "#6B7F3A", "sage": "#8A9A7B",
    "mint": "#8FD6B4", "lime": "#7CB518", "moss": "#4A5D23",
    "teal": "#0F766E", "turquoise": "#12A5A0", "aqua": "#31C9C1",
    "cyan": "#22B8CF",
    "blue": "#1D4ED8", "navy": "#14213D", "royal blue": "#2447D6",
    "sky blue": "#5BA8E5", "sky": "#5BA8E5", "cobalt": "#1B4AC4",
    "steel blue": "#4A7BA7", "powder blue": "#A9C7DE", "denim": "#3A5F8A",
    "indigo": "#3F3D9E", "periwinkle": "#8C90D8",
    "purple": "#6B21A8", "violet": "#7A3FC4", "lavender": "#B7A6DE",
    "plum": "#6A2C55", "aubergine": "#4A203E", "mauve": "#A47DA0",
    "magenta": "#C2258F", "fuchsia": "#C1359B",
    "pink": "#E86A92", "rose": "#D45D79", "blush": "#F3C4CB",
    "salmon": "#F09080",
    "brown": "#6F4E37", "chocolate": "#4E342E", "tan": "#C39A6B",
    "beige": "#E8DCC4", "khaki": "#B5A642", "copper": "#B87333",
    "bronze": "#A0703A",
}

_NAMES_LONGEST_FIRST = sorted(NAMED_COLOURS, key=len, reverse=True)
_NAME_RE = re.compile(
    r"(?<![a-z])(" + "|".join(re.escape(n) for n in _NAMES_LONGEST_FIRST) + r")(?![a-z])"
)

# What a word before a colour does to it. Lightness first, then how much
# of the hue survives - "muted teal" is the same hue, less of it.
MODIFIERS: dict[str, tuple[Optional[float], float]] = {
    "light": (0.72, 0.9), "pale": (0.82, 0.7), "soft": (0.74, 0.75),
    "slight": (0.80, 0.7), "subtle": (0.80, 0.7), "faint": (0.86, 0.6),
    "muted": (None, 0.55), "dusty": (0.68, 0.55), "washed": (0.84, 0.5),
    "dark": (0.24, 1.0), "deep": (0.22, 1.05), "rich": (0.30, 1.15),
    "bright": (0.55, 1.35), "vivid": (0.52, 1.45), "bold": (0.45, 1.3),
    "electric": (0.55, 1.5), "pastel": (0.84, 0.6),
}
_MODIFIER_RE = re.compile(
    r"(?<![a-z])(" + "|".join(sorted(MODIFIERS, key=len, reverse=True)) + r")(?![a-z])"
)

# Words that make the colour beside them a highlight rather than the deck.
_ACCENT_AFTER = re.compile(
    r"^\W*(accent|accents|accented|highlight|highlights|detail|details|trim|"
    r"pop|pops|touch|touches|hint|hints)\b"
)
_ACCENT_BEFORE = re.compile(
    r"(accent[s]?\s+(?:of|in)|highlight[s]?\s+(?:of|in)|touch(?:es)?\s+of|"
    r"hint[s]?\s+of|pop[s]?\s+of|flash(?:es)?\s+of|with\s+a\s+little|"
    r"with\s+some)\s*$"
)

# "As proposed" is an answer, and it is an answer of "change nothing".
_NO_CHANGE = re.compile(
    r"as proposed|colours in the previews|colors in the previews|"
    r"leave (it|them) as|keep (it|them) as|no change",
    re.IGNORECASE,
)

# How close to white a colour has to be before it reads as the paper rather
# than as the deck's colour.
NEAR_WHITE_LIGHTNESS = 0.85


@dataclass(frozen=True)
class ColourRequest:
    """A colour answer, split into the two roles a palette has."""

    #: The deck's dominant colour, when one was named.
    base: Optional[str] = None
    #: What the highlights are, when the phrase gave the colour that role.
    accent: Optional[str] = None
    #: What the user actually wrote, for the log and the narration.
    phrase: str = ""

    def __bool__(self) -> bool:
        return bool(self.base or self.accent)

    @property
    def base_is_paper(self) -> bool:
        """Whether the base is light enough to be the page rather than the ink."""
        return bool(self.base) and is_near_white(self.base or "")


def is_near_white(hex_value: str) -> bool:
    parsed = colors._to_hls(hex_value)
    return bool(parsed and parsed[1] >= NEAR_WHITE_LIGHTNESS)


def _apply_modifier(hex_value: str, word: Optional[str]) -> str:
    if not word:
        return hex_value
    lightness, saturation = MODIFIERS[word]
    current = colors._to_hls(hex_value)
    if current is None:
        return hex_value
    return colors.with_lightness(
        hex_value, lightness if lightness is not None else current[1], saturation
    )


def _preceding_modifier(text: str, at: int) -> Optional[str]:
    """The modifier immediately before a colour word, if there is one."""
    before = text[max(0, at - 24):at]
    found = list(_MODIFIER_RE.finditer(before))
    if not found:
        return None
    # Only if it is genuinely adjacent - "dark slides with a green rule"
    # must not read as "dark green".
    tail = before[found[-1].end():]
    return found[-1].group(1) if re.fullmatch(r"[\s-]*", tail) else None


def parse(text: str) -> Optional[ColourRequest]:
    """
    Read a colour answer, or return ``None`` if it names no colour.

    A hex anywhere in the reply wins outright - it is what the offered
    swatches carry, and it is unambiguous.
    """
    raw = str(text or "").strip()
    if not raw:
        return None

    hexes = [m.group(0).upper() for m in re.finditer(r"#[0-9A-Fa-f]{6}", raw)]
    lowered = raw.lower()

    mentions: list[tuple[str, bool]] = []
    for match in _NAME_RE.finditer(lowered):
        value = _apply_modifier(
            NAMED_COLOURS[match.group(1)],
            _preceding_modifier(lowered, match.start()),
        )
        is_accent = bool(
            _ACCENT_AFTER.match(lowered[match.end():match.end() + 24])
            or _ACCENT_BEFORE.search(lowered[:match.start()])
        )
        mentions.append((value, is_accent))

    if hexes:
        # A picked swatch, possibly alongside a word describing its role.
        accent_words = [v for v, is_accent in mentions if is_accent]
        if accent_words and len(hexes) == 1 and not any(
            not is_accent for _, is_accent in mentions
        ):
            return ColourRequest(base=hexes[0], accent=accent_words[0], phrase=raw)
        base, accent = hexes[0], (hexes[1] if len(hexes) > 1 else None)
        return ColourRequest(base=base, accent=accent or (accent_words[0] if accent_words else None),
                             phrase=raw)

    if not mentions:
        return None
    if _NO_CHANGE.search(raw) and len(mentions) == 0:
        return None

    grounds = [v for v, is_accent in mentions if not is_accent]
    accents = [v for v, is_accent in mentions if is_accent]
    if not accents and len(grounds) > 1:
        # "white and pink" - the second colour is the one doing less work.
        grounds, accents = grounds[:1], grounds[1:2]
    return ColourRequest(base=grounds[0] if grounds else None,
                         accent=accents[0] if accents else None,
                         phrase=raw)


def name_for(hex_value: str) -> str:
    """The closest colour word to a hex, for naming an offered swatch.

    A swatch has to be readable as a choice - "Teal" is a choice, "#0F766E"
    is a value.
    """
    target = colors.parse_hex(hex_value)
    if target is None:
        return "Custom"
    best, best_distance = "Custom", None
    for name, candidate in NAMED_COLOURS.items():
        rgb = colors.parse_hex(candidate)
        if rgb is None:
            continue
        distance = sum((a - b) ** 2 for a, b in zip(target, rgb))
        if best_distance is None or distance < best_distance:
            best, best_distance = name, distance
    return best[:1].upper() + best[1:]
