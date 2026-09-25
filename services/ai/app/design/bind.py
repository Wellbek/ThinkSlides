"""
Turning a chosen archetype and its binding into a page.

The archetype supplies every rectangle; this supplies only what goes in
them. That separation is the point of the whole layer: nothing here computes
a coordinate, so nothing here can invent one, and a page is wrong in ways
that are attributable to a pack or to a fit cost rather than to a model
having guessed.
"""
from __future__ import annotations

from typing import Optional

from app.design.material import MaterialItem, MaterialShape
from app.design.pack import Pack, PageArchetype
from app.design.schema import (
    Exhibit,
    Figure,
    Frame,
    GridPlace,
    Page,
    Paragraph,
    Provenance,
    Run,
    TextBlock,
)
from app.design.roles import type_role_for
from app.design.select import Selection


def _type_role(frame: Frame, pack: Pack, item: MaterialItem) -> str:
    return type_role_for(frame.role, item.kind, pack, frame.type_role)


def _text_block(frame: Frame, pack: Pack, item: MaterialItem) -> TextBlock:
    paragraphs = tuple(
        Paragraph(runs=(Run(text=line.strip()),))
        for line in item.text.split("\n") if line.strip()
    )
    return TextBlock(type_role=_type_role(frame, pack, item),
                     paragraphs=paragraphs)


def _content_for(frame: Frame, pack: Pack, item: MaterialItem):
    if item.kind in ("text", "quote", "caption", "footnote"):
        return _text_block(frame, pack, item)
    if item.kind == "figure":
        payload = dict(item.payload or {})
        # Every real producer - generate_image, select_source_image,
        # compute_visual_with_code - nests the actual source under "image",
        # the same convention build_chart nests a chart spec under "chart".
        # A bare top-level "source" was never real: no producer, and no test
        # fixture, ever wrote one, so looking for it first meant this always
        # fell through to the whole raw block instead - `{"type": "image",
        # "image": {...}}` handed to the renderer as its own image source,
        # which resolves nothing and draws nothing. See issue #287.
        source = payload.get("image")
        if not isinstance(source, dict):
            source = payload.get("source") if isinstance(
                payload.get("source"), dict) else {}
        alt = (source.get("altText") if isinstance(source, dict) else None)             or payload.get("altText") or ""
        return Figure(source=source, fit="cover", alt_text=str(alt))
    payload = dict(item.payload or {})
    # The shape the exhibit declared travels with it, so the repair loop can
    # see that a wide graph landed in a tall slot.
    return Exhibit(exhibit_kind=item.kind, payload=payload,
                   aspect=item.aspect)


def _fill(frames: tuple[Frame, ...], contents: dict[str, list],
          pack: Pack) -> tuple[Frame, ...]:
    """Copy the archetype's frames, putting bound content into them.

    A frame that ends up with nothing in it is dropped, unless it carries a
    ground or a scrim - those are the page's structure and paint whether or
    not anything sits on them. Keeping empty content frames is how an
    archetype with a figure slot and a page with no figure produces a third
    of a page of white where the reference has a full-bleed panel.
    """
    out = []
    for frame in frames:
        items = contents.get(frame.id) or []
        children = _fill(frame.children, contents, pack)
        payload = tuple(_content_for(frame, pack, item) for item in items)
        content = payload or frame.content
        structural = bool(frame.ground or frame.scrim)
        if not content and not children and not structural:
            continue
        update = {"children": children, "content": content}
        # A bleed is for a photograph. A table, chart or diagram carries text
        # that has to sit inside the margins the rest of the page keeps: a
        # language whose figure slot bleeds set every exhibit bound to it
        # hard against the edge of the page, with its first column clipped.
        if frame.bleed and any(isinstance(c, Exhibit) for c in payload):
            update["bleed"] = ()
        out.append(frame.model_copy(update=update))
    return tuple(out)


