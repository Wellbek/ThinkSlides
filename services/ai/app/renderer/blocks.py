"""
The block vocabulary: the units a consultancy deck is actually built from.

There were four content types - text, image, table, chart - and the reference
deck is built from at least seven more. They are not decoration; they are how
it says things. A slide whose point is "56% of firms now use AI" says it with
a large coloured figure and a caption, not with a bullet reading "56% of firms
now use AI". A page comparing seven industries says it with seven ruled bands
of icon, label and implication, not with seven bullets.

Everything approximated as bulleted text is why generated slides read as lists
regardless of what they are saying.

**Blocks compile to primitives; they do not draw.** ``compile`` returns a list
of ``primitives.Primitive`` and touches no slide, which buys three things that
matter more than the drawing code it replaces:

* **Measurement cannot disagree with rendering.** A block's height is the
  bounding box of the very primitives that will be drawn, compiled at the same
  width - not a parallel estimate that drifts from the drawing code.
* **The escape hatch is the same layer blocks are built on.** A slide can drop
  to primitives for something no block covers ("a hairline two thirds across")
  without leaving the region model, the contrast enforcement or the geometry
  checks behind.
* **Every part of a block is addressable.** A stat callout's figure and its
  caption are separate primitives with stable ids, so "make that number
  smaller" can one day be a patch rather than a redesign.

Semantic colour is resolved in one place (``resolve_tone``) and used by both
the accent-bar cards and the legend that explains them, so "blue means
benefit, red means risk" cannot end up saying two different things on the
same slide.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from typing import Any, Optional

from app import colors
from app.observability.logging import get_logger
from app.renderer import apparatus, diagrams, icons, primitives
from app.renderer.deck_theme import SlideStyle
from app.renderer.layout import PositionConfig
from app.renderer.primitives import Primitive, TextRunSpec
from app.renderer.typography import TypeScale

# The payload key each block type carries - the one set the schema, the
# validator, the builder tools and the compiler all agree on.
BLOCK_PAYLOAD_KEY = {
    "statCallout": "statCallouts",
    "iconCard": "iconCards",
    "accentCard": "accentCards",
    "rowBand": "rowBands",
    "imageCardRow": "imageCardRow",
    "banner": "banner",
    "legend": "legend",
    "diagram": "diagram",
}
BLOCK_TYPES = frozenset(BLOCK_PAYLOAD_KEY)

# Semantic tones. The reference's benefit/risk cards (page 130) colour a card
# by what it *means* and put a legend on the slide saying so - which only
# works if both read the same table.
TONE_FALLBACKS = {
    "benefit": "#2251FF",
    "risk": "#E5321B",
    "positive": "#1E8449",
    "negative": "#C0392B",
    "neutral": "#8C97A8",
}
# Which palette slot a tone prefers, when the deck's own palette has one.
TONE_PALETTE_SLOT = {
    "benefit": "secondary",
    "neutral": "gray",
}
TONES = frozenset(TONE_FALLBACKS)

# Geometry, in inches.
HAIRLINE_IN = 0.014
ACCENT_BAR_IN = 0.055
BLOCK_GAP_IN = 0.10
ICON_BADGE_MAX_IN = 0.72
ICON_COLUMN_IN = 0.34
ROW_BAND_LABEL_SHARE = 0.24
ROW_BAND_GAP_IN = 0.13
LEGEND_SWATCH_IN = 0.13
BANNER_PAD_IN = 0.11
IMAGE_ASPECT = 0.72

# A stat callout's figure is sized off its own column rather than fixed, so
# four in a row and one on its own both read as the headline they are.
STAT_VALUE_MAX_PT = 54.0
STAT_VALUE_MIN_PT = 20.0
STAT_VALUE_PER_INCH_PT = 15.0

# Icons are rasterised at this size and scaled down; big enough that a badge
# at any slide size stays crisp, small enough to keep the PNG a few KB.
ICON_RASTER_PX = 192

# The nominal height a block is compiled into when it is only being measured.
# Compilers derive every height from their content, so nothing is clipped and
# nothing inherits this number.
MEASURE_HEIGHT_IN = 100.0

# Type scales a block is retried at when it does not fit the region it was
# given. The block shrinks as ONE thing: seven row bands set at seven sizes
# because each was fitted separately is exactly the "one type scale per slide"
# smell the composition rules exist to prevent.
FIT_STEPS = (1.0, 0.94, 0.88, 0.82, 0.76, 0.70, 0.64, 0.58, 0.52)


def resolve_tone(tone: Optional[str], design_system: Optional[dict],
                 default: str = "neutral") -> str:
    """
    A semantic tone as a concrete hex.

    An explicit ``#RRGGBB`` passes through, a named tone prefers the deck's own
    palette slot where it has one, and anything unrecognised falls back to the
    default rather than to an arbitrary colour - a card whose bar means nothing
    is worse than one with no bar.
    """
    raw = str(tone or "").strip()
    if colors.parse_hex(raw):
        return raw if raw.startswith("#") else f"#{raw}"

    name = raw.lower() or default
    if name not in TONE_FALLBACKS:
        name = default

    slot = TONE_PALETTE_SLOT.get(name)
    if slot:
        palette = ((design_system or {}).get("palette") or {}).get("colors") or []
        for entry in palette:
            if isinstance(entry, dict) and entry.get("name") == slot:
                value = entry.get("value")
                if colors.parse_hex(value):
                    return value
    return TONE_FALLBACKS[name]


@dataclass(frozen=True)
class BlockCompiler:
    """
    Turns a block into the marks that draw it.

    Holds no slide and draws nothing, which is what lets the same call measure
    a block and render it. Constructed with the style of the ground the block
    is landing on, so a block bound to a navy half colours itself for navy
    without knowing anything about regions.
    """
    style: SlideStyle
    type_scale: TypeScale
    design_system: dict
    # How far this block's type and spacing are scaled. Set by ``fit``, never
    # by a caller: it is the block's answer to "you have less room than you
    # asked for", and it applies to the whole block at once.
    scale: float = 1.0

    # ------------------------------------------------------------ measuring

    def _measure(self, text, size_pt: float, width_in: float,
                 bold: bool = False) -> float:
        """``primitives.measure_text``, told which face this deck is set in.

        Measurement now reads real advance widths, so it has to be given the
        right font: measuring Arial for a block that renders in a wider face
        reports a line that fits and draws one that does not. The old
        character-ratio estimate was face-blind, which hid the question by
        being equally wrong for everything.
        """
        return primitives.measure_text(
            text, size_pt, width_in, bold=bold,
            family=(self.type_scale.title_font if bold
                    else self.type_scale.body_font),
        )

    # ----------------------------------------------------------------- API

    def compile(self, kind: str, item: dict, rect: PositionConfig,
                id_prefix: str = "b") -> list[Primitive]:
        """The primitives that draw ``item`` inside ``rect``, at this scale."""
        handler = getattr(self, f"_compile_{_snake(kind)}", None)
        if handler is None or rect.width <= 0:
            return []
        return [p for p in handler(item, rect, id_prefix) if p is not None]

    def fit(self, kind: str, item: dict, rect: PositionConfig,
            id_prefix: str = "b") -> list[Primitive]:
        """
        Compile ``item`` at the largest scale that fits inside ``rect``.

        A block laid out at its natural size and handed a shorter region used
        to simply run off the bottom of the slide - seven row bands over the
        footer and past the edge. Retrying the whole block at a smaller scale
        keeps it inside its region *and* keeps its type uniform, which fitting
        each row separately would not.
        """
        if rect.height <= 0:
            return self.compile(kind, item, rect, id_prefix)
        for step in FIT_STEPS:
            compiled = replace(self, scale=step).compile(kind, item, rect, id_prefix)
            if not compiled:
                return compiled
            extent = primitives.bounds(compiled)
            if extent is None or extent.height <= rect.height + 0.01:
                return compiled
        return replace(self, scale=FIT_STEPS[-1]).compile(kind, item, rect, id_prefix)

    def measure(self, kind: str, item: dict, width_in: float) -> float:
        """
        The height this block needs at ``width_in``.

        Measured by compiling it and taking the extent of the result, so the
        number cannot drift from what is actually drawn - the failure mode of
        every parallel estimate.
        """
        if width_in <= 0:
            return 0.0
        compiled = replace(self, scale=1.0).compile(
            kind, item,
            PositionConfig(left=0.0, top=0.0, width=width_in,
                           height=MEASURE_HEIGHT_IN),
            id_prefix="measure",
        )
        extent = primitives.bounds(compiled)
        return max(0.05, extent.height) if extent else 0.0

    # ------------------------------------------------------- stat callouts

    def _compile_stat_callout(self, item: dict, rect: PositionConfig,
                              prefix: str) -> list[Primitive]:
        # "metrics" reaches here too - `compile._EXHIBIT_TO_BLOCK_KIND` sends
        # it to this method, because a metrics exhibit and a stat callout
        # are the same shape on the page. `build_key_metrics` writes its
        # items under the "metrics" key with a "label" field rather than
        # this block's own "statCallout"/"caption", so both are read.
        entries = _items(item, "statCallouts", "statCallout", "metrics")
        entries = [e for e in entries if str(e.get("value", "")).strip()]
        if not entries:
            return []

        column = _column_width(rect.width, len(entries))
        value_pt = self._pt(max(STAT_VALUE_MIN_PT,
                              min(STAT_VALUE_MAX_PT, column * STAT_VALUE_PER_INCH_PT)))
        caption_pt = self._pt(self.type_scale.body_style(1).font_size_pt)
        out: list[Primitive] = []

        for index, entry in enumerate(entries):
            left = rect.left + index * (column + BLOCK_GAP_IN)
            tone = entry.get("tone")
            value_color = (resolve_tone(tone, self.design_system) if tone
                           else self.style.accent_color)
            value = str(entry.get("value", "")).strip()
            value_height = self._measure(value, value_pt, column, bold=True)
            out.append(self._text(
                f"{prefix}-stat{index}-value", left, rect.top, column, value_height,
                value, value_pt, value_color, bold=True,
                font=self.type_scale.title_font,
            ))
            caption = str(entry.get("caption") or entry.get("label") or "").strip()
            if caption:
                caption_height = self._measure(caption, caption_pt, column)
                out.append(self._text(
                    f"{prefix}-stat{index}-caption", left,
                    rect.top + value_height + self._gap * 0.4,
                    column, caption_height, caption, caption_pt,
                    self.style.body_color, font=self.type_scale.body_font,
                ))
        return out

    # ----------------------------------------------------------- icon cards

    def _compile_icon_card(self, item: dict, rect: PositionConfig,
                           prefix: str) -> list[Primitive]:
        entries = _items(item, "iconCards")
        if not entries:
            return []

        column = _column_width(rect.width, len(entries))
        badge = min(ICON_BADGE_MAX_IN, column * 0.34)
        heading_pt = self._pt(self.type_scale.body_style(0).font_size_pt)
        body_pt = self._pt(self.type_scale.body_style(1).font_size_pt)
        out: list[Primitive] = []

        # Headings wrap to different depths, and letting each card start its
        # own body underneath its own heading staggers the body copy across
        # the row. A card row is read across, so the bodies share a baseline
        # and the headings hang above it.
        headings = [str(entry.get("heading") or "").strip() for entry in entries]
        heading_heights = [
            self._measure(heading, heading_pt, column, bold=True)
            for heading in headings
        ]
        heading_top = rect.top + badge + self._gap * 1.4
        body_top = heading_top + (max(heading_heights) if heading_heights else 0.0) \
            + self._gap * 0.5

        for index, entry in enumerate(entries):
            left = rect.left + index * (column + BLOCK_GAP_IN)
            heading = headings[index]
            out.extend(self._badge(
                f"{prefix}-card{index}", left, rect.top, badge,
                str(entry.get("icon") or heading),
            ))
            if heading:
                out.append(self._text(
                    f"{prefix}-card{index}-heading", left, heading_top, column,
                    heading_heights[index], heading, heading_pt,
                    self.style.title_color, bold=True,
                    font=self.type_scale.title_font,
                ))
            top = body_top
            for line_index, line in enumerate(_lines(entry.get("body"))):
                height = self._measure(line, body_pt, column)
                out.append(self._text(
                    f"{prefix}-card{index}-body{line_index}", left, top, column,
                    height, line, body_pt, self.style.body_color,
                    font=self.type_scale.body_font,
                ))
                top += height + self._gap * 0.5
        return out

    def _badge(self, prefix: str, left: float, top: float, size: float,
               concept: str) -> list[Primitive]:
        """A filled disc with a line-art glyph reversed out of it."""
        if size <= 0.05:
            return []
        fill_hex = self.style.accent_color
        out = [Primitive(
            id=f"{prefix}-badge", kind="ellipse",
            box=PositionConfig(left=left, top=top, width=size, height=size),
            fill=fill_hex,
        )]
        glyph_hex = colors.readable_on(fill_hex, self.style.background,
                                       ("#FFFFFF", "#1A1A1A"))
        png = icons.render_icon_png(concept, glyph_hex, size_px=ICON_RASTER_PX)
        if png:
            inset = size * 0.28
            out.append(_image_primitive(
                f"{prefix}-icon", png,
                PositionConfig(left=left + inset / 2, top=top + inset / 2,
                               width=size - inset, height=size - inset),
                f"{icons.resolve_glyph(concept)} icon",
            ))
        return out

    # --------------------------------------------------------- accent cards

    def _compile_accent_card(self, item: dict, rect: PositionConfig,
                             prefix: str) -> list[Primitive]:
        entries = _items(item, "accentCards")
        if not entries:
            return []

        columns = _clamp_columns(item.get("columns"))
        column = _column_width(rect.width, columns)
        text_width = max(0.4, column - ACCENT_BAR_IN - BLOCK_GAP_IN)
        heading_pt = self._pt(self.type_scale.body_style(0).font_size_pt)
        body_pt = self._pt(self.type_scale.body_style(1).font_size_pt)

        # Measured for every card first, because the bars of one row have to
        # be one length. Sizing each bar to its own card leaves three cards
        # side by side with three different rules beside them, which reads as
        # a mistake rather than as a set.
        measured = []
        for entry in entries:
            heading = str(entry.get("heading") or "").strip()
            body_lines = _lines(entry.get("body"))
            heading_height = self._measure(heading, heading_pt, text_width,
                                                     bold=True) if heading else 0.0
            body_heights = [self._measure(line, body_pt, text_width)
                            for line in body_lines]
            measured.append((
                heading, heading_height, body_lines, body_heights,
                (heading_height + sum(body_heights)
                 + self._gap * 0.4 * max(0, len(body_lines))),
            ))
        row_heights = [
            max(card[4] for card in measured[start:start + columns])
            for start in range(0, len(measured), columns)
        ]

        out: list[Primitive] = []
        tops = [rect.top] * columns
        for index, entry in enumerate(entries):
            slot = index % columns
            left = rect.left + slot * (column + BLOCK_GAP_IN)
            top = tops[slot]
            heading, heading_height, body_lines, body_heights, _ = measured[index]
            row_height = row_heights[index // columns]

            out.append(Primitive(
                id=f"{prefix}-accent{index}-bar", kind="rect",
                box=PositionConfig(left=left, top=top, width=ACCENT_BAR_IN,
                                   height=max(HAIRLINE_IN, row_height)),
                fill=resolve_tone(entry.get("tone"), self.design_system),
            ))
            text_left = left + ACCENT_BAR_IN + BLOCK_GAP_IN
            cursor = top
            if heading:
                out.append(self._text(
                    f"{prefix}-accent{index}-heading", text_left, cursor,
                    text_width, heading_height, heading, heading_pt,
                    self.style.title_color, bold=True,
                    font=self.type_scale.title_font,
                ))
                cursor += heading_height + self._gap * 0.4
            for line_index, (line, height) in enumerate(zip(body_lines, body_heights)):
                out.append(self._text(
                    f"{prefix}-accent{index}-body{line_index}", text_left, cursor,
                    text_width, height, line, body_pt, self.style.body_color,
                    font=self.type_scale.body_font,
                ))
                cursor += height + self._gap * 0.4
            tops[slot] = top + row_height + self._gap * 1.8
        return out

    # ------------------------------------------------------------ row bands

    def _compile_row_band(self, item: dict, rect: PositionConfig,
                          prefix: str) -> list[Primitive]:
        rows = _items(item, "rowBands", "rowBand")
        if not rows:
            return []

        label_pt = self._pt(self.type_scale.body_style(0).font_size_pt)
        prose_pt = self._pt(self.type_scale.body_style(1).font_size_pt)
        has_icons = any(str(row.get("icon") or "").strip() for row in rows)
        label_width, prose_width = _band_columns(rect.width, has_icons)
        rule_hex = colors.mix(self.style.background, self.style.body_color, 0.35)

        out: list[Primitive] = []
        top = rect.top
        for index, row in enumerate(rows):
            label = str(row.get("label") or "").strip()
            prose = str(row.get("prose") or "").strip()
            label_height = self._measure(label, label_pt, label_width,
                                                   bold=True)
            prose_height = self._measure(prose, prose_pt, prose_width)
            height = max(label_height, prose_height)

            out.append(Primitive(
                id=f"{prefix}-band{index}-rule", kind="rect",
                box=PositionConfig(left=rect.left, top=top, width=rect.width,
                                   height=HAIRLINE_IN),
                fill=rule_hex,
            ))
            body_top = top + HAIRLINE_IN + self._band_gap * 0.4
            left = rect.left
            if has_icons:
                concept = str(row.get("icon") or label)
                glyph = icons.render_icon_png(
                    concept, self.style.body_color, size_px=ICON_RASTER_PX
                )
                if glyph:
                    size = min(ICON_COLUMN_IN, max(0.16, height))
                    out.append(_image_primitive(
                        f"{prefix}-band{index}-icon", glyph,
                        PositionConfig(left=left, top=body_top, width=size,
                                       height=size),
                        f"{icons.resolve_glyph(concept)} icon",
                    ))
                left += ICON_COLUMN_IN + BLOCK_GAP_IN

            out.append(self._text(
                f"{prefix}-band{index}-label", left, body_top, label_width,
                max(label_height, 0.12), label, label_pt, self.style.title_color,
                bold=True, font=self.type_scale.title_font,
            ))
            out.append(self._text(
                f"{prefix}-band{index}-prose",
                left + label_width + BLOCK_GAP_IN * 2, body_top, prose_width,
                max(prose_height, 0.12), prose, prose_pt, self.style.body_color,
                font=self.type_scale.body_font,
            ))
            top = body_top + height + self._band_gap * 0.7
        return out

    # --------------------------------------------------------------- banner

    def _compile_banner(self, item: dict, rect: PositionConfig,
                        prefix: str) -> list[Primitive]:
        text = _banner_text(item)
        if not text:
            return []
        size_pt = self._pt(self.type_scale.body_style(0).font_size_pt * 1.15)
        rule_hex = colors.mix(self.style.background, self.style.body_color, 0.45)
        inner_width = max(0.4, rect.width - self._banner_pad * 2)
        body_height = self._measure(text, size_pt, inner_width, bold=True)

        top = rect.top
        out = [Primitive(
            id=f"{prefix}-banner-rule-top", kind="rect",
            box=PositionConfig(left=rect.left, top=top, width=rect.width,
                               height=HAIRLINE_IN),
            fill=rule_hex,
        )]
        out.append(self._text(
            f"{prefix}-banner-text", rect.left + self._banner_pad,
            top + HAIRLINE_IN + self._banner_pad, inner_width, body_height,
            text, size_pt, self.style.title_color, bold=True,
            font=self.type_scale.title_font, align="center",
        ))
        out.append(Primitive(
            id=f"{prefix}-banner-rule-bottom", kind="rect",
            box=PositionConfig(
                left=rect.left,
                top=top + HAIRLINE_IN + self._banner_pad * 2 + body_height,
                width=rect.width, height=HAIRLINE_IN,
            ),
            fill=rule_hex,
        ))
        return out

    # --------------------------------------------------------------- legend

    # ------------------------------------------------------------ diagram

    def _compile_diagram(self, item: dict, rect: PositionConfig,
                         prefix: str) -> list[Primitive]:
        """A structural graphic, drawn from the deck's own marks.

        Delegated rather than inlined: eight kinds of geometry is a module, and
        keeping it out of here means the diagram code can be read without
        wading through seven other block types. What stays here is the part
        that is this class's business - turning the deck's design system into
        the colours and sizes the drawing uses, which is the same translation
        every other block does.
        """
        drawn = (item.get("diagram") or {}) if isinstance(item, dict) else {}
        if str(drawn.get("format") or "").lower() == "svg":
            # A figure the typed vocabulary cannot express, drawn as an SVG
            # and imported as shapes. It goes through the same block hook as
            # the eight typed kinds because it is the same thing to a page: a
            # graphic occupying a rect, in the deck's colours.
            from app.renderer.svg.importer import UnsupportedSVG, to_primitives

            try:
                return to_primitives(
                    str(drawn.get("svg") or ""), rect,
                    palette=self._palette_by_role(), prefix=prefix,
                    body_font=self.type_scale.body_font,
                )
            except UnsupportedSVG as exc:
                # Named rather than swallowed. A figure that silently did not
                # draw is a hole in a page nobody can explain.
                get_logger(__name__).warning("svg_figure_refused",
                                             error=str(exc))
                return []

        return diagrams.compile_diagram(
            item, rect,
            palette=self._diagram_palette(),
            label_pt=self._pt(self.type_scale.body_style(1).font_size_pt),
            detail_pt=max(
                diagrams.MIN_DETAIL_PT,
                self._pt(self.type_scale.body_style(2).font_size_pt),
            ),
            prefix=prefix,
        )

    def _palette_by_role(self) -> dict[str, str]:
        """The deck's palette keyed by role, which is what recolouring wants."""
        entries = ((self.design_system or {}).get("palette") or {}).get("colors") or []
        return {
            str(entry.get("name")): str(entry.get("value"))
            for entry in entries
            if isinstance(entry, dict) and colors.parse_hex(entry.get("value"))
        }

    def _diagram_palette(self) -> "diagrams.DiagramPalette":
        """The deck's colours, in the shape a diagram draws from."""
        palette = ((self.design_system or {}).get("palette") or {}).get("colors") or []
        sequence = [
            entry.get("value") for entry in palette
            if isinstance(entry, dict) and colors.parse_hex(entry.get("value"))
            and entry.get("name") not in {"background", "text"}
        ]
        return diagrams.DiagramPalette(
            ground=self.style.background,
            ink=self.style.body_color,
            heading=self.style.title_color,
            accent=self.style.accent_color,
            rule=colors.mix(self.style.background, self.style.body_color, 0.45),
            sequence=sequence or [self.style.accent_color],
            font=self.type_scale.body_font,
        )

    def _compile_legend(self, item: dict, rect: PositionConfig,
                        prefix: str) -> list[Primitive]:
        entries = [e for e in _items(item, "legend")
                   if str(e.get("label", "")).strip()]
        if not entries:
            return []
        size_pt = self._pt(self.type_scale.body_style(1).font_size_pt)
        column = _column_width(rect.width, len(entries))
        label_width = max(0.3, column - LEGEND_SWATCH_IN - self._gap * 0.5)
        out: list[Primitive] = []

        for index, entry in enumerate(entries):
            left = rect.left + index * (column + BLOCK_GAP_IN)
            label = str(entry.get("label")).strip()
            height = self._measure(label, size_pt, label_width)
            out.append(Primitive(
                id=f"{prefix}-legend{index}-swatch", kind="rect",
                box=PositionConfig(
                    left=left, top=rect.top + max(0.0, (height - LEGEND_SWATCH_IN) / 2),
                    width=LEGEND_SWATCH_IN, height=LEGEND_SWATCH_IN,
                ),
                fill=resolve_tone(entry.get("tone") or entry.get("color"),
                                  self.design_system),
            ))
            out.append(self._text(
                f"{prefix}-legend{index}-label",
                left + LEGEND_SWATCH_IN + self._gap * 0.5, rect.top,
                label_width, height, label, size_pt, self.style.body_color,
                font=self.type_scale.body_font,
            ))
        return out

    # ------------------------------------------------------ image card row

    def _compile_image_card_row(self, item: dict, rect: PositionConfig,
                                prefix: str) -> list[Primitive]:
        payload = item.get("imageCardRow") or {}
        cards = [c for c in (payload.get("cards") or []) if isinstance(c, dict)]
        if not cards:
            return []
        groups = [g for g in (payload.get("groups") or []) if isinstance(g, dict)]
        column = _column_width(rect.width, len(cards))
        title_pt = self._pt(self.type_scale.body_style(0).font_size_pt)
        caption_pt = self._pt(self.type_scale.body_style(1).font_size_pt)
        rule_hex = colors.mix(self.style.background, self.style.body_color, 0.35)

        out: list[Primitive] = []
        top = rect.top
        if groups:
            # A group heading spans an unequal subset of the row, which is what
            # makes it a grouping rather than a per-card label.
            group_height = self._measure("Ag", title_pt, rect.width, bold=True)
            cursor = 0
            for index, group in enumerate(groups):
                span = max(1, int(group.get("span") or 1))
                width = span * column + (span - 1) * BLOCK_GAP_IN
                out.append(self._text(
                    f"{prefix}-group{index}", rect.left + cursor * (column + BLOCK_GAP_IN),
                    top, width, group_height,
                    str(group.get("label") or "").strip(), title_pt,
                    self.style.title_color, bold=True,
                    font=self.type_scale.title_font,
                ))
                cursor += span
            top += group_height + self._gap * 0.6

        image_height = column * IMAGE_ASPECT
        # Titles wrap to different depths; captions share a baseline for the
        # same reason card bodies do.
        title_heights = [
            self._measure(str(card.get("title") or ""), title_pt, column,
                                    bold=True)
            for card in cards
        ]
        tallest_title = max(title_heights) if title_heights else 0.0

        for index, card in enumerate(cards):
            left = rect.left + index * (column + BLOCK_GAP_IN)
            image = card.get("image")
            box = PositionConfig(left=left, top=top, width=column, height=image_height)
            if isinstance(image, dict):
                out.append(Primitive(
                    id=f"{prefix}-image{index}", kind="image", box=box, image=image,
                    alt_text=str(image.get("altText") or card.get("title") or ""),
                ))
            else:
                # A card whose image is missing keeps its slot, tinted, rather
                # than collapsing the row and misaligning every card after it.
                out.append(Primitive(
                    id=f"{prefix}-image{index}-placeholder", kind="rect", box=box,
                    fill=colors.mix(self.style.background, self.style.body_color, 0.12),
                ))

            rule_top = top + image_height + self._gap * 0.4
            out.append(Primitive(
                id=f"{prefix}-image{index}-rule", kind="rect",
                box=PositionConfig(left=left, top=rule_top, width=column,
                                   height=HAIRLINE_IN),
                fill=rule_hex,
            ))
            cursor = rule_top + HAIRLINE_IN + self._gap * 0.35
            title = str(card.get("title") or "").strip()
            if title:
                out.append(self._text(
                    f"{prefix}-image{index}-title", left, cursor, column,
                    title_heights[index], title, title_pt, self.style.title_color,
                    bold=True, font=self.type_scale.title_font,
                ))
            cursor += tallest_title + self._gap * 0.3
            caption = str(card.get("caption") or "").strip()
            if caption:
                height = self._measure(caption, caption_pt, column)
                out.append(self._text(
                    f"{prefix}-image{index}-caption", left, cursor, column, height,
                    caption, caption_pt, self.style.body_color,
                    font=self.type_scale.body_font,
                ))
        return out

    # ----------------------------------------------------------- primitives

    def _pt(self, size_pt: float) -> float:
        """A point size at this block's fitted scale."""
        return max(6.0, size_pt * self.scale)

    @property
    def _gap(self) -> float:
        """The block's internal gap, scaled with its type."""
        return BLOCK_GAP_IN * self.scale

    @property
    def _band_gap(self) -> float:
        return ROW_BAND_GAP_IN * self.scale

    @property
    def _banner_pad(self) -> float:
        return BANNER_PAD_IN * self.scale

    def _text(self, primitive_id: str, left: float, top: float, width: float,
              height: float, text: str, size_pt: float, color: str,
              bold: bool = False, font: Optional[str] = None,
              align: str = "left") -> Optional[Primitive]:
        if not str(text or "").strip():
            return None
        return Primitive(
            id=primitive_id, kind="text",
            box=PositionConfig(left=left, top=top, width=max(0.1, width),
                               height=max(0.08, height)),
            runs=_runs_with_markers(str(text), bold),
            size_pt=size_pt, color=color, bold=bold,
            font=font or self.type_scale.body_font, align=align,
        )


