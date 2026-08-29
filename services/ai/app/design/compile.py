"""
DesignDoc + Pack -> primitives.

This is where a page stops being a description and becomes marks. Everything
above it reasons about frames and roles; everything below it paints
rectangles. Keeping the boundary here is what lets the same page be measured
and rendered without two implementations that can disagree.

Type never comes from the document. A ``TextBlock`` names a role and the
pack says what that role is - size, leading, tracking, case, colour, face.
That is the mechanism behind "the deck reads as one document": there is
exactly one place a size can come from, and it is shared by every page.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Callable, Optional

from app.design.geometry import ResolvedFrame, resolve_page
from app.design.pack import Pack, TypeRole
from app.design.schema import (
    DesignDoc,
    Exhibit,
    Figure,
    Mark,
    Page,
    Paragraph,
    Rule,
    TextBlock,
)
from app.renderer.layout import PositionConfig
from app.renderer.primitives import Primitive, TextRunSpec

# A frame with nothing in it still paints its ground; a frame with no ground
# and no content paints nothing at all.
_EMPTY: tuple[Primitive, ...] = ()

BlockCompilerFactory = Callable[[Optional[str]], object]


def _inset(rect: PositionConfig, pad_in: float) -> PositionConfig:
    if pad_in <= 0:
        return rect
    return PositionConfig(
        left=rect.left + pad_in,
        top=rect.top + pad_in,
        width=max(0.01, rect.width - 2 * pad_in),
        height=max(0.01, rect.height - 2 * pad_in),
    )


def _runs_from_paragraphs(paragraphs: tuple[Paragraph, ...], role: TypeRole,
                          pack: Pack) -> tuple[TextRunSpec, ...]:
    """Flatten paragraphs into runs, marking where the line breaks fall.

    A paragraph boundary becomes ``break_before`` on the first run of the
    next paragraph, which is the primitive layer's existing way of carrying
    several paragraphs in one text box - and one box is what keeps a story
    editable as a story.
    """
    out: list[TextRunSpec] = []
    for p_index, paragraph in enumerate(paragraphs):
        for r_index, run in enumerate(paragraph.runs):
            out.append(
                TextRunSpec(
                    text=run.text,
                    bold=(role.weight in ("semibold", "bold")
                          if run.bold is None else run.bold),
                    italic=bool(run.italic),
                    underline=run.underline,
                    size_pt=run.size_pt,
                    color=(pack.colors.get(run.color_role)
                           if run.color_role else None),
                    font=None,
                    break_before=(p_index > 0 and r_index == 0),
                    superscript=run.superscript,
                    smallcaps=run.smallcaps or role.case == "small",
                    tracking_per_mille=(
                        run.tracking_per_mille
                        if run.tracking_per_mille is not None
                        else (role.tracking_per_mille or None)
                    ),
                )
            )
    return tuple(out)


def _text_primitive(block: TextBlock, rect: PositionConfig, pack: Pack,
                    frame: ResolvedFrame, ident: str) -> Optional[Primitive]:
    role = pack.role(block.type_role)
    runs = _runs_from_paragraphs(block.paragraphs, role, pack)
    if not any(run.text.strip() for run in runs):
        return None

    text = "".join(r.text for r in runs)
    if role.case == "upper":
        runs = tuple(replace(r, text=r.text.upper()) for r in runs)

    return Primitive(
        id=ident,
        kind="text",
        box=rect,
        runs=runs,
        align=block.align,
        anchor=block.anchor,
        size_pt=role.size_pt,
        color=pack.colors.get(role.color_role),
        font=pack.face_for(role),
        bold=role.weight in ("semibold", "bold"),
        line_spacing_pt=role.line_height_pt,
        space_before_pt=pack.spacing.pt(role.space_before),
        space_after_pt=pack.spacing.pt(role.space_after),
        columns=block.columns,
        column_gap_pt=pack.spacing.pt(block.column_gap),
        tracking_per_mille=role.tracking_per_mille or None,
        smallcaps=role.case == "small",
        region_id=frame.id,
        alt_text=text[:200],
        # A publication page is typeset to fit, not shrunk to fit. Overflow
        # is a layout fault to be reported, not silently absorbed by making
        # the type smaller than the role says it is.
        autofit=False,
    )


def _figure_primitive(figure: Figure, rect: PositionConfig,
                      frame: ResolvedFrame, ident: str) -> Primitive:
    return Primitive(
        id=ident,
        kind="image",
        box=rect,
        image=dict(figure.source),
        fit=figure.fit,
        focal=(figure.focal_x, figure.focal_y),
        crop=(
            (figure.crop.left, figure.crop.top, figure.crop.right,
             figure.crop.bottom)
            if figure.crop else None
        ),
        alt_text=figure.alt_text,
        region_id=frame.id,
    )


def _rule_primitive(rule: Rule, rect: PositionConfig, pack: Pack,
                    frame: ResolvedFrame, ident: str) -> Primitive:
    inset = pack.spacing.inches(rule.inset)
    if rule.orientation == "horizontal":
        y = rect.top
        box = PositionConfig(left=rect.left + inset, top=y,
                             width=max(0.01, rect.width - 2 * inset), height=0.0)
        end = (box.left + box.width, y)
    else:
        x = rect.left
        box = PositionConfig(left=x, top=rect.top + inset, width=0.0,
                             height=max(0.01, rect.height - 2 * inset))
        end = (x, box.top + box.height)
    return Primitive(
        id=ident,
        kind="line",
        box=box,
        end=end,
        stroke=pack.colors.get(rule.color_role),
        stroke_pt=rule.weight_pt,
        region_id=frame.id,
    )


def _ground_primitives(frame: ResolvedFrame, pack: Pack) -> list[Primitive]:
    out: list[Primitive] = []
    if frame.frame.ground:
        out.append(
            Primitive(
                id=f"{frame.id}-ground",
                kind="rect",
                box=frame.rect,
                fill=_color(frame.frame.ground, pack),
                region_id=frame.id,
            )
        )
    scrim = frame.frame.scrim
    if scrim is not None:
        out.append(
            Primitive(
                id=f"{frame.id}-scrim",
                kind="rect",
                box=frame.rect,
                fill=pack.colors.get(scrim.color_role),
                fill_alpha=scrim.opacity,
                region_id=frame.id,
            )
        )
    # Drawn after the ground and the scrim so the rule sits on top of both,
    # and with no fill of its own - an outlined box that also filled itself
    # would hide whatever it was drawn over, which on a bleeding panel is
    # the photograph.
    if frame.frame.border:
        out.append(
            Primitive(
                id=f"{frame.id}-border",
                kind="rect",
                box=frame.rect,
                stroke=_color(frame.frame.border, pack),
                stroke_pt=frame.frame.border_pt,
                region_id=frame.id,
            )
        )
    return out


def _color(value: str, pack: Pack) -> str:
    """A colour role name, or a literal hex when a page insists on one."""
    if value.startswith("#"):
        return value
    return pack.colors.get(value)


def _stack(rect: PositionConfig, count: int, gap_in: float) -> list[PositionConfig]:
    """Split a frame into ``count`` stacked bands.

    A deliberately plain rule. A frame holding several things is uncommon in
    a measured archetype - the archetype usually gives each thing its own
    frame - so this exists to be correct rather than clever, and anything
    needing real vertical distribution should declare child frames.
    """
    if count <= 1:
        return [rect]
    total_gap = gap_in * (count - 1)
    band = max(0.01, (rect.height - total_gap) / count)
    return [
        PositionConfig(
            left=rect.left,
            top=rect.top + i * (band + gap_in),
            width=rect.width,
            height=band,
        )
        for i in range(count)
    ]


def compile_page(doc: DesignDoc, page: Page, pack: Pack, *,
                 block_compiler: object | None = None) -> list[Primitive]:
    """Every mark on this page, already in paint order.

    Paint order is the frames' z order, and within a frame it is ground,
    then scrim, then content - so type always lands on top of the thing it
    was written to sit on.
    """
    marks: list[Primitive] = []
    for frame in resolve_page(doc, page):
        marks.extend(_ground_primitives(frame, pack))

        if not frame.frame.content:
            continue
        pad_in = pack.spacing.inches(frame.frame.pad)
        inner = _inset(frame.rect, pad_in)
        bands = _stack(inner, len(frame.frame.content),
                       pack.spacing.inches(1))

        for index, item in enumerate(frame.frame.content):
            rect = bands[index]
            ident = f"{frame.id}-{index}"
            if isinstance(item, TextBlock):
                mark = _text_primitive(item, rect, pack, frame, ident)
                if mark is not None:
                    marks.append(mark)
            elif isinstance(item, Figure):
                marks.append(_figure_primitive(item, rect, frame, ident))
            elif isinstance(item, Rule):
                marks.append(_rule_primitive(item, rect, pack, frame, ident))
            elif isinstance(item, Mark):
                marks.append(
                    Primitive(
                        id=ident,
                        kind=item.primitive,
                        box=rect,
                        rotation_deg=item.rotation_deg,
                        region_id=frame.id,
                        **_mark_fields(item.payload, pack),
                    )
                )
            elif isinstance(item, Exhibit):
                marks.extend(
                    _exhibit_primitives(item, rect, pack, frame, ident,
                                        block_compiler)
                )
    return marks


def _mark_fields(payload: dict, pack: Pack) -> dict:
    """The subset of primitive fields a raw mark is allowed to set."""
    allowed = {
        "fill", "stroke", "stroke_pt", "align", "anchor", "size_pt",
        "color", "font", "bold", "image", "alt_text", "fill_alpha",
    }
    out = {k: v for k, v in payload.items() if k in allowed}
    for key in ("fill", "stroke", "color"):
        if isinstance(out.get(key), str) and out[key] and not out[key].startswith("#"):
            out[key] = _color(out[key], pack)
    runs = payload.get("runs")
    if runs:
        out["runs"] = tuple(TextRunSpec.from_dict(r) for r in runs)
    return out


def _exhibit_primitives(exhibit: Exhibit, rect: PositionConfig, pack: Pack,
                        frame: ResolvedFrame, ident: str,
                        block_compiler: object | None) -> list[Primitive]:
    """Charts, tables, diagrams - drawn by the code that already draws them.

    Routed through the existing builders rather than reimplemented: they
    already own their payload contracts, and a second implementation of the
    same contract is a second thing to keep in step.
    """
    if exhibit.exhibit_kind == "chart":
        # The block, unwrapped. Every other exhibit kind is handed the whole
        # block and reaches inside it - `_compile_diagram` reads
        # `item["diagram"]` - but the chart renderer wants the spec itself,
        # and handing it the wrapper meant `chartType` came back None on
        # every chart this path composed. `render_chart_to_png` returns None
        # rather than raising for an unrecognised chart, so the graph simply
        # was not on the page and nothing said so.
        spec = exhibit.payload.get("chart") or exhibit.payload
        return [
            Primitive(
                id=ident,
                kind="image",
                box=rect,
                image={"chart": spec},
                fit="fill",
                region_id=frame.id,
                alt_text=str(spec.get("title") or "chart"),
            )
        ]
    if exhibit.exhibit_kind == "table":
        # Drawn as an image for the same reason a chart is - see
        # `chart_renderer.render_table_to_png`. A native PowerPoint table
        # would be a sixth primitive kind, and the primitive layer's whole
        # guarantee is that there is no sixth.
        table = exhibit.payload.get("table") or exhibit.payload
        return [
            Primitive(
                id=ident,
                kind="image",
                box=rect,
                image={"table": table},
                fit="fill",
                region_id=frame.id,
                alt_text="table",
            )
        ]
    if block_compiler is None:
        return []
    kind = (
        exhibit.payload.get("kind")
        if exhibit.exhibit_kind == "block" else exhibit.exhibit_kind
    )
    # A "metrics" exhibit and the compiler's own "statCallout" block are the
    # same shape on the page - a row of headline figures - authored under two
    # different names by two different callers (`build_key_metrics` writes
    # "metrics"; a hand-built block writes "statCallout"). The compiler is
    # registered under neither name as such - see `BlockCompiler._compile_stat_callout`
    # - so this is the one place that reconciles them, rather than asking two
    # callers to agree on a name neither owns.
    kind = _EXHIBIT_TO_BLOCK_KIND.get(str(kind), str(kind))
    compiled = block_compiler.fit(kind, exhibit.payload, rect, ident)
    return [replace(p, region_id=frame.id) for p in compiled]


# Where an exhibit's own kind name and the block compiler's method name
# genuinely differ. Anything not listed here is passed through unchanged -
# "diagram" reaches `_compile_diagram` by already matching, and that is
# coincidence worth keeping working rather than a rule to lean on for the
# next kind that does not.
_EXHIBIT_TO_BLOCK_KIND = {"metrics": "statCallout"}
