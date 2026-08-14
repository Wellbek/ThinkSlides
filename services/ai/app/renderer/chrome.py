"""
Deck chrome: the eyebrow, the brand mark, the page number, the footer rule.

Every content page in the reference deck carries three quiet pieces of
furniture - a section eyebrow top right naming the chapter, a brand mark
bottom right, and a page number beside it. The eyebrow is the one doing the
most work: it is what tells a reader where they are in a 184-page document,
and it is the difference between a deck and a pile of slides.

We had one footer band with one line of text in it, positioned by a constant
and drawn by a method. That is not a design system, it is a hardcoded footer,
and it means a design direction (#113) cannot move or drop chrome - which is
one of the things that distinguishes one look from another.

So chrome is **declared** in the design system and **compiled to primitives**,
the same way blocks are. Two consequences worth stating:

* Chrome is per **slide role**. A cover carries none of it; a section break
  carries a page number and nothing else. That is a property of the deck's
  design, not of the renderer, so it is declared rather than branched on.
* Chrome that a deck does not declare falls back to exactly the footer band
  that was there before, so a deck built before any of this renders the same.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app import colors
from app.renderer.deck_theme import (
    ROLE_CONTENT, ROLE_COVER, SlideStyle,
)
from app.renderer.layout import PositionConfig
from app.renderer.primitives import Primitive, TextRunSpec
from app.renderer.typography import TypeScale

# Where a piece of chrome sits, as a fraction of the canvas. Named positions
# rather than coordinates: a design direction moves chrome by naming a
# different corner, and every corner then insets consistently.
POSITIONS = {
    "topLeft": (0.0, 0.0),
    "topRight": (1.0, 0.0),
    "bottomLeft": (0.0, 1.0),
    "bottomRight": (1.0, 1.0),
}
DEFAULT_MARGIN_X = 0.033
DEFAULT_MARGIN_Y = 0.043

# A chrome line is small by definition - it is furniture, and furniture that
# competes with the content is worse than none.
DEFAULT_EYEBROW_PT = 8.5
DEFAULT_PAGE_NUMBER_PT = 9.0
DEFAULT_BRAND_PT = 9.0
CHROME_LINE_HEIGHT_IN = 0.22
CHROME_BLOCK_WIDTH_IN = 4.2
BRAND_IMAGE_HEIGHT_IN = 0.24

# What the eyebrow says. "sectionName" is the reference deck's own choice; a
# deck with no sections falls back rather than printing an empty line.
EYEBROW_SOURCES = frozenset({"sectionName", "deckTitle", "none"})

# Roles that carry no chrome unless the deck says otherwise. A cover already
# carries the deck's title, and no real deck numbers its cover.
DEFAULT_SUPPRESSED_ROLES = frozenset({ROLE_COVER})


@dataclass(frozen=True)
class ChromeElement:
    """One declared piece of chrome."""
    enabled: bool = True
    position: str = "bottomRight"
    size_pt: float = DEFAULT_PAGE_NUMBER_PT
    color: Optional[str] = None
    roles: Optional[frozenset] = None       # None means "every role that shows chrome"

    def shows_on(self, role: str) -> bool:
        return self.enabled and (self.roles is None or role in self.roles)


@dataclass(frozen=True)
class ChromeSpec:
    """The deck's chrome, resolved from its design system."""
    eyebrow: ChromeElement = field(
        default_factory=lambda: ChromeElement(
            enabled=False, position="topRight", size_pt=DEFAULT_EYEBROW_PT
        )
    )
    eyebrow_source: str = "sectionName"
    brand_mark: ChromeElement = field(
        default_factory=lambda: ChromeElement(
            enabled=False, position="bottomRight", size_pt=DEFAULT_BRAND_PT
        )
    )
    brand_kind: str = "text"                # "text" | "image"
    brand_value: str = ""
    page_number: ChromeElement = field(
        default_factory=lambda: ChromeElement(position="bottomRight")
    )
    page_number_format: str = "{n}"
    footer_label: ChromeElement = field(
        default_factory=lambda: ChromeElement(position="bottomLeft")
    )
    footer_rule: Optional[ChromeElement] = None
    suppressed_roles: frozenset = DEFAULT_SUPPRESSED_ROLES
    margin_x: float = DEFAULT_MARGIN_X
    margin_y: float = DEFAULT_MARGIN_Y

    @classmethod
    def from_design_system(cls, design_system: Optional[dict]) -> "ChromeSpec":
        """
        Read a deck's chrome declaration, defaulting to the footer band.

        A deck that declares nothing gets a footer label bottom left and a page
        number bottom right - which is exactly what the renderer drew before
        chrome was declarable, so nothing that already worked changes.
        """
        declared = ((design_system or {}).get("chrome") or {})
        if not isinstance(declared, dict):
            declared = {}

        spec = cls()
        eyebrow = _element(declared.get("eyebrow"), spec.eyebrow)
        brand = _element(declared.get("brandMark"), spec.brand_mark)
        page = _element(declared.get("pageNumber"), spec.page_number)
        footer = _element(declared.get("footer"), spec.footer_label)
        rule = _element(declared.get("footerRule"), None)

        source = str((declared.get("eyebrow") or {}).get("source")
                     if isinstance(declared.get("eyebrow"), dict) else "") or "sectionName"
        brand_declaration = declared.get("brandMark") if isinstance(
            declared.get("brandMark"), dict) else {}

        return cls(
            eyebrow=eyebrow,
            eyebrow_source=source if source in EYEBROW_SOURCES else "sectionName",
            brand_mark=brand,
            brand_kind=str(brand_declaration.get("kind") or "text").lower(),
            brand_value=str(brand_declaration.get("value") or ""),
            page_number=page,
            page_number_format=str(declared.get("pageNumberFormat") or "{n}"),
            footer_label=footer,
            footer_rule=rule,
            suppressed_roles=frozenset(
                declared.get("suppressedRoles") or DEFAULT_SUPPRESSED_ROLES
            ),
            margin_x=_positive(declared.get("marginX"), DEFAULT_MARGIN_X),
            margin_y=_positive(declared.get("marginY"), DEFAULT_MARGIN_Y),
        )

    def shows_any(self, role: str) -> bool:
        if role in self.suppressed_roles:
            return False
        return any(element.shows_on(role) for element in (
            self.eyebrow, self.brand_mark, self.page_number, self.footer_label,
        ))


