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
    Page,
    Paragraph,
    Provenance,
    Run,
    TextBlock,
)
from app.design.roles import type_role_for
from app.design.select import Selection


def _type_role(frame: Frame, pack: Pack, item: MaterialItem) -> str:
    return type_role_for(frame.role, item.kind, pack)


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
        return Figure(source=payload.get("source", payload), fit="cover",
                      alt_text=str(payload.get("altText", "")))
    payload = dict(item.payload or {})
    return Exhibit(exhibit_kind=item.kind, payload=payload)


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
        out.append(frame.model_copy(update={
            "children": children,
            "content": content,
        }))
    return tuple(out)


def build_page(page_id: str, shape: MaterialShape, selection: Selection,
               pack: Pack, *, considered: tuple[str, ...] = ()) -> Page:
    """The page this selection describes, with its material in place."""
    archetype: PageArchetype = selection.archetype
    contents: dict[str, list] = {}
    for placement in selection.placements:
        contents.setdefault(placement.slot.frame_id, []).append(placement.item)

    return Page(
        id=page_id,
        archetype=archetype.id,
        role=shape.role,
        grid=archetype.grid,
        frames=_fill(archetype.frames, contents, pack),
        provenance=Provenance(
            selected_by="fit",
            fit_cost=round(selection.cost, 4),
            considered=considered,
        ),
    )


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
