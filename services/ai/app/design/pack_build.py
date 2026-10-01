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
from typing import NamedTuple, Optional, Sequence

import fitz

from app.design.pack import (
    ColorRoles,
    Illustration,
    Confidence,
    LegibilityPolicy,
    Pack,
    PageArchetype,
    Rhythm,
    SlotSpec,
    SpacingScale,
    TypeRole,
)
from app.design.geometry import content_box
from app.design.schema import Canvas, FracPlace, Frame, GridPlace, GridSpec, Insets
from app.measure import clusters as clusters_model
from app.measure import color_roles as color_model
from app.measure import illustration as illustration_model
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

# The narrowest a slot may be and still be offered body copy, in characters
# of that pack's own body face at its own size. Below this a slot is a label,
# not a measure: it is a chart's tick, a data callout, a page number. Offering
# it as somewhere to put a paragraph is how a generated page ended up with its
# copy scattered into the positions a chart's axis used to occupy.
MIN_MEASURE_CHARS = 18

# How much of a text block has to sit inside a figure before it is read as
# that figure's own furniture rather than as a sibling of it. Labels sit
# wholly within the chart they annotate; a caption sits below it.
FIGURE_CONTAINMENT = 0.7

# A figure covering more of the page than this is a background, not an
# illustration, and the type over it is the page's copy rather than its
# furniture. Without this a full-bleed image swallows every block on the page.
FIGURE_IS_BACKGROUND = 0.55

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


def _char_width_pt(role_name: str, roles: dict[str, type_model.RungEvidence],
                   face: str) -> float:
    """The average advance of this role's face at its own size, in points.

    Measured from the font rather than estimated. A chars-per-line constant
    is wrong by nearly a factor of two between narrow and wide copy, and the
    number it produces is what decides whether a slot is a measure or a
    label.
    """
    rung = roles.get(role_name)
    if rung is None or rung.size_pt <= 0:
        return 0.0
    from app.renderer.typeset.fonts import width_pt

    sample = ("The quick brown fox jumps over the lazy dog and keeps going "
              "until the line is comfortably longer than any measure")
    return width_pt(sample, face, rung.size_pt) / len(sample)


class Faces(NamedTuple):
    """The pack's two faces, as `Pack.face_for` will draw them."""

    body: str
    display: str


def _capacity_chars(block: dict, role_name: str,
                    roles: dict[str, type_model.RungEvidence],
                    faces: Faces, width_pt_page: float,
                    height_pt_page: float) -> int:
    """How much copy this slot actually holds, at the role it is set in.

    The capacity used to be `chars * 1.4` - the amount of text that happened
    to be there on the day, inflated a little. That is a description of the
    reference page, not of the frame, and the two disagree wildly: a slot
    traced from a nine-character label reported room for twenty characters
    while measuring an inch and a half wide. Every later stage reads this
    number, so it has to describe the frame.

    And it is measured as the role will be drawn: in the face the pack sets
    it in, at its weight and case. It was measured in the body face at
    regular weight for every role, so a headline slot - drawn bold, in the
    display face - was promised the room of a line of body-weight copy.
    """
    rung = roles.get(role_name)
    if rung is None or rung.size_pt <= 0:
        return max(20, int(block["chars"] * 1.4))
    from app.design.setting import capacity

    role = _role_of(role_name, rung)
    face = faces.display if role.face == "display" else faces.body
    width = block["w"] * width_pt_page
    height = block["h"] * height_pt_page
    # The leading that was measured on the page, which is what the pack will
    # carry, so the capacity and the typesetter agree. And the line count is
    # floored, not rounded: a frame 1.6 lines tall holds one line. Rounding it
    # to two is how a slot advertised 490 characters and overflowed by 138
    # points when they were set in it.
    lines = max(1, int(height / role.line_height_pt))
    return max(1, capacity(role, face, width, lines))


def _is_a_measure(block: dict, roles: dict[str, type_model.RungEvidence],
                  face: str, width_pt_page: float) -> bool:
    """Whether copy could be set in this slot at all.

    Asked against the pack's *body* face, whatever the block was set in,
    because the question is whether a paragraph could go here - not whether
    the label that was here fits.
    """
    per_char = _char_width_pt("body", roles, face)
    if per_char <= 0:
        return True
    return (block["w"] * width_pt_page) / per_char >= MIN_MEASURE_CHARS


def _figure_rects(page) -> list[tuple[float, float, float, float]]:
    """Every raster figure on the page, as fractions, backgrounds excluded."""
    width, height = float(page.rect.width), float(page.rect.height)
    out = []
    for entry in page.get_images(full=True):
        for rect in page.get_image_rects(entry[0]):
            x0, y0 = rect.x0 / width, rect.y0 / height
            x1, y1 = rect.x1 / width, rect.y1 / height
            if (x1 - x0) * (y1 - y0) > FIGURE_IS_BACKGROUND:
                continue
            out.append((x0, y0, x1, y1))
    return out