def compile_chrome(
    spec: ChromeSpec,
    style: SlideStyle,
    type_scale: TypeScale,
    canvas: PositionConfig,
    role: str,
    slide_number: int,
    total_slides: int,
    deck_title: str = "",
    section_name: str = "",
) -> list[Primitive]:
    """
    The marks that draw this slide's chrome.

    Compiled rather than drawn, for the same reason blocks are: chrome is
    measured (the density checks have to know it is furniture), positioned
    against the canvas fractionally, and contrast-corrected against whatever
    ground it lands on.
    """
    if role in spec.suppressed_roles:
        return []

    marks: list[Primitive] = []
    colour = style.footer_color

    eyebrow_text = _eyebrow_text(spec, deck_title, section_name)
    if spec.eyebrow.shows_on(role) and eyebrow_text:
        marks.append(_line_mark(
            "chrome-eyebrow", eyebrow_text, spec.eyebrow, spec, canvas,
            type_scale, spec.eyebrow.color or colour,
        ))

    if spec.footer_label.shows_on(role) and deck_title.strip():
        marks.append(_line_mark(
            "chrome-footer", deck_title.strip(), spec.footer_label, spec, canvas,
            type_scale, spec.footer_label.color or colour,
        ))

    if spec.page_number.shows_on(role):
        marks.append(_line_mark(
            "chrome-page-number",
            _format_page_number(spec.page_number_format, slide_number, total_slides),
            spec.page_number, spec, canvas, type_scale,
            spec.page_number.color or colour,
        ))

    if spec.brand_mark.shows_on(role) and spec.brand_value:
        marks.append(_brand_mark(spec, canvas, type_scale, colour))

    if spec.footer_rule is not None and spec.footer_rule.shows_on(role):
        rule_colour = spec.footer_rule.color or colors.mix(
            style.background, style.body_color, 0.30
        )
        top = canvas.height * (1.0 - spec.margin_y) - CHROME_LINE_HEIGHT_IN * 0.6
        marks.append(Primitive(
            id="chrome-footer-rule", kind="rect",
            box=PositionConfig(
                left=canvas.width * spec.margin_x, top=top,
                width=canvas.width * (1.0 - spec.margin_x * 2), height=0.012,
            ),
            fill=rule_colour, chrome=True,
        ))

    return [mark for mark in marks if mark is not None]


