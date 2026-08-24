"""
Building a Design Language Pack from a reference publication.

This is the step that makes "make it look like that report" mean something.
Everything it emits is measured: the grid by ``measure.grid``, the rhythm by
``measure.rhythm``, the spacing ladder by ``measure.spacing``, the type roles
by ``measure.type_roles``, the palette by ``measure.color_roles``, and the
page archetypes by combining ``measure.grammar``'s structural regions with
the page's own text blocks named by which rung of the ramp they are set in.

Two rules run through all of it.

**Nothing is invented.** Where a reading cannot be recovered the pack says so
and carries a stated fallback, rather than a confident number nobody
measured. A pack that quietly invents its grid teaches every later stage to
converge on a fiction.

**A pack is a reviewable artefact.** It is written to disk as JSON, with the
confidence of each recovery beside the reading, so a designer can look at
what was extracted and correct it. Ingestion is not required to be perfect;
it is required to be honest about where it is unsure.
"""
from __future__ import annotations

import os
from typing import Optional, Sequence

import fitz

from app.design.pack import (
    ColorRoles,
    Confidence,
    LegibilityPolicy,
    Pack,
    PageArchetype,
    Rhythm,
    SlotSpec,
    SpacingScale,
    TypeRole,
)
from app.design.schema import Canvas, FracPlace, Frame, GridPlace, GridSpec, Insets
from app.measure import color_roles as color_model
from app.measure import grammar as grammar_model
from app.measure import spacing as spacing_model
from app.measure import type_roles as type_model
from app.measure.grid import edges_from_boxes, recover_grid
from app.measure.rhythm import columns_from_page, recover_rhythm

# A text block narrower than this is furniture rather than a slot worth
# reproducing.
MIN_BLOCK_WIDTH = 0.02
# How close a block's edges have to sit to a grid track before the archetype
# records it as a grid frame rather than a free one.
GRID_SNAP = 0.012

# Type role -> the frame role that carries it.
_FRAME_ROLE = {
    "headline": "headline",
    "standfirst": "standfirst",
    "pullquote": "pullquote",
    "eyebrow": "eyebrow",
    "folio": "folio",
    "caption": "caption",
    "footnote": "footnotes",
    "body": "column",
}


def _page_blocks(page) -> list[dict]:
    width, height = float(page.rect.width), float(page.rect.height)
    out = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        spans = [
            span for line in block.get("lines", []) for span in line["spans"]
            if span["text"].strip()
        ]
        if not spans:
            continue
        x0, y0, x1, y1 = block["bbox"]
        if (x1 - x0) / width < MIN_BLOCK_WIDTH:
            continue
        out.append({
            "x": x0 / width, "y": y0 / height,
            "w": (x1 - x0) / width, "h": (y1 - y0) / height,
            "size": round(max(s["size"] for s in spans), 1),
            "chars": sum(len(s["text"].strip()) for s in spans),
        })
    return out


def _merge_overlapping(blocks: list[dict], threshold: float = 0.25) -> list[dict]:
    """Fuse text blocks that share space into the single slot they read as.

    A PDF's text blocks overlap freely - a heading and its first line, a
    label sitting inside a panel, two runs of one paragraph - and turning
    each one into its own frame produces an archetype whose frames fight for
    the same rectangle. What a reader sees as one area of type has to become
    one slot, or every page built from the archetype has two blocks of copy
    in the same place.
    """
    kept = [dict(b) for b in sorted(blocks, key=lambda b: -(b["w"] * b["h"]))]
    # Merging two blocks can bring the result into contact with a third, so
    # this runs to a fixed point rather than once over the list.
    for _ in range(6):
        merged_any = _merge_pass(kept, threshold)
        if not merged_any:
            break
    return sorted(kept, key=lambda b: (b["y"], b["x"]))


def _merge_pass(kept: list[dict], threshold: float) -> bool:
    """One sweep. Returns whether anything merged."""
    ordered = list(kept)
    kept.clear()
    changed = False
    for block in ordered:
        merged = False
        for existing in kept:
            overlap = _overlap_area(block, existing)
            smaller = min(block["w"] * block["h"], existing["w"] * existing["h"])
            if smaller > 0 and overlap / smaller > threshold:
                right = max(existing["x"] + existing["w"], block["x"] + block["w"])
                bottom = max(existing["y"] + existing["h"], block["y"] + block["h"])
                existing["x"] = min(existing["x"], block["x"])
                existing["y"] = min(existing["y"], block["y"])
                existing["w"] = right - existing["x"]
                existing["h"] = bottom - existing["y"]
                existing["chars"] += block["chars"]
                # The merged slot is named by its largest type, which is the
                # role a reader would give the whole area.
                existing["size"] = max(existing["size"], block["size"])
                merged = True
                changed = True
                break
        if not merged:
            kept.append(dict(block))
    return changed