def _inside_a_figure(block: dict,
                     figures: Sequence[tuple[float, float, float, float]]) -> bool:
    """Whether this text belongs to a figure rather than beside one.

    A chart's tick labels and data callouts sit wholly inside the chart they
    annotate. Traced as siblings they become content slots, and the page then
    offers a paragraph the position an axis label used to occupy.
    """
    area = block["w"] * block["h"]
    if area <= 0:
        return False
    for x0, y0, x1, y1 in figures:
        left = max(block["x"], x0)
        top = max(block["y"], y0)
        right = min(block["x"] + block["w"], x1)
        bottom = min(block["y"] + block["h"], y1)
        if right <= left or bottom <= top:
            continue
        if ((right - left) * (bottom - top)) / area >= FIGURE_CONTAINMENT:
            return True
    return False


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
                        house_grid: Optional[GridSpec] = None,
                        faces: Faces = Faces("Arial", "Arial")) -> PageArchetype:
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
        if not region.get("ground"):
            # A region that paints nothing and holds nothing is a *feature*
            # the grammar reader noticed - "this page has an exhibit on it",
            # "this page is on a grid" - not an area of the page. Emitted as a
            # frame it covers the whole canvas, renders nothing, and collides
            # with every real panel underneath it at the same depth: `grid`
            # and `exhibit` overlapped `panel-0` by 100% on WTI page 4. The
            # feature is already recorded in this archetype's label.
            continue
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

    # Figures are read first because what is inside one is that figure's own
    # furniture, not a sibling of it.
    figures = _figure_rects(page)
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

    set_aside = {"in a figure": 0, "too narrow to set copy in": 0,
                 "page furniture": 0}
    role_of: dict[str, str] = {}
    for index, block in enumerate(_merge_overlapping(_page_blocks(page))):
        role_name = _abstracted_role(block, roles)
        if _inside_a_figure(block, figures):
            set_aside["in a figure"] += 1
            continue
        if role_name == "folio":
            # A page number is chrome. It is the same on every page, it is
            # not somewhere the deck's material goes, and traced as a slot it
            # is one more tiny box competing for a paragraph.
            set_aside["page furniture"] += 1
            continue
        if not _is_a_measure(block, roles, faces.body, width):
            set_aside["too narrow to set copy in"] += 1
            continue
        frame_role = _FRAME_ROLE.get(role_name, "column")
        frame_id = f"{role_name}-{index}"
        role_of[frame_id] = role_name
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
            max_chars=_capacity_chars(block, role_name, roles, faces,
                                      width, height),
        ))

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
        frames = _snap_all(frames, canvas, applied_grid)
    # Only now is a frame's real size known: snapping re-expresses it as a
    # span of columns, which can be narrower than the ink that was traced.
    frames, slots, dropped = _enforce_slot_floors(
        frames, slots, role_of, roles, canvas,
        applied_grid or house_grid, faces.body,
    )
    set_aside["too narrow to set copy in"] += dropped
    if applied_grid is not None or house_grid is not None:
        # Artwork only. A ground panel raised out of its own layer lands at
        # the same depth as the type above it, which is the very ambiguity
        # this is meant to remove: WTI's `panel-0` came forward to z=2 and
        # covered `body-12`. Grounds no longer collide anyway, now that the
        # feature regions that used to blanket the page are not frames.
        frames = _layer_overlapping(
            frames, canvas, applied_grid or house_grid, frozenset({"figure"}),
        )
        frames, slots, collided = _separate_text_frames(
            frames, slots, role_of, roles, canvas,
            applied_grid or house_grid,
        )
        if collided:
            set_aside["lying under another frame"] = collided

    return PageArchetype(
        id=f"page-{page_number}",
        label=label,
        # A page whose only text frames are captions and footnotes is a page
        # of apparatus - endnotes, a disclaimer, a glossary. It is a real page
        # of the publication and a useless archetype: bound to a deck's
        # material it sets every paragraph at footnote size. Recorded as what
        # it is so the selector will not offer it content.
        role="content" if _carries_copy(frames, slots) else "appendix",
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
        note=(f"{len(frames)} frames read from page {page_number}"
              + (", set aside: " + ", ".join(
                  f"{count} {reason}"
                  for reason, count in set_aside.items() if count)
                 if any(set_aside.values()) else "")),
    )


# The frame roles a page's own material can be set in. A page with none of
# them cannot carry a deck, whatever else it has on it.
COPY_FRAME_ROLES = frozenset({"column", "headline", "standfirst", "pullquote",
                              "band", "bandRow", "statRow", "marginNote"})