def build_page(page_id: str, shape: MaterialShape, selection: Selection,
               pack: Pack, *, considered: tuple[str, ...] = (),
               source_slide: Optional[int] = None) -> Page:
    """The page this selection describes, with its material in place."""
    archetype: PageArchetype = selection.archetype
    contents: dict[str, list] = {}
    for placement in selection.placements:
        contents.setdefault(placement.slot.frame_id, []).append(placement.item)

    heading_frame = next((p.slot.frame_id for p in selection.placements
                          if p.item.role_hint == "heading"), "")
    frames = _compact(_fill(archetype.frames, contents, pack), archetype.frames)
    heading_rung = next(
        (block.type_role for frame in _walk(frames) if frame.id == heading_frame
         for block in frame.content if isinstance(block, TextBlock)), "")
    return Page(
        id=page_id,
        archetype=archetype.id,
        role=shape.role,
        grid=archetype.grid,
        frames=frames,
        provenance=Provenance(
            selected_by="fit",
            fit_cost=round(selection.cost, 4),
            considered=considered,
            source_slide=source_slide,
            heading_frame=heading_frame,
            heading_rung=heading_rung,
        ),
    )


def _compact(frames: tuple[Frame, ...],
             original: tuple[Frame, ...]) -> tuple[Frame, ...]:
    """Close the gap a dropped frame leaves behind.

    `_fill` drops a frame nothing was bound to, which is right - an empty
    figure slot painted as a third of a page of white is worse than no figure
    slot. What it did not do is move what was below it. So a content page
    whose archetype has a figure slot, used for a page with no figure, came
    out as a headline, a hole where the graph would have gone, and a
    paragraph three quarters of the way down. Every page of a text-only deck
    looked unfinished, and the reason was invisible: the page was laid out
    exactly as its archetype says, and the archetype is not wrong.

    Only pages that actually lost a frame are touched. A page that kept all
    of them is composed exactly as the archetype states, which is what a
    measured pack is for.

    Frames are compacted by row rather than one at a time, so two frames side
    by side stay side by side. The leftover at the bottom goes to the last
    row, which is where a column of prose or a figure wants it.
    """
    # A *top-level* frame going missing is what leaves a hole in the stack.
    # A child dropping out changes nothing about where its siblings sit -
    # a child's place is relative to its parent - so compacting for one
    # closed a gap that was never there and pushed two EY frames into each
    # other. `_walk` descends, which is right for asking what was kept and
    # wrong for asking whether the stack moved.
    kept = {frame.id for frame in frames}
    if all(frame.id in kept for frame in original):
        return frames

    # Only a clean vertical stack is compacted.
    #
    # The schema deliberately allows siblings to overlap at different `z` -
    # type over a full-bleed image, a pull-quote across a panel edge - and a
    # page built that way cannot be closed up by moving frames down a column,
    # because "the row below" is not a thing it has. A first version tried
    # anyway, grouped the overlapping frames into one row, and produced a
    # document the renderer refused: "frames 'pullquote-0' and 'body-2'
    # overlap by 25% at the same z".
    #
    # So the rule is: every frame either states a band or is structural
    # (a ground or a scrim, which is a background and does not move), and no
    # two bands overlap. That is exactly the shape an authored language
    # derives - the case this exists for - and it leaves every measured pack
    # composed as its own pages were.
    placed = []
    for frame in frames:
        if frame.ground or frame.scrim:
            continue
        band = _band(frame)
        # Grid-placed frames only. A measured pack mixes grid and fractional
        # placement, and their vertical fractions are not of the same box -
        # the content box and the page - so a band from one cannot be
        # compared with a band from the other. Such a page is left exactly as
        # its archetype states, which is what a measured pack is for.
        if band is None or not isinstance(frame.place, GridPlace):
            return frames
        placed.append(frame)
    if len(placed) < 2:
        return frames

    ordered = sorted(placed, key=lambda f: _band(f)[0])
    if _stacked_over(ordered):
        return frames

    # The archetype's own spacing, taken from the layout that still has every
    # frame in it. Measuring between survivors instead would preserve the gap
    # the *dropped* frame was sitting in, which is the hole this exists to
    # close - and the first version of this did exactly that and moved
    # nothing.
    gap = _typical_gap([f for f in original if _band(f) is not None])

    rows = _rows(ordered)
    moved: dict[str, float] = {}
    cursor = min(_band(f)[0] for f in rows[0])
    for row in rows:
        top = min(_band(f)[0] for f in row)
        bottom = max(_band(f)[1] for f in row)
        for frame in row:
            moved[frame.id] = _band(frame)[0] - top + cursor
        cursor += (bottom - top) + gap

    # Whatever is left at the bottom goes to the last row's frames, which is
    # the one a reader's eye ends on. The trailing gap added above is not
    # part of the page, so it comes back here.
    spare = 1.0 - (cursor - gap)
    grown = {f.id for f in rows[-1]} if spare > 0 else set()

    columns = _closed_up(rows, original)

    out = []
    for frame in frames:
        if frame.id not in moved:
            out.append(frame)
            continue
        band = _band(frame)
        height = band[1] - band[0] + (spare if frame.id in grown else 0.0)
        update = {"y": round(moved[frame.id], 4), "h": round(height, 4)}
        update.update(columns.get(frame.id, {}))
        out.append(frame.model_copy(update={
            "place": frame.place.model_copy(update=update),
        }))

    # Checked rather than trusted. Everything above reasons about a stack,
    # and the cost of being wrong is a document `render_doc` refuses outright
    # - which is a failed run, not a slightly worse page. A compaction that
    # did not come out as a clean stack is discarded and the page renders
    # with its hole, which is ugly and correct.
    compacted = [f for f in out if f.id in moved]
    compacted.sort(key=lambda f: _band(f)[0])
    if _stacked_over(compacted, tolerance=1e-6):
        return frames
    if compacted and _band(compacted[-1])[1] > 1.0 + 1e-6:
        return frames
    return tuple(out)


