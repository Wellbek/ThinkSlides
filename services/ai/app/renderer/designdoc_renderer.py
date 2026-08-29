"""
Painting a DesignDoc into a .pptx.

Deliberately not a branch inside ``PPTXRenderer``. A DesignDoc page renders
onto a **blank** layout and inherits nothing: no placeholder geometry, no
master text defaults, none of ``_resize_shape``'s corrections for shapes
that were never meant to be where the layout put them. That whole apparatus
exists to make stock Office layouts behave, and a page whose geometry came
from a measured publication has no use for it.

Every text run is still a real PowerPoint run. Fidelity here does not cost
editability: the geometry is computed, but what lands in the file is a text
box a reader can click into and retype.
"""
from __future__ import annotations

import base64
import os
from typing import Optional

from pptx import Presentation
from pptx.util import Inches

from app.design.compile import compile_page
from app.design.geometry import check_doc, resolve_page
from app.design.pack import Pack
from app.design.schema import DesignDoc
from app.renderer import primitives
from app.renderer.primitives import PaintContext, Primitive
from app.renderer.typography import TypeScale

# python-pptx's stock master, in order. 6 is Blank - no placeholders at all,
# which is the only layout a computed page wants.
_BLANK_LAYOUT_INDEX = 6


def _type_scale(pack: Pack) -> TypeScale:
    """A TypeScale standing in for the pack, for the painters that ask."""
    body = pack.type_roles.get("body")
    display = pack.type_roles.get("headline") or pack.type_roles.get("display")
    return TypeScale(
        title_font=pack.display_face,
        body_font=pack.body_face,
        title_size_pt=display.size_pt if display else 30.0,
        body_size_pt=body.size_pt if body else 14.0,
        caption_size_pt=(
            pack.type_roles["caption"].size_pt
            if "caption" in pack.type_roles else 10.0
        ),
    )


def _block_compiler(pack: Pack, design_system: dict, ground: str):
    """A ``BlockCompiler`` for this page's ground.

    Every exhibit kind that is not a chart or a table (a metrics row, a
    diagram, a typed block) is drawn through the same ``BlockCompiler`` the
    template-path renderer uses - but nothing on this path ever built one, so
    ``compile_page``'s ``block_compiler`` parameter was always ``None`` and
    ``_exhibit_primitives`` returned nothing for every one of them the moment
    it saw that. See issue #286.

    Built once per page rather than per mark: the block compiler's `fit`
    already scales a whole block together, and the page's own ground -
    exactly what ``_materialise_charts`` already uses for a chart's ground -
    is the same simplification, not a new one.
    """
    from app import colors as colors_module
    from app.renderer.blocks import BlockCompiler
    from app.renderer.deck_theme import SlideStyle

    on_dark = colors_module.is_dark(ground)
    ink = pack.colors.ink_reversed if on_dark else pack.colors.ink
    style = SlideStyle(
        role="content",
        background=ground,
        title_color=ink,
        body_color=ink,
        bullet_color=pack.colors.accent,
        accent_color=pack.colors.accent,
        footer_color=pack.colors.ink_muted,
    )
    return BlockCompiler(style=style, type_scale=_type_scale(pack),
                         design_system=design_system)


def _materialise_charts(marks: list[Primitive], pack: Pack,
                        design_system: dict) -> list[Primitive]:
    """Turn chart and table payloads into image bytes before anything tries
    to paint them.

    Rasterised because neither is a text box, and the existing renderer
    already produces publication-grade output for both. Doing it here rather
    than inside the painter keeps ``_resolve_image_bytes`` ignorant of either,
    which is the only reason it stayed simple.
    """
    from dataclasses import replace

    from app.renderer.chart_renderer import render_chart_to_png, render_table_to_png

    out: list[Primitive] = []
    for mark in marks:
        image = mark.image or {}
        chart_payload = image.get("chart") if mark.kind == "image" else None
        table_payload = image.get("table") if mark.kind == "image" else None
        if chart_payload:
            png = render_chart_to_png(
                chart_payload, design_system, mark.box.width, mark.box.height,
                dpi=200, ground=pack.colors.ground,
            )
        elif table_payload:
            png = render_table_to_png(
                table_payload, design_system, mark.box.width, mark.box.height,
                dpi=200,
            )
        else:
            out.append(mark)
            continue
        if not png:
            continue
        out.append(
            replace(
                mark,
                image={
                    "source": "base64",
                    "data": base64.b64encode(png).decode("ascii"),
                },
            )
        )
    return out