def _carries_copy(frames: Sequence[Frame], slots: Sequence[SlotSpec]) -> bool:
    """Whether this page has anywhere the deck's own copy could go."""
    by_id = {frame.id: frame for frame in frames}
    for slot in slots:
        if slot.accepts and "text" not in slot.accepts:
            continue
        frame = by_id.get(slot.frame_id)
        if frame is not None and frame.role in COPY_FRAME_ROLES:
            return True
    return False


def _enforce_slot_floors(frames: list[Frame], slots: list[SlotSpec],
                         role_of: dict[str, str],
                         roles: dict[str, type_model.RungEvidence],
                         canvas: Canvas, grid: Optional[GridSpec],
                         body_face: str) -> tuple[list[Frame], list[SlotSpec], int]:
    """Make every text slot big enough to hold the type it is set in.

    Two corrections, and both have to happen here rather than when the block
    was read, because snapping to the grid is what finally decides a frame's
    width.

    A frame is traced at the height of its *ink*, and ink is shorter than the
    line box around it: a single line of 16pt type inks about 11pt and needs
    21pt of leading. So every single-line frame came out too short for the one
    line it holds, which is most of what "this slot cannot hold a line of its
    own body copy" was measuring. Frames are grown to their type's line box.
    Growing downward is safe in a way that widening is not - it runs into the
    whitespace under the line - and any residual collision is a geometry
    finding the repair loop can act on.

    A frame narrower than a real measure is a label, whatever it was traced
    from, and it is dropped along with its slot rather than left as somewhere
    a paragraph might be sent.
    """
    if grid is None:
        return frames, slots, 0
    box = content_box(canvas, grid)
    column = grid.column_width_in(canvas)
    per_char = _char_width_pt("body", roles, body_face)
    floor_in = (MIN_MEASURE_CHARS * per_char / 72.0) if per_char > 0 else 0.0

    by_id = {frame.id: frame for frame in frames}
    drop: set[str] = set()
    grown: dict[str, Frame] = {}

    for slot in slots:
        if slot.accepts and "text" not in slot.accepts:
            continue
        frame = by_id.get(slot.frame_id)
        if frame is None:
            continue
        role = roles.get(role_of.get(frame.id, "body"))
        if isinstance(frame.place, GridPlace):
            width = (frame.place.span * column
                     + max(0, frame.place.span - 1) * grid.gutter_in)
            height = (frame.place.h or 0.0) * box.height
        else:
            width = frame.place.w * canvas.w_in
            height = frame.place.h * canvas.h_in

        if floor_in > 0 and width < floor_in:
            drop.add(frame.id)
            continue
        if role is None or box.height <= 0 or height <= 0:
            continue
        # One line box of this role, in inches, at the leading that was
        # *measured* on the page - which is what the pack will carry, so the
        # frame and the type it holds agree exactly rather than nearly. EY's
        # body leads at 21.0pt against a 16pt size; assuming 1.3 puts it at
        # 20.8 and leaves every single-line body frame a fifth of a point
        # short of the line inside it.
        line_in = (role.line_height_pt or role.size_pt * 1.3) / 72.0
        lines = max(1, round(height / line_in))
        needed = lines * line_in
        if needed > height:
            if isinstance(frame.place, GridPlace):
                grown[frame.id] = frame.model_copy(update={
                    "place": frame.place.model_copy(update={
                        "h": needed / box.height})})
            else:
                grown[frame.id] = frame.model_copy(update={
                    "place": frame.place.model_copy(update={
                        "h": min(1.0, needed / canvas.h_in)})})

    kept_frames = [grown.get(f.id, f) for f in frames if f.id not in drop]
    kept_slots = [s for s in slots if s.frame_id not in drop]
    return kept_frames, kept_slots, len(drop)


def _with_top_and_height(frame: Frame, top_in: float, height_in: float,
                         canvas: Canvas, grid: GridSpec) -> Frame:
    """The same frame, moved and resized in inches on its own placement kind."""
    if isinstance(frame.place, GridPlace):
        box = content_box(canvas, grid)
        if box.height <= 0:
            return frame
        return frame.model_copy(update={"place": frame.place.model_copy(
            update={"y": (top_in - box.top) / box.height,
                    "h": height_in / box.height})})
    return frame.model_copy(update={"place": frame.place.model_copy(
        update={"y": max(0.0, top_in / canvas.h_in),
                "h": max(1e-4, height_in / canvas.h_in)})})