# --------------------------------------------------------------------------


def _image_primitive(primitive_id: str, png: bytes, box: PositionConfig,
                     alt_text: str) -> Primitive:
    """
    A rendered PNG as an image primitive.

    Base64 rather than a path: a block compiles to data with no side effects,
    so the same call can measure it or draw it, and nothing has to clean up a
    temp file that measurement never wanted in the first place. The encoding
    never reaches the model - blocks are compiled inside the renderer, and the
    slide JSON only ever holds the block.
    """
    return Primitive(
        id=primitive_id, kind="image", box=box, alt_text=alt_text,
        image={"source": "base64",
               "data": base64.b64encode(png).decode("ascii")},
    )


def _runs_with_markers(text: str, bold: bool) -> tuple[TextRunSpec, ...]:
    """
    A block's text, with any inline footnote marker as its own superscript run.

    A stat callout's caption and a row band's implication are exactly where the
    reference deck puts markers, so a block has to carry them like body copy
    does rather than printing "[^1]" literally.
    """
    if not apparatus.has_marker(text):
        return (TextRunSpec(text=text, bold=bold),)
    return tuple(
        TextRunSpec(text=part, bold=bold, superscript=marker is not None)
        for part, marker in apparatus.split_markers(text)
        if part
    )


def _snake(kind: str) -> str:
    out = []
    for char in kind:
        if char.isupper():
            out.append("_")
            out.append(char.lower())
        else:
            out.append(char)
    return "".join(out)