def _overlap_area(a: dict, b: dict) -> float:
    dx = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    dy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    return dx * dy if dx > 0 and dy > 0 else 0.0


# A slot holding more than this many characters is a column of copy, whatever
# size the block that produced it happened to be set at.
DISPLAY_MAX_CHARS = 180


def _abstracted_role(block: dict, roles: dict[str, type_model.RungEvidence]) -> str:
    """The role this slot plays, rather than the size it was traced at.

    An archetype read straight off a page is a *trace*: every slot inherits
    the exact size of the block that happened to be there. Reconstructing
    that page works, because the same copy goes back into the same holes.
    Generating against it does not - a three-hundred-character paragraph
    bound into a slot traced from a thirty-point standfirst is set at thirty
    points and swamps the page.
    
    So a slot's role is decided by what it *holds*, not by what it held. Only
    a short slot can be a display role; anything carrying a paragraph is a
    column of body copy however it was set in the reference.
    """
    role_name = _role_for_size(block["size"], roles)
    if role_name in ("headline", "standfirst", "pullquote", "folio", "eyebrow"):
        if block["chars"] > DISPLAY_MAX_CHARS:
            return "body"
    return role_name


def _role_for_size(size: float, roles: dict[str, type_model.RungEvidence]) -> str:
    best, best_distance = "body", float("inf")
    for name, rung in roles.items():
        distance = abs(rung.size_pt - size)
        if distance < best_distance:
            best, best_distance = name, distance
    return best


def _page_grid(page) -> object:
    """The grid this one page is set on, recovered from its own edges."""
    width = float(page.rect.width)
    boxes = [
        (b["x"] * width, b["y"] * float(page.rect.height),
         (b["x"] + b["w"]) * width, (b["y"] + b["h"]) * float(page.rect.height))
        for b in _page_blocks(page)
    ]
    return recover_grid(edges_from_boxes(boxes, width))