def _layer_overlapping(frames: list[Frame], canvas: Canvas,
                       grid: GridSpec, roles: frozenset[str]) -> list[Frame]:
    """Give overlapping frames of these roles a depth each.

    Unlike type over type, artwork over artwork is a layering a publication
    means: WTI's cover is three images stacked into one picture. The trace
    flattened them onto one depth, which the renderer refuses as ambiguous.
    Largest first, so the biggest sits at the back and each thing placed on
    top of it comes forward.
    """
    indexed = [(index, frame) for index, frame in enumerate(frames)
               if frame.role in roles]
    if len(indexed) < 2:
        return frames
    indexed.sort(key=lambda entry: -_area(_rect_of(entry[1], canvas, grid)))
    settled = list(frames)
    placed: list[tuple[tuple[float, float, float, float], int]] = []
    for index, frame in indexed:
        rect = _rect_of(frame, canvas, grid)
        depth = frame.z
        for other_rect, other_z in placed:
            if (_overlap_share(rect, other_rect) > SNAP_COLLISION_TOLERANCE
                    and depth <= other_z):
                depth = other_z + 1
        settled[index] = frame.model_copy(update={"z": depth})
        placed.append((rect, depth))
    return settled


def _separate_text_frames(frames: list[Frame], slots: list[SlotSpec],
                          role_of: dict[str, str],
                          roles: dict[str, type_model.RungEvidence],
                          canvas: Canvas,
                          grid: GridSpec) -> tuple[list[Frame], list[SlotSpec], int]:
    """Pull apart text frames that sit on each other.

    Type over type is never a layering a publication intends, so unlike a
    figure under a caption this is not resolved by giving one a different
    depth - it is resolved by making them not overlap. Growing a frame to fit
    its own line box can push it into the block beneath, and a few traced
    blocks overlap by a sliver to begin with.

    The frame with slack gives way: the upper one's bottom is pulled back to
    the lower one's top when that still leaves it a full line, and failing
    that the lower one's top is pushed down. Only when neither has room does a
    frame go, and then it is the smaller - a frame lying under another is not
    somewhere material can be sent anyway.
    """
    by_id = {frame.id: frame for frame in frames}
    text_ids = [
        slot.frame_id for slot in slots
        if (not slot.accepts or "text" in slot.accepts)
        and slot.frame_id in by_id
    ]
    dropped: set[str] = set()

    def line_of(frame_id: str) -> float:
        rung = roles.get(role_of.get(frame_id, "body"))
        if rung is None:
            return 0.0
        return (rung.line_height_pt or rung.size_pt * 1.3) / 72.0

    for _ in range(4):
        moved = False
        ordered = [i for i in text_ids if i not in dropped]
        for position, first_id in enumerate(ordered):
            for second_id in ordered[position + 1:]:
                if first_id in dropped or second_id in dropped:
                    continue
                one, two = by_id[first_id], by_id[second_id]
                if one.z != two.z:
                    continue
                a, b = _rect_of(one, canvas, grid), _rect_of(two, canvas, grid)
                if _overlap_share(a, b) <= SNAP_COLLISION_TOLERANCE:
                    continue
                upper, lower = (one, two) if a[1] <= b[1] else (two, one)
                top, bottom = _rect_of(upper, canvas, grid), _rect_of(lower, canvas, grid)

                trimmed = bottom[1] - top[1]
                if trimmed >= line_of(upper.id) - 1e-6:
                    by_id[upper.id] = _with_top_and_height(
                        upper, top[1], trimmed, canvas, grid)
                    moved = True
                    continue
                pushed = bottom[3] - top[3]
                if pushed >= line_of(lower.id) - 1e-6:
                    by_id[lower.id] = _with_top_and_height(
                        lower, top[3], pushed, canvas, grid)
                    moved = True
                    continue
                smaller = min((one, two), key=lambda f: _area(
                    _rect_of(f, canvas, grid)))
                dropped.add(smaller.id)
                moved = True
        if not moved:
            break

    kept = [by_id[f.id] if f.id in by_id else f
            for f in frames if f.id not in dropped]
    return (kept, [s for s in slots if s.frame_id not in dropped],
            len(dropped))


def _rect_of(frame: Frame, canvas: Canvas,
             grid: GridSpec) -> tuple[float, float, float, float]:
    """This frame as (left, top, right, bottom) in inches."""
    place = frame.place
    if isinstance(place, GridPlace):
        box = content_box(canvas, grid)
        column = grid.column_width_in(canvas)
        left = box.left + place.col * (column + grid.gutter_in)
        width = place.span * column + max(0, place.span - 1) * grid.gutter_in
        top = box.top + (place.y or 0.0) * box.height
        height = (place.h or 0.0) * box.height
    else:
        left, top = place.x * canvas.w_in, place.y * canvas.h_in
        width, height = place.w * canvas.w_in, place.h * canvas.h_in
    return (left, top, left + width, top + height)


