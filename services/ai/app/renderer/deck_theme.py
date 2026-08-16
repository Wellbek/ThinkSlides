"""
Resolves a run's design system into the concrete per-slide styling the
renderer applies: background, text colours, the repeated accent motif, and
the footer.

Before this existed the palette's ``secondary``/``dark``/``light``/``gray``
slots were extracted, shown to the model, and then never used - the only
token that reached a slide was ``primary``, as the title's text colour. Every
slide in every deck was therefore a white page with a coloured heading, seven
times over. What makes a deck look designed is not more colours, it is
*rhythm*: a dark cover and closing against light content slides, one motif
repeated in the same place on every slide, and a consistent footer.

Roles, and what each one means visually:

* ``cover``   - the opening slide. Brand-dark background, white title.
* ``section`` - a divider between parts of the deck. Same treatment as cover.
* ``content`` - the working slides. White background, brand title, accent rule.
* ``closing`` - the final slide. Dark again, closing the deck symmetrically.
"""

from __future__ import annotations
from dataclasses import dataclass, field

from app import colors

ROLE_COVER = "cover"
ROLE_SECTION = "section"
ROLE_CONTENT = "content"
ROLE_CLOSING = "closing"

ROLES = frozenset({ROLE_COVER, ROLE_SECTION, ROLE_CONTENT, ROLE_CLOSING})

# The roles that get a hero treatment - a stepped-up title, no chrome. Named
# for the ground they were once guaranteed to sit on, which is no longer true:
# a design direction can put a cover on a light ground, and the role still
# has to survive that.
DARK_ROLES = frozenset({ROLE_COVER, ROLE_SECTION, ROLE_CLOSING})

# Near-black rather than pure black: #000 against white is harsher than any
# real deck uses, and reads as "unstyled default" at body sizes.
BODY_ON_LIGHT = "#1F2328"

_FALLBACK = {
    "primary": "#1F4E78",
    "secondary": "#2980B9",
    "accent": "#FF7F50",
    "dark": "#15334F",
    "light": "#F0F4F8",
    "white": "#FFFFFF",
    "gray": "#808080",
}


@dataclass(frozen=True)
class SlideStyle:
    """Everything the renderer needs to paint one slide's chrome and text."""
    role: str
    background: str
    title_color: str
    body_color: str
    bullet_color: str
    accent_color: str
    footer_color: str

    @property
    def on_dark(self) -> bool:
        return colors.is_dark(self.background)


@dataclass(frozen=True)
class DeckTheme:
    primary: str
    secondary: str
    accent: str
    dark: str
    light: str
    white: str
    gray: str
    # Where each kind of slide sits, when a design system says so explicitly.
    # Without this every deck put its cover on the dark tone and its content on
    # white, which meant three "different" design directions rendered three
    # identical covers - the difference between them was a background colour on
    # the content pages and nothing else.
    role_grounds: dict = field(default_factory=dict)

    @classmethod
    def from_design_system(cls, design_system: dict | None) -> "DeckTheme":
        palette = ((design_system or {}).get("palette") or {}).get("colors") or []
        by_name = {
            entry.get("name"): entry.get("value")
            for entry in palette
            if isinstance(entry, dict) and colors.parse_hex(entry.get("value"))
        }
        resolved = {name: by_name.get(name) or default for name, default in _FALLBACK.items()}
        declared = (design_system or {}).get("grounds")
        grounds = {
            role: value for role, value in (declared or {}).items()
            if isinstance(value, str) and colors.parse_hex(value)
        } if isinstance(declared, dict) else {}
        return cls(**resolved, role_grounds=grounds)

    def style_for(self, role: str) -> SlideStyle:
        """The concrete colours for a slide in ``role``."""
        declared = self.role_grounds.get(role)
        background = declared or (self.dark if role in DARK_ROLES else self.white)
        return self.style_on(background, role)

    def style_on(self, background: str, role: str = ROLE_CONTENT) -> SlideStyle:
        """
        The concrete colours for text sitting on ``background``.

        The ground decides the treatment, not the role. That distinction only
        started to matter once a slide could carry more than one ground: a
        content slide split white/navy needs both treatments *on the same
        slide*, and picking either one from the slide's role would leave half
        the text invisible - which is the exact bug ``_apply_text_color``
        exists to prevent, returning the moment a slide has two canvases.

        The ground decides the *treatment* and nothing else: the role is
        carried through both branches untouched. It used to be rewritten to
        ``content`` on any light ground, on the assumption that a hero slide
        was always a dark one. A design direction breaks that assumption by
        construction - ``light`` puts the cover on a tinted ground over white
        pages - and a cover that came back labelled ``content`` was a cover
        with a footer, a page number and a brand mark stamped on it, its
        standfirst set as a bulleted agenda, and its title at body size.
        """
        if not colors.parse_hex(background):
            background = self.white

        if colors.is_dark(background):
            return SlideStyle(
                role=role if role in ROLES else ROLE_CONTENT,
                background=background,
                # White, not the brand colour: a brand hex deep enough to serve
                # as a dark background is by definition too close to it to read
                # as text on top of it.
                title_color=colors.readable_on(background, self.white),
                body_color=colors.readable_on(background, self.light, (self.white,)),
                bullet_color=colors.readable_on(background, self.accent,
                                                (self.light, self.white)),
                accent_color=colors.readable_on(background, self.accent,
                                                (self.light, self.white)),
                # Deliberately quiet: a footer that competes with the content
                # is worse than no footer.
                footer_color=colors.mix(background, self.light, 0.55),
            )

        return SlideStyle(
            role=role if role in ROLES else ROLE_CONTENT,
            background=background,
            title_color=colors.readable_on(background, self.primary, (self.dark,)),
            body_color=colors.readable_on(background, BODY_ON_LIGHT, (self.dark, self.white)),
            bullet_color=colors.readable_on(background, self.primary, (self.dark,),
                                            minimum=3.0),
            accent_color=colors.readable_on(background, self.accent, (self.primary, self.dark),
                                            minimum=1.6),
            footer_color=colors.readable_on(background, self.gray, (BODY_ON_LIGHT,),
                                            minimum=3.0),
        )


# Which layout catalog entry a rendered slide should be laid out against.
# Keyed by the LayoutType the renderer inferred from the slide's contents.
LAYOUT_ID_FOR_TYPE = {
    "title": "title",
    "section": "section",
    "content": "content",
    "two_column": "twoColumn",
    "image": "image",
    "table": "table",
    "chart": "chart",
    "conclusion": "conclusion",
}


def layout_entry(design_system: dict | None, layout_id: str) -> dict | None:
    """The catalog entry for ``layout_id``, or None when it isn't published."""
    for entry in ((design_system or {}).get("layouts") or []):
        if isinstance(entry, dict) and entry.get("layoutId") == layout_id:
            return entry
    return None


def role_of(design_system: dict | None, layout_id: str, default: str = ROLE_CONTENT) -> str:
    """The catalog's declared role for a layout, defaulting to content."""
    entry = layout_entry(design_system, layout_id) or {}
    role = entry.get("role")
    return role if role in ROLES else default