def _archetype_for_page(page, page_number: int,
                        roles: dict[str, type_model.RungEvidence],
                        canvas: Canvas, baseline_pt: float,
                        house_grid: Optional[GridSpec] = None) -> PageArchetype:
    """One page, read as a reusable pattern of frames.

    The structural regions come from ``grammar`` - the rails, panels and
    ground splits a page is built on - and the content frames come from the
    page's own blocks, each named by which rung of the type ramp it is set
    in. Neither half is sufficient alone: grammar finds the bleed panel and
    misses the headline, and the blocks find the headline and know nothing
    about the panel behind it.
    """
    width, height = float(page.rect.width), float(page.rect.height)
    frames: list[Frame] = []
    slots: list[SlotSpec] = []

    structural = grammar_model.read_page(page, page_number)
    for index, region in enumerate(structural.regions):
        rx, ry = _clamp(region.get("x", 0.0)), _clamp(region.get("y", 0.0))
        rw, rh = _positive(region.get("w", 1.0)), _positive(region.get("h", 1.0))
        frames.append(Frame(
            id=f"{region.get('id') or f'region-{index}'}",
            role="rail" if region.get("kind") == "rail" else "panel",
            place=FracPlace(x=rx, y=ry, w=rw, h=rh),
            bleed=_bleed_edges(rx, ry, rw, rh),
            ground=region.get("ground"),
            z=0,
        ))

    for index, block in enumerate(_merge_overlapping(_page_blocks(page))):
        role_name = _abstracted_role(block, roles)
        frame_role = _FRAME_ROLE.get(role_name, "column")
        frame_id = f"{role_name}-{index}"
        frames.append(Frame(
            id=frame_id,
            role=frame_role,
            place=FracPlace(x=_clamp(block["x"]), y=_clamp(block["y"]),
                            w=_positive(block["w"]), h=_positive(block["h"])),
            z=2,
        ))
        slots.append(SlotSpec(
            frame_id=frame_id,
            accepts=("text",),
            # Only the headline is required. An archetype read off a page
            # with three body columns is not a template that demands three
            # columns of copy - marking every one of them required means the
            # archetype can only ever carry exactly what the page it came
            # from carried, and nothing selects at all.
            required=(role_name == "headline" and not any(
                slot.required for slot in slots
            )),
            prominence=min(1.0, block["size"] / 60.0),
            max_chars=max(20, int(block["chars"] * 1.4)),
        ))

    for entry in page.get_images(full=True):
        for rect in page.get_image_rects(entry[0]):
            frame_id = f"figure-{len(frames)}"
            fx, fy = _clamp(rect.x0 / width), _clamp(rect.y0 / height)
            fw = _positive((rect.x1 - rect.x0) / width)
            fh = _positive((rect.y1 - rect.y0) / height)
            frames.append(Frame(
                id=frame_id, role="figure",
                place=FracPlace(x=fx, y=fy, w=fw, h=fh),
                bleed=_bleed_edges(fx, fy, fw, fh),
                z=1,
            ))
            slots.append(SlotSpec(frame_id=frame_id, accepts=("figure",),
                                  prominence=0.6))

    label = ", ".join(structural.features) or "content"
    own_grid = _page_grid(page)
    # Express content frames as column spans on whichever grid applies - the
    # page's own where it has one, the house grid otherwise. A page with no
    # recoverable grid of its own is exactly the page whose frames most need
    # putting on one: left to its traced coordinates it aligns with nothing,
    # including itself.
    applied_grid = (_grid_spec(own_grid, canvas, baseline_pt)
                    if own_grid is not None else house_grid)
    if applied_grid is not None and applied_grid.columns > 1:
        frames = [_snapped(frame, applied_grid, canvas, applied_grid)
                  for frame in frames]
    return PageArchetype(
        id=f"page-{page_number}",
        label=label,
        role="content",
        grid=_grid_spec(own_grid, canvas, baseline_pt) if own_grid else None,
        grid_confidence=Confidence(
            value=own_grid.edge_snap if own_grid else 0.0,
            residual=own_grid.residual if own_grid else 0.0,
            note=(f"{own_grid.columns} columns on this page" if own_grid
                  else "this page is not on a recoverable grid"),
        ),
        frames=tuple(frames),
        slots=tuple(slots),
        example_pages=(page_number,),
        note=f"{len(frames)} frames read from page {page_number}",
    )


def _snapped(frame: Frame, _unused, canvas: Canvas,
             page_grid: GridSpec) -> Frame:
    """Re-express a traced content frame as a span of grid columns.

    Nudging a traced frame onto the nearest track does not work, and it is
    worth saying why: measured across one of EY's pages, not one of its
    twenty-seven frames sat within two hundredths of a track, because the
    grid is recovered from edge *mass* - a few large blocks decide it - while
    most frames are small and sit wherever their copy happened to stop.

    Alignment is most of what separates a typeset page from a slide: the
    reference puts many elements on few vertical lines, and a literal trace
    puts each one on its own. So a content frame is *rebuilt* as the column
    span that best covers it, which puts it on the grid by construction
    rather than by proximity.

    Narrow furniture - a folio hung in the margin, a rule - keeps its
    measured position: it was never on the grid in the first place.
    """
    place = frame.place
    if not isinstance(place, FracPlace):
        return frame
    children = tuple(_snapped(c, _unused, canvas, page_grid)
                     for c in frame.children)
    column = page_grid.column_width_in(canvas) / canvas.w_in
    if column <= 0 or place.w < column * 0.6:
        return frame.model_copy(update={"children": children})

    pitch = column + page_grid.gutter_in / canvas.w_in
    margin = page_grid.margin.left / canvas.w_in
    start = max(0, min(page_grid.columns - 1,
                       int(round((place.x - margin) / pitch))))
    span = max(1, min(page_grid.columns - start,
                      int(round((place.w + page_grid.gutter_in / canvas.w_in)
                                / pitch))))
    return frame.model_copy(update={
        "place": GridPlace(col=start, span=span, y=place.y, h=place.h),
        "children": children,
    })


def _bleed_edges(x: float, y: float, w: float, h: float,
                 tolerance: float = 0.005) -> tuple[str, ...]:
    """Which edges of the trim this element reaches, and therefore bleeds off.

    An element measured at 0.745..1.002 of the page width is not an element
    that overshot by two thousandths; it is a panel that bleeds off the right
    edge, which is how publications use the trim. Recording that as a bleed
    rather than as an overshoot is what lets the frame be reproduced - and
    what stops the geometry check from reporting a deliberate design device
    as an error on every page that uses one.
    """
    edges = []
    if x <= tolerance:
        edges.append("left")
    if y <= tolerance:
        edges.append("top")
    if x + w >= 1.0 - tolerance:
        edges.append("right")
    if y + h >= 1.0 - tolerance:
        edges.append("bottom")
    return tuple(edges)