def _overlap_share(a: tuple[float, float, float, float],
                   b: tuple[float, float, float, float]) -> float:
    """How much of the smaller of two rectangles the other covers."""
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    if right <= left or bottom <= top:
        return 0.0
    smaller = min((a[2] - a[0]) * (a[3] - a[1]),
                  (b[2] - b[0]) * (b[3] - b[1]))
    return ((right - left) * (bottom - top)) / smaller if smaller > 0 else 0.0


# How much two frames may share before the overlap is called ambiguous. Kept
# just under the renderer's own tolerance so a pack never ships a page the
# renderer will refuse.
SNAP_COLLISION_TOLERANCE = 0.01


def _snap_all(frames: list[Frame], canvas: Canvas,
              grid: GridSpec) -> list[Frame]:
    """Snap what can be snapped without putting two frames on each other.

    Snapping *widens* a frame to the columns it best covers - which is the
    whole point, since alignment is most of what separates a typeset page
    from a slide - but a frame widened from 2.9in of ink to a full 5.4in
    column lands on whatever was beside it. That is where most of the
    ambiguous overlaps in the shipped packs came from, and it is not a
    trade worth making: a frame that cannot be put on the grid without
    colliding keeps its measured position, which is what it had before.

    Decided largest first, so the frames that carry the page's alignment get
    the grid and the small ones give way.
    """
    order = sorted(
        range(len(frames)),
        key=lambda i: -_area(_rect_of(frames[i], canvas, grid)),
    )
    settled = list(frames)
    for index in order:
        candidate = _snapped(settled[index], None, canvas, grid)
        if candidate is settled[index] or candidate.place == settled[index].place:
            continue
        rect = _rect_of(candidate, canvas, grid)
        collides = any(
            _overlap_share(rect, _rect_of(other, canvas, grid))
            > SNAP_COLLISION_TOLERANCE
            for position, other in enumerate(settled)
            if position != index and other.z == candidate.z
        )
        if not collides:
            settled[index] = candidate
    return settled


def _area(rect: tuple[float, float, float, float]) -> float:
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


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


# How a cluster's label maps onto the kind of page it is. Anything not named
# here carries the deck's material and is content.
_ROLE_FOR_LABEL = {
    clusters_model.COVER: "cover",
    clusters_model.DIVIDER: "section",
    clusters_model.EXHIBIT: "exhibit",
}

# A readable id for an archetype, from what the cluster is rather than from
# which page it happened to be traced off. `page-12` says nothing about what
# the page does; `card-row` does.
_SLUG = {
    clusters_model.COVER: "cover",
    clusters_model.DIVIDER: "divider",
    clusters_model.SPLIT_CANVAS: "split-canvas",
    clusters_model.EXHIBIT: "exhibit",
    clusters_model.CARD_ROW: "card-row",
    clusters_model.MATRIX: "matrix",
    clusters_model.ROW_BANDS: "row-bands",
    clusters_model.TEXT_PAGE: "text-page",
}

# A cluster this small is one page that looked like nothing else, not a
# pattern the publication repeats.
MIN_CLUSTER_PAGES = 2
# ...unless too few clusters clear that bar, in which case the biggest
# singletons are taken so a pack is not left with nothing to compose from.
MIN_ARCHETYPES = 4
# And a ceiling, so a long deck does not produce a pack nobody can review.
MAX_ARCHETYPES = 8
# How many pages of one cluster to trace before picking the best of them.
CANDIDATES_PER_CLUSTER = 3


def _one_per_cluster(archetypes: Sequence[PageArchetype],
                     plan: Sequence) -> tuple[PageArchetype, ...]:
    """Keep the best-carrying page of each group, named for what the group is.

    The frequency each archetype ends up with is the share of the
    publication's pages that look like it. That number was zero on every
    archetype of every shipped pack, which quietly disabled two things that
    depend on it: ties break toward the page the house uses most, and the
    selector discourages running the same archetype twice so a deck
    reproduces the house's *mix* rather than its single best-fitting page.
    """
    by_page = {page: archetype for archetype in archetypes
               for page in archetype.example_pages}
    chosen: list[PageArchetype] = []
    used: set[str] = set()
    for cluster, group, frequency in plan:
        candidates = [by_page[p] for p in group[:CANDIDATES_PER_CLUSTER]
                      if p in by_page]
        if not candidates:
            continue
        best = max(candidates, key=_archetype_richness)
        slug = _SLUG.get(cluster.label, "page")
        identifier = slug
        suffix = 2
        while identifier in used:
            identifier = f"{slug}-{suffix}"
            suffix += 1
        used.add(identifier)
        role = _ROLE_FOR_LABEL.get(cluster.label, "content")
        if best.role == "appendix" and cluster.label not in _ROLE_FOR_LABEL:
            # A page with nowhere for copy is a page of apparatus - unless the
            # clustering already said what it is. WTI's covers are entirely
            # image and carry no copy at all, and calling one an appendix both
            # mislabels it and costs the pack the only real opener it has.
            role = "appendix"
        chosen.append(best.model_copy(update={
            "id": identifier,
            "label": cluster.label,
            "role": role,
            "frequency": round(min(1.0, frequency), 4),
            "example_pages": tuple(group),
            "note": (f"{best.note}; traced from page {best.example_pages[0]}, "
                     f"one of {len(group)} pages the publication sets this way"),
        }))
    return tuple(chosen)