def _closed_up(rows: list[list[Frame]],
               original: tuple[Frame, ...]) -> dict[str, dict]:
    """Where each survivor sits across its row, once the row has lost members.

    The horizontal half of the same problem. `_compact` closed the gap a
    dropped frame left *above* the frames below it, and once slots could share
    a row a frame could also lose the thing beside it - so an archetype of
    `heading 6 / figure 4 / prose 2`, used for a page with no chart, put its
    only paragraph in a two-column strip at the right-hand edge with four
    empty columns to its left. The stack was clean and the page was broken.

    A survivor moves left to where the row now starts. A survivor left *alone*
    takes the width the row occupied, because a lone narrow column on a wide
    page is the same unfinished look this exists to prevent.
    """
    spans: dict[str, tuple[int, int]] = {}
    for frame in original:
        if isinstance(frame.place, GridPlace):
            spans[frame.id] = (frame.place.col, frame.place.span)

    out: dict[str, dict] = {}
    for row in rows:
        placed = [f for f in row if f.id in spans]
        if not placed:
            continue
        before = [spans[f.id] for f in original
                  if f.id in spans and _same_band(f, row)]
        if len(before) <= len(placed):
            continue
        # Only a true row is redistributed: members side by side, no two in
        # the same columns. A tall figure beside a heading stacked over prose
        # shares a height band with both, and spreading the three across the
        # band set the heading and the prose beside each other.
        if not _side_by_side(before):
            continue
        left = min(col for col, _ in before)
        width = max(col + span for col, span in before) - left
        placed.sort(key=lambda f: spans[f.id][0])
        if len(placed) == 1:
            out[placed[0].id] = {"col": left, "span": max(1, width)}
            continue
        # The survivors share the row's whole width, in proportion to the
        # widths they had. Packing them left at their old widths moved the
        # hole from the middle of the row to its end: two of three prose
        # columns came out at a third of the page each with a third of the
        # page white beside them (#357).
        shares = _shares([spans[f.id][1] for f in placed], width)
        if shares is None:
            continue
        cursor = left
        for frame, span in zip(placed, shares):
            out[frame.id] = {"col": cursor, "span": span}
            cursor += span
    return out


def _side_by_side(spans: list[tuple[int, int]]) -> bool:
    """Whether these column spans sit beside each other without sharing any."""
    ordered = sorted(spans)
    return all(a[0] + a[1] <= b[0] for a, b in zip(ordered, ordered[1:]))


def _shares(weights: list[int], width: int) -> Optional[list[int]]:
    """`width` whole columns split in proportion to `weights`, each at least
    one, summing exactly to `width` - largest remainder, so rounding can
    never push the last member off the grid. None when they cannot all fit."""
    if width < len(weights):
        return None
    total = sum(weights) or len(weights)
    exact = [w * width / total for w in weights]
    spans = [max(1, int(e)) for e in exact]
    while sum(spans) > width:
        spans[spans.index(max(spans))] -= 1
    order = sorted(range(len(weights)), key=lambda i: exact[i] - int(exact[i]),
                   reverse=True)
    index = 0
    while sum(spans) < width:
        spans[order[index % len(order)]] += 1
        index += 1
    return spans