def _clamp(value: float) -> float:
    return max(-1.0, min(2.0, float(value)))


def _positive(value: float) -> float:
    return max(0.001, min(3.0, float(value)))


def from_pdf(pdf_path: str, pages: Sequence[int], *, pack_id: str,
             label: str = "", note: str = "") -> Pack:
    """Read a publication into a pack. Design-split pages only."""
    doc = fitz.open(pdf_path)
    sample = [(number, doc[number - 1]) for number in pages]
    first = sample[0][1]
    width, height = float(first.rect.width), float(first.rect.height)
    canvas = Canvas(w_in=width / 72.0, h_in=height / 72.0)

    boxes = []
    for _, page in sample:
        for block in _page_blocks(page):
            boxes.append((block["x"] * width, block["y"] * height,
                          (block["x"] + block["w"]) * width,
                          (block["y"] + block["h"]) * height))
    grid = recover_grid(edges_from_boxes(boxes, width))

    rhythm_columns: list[list[float]] = []
    for _, page in sample:
        rhythm_columns.extend(columns_from_page(page))
    rhythm = recover_rhythm(rhythm_columns)

    gaps: list[float] = []
    for _, page in sample:
        gaps.extend(spacing_model.gaps_from_page(page))
    spacing = spacing_model.recover_spacing(gaps)

    rungs = type_model.read_rungs(sample)
    roles = type_model.assign_roles(rungs)
    body_face, display_face = type_model.faces_for(roles)
    pairing = type_model.pairing_face(roles)

    evidence, _ = color_model.read_colors(sample)
    palette = color_model.assign_roles(evidence)

    baseline_pt = rhythm.unit_pt if rhythm else 0.0
    base_pt = spacing.base_pt if spacing else (baseline_pt / 4.0 or 6.0)

    grid_spec = _grid_spec(grid, canvas, baseline_pt)
    type_map = _type_roles(roles, palette, base_pt)
    # Built twice on purpose: the house grid is the one the pages agree on,
    # which is not known until every page has been read, and pages with no
    # grid of their own have to be expressed on it.
    archetypes = tuple(
        _archetype_for_page(page, number, roles, canvas, baseline_pt)
        for number, page in sample
    )
    house = _grid_spec(grid, canvas, baseline_pt) if grid else _modal_grid(archetypes)[0]
    if house is not None:
        archetypes = tuple(
            _archetype_for_page(page, number, roles, canvas, baseline_pt,
                                house_grid=house)
            for number, page in sample
        )
    # The pack's grid is the one its pages most often agree on. Pooling every
    # page's edges and fitting once finds nothing, because the pages are not
    # all on the same grid; asking each page and taking the mode finds the
    # house grid and leaves the exceptions to carry their own.
    grid_note = (f"{grid.columns} columns recovered across all design pages"
                 if grid else "")
    grid_snap = grid.edge_snap if grid else 0.0
    grid_residual = grid.residual if grid else 0.0
    if grid is None:
        modal, modal_snap, modal_pages = _modal_grid(archetypes)
        if modal is not None:
            grid_spec = modal
            grid_snap = modal_snap
            grid_note = (f"{modal.columns} columns, agreed by {modal_pages} of "
                         f"{len(archetypes)} pages; no single grid fits them all")
        else:
            grid_note = "no grid recovered on any page; frames are placed by fraction"
    body = roles.get("body")

    return Pack(
        id=pack_id,
        label=label or pack_id,
        source_note=note or f"measured from {os.path.basename(pdf_path)}, "
                            f"pages {', '.join(str(p) for p in pages)}",
        canvas=canvas,
        grid=grid_spec,
        grid_confidence=Confidence(value=grid_snap, residual=grid_residual,
                                   note=grid_note),
        rhythm=Rhythm(
            unit_pt=baseline_pt,
            phase_pt=rhythm.phase_pt if rhythm else 0.0,
            confidence=Confidence(
                value=rhythm.confidence if rhythm else 0.0,
                residual=rhythm.residual if rhythm else 0.0,
                note=(f"{rhythm.lines} body lines" if rhythm
                      else "no baseline rhythm recovered"),
            ),
        ),
        spacing=SpacingScale(
            base_pt=base_pt,
            steps=spacing.steps if spacing else SpacingScale().steps,
            confidence=Confidence(
                value=spacing.explained if spacing else 0.0,
                note=(f"{spacing.samples} gaps" if spacing
                      else "no spacing ladder recovered; derived from the rhythm"),
            ),
        ),
        type_roles=type_map,
        colors=_colors(palette),
        archetypes=archetypes,
        body_face=type_model.clean_face(body_face) or "Arial",
        display_face=type_model.clean_face(pairing or display_face) or "Arial",
        policy=_policy(body, canvas),
    )