# A grid, a baseline rhythm and a spacing ladder are properties of the
# publication's *ordinary* page, so they are measured on the pages that most
# look alike rather than on everything. Pooling every page halves the reading:
# EY's 21.0pt rhythm comes back as 10.5pt over all its pages, because covers
# and dividers contribute gaps that a smaller unit "explains" better. Five is
# the sample size these recoveries were calibrated at.
TOKEN_SAMPLE = 5

# How many pages of each kind the type ramp and the palette look at. Every
# kind, so the ramp sees every rung; a couple of each, so one page's quirk
# does not become a rung of its own.
RAMP_PAGES_PER_CLUSTER = 2

# An opener and a divider carry almost no body copy, so they say nothing about
# the measure or the rhythm and a great deal that is misleading.
_NOT_ORDINARY = frozenset({clusters_model.COVER, clusters_model.DIVIDER})


def _ramp_pages(plan: Sequence, fallback: Sequence[int]) -> list[int]:
    """The pages the type ramp and palette are read from.

    A different sample from the grid and the rhythm, because it answers a
    different question. A measure and a baseline are properties of the
    publication's ordinary page, and are read from a handful of pages that
    look alike. A *ramp* is a property of the publication as a whole - it has
    to see the headline, the standfirst, the caption and the footnote, and
    those do not all appear on one kind of page. Read only from BXSL's
    commonest pages, which are exhibits, the ramp came back with no headline
    and no standfirst at all.

    One page per kind of page, which is what the clustering is for.
    """
    if not plan:
        return list(fallback)
    seen: list[int] = []
    for cluster, group, _ in plan:
        if cluster.label in _NOT_ORDINARY:
            # A cover's word-mark and a divider's section numeral are display
            # type, not rungs of the ramp that sets the publication's copy -
            # and the `display` rung already covers them. Reading EY's
            # dividers in gave the pack a 387pt "headline", which is a third
            # of the page, and dragged every rung beneath it out of place.
            continue
        for page in group[:RAMP_PAGES_PER_CLUSTER]:
            if page not in seen:
                seen.append(page)
    return sorted(seen) or list(fallback)


def _token_pages(plan: Sequence, fallback: Sequence[int]) -> list[int]:
    """The pages the house's grid, rhythm and spacing ladder are read from."""
    if not plan:
        return list(fallback)
    ordinary = [entry for entry in plan
                if entry[0].label not in _NOT_ORDINARY] or list(plan)
    ordinary.sort(key=lambda entry: -len(entry[1]))
    chosen = list(ordinary[0][1][:TOKEN_SAMPLE])
    for cluster, group, _ in ordinary[1:]:
        if len(chosen) >= 3:
            break
        chosen.extend(p for p in group if p not in chosen)
    return sorted(chosen[:TOKEN_SAMPLE]) or list(fallback)


def _archetype_richness(archetype: PageArchetype) -> tuple:
    """How good a representative of its cluster this traced page is.

    A page whose headline is missing cannot state its own argument, so that
    comes first; then how much of the deck's material it could carry; then
    how many places it has to put it. Sorting by this rather than taking the
    cluster's centroid-nearest page is the difference between an archetype
    that can hold a slide and one that happens to sit in the middle.
    """
    frames = {frame.id: frame for frame in _walk_frames(archetype.frames)}
    has_headline = any(f.role == "headline" for f in frames.values())
    text_slots = [
        slot for slot in archetype.slots
        if (not slot.accepts or "text" in slot.accepts)
        and frames.get(slot.frame_id) is not None
        and frames[slot.frame_id].role in COPY_FRAME_ROLES
    ]
    capacity = sum(slot.max_chars or 0 for slot in text_slots)
    figures = sum(1 for slot in archetype.slots
                  if slot.accepts and "figure" in slot.accepts)
    return (has_headline, capacity, len(text_slots), figures)


def _walk_frames(frames):
    for frame in frames:
        yield from frame.walk()