def _stacked_over(ordered: list[Frame], tolerance: float = 0.0) -> bool:
    """Whether any two frames overlap on the page, not merely in height.

    Two frames side by side share a band - that is what a row is - and
    checking height alone read every row as an overlap, so no page with one
    was ever compacted (#357).
    """
    for index, upper in enumerate(ordered):
        for lower in ordered[index + 1:]:
            if _band(lower)[0] >= _band(upper)[1] - tolerance:
                break
            if _across(upper, lower):
                return True
    return False


def _across(a: Frame, b: Frame) -> bool:
    """Whether two frames overlap horizontally. Unknown extents count as yes."""
    def extent(frame: Frame):
        place = frame.place
        if isinstance(place, GridPlace):
            return (place.col, place.col + place.span)
        x, w = getattr(place, "x", None), getattr(place, "w", None)
        if x is None or w is None:
            return None
        return (float(x), float(x) + float(w))

    ea, eb = extent(a), extent(b)
    if ea is None or eb is None or type(a.place) is not type(b.place):
        return True
    return ea[0] < eb[1] and eb[0] < ea[1]


def _same_band(frame: Frame, row: list[Frame]) -> bool:
    """Whether this frame sat in the band this row occupies."""
    band = _band(frame)
    if band is None:
        return False
    top = min(_band(f)[0] for f in row)
    bottom = max(_band(f)[1] for f in row)
    middle = (band[0] + band[1]) / 2.0
    return top - 1e-6 <= middle <= bottom + 1e-6


def _typical_gap(original: list[Frame]) -> float:
    """The space this archetype leaves between one row and the next.

    The smallest of them rather than the mean: an archetype whose largest gap
    is the one a figure sits under would carry that gap into every page, and
    a page with no figure would be as short of material as before.
    """
    rows = _rows(sorted(original, key=lambda f: _band(f)[0]))
    gaps = [
        max(0.0, min(_band(f)[0] for f in rows[index + 1])
            - max(_band(f)[1] for f in rows[index]))
        for index in range(len(rows) - 1)
    ]
    return min(gaps) if gaps else 0.0


def _band(frame: Frame) -> Optional[tuple[float, float]]:
    """This frame's top and bottom, or None if it does not state them.

    A frame on a row-based grid, or one bleeding off the page, is left where
    it is: compacting something whose vertical extent is not a pair of
    fractions would be guessing at it.
    """
    place = frame.place
    y = getattr(place, "y", None)
    h = getattr(place, "h", None)
    if y is None or h is None or frame.bleed:
        return None
    return (float(y), float(y) + float(h))


def _rows(placed: list[Frame]) -> list[list[Frame]]:
    """Frames grouped into bands that overlap vertically.

    Two frames side by side share a band and move together; a stack is one
    frame per band. Written as a sweep rather than a pairwise check because
    a row is transitive - three frames across a page overlap their
    neighbours, not necessarily each other.
    """
    rows: list[list[Frame]] = []
    edge = None
    for frame in placed:
        top, bottom = _band(frame)
        if edge is None or top >= edge:
            rows.append([frame])
            edge = bottom
        else:
            rows[-1].append(frame)
            edge = max(edge, bottom)
    return rows


def _walk(frames):
    for frame in frames:
        yield from frame.walk()


def unplaced_report(selection: Selection) -> Optional[str]:
    """What this archetype could not carry, in words worth acting on.

    Returned rather than logged because the caller's options are real: pick
    a different archetype, split the page, or cut the material. Silently
    dropping the third bullet is not among them.
    """
    if not selection.unplaced:
        return None
    kinds: dict[str, int] = {}
    for item in selection.unplaced:
        kinds[item.kind] = kinds.get(item.kind, 0) + 1
    described = ", ".join(f"{count} {kind}" for kind, count in kinds.items())
    return (
        f"archetype {selection.archetype.id!r} has no slot for {described}; "
        f"the page needs a different archetype or less material"
    )