def _modal_grid(
    archetypes: Sequence[PageArchetype],
) -> tuple[Optional[GridSpec], float, int]:
    """The grid the most pages agree on, and how many of them agreed.

    Returns the spec, the mean confidence of the pages that use it, and the
    count of those pages - so a pack can say "four columns, agreed by three
    of five pages" rather than presenting a house grid the publication only
    half keeps.
    """
    scored: dict[int, list] = {}
    for archetype in archetypes:
        if archetype.grid is None:
            continue
        entry = scored.setdefault(archetype.grid.columns, [0.0, 0, archetype.grid])
        entry[0] += archetype.grid_confidence.value
        entry[1] += 1
    if not scored:
        return (None, 0.0, 0)
    total, count, spec = max(scored.values(), key=lambda e: (e[1], e[0]))
    return (spec, total / count if count else 0.0, count)


def _grid_spec(grid, canvas: Canvas, baseline_pt: float) -> GridSpec:
    if grid is None:
        # No grid was recovered, and inventing one would give every later
        # stage something false to conform to. A single column spanning the
        # page is the honest stand-in: it constrains nothing.
        return GridSpec(columns=1, gutter_in=0.0,
                        margin=Insets(top=0.04 * canvas.h_in,
                                      right=0.04 * canvas.w_in,
                                      bottom=0.04 * canvas.h_in,
                                      left=0.04 * canvas.w_in),
                        baseline_in=baseline_pt / 72.0)
    return GridSpec(
        columns=grid.columns,
        gutter_in=grid.gutter * canvas.w_in,
        margin=Insets(
            left=grid.margin_left * canvas.w_in,
            right=grid.margin_right * canvas.w_in,
            top=0.04 * canvas.h_in,
            bottom=0.04 * canvas.h_in,
        ),
        baseline_in=baseline_pt / 72.0,
    )


def _type_roles(roles: dict[str, type_model.RungEvidence],
                palette: dict[str, str], base_pt: float) -> dict[str, TypeRole]:
    out: dict[str, TypeRole] = {}
    for name, rung in roles.items():
        color_role = color_model.nearest_role(rung.color, palette) or "ink"
        out[name] = TypeRole(
            size_pt=rung.size_pt,
            line_height_pt=rung.line_height_pt,
            weight="bold" if "bold" in rung.face.lower() else (
                "light" if "light" in rung.face.lower() else "regular"
            ),
            face="display" if name in ("headline", "pullquote", "standfirst")
                 else "body",
            case="upper" if rung.upper_share >= 0.7 else "none",
            color_role=color_role if color_role.startswith("ink") else "ink",
            measured_min_pt=rung.size_pt,
            measured_median_pt=rung.size_pt,
            measured_max_pt=rung.size_pt,
        )
    if "body" not in out:
        out["body"] = TypeRole(size_pt=14.0, line_height_pt=18.0)
    return out


def _colors(palette: dict[str, str]) -> ColorRoles:
    defaults = ColorRoles()
    return ColorRoles(**{
        field: palette.get(field, getattr(defaults, field))
        for field in ColorRoles.model_fields
    })


def _policy(body: Optional[type_model.RungEvidence],
            canvas: Canvas) -> LegibilityPolicy:
    """The floors this publication's own type implies.

    Measured as a share of canvas height, because point sizes are not
    comparable across canvases: 10pt on a 792pt page is smaller type than
    12pt on a 540pt one, and a single global floor in points forbids three of
    the four publications we hold up as the bar.
    """
    page_pt = canvas.h_in * 72.0
    if body is None or page_pt <= 0:
        return LegibilityPolicy()
    ratio = body.size_pt / page_pt
    return LegibilityPolicy(
        body_min_ratio=max(0.005, ratio * 0.95),
        caption_min_ratio=max(0.004, ratio * 0.65),
        display_min_ratio=max(0.010, ratio * 1.5),
    )