def _eyebrow_text(spec: ChromeSpec, deck_title: str, section_name: str) -> str:
    """
    What the eyebrow says.

    A section name when the slide has one - which is the reference deck's own
    choice, and what makes the eyebrow a position indicator rather than a
    repeated title. A slide with no section falls back to the deck title
    rather than printing an empty line, and a deck that wants neither says
    ``"source": "none"``.
    """
    if spec.eyebrow_source == "none":
        return ""
    if spec.eyebrow_source == "deckTitle":
        return deck_title.strip()
    return (section_name or deck_title).strip()


def _line_mark(primitive_id: str, text: str, element: ChromeElement,
               spec: ChromeSpec, canvas: PositionConfig, type_scale: TypeScale,
               colour: str) -> Optional[Primitive]:
    if not str(text or "").strip():
        return None
    anchor_x, anchor_y = POSITIONS.get(element.position, POSITIONS["bottomRight"])
    width = min(CHROME_BLOCK_WIDTH_IN, canvas.width * (1.0 - spec.margin_x * 2))
    inset_x = canvas.width * spec.margin_x
    inset_y = canvas.height * spec.margin_y

    left = inset_x if anchor_x == 0.0 else canvas.width - inset_x - width
    top = inset_y if anchor_y == 0.0 else canvas.height - inset_y - CHROME_LINE_HEIGHT_IN
    align = "left" if anchor_x == 0.0 else "right"

    return Primitive(
        id=primitive_id, kind="text",
        box=PositionConfig(left=left, top=top, width=width,
                           height=CHROME_LINE_HEIGHT_IN),
        runs=(TextRunSpec(text=str(text)),),
        size_pt=element.size_pt, color=colour, align=align,
        anchor="middle" if anchor_y else "top",
        font=type_scale.body_font, autofit=False, wrap=False, chrome=True,
    )


def _brand_mark(spec: ChromeSpec, canvas: PositionConfig, type_scale: TypeScale,
                colour: str) -> Optional[Primitive]:
    """
    The brand mark, as words or as a picture.

    An image mark is placed to the *left* of the page number rather than in
    the same corner, because a logo and a number sharing a corner is how the
    two end up on top of each other at the one point in the deck where the
    page number reaches three digits.
    """
    anchor_x, anchor_y = POSITIONS.get(spec.brand_mark.position,
                                       POSITIONS["bottomRight"])
    inset_x = canvas.width * spec.margin_x
    inset_y = canvas.height * spec.margin_y
    page_gutter = 0.5 if spec.page_number.shows_on(ROLE_CONTENT) and \
        spec.page_number.position == spec.brand_mark.position else 0.0

    if spec.brand_kind == "image":
        width = BRAND_IMAGE_HEIGHT_IN * 4.0
        left = (inset_x if anchor_x == 0.0
                else canvas.width - inset_x - width - page_gutter)
        top = (inset_y if anchor_y == 0.0
               else canvas.height - inset_y - BRAND_IMAGE_HEIGHT_IN)
        return Primitive(
            id="chrome-brand", kind="image",
            box=PositionConfig(left=left, top=top, width=width,
                               height=BRAND_IMAGE_HEIGHT_IN),
            image={"source": "url", "data": spec.brand_value},
            alt_text="Brand mark", chrome=True,
        )

    width = min(CHROME_BLOCK_WIDTH_IN, canvas.width * (1.0 - spec.margin_x * 2))
    left = (inset_x if anchor_x == 0.0
            else canvas.width - inset_x - width - page_gutter)
    top = (inset_y if anchor_y == 0.0
           else canvas.height - inset_y - CHROME_LINE_HEIGHT_IN)
    return Primitive(
        id="chrome-brand", kind="text",
        box=PositionConfig(left=left, top=top, width=width,
                           height=CHROME_LINE_HEIGHT_IN),
        runs=(TextRunSpec(text=spec.brand_value),),
        size_pt=spec.brand_mark.size_pt, color=spec.brand_mark.color or colour,
        align="left" if anchor_x == 0.0 else "right",
        anchor="middle" if anchor_y else "top",
        font=type_scale.body_font, autofit=False, wrap=False, chrome=True,
    )