class InvalidDesignDoc(ValueError):
    """A document whose geometry cannot be placed as declared."""


def render_doc(doc: DesignDoc, pack: Pack, output_path: str, *,
               design_system: Optional[dict] = None,
               block_compiler: object | None = None,
               validate: bool = True) -> str:
    """Render every page of ``doc`` and return the path written.

    Validation runs here, in code, and refuses to render a document that
    does not place. It is deliberately not a tool a caller may decline to
    call: the validator this replaces was exactly that, and it was invoked
    zero times across every observed run, which left density, title-register
    and apparatus checking inert in practice. A correctness check that can be
    skipped is not a correctness mechanism.
    """
    if validate:
        problems = check_doc(doc)
        if problems:
            raise InvalidDesignDoc(
                "this document cannot be placed as declared:\n  "
                + "\n  ".join(problems[:10])
            )
    presentation = Presentation()
    presentation.slide_width = Inches(doc.canvas.w_in)
    presentation.slide_height = Inches(doc.canvas.h_in)
    blank = presentation.slide_layouts[_BLANK_LAYOUT_INDEX]

    scale = _type_scale(pack)
    system = design_system or {}

    for page in doc.pages:
        slide = presentation.slides.add_slide(blank)
        ground = (
            page.ground if page.ground and page.ground.startswith("#")
            else (pack.colors.get(page.ground) if page.ground
                  else pack.colors.ground)
        )
        _paint_page_ground(slide, doc, ground)

        marks = compile_page(
            doc, page, pack,
            block_compiler=block_compiler or _block_compiler(pack, system, ground),
        )
        marks = _materialise_charts(marks, pack, system)

        context = PaintContext(
            ground=ground,
            default_color=pack.colors.ink,
            type_scale=scale,
            default_size_pt=scale.body_size_pt,
            default_font=pack.body_face,
        )
        resolved = resolve_page(doc, page)
        for mark in marks:
            local = _context_for(mark, resolved, pack, context)
            primitives.paint(slide, mark, local)

    directory = os.path.dirname(os.path.abspath(output_path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    presentation.save(output_path)
    return output_path


def _ground_under(frame_id: str, resolved, pack: Pack,
                  page_ground: str) -> str:
    """The colour a mark will actually be sitting on.

    Not the mark's own frame, and not its parent: the topmost frame *below*
    it in the z-stack whose rectangle it lands inside and which declares a
    ground. A quote frame layered over a full-bleed dark panel declares no
    ground of its own - the whole point is that the panel shows through - so
    asking the frame or its parent returns the page's white, and the
    contrast check then helpfully darkens white type into invisibility
    against the dark panel it was written to be reversed out of.
    """
    target = next((r for r in resolved if r.id == frame_id), None)
    if target is None:
        return page_ground
    own = target.frame.ground
    if own:
        return own if own.startswith("#") else pack.colors.get(own)

    best = None
    for other in resolved:
        if other.id == frame_id or not other.frame.ground:
            continue
        if other.z > target.z:
            continue
        # Contained, allowing for the hairline that fractional geometry
        # rounds to.
        if (other.rect.left - 0.02 <= target.rect.left
                and other.rect.top - 0.02 <= target.rect.top
                and other.right + 0.02 >= target.right
                and other.bottom + 0.02 >= target.bottom):
            if best is None or other.z > best.z:
                best = other
    if best is None:
        return page_ground
    ground = best.frame.ground
    return ground if ground.startswith("#") else pack.colors.get(ground)


def _context_for(mark: Primitive, resolved, pack: Pack,
                 base: PaintContext) -> PaintContext:
    """The paint context for one mark, grounded on what is behind it."""
    from dataclasses import replace as _replace

    if not mark.region_id:
        return base
    ground = _ground_under(mark.region_id, resolved, pack, base.ground)
    if ground == base.ground:
        return base
    return _replace(base, ground=ground)


def _paint_page_ground(slide, doc: DesignDoc, ground: str) -> None:
    from app.renderer.layout import PositionConfig

    primitives.paint(
        slide,
        Primitive(
            id="page-ground",
            kind="rect",
            box=PositionConfig(0.0, 0.0, doc.canvas.w_in, doc.canvas.h_in),
            fill=ground,
            chrome=True,
        ),
        PaintContext(ground=ground, default_color="#000000",
                     type_scale=TypeScale()),
    )