def _cluster_plan(pdf_path: str, exclude: Sequence[int]) -> list:
    """Which pages to trace, grouped by the kind of page they are.

    The pack this replaces was built from five page numbers written down by
    hand, so every archetype was a trace of one specific page and the pack
    described those five pages and nothing else. `measure.clusters` already
    groups a publication's pages by what they look like - it was only ever
    feeding an eval fixture - so the groups become the archetypes and the
    pages inside a group become candidates for representing it.
    """
    survey = clusters_model.survey_pdf(pdf_path)
    held_out = set(exclude)
    groups = []
    for cluster in survey.clusters:
        pages = [p for p in cluster.pages if p not in held_out]
        if pages:
            groups.append((cluster, pages))
    groups.sort(key=lambda entry: -len(entry[1]))

    kept = [g for g in groups if len(g[1]) >= MIN_CLUSTER_PAGES]
    for group in groups:
        if len(kept) >= MIN_ARCHETYPES:
            break
        if group not in kept:
            kept.append(group)
    considered = sum(len(pages) for _, pages in groups) or 1
    return [(cluster, pages, len(pages) / considered)
            for cluster, pages in kept[:MAX_ARCHETYPES]]


def from_pdf(pdf_path: str, pages: Optional[Sequence[int]] = None, *,
             pack_id: str, label: str = "", note: str = "",
             exclude_pages: Sequence[int] = ()) -> Pack:
    """Read a publication into a pack.

    With no ``pages`` the publication's own pages are clustered by what they
    look like and each group becomes one archetype, which is the point: a
    pack built from five page numbers written down by hand describes those
    five pages and cannot compose a sixth. ``exclude_pages`` is the held-out
    validation set, and nothing in it is read - a pack fitted to the pages it
    will later be scored against is not measuring anything.

    An explicit ``pages`` list still works, and still means "trace exactly
    these", which is what the reference profiles need.
    """
    plan = None if pages else _cluster_plan(pdf_path, exclude_pages)
    doc = fitz.open(pdf_path)
    if plan is not None:
        pages = sorted({page for _, group, _ in plan for page in group})
    sample = [(number, doc[number - 1]) for number in pages]
    # Two samples, deliberately. The archetypes come from every clustered
    # page, because the point is to describe more compositions than a handful
    # of pages can show. The house's grid, rhythm, spacing ladder, type ramp
    # and palette come from its ordinary page instead: pooling covers and
    # dividers in with the body pages turns EY's 21.0pt rhythm into 10.5pt,
    # because those pages contribute gaps a smaller unit explains better.
    token_pages = _token_pages(plan, pages) if plan is not None else list(pages)
    tokens = [(number, doc[number - 1]) for number in token_pages]
    ramp_pages = _ramp_pages(plan, pages) if plan is not None else list(pages)
    ramp = [(number, doc[number - 1]) for number in ramp_pages]
    first = sample[0][1]
    width, height = float(first.rect.width), float(first.rect.height)
    canvas = Canvas(w_in=width / 72.0, h_in=height / 72.0)

    boxes = []
    for _, page in tokens:
        for block in _page_blocks(page):
            boxes.append((block["x"] * width, block["y"] * height,
                          (block["x"] + block["w"]) * width,
                          (block["y"] + block["h"]) * height))
    grid = recover_grid(edges_from_boxes(boxes, width))

    rhythm_columns: list[list[float]] = []
    for _, page in tokens:
        rhythm_columns.extend(columns_from_page(page))
    rhythm = recover_rhythm(rhythm_columns)

    gaps: list[float] = []
    for _, page in tokens:
        gaps.extend(spacing_model.gaps_from_page(page))
    spacing = spacing_model.recover_spacing(gaps)

    rungs = type_model.read_rungs(ramp)
    roles = type_model.assign_roles(rungs)
    body_face, display_face = type_model.faces_for(roles)
    pairing = type_model.pairing_face(roles)
    # The faces as the pack will name them, which is what the renderer draws
    # and so what every slot is measured in. The raw PostScript names
    # ("ABCDEF+Interstate-Light") resolve to nothing and measure the
    # fallback face instead.
    faces = Faces(body=type_model.clean_face(body_face) or "Arial",
                  display=type_model.clean_face(pairing or display_face) or "Arial")

    evidence, _ = color_model.read_colors(ramp)
    palette = color_model.assign_roles(evidence)

    baseline_pt = rhythm.unit_pt if rhythm else 0.0
    base_pt = spacing.base_pt if spacing else (baseline_pt / 4.0 or 6.0)

    grid_spec = _grid_spec(grid, canvas, baseline_pt)
    type_map = _type_roles(roles, palette, base_pt)
    # Built twice on purpose: the house grid is the one the pages agree on,
    # which is not known until every page has been read, and pages with no
    # grid of their own have to be expressed on it.
    archetypes = tuple(
        _archetype_for_page(page, number, roles, canvas, baseline_pt,
                            faces=faces)
        for number, page in sample
    )
    house = _grid_spec(grid, canvas, baseline_pt) if grid else _modal_grid(archetypes)[0]
    if house is not None:
        archetypes = tuple(
            _archetype_for_page(page, number, roles, canvas, baseline_pt,
                                house_grid=house, faces=faces)
            for number, page in sample
        )
    if plan is not None:
        archetypes = _one_per_cluster(archetypes, plan)
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
        illustration=_read_illustration(sample, canvas),
        archetypes=archetypes,
        body_face=faces.body,
        display_face=faces.display,
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