def _format_page_number(template: str, slide_number: int, total: int) -> str:
    try:
        return template.format(n=slide_number, total=total)
    except (KeyError, IndexError, ValueError):
        return str(slide_number)


def _element(declared: Any, default: Optional[ChromeElement]) -> Optional[ChromeElement]:
    if declared is None:
        return default
    if declared is False:
        return ChromeElement(enabled=False) if default is None else \
            ChromeElement(enabled=False, position=default.position,
                          size_pt=default.size_pt)
    if not isinstance(declared, dict):
        return default

    base = default or ChromeElement()
    roles = declared.get("roles")
    return ChromeElement(
        enabled=bool(declared.get("enabled", True)),
        position=str(declared.get("position") or base.position),
        size_pt=_positive(declared.get("sizePt"), base.size_pt),
        color=_hex(declared.get("color")) or base.color,
        roles=frozenset(roles) if isinstance(roles, (list, tuple, set)) else base.roles,
    )


def _positive(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _hex(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    if not text.startswith("#"):
        text = f"#{text}"
    return text.upper() if colors.parse_hex(text) else None


def default_chrome_declaration() -> dict:
    """
    The chrome the built-in design system publishes.

    A footer label and a page number, which is what the renderer drew before
    chrome was declarable. The eyebrow and brand mark are declared but off:
    they exist so a design direction can switch them on, and so a template
    extractor has somewhere to put what it found.
    """
    return {
        "eyebrow": {"enabled": False, "position": "topRight",
                    "sizePt": DEFAULT_EYEBROW_PT, "source": "sectionName"},
        "brandMark": {"enabled": False, "position": "bottomRight",
                      "sizePt": DEFAULT_BRAND_PT, "kind": "text", "value": ""},
        "pageNumber": {"enabled": True, "position": "bottomRight",
                       "sizePt": DEFAULT_PAGE_NUMBER_PT},
        "footer": {"enabled": True, "position": "bottomLeft",
                   "sizePt": DEFAULT_PAGE_NUMBER_PT},
        "suppressedRoles": [ROLE_COVER],
    }


def reference_chrome_declaration() -> dict:
    """
    The reference deck's own arrangement, for decks that want it.

    Eyebrow top right naming the chapter, brand mark and page number bottom
    right, no footer label - the deck's title is on its cover, not on all 184
    of its pages.
    """
    return {
        "eyebrow": {"enabled": True, "position": "topRight",
                    "sizePt": DEFAULT_EYEBROW_PT, "source": "sectionName",
                    "roles": [ROLE_CONTENT]},
        "brandMark": {"enabled": True, "position": "bottomRight",
                      "sizePt": DEFAULT_BRAND_PT, "kind": "text",
                      "value": "", "roles": [ROLE_CONTENT]},
        "pageNumber": {"enabled": True, "position": "bottomRight",
                       "sizePt": DEFAULT_PAGE_NUMBER_PT},
        "footer": {"enabled": False},
        "suppressedRoles": [ROLE_COVER],
    }