def _items(item: dict, *keys: str) -> list[dict]:
    """The entries a block carries, under whichever key it used."""
    for key in keys:
        payload = item.get(key)
        if payload is None:
            continue
        if isinstance(payload, dict):
            payload = payload.get("items") or payload.get("rows") \
                or payload.get("cards") or [payload]
        if isinstance(payload, (list, tuple)):
            return [entry for entry in payload if isinstance(entry, dict)]
    return []


def _lines(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v or "").strip()]
    return [str(value)]


def _banner_text(item: dict) -> str:
    payload = item.get("banner") or {}
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, dict):
        return str(payload.get("text") or "").strip()
    return ""


def _column_width(width_in: float, count: int) -> float:
    count = max(1, count)
    return max(0.3, (width_in - BLOCK_GAP_IN * (count - 1)) / count)


def _clamp_columns(value: Any) -> int:
    try:
        return max(1, min(3, int(value or 1)))
    except (TypeError, ValueError):
        return 1


def _band_columns(width_in: float, has_icons: bool) -> tuple[float, float]:
    usable = width_in - (ICON_COLUMN_IN + BLOCK_GAP_IN if has_icons else 0.0)
    label = max(0.6, usable * ROW_BAND_LABEL_SHARE)
    prose = max(0.6, usable - label - BLOCK_GAP_IN * 2)
    return label, prose


def block_entry_count(item: dict) -> int:
    """
    How many things a block carries.

    The density budget counts blocks, and "one block" is not the same amount
    of slide for a banner as for seven row bands - so it counts entries, and
    this is where that number comes from rather than from each caller's own
    reading of the payload.
    """
    kind = str(item.get("type") or "")
    if kind == "banner":
        return 1 if _banner_text(item) else 0
    if kind == "diagram":
        # A diagram is one exhibit however many nodes it has. Counting the
        # nodes would make a seven-step flow read as seven blocks and blow the
        # slide's whole block budget on one graphic.
        payload = item.get("diagram") or {}
        return 1 if (payload.get("items") or []) else 0
    if kind == "imageCardRow":
        payload = item.get("imageCardRow") or {}
        return len([c for c in (payload.get("cards") or []) if isinstance(c, dict)])
    key = BLOCK_PAYLOAD_KEY.get(kind)
    return len(_items(item, key)) if key else 0