def _role_of(name: str, rung: type_model.RungEvidence) -> TypeRole:
    """The rung as a role: its size, leading, weight, face and case.

    Shared by the pack's ramp and the slot capacities, so a slot is measured
    in exactly the type the pack will draw in it.
    """
    return TypeRole(
        size_pt=rung.size_pt,
        line_height_pt=rung.line_height_pt,
        weight="bold" if "bold" in rung.face.lower() else (
            "light" if "light" in rung.face.lower() else "regular"
        ),
        face="display" if name in ("headline", "pullquote", "standfirst")
             else "body",
        case="upper" if rung.upper_share >= 0.7 else "none",
        measured_min_pt=rung.size_pt,
        measured_median_pt=rung.size_pt,
        measured_max_pt=rung.size_pt,
    )


def _type_roles(roles: dict[str, type_model.RungEvidence],
                palette: dict[str, str], base_pt: float) -> dict[str, TypeRole]:
    out: dict[str, TypeRole] = {}
    for name, rung in roles.items():
        color_role = color_model.nearest_role(rung.color, palette) or "ink"
        out[name] = _role_of(name, rung).model_copy(update={
            "color_role": color_role if color_role.startswith("ink") else "ink",
        })
    if "body" not in out:
        out["body"] = TypeRole(size_pt=14.0, line_height_pt=18.0)
    return out


# How many pixels of one image to sample. Enough to read a treatment from,
# few enough that reading twenty images costs nothing worth measuring.
ILLUSTRATION_SAMPLE = 400


def _read_illustration(sample, canvas: Canvas) -> Illustration:
    """How this publication treats its imagery, read from its imagery.

    A deck's illustration is as much part of how it reads as its palette, and
    the palette has been measured from the reference since the beginning while
    illustration was decided per slide.
    """
    readings = []
    for _, page in sample:
        width, height = float(page.rect.width), float(page.rect.height)
        for entry in page.get_images(full=True):
            rects = page.get_image_rects(entry[0])
            if not rects:
                continue
            rect = rects[0]
            bleeds = (
                rect.x0 / width <= illustration_model.BLEED_TOLERANCE
                or rect.y0 / height <= illustration_model.BLEED_TOLERANCE
                or rect.x1 / width >= 1 - illustration_model.BLEED_TOLERANCE
                or rect.y1 / height >= 1 - illustration_model.BLEED_TOLERANCE
            )
            pixels = _sample_pixels(page, entry[0])
            if pixels:
                readings.append(illustration_model.read_image(
                    pixels, bleeds=bleeds))

    reading = illustration_model.recover(readings)
    return Illustration(
        treatment=reading.treatment,
        placement=reading.placement,
        style_note=_style_note(reading),
        confidence=Confidence(value=reading.confidence, note=reading.note),
    )


def _sample_pixels(page, xref: int):
    """A handful of an image's pixels, or nothing when it will not open."""
    try:
        import fitz as _fitz

        pixmap = _fitz.Pixmap(page.parent, xref)
        if pixmap.alpha or pixmap.n > 3:
            pixmap = _fitz.Pixmap(_fitz.csRGB, pixmap)
        if pixmap.n < 3 or pixmap.width < 2 or pixmap.height < 2:
            return []
        data = pixmap.samples
        stride = max(1, (pixmap.width * pixmap.height) // ILLUSTRATION_SAMPLE)
        out = []
        for index in range(0, pixmap.width * pixmap.height, stride):
            offset = index * pixmap.n
            if offset + 2 < len(data):
                out.append((data[offset], data[offset + 1], data[offset + 2]))
        return out
    except Exception:
        return []


def _style_note(reading) -> str:
    """Anything worth telling a provider that the treatment does not already
    say.

    Deliberately empty for now. The sentence handed to a provider is composed
    from the treatment and the placement in `renderer/illustration.py`, and a
    note repeating them produced "desaturated, muted colour, inset within the
    page's margins, desaturated, muted colour, inset within the page's
    margins". This is where a hand-edited pack can add what was actually
    observed and could not be measured.
    """
    return ""


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
