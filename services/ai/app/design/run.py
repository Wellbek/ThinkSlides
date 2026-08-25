"""
Putting a run on a design language pack.

Everything in `app/design` was built, gated and left unreachable: no module
under `app/agent` imports it, so every real generation still went through
`select_layout` - keyword scoring over eight hardcoded layouts - and rendered
title-plus-bullets. This is the seam between the two.

Three decisions live here.

**A pack is a composition language, not a skin.** It carries grid, rhythm,
spacing ladder, type ramp and archetypes. The colours and faces the user chose
from `propose_design_directions` are laid over the top by `overlay`. A user who
picked a green palette gets a green deck set in EY's composition, not an EY
deck. Conflating the two would mean choosing a house style silently
overrode the one design decision the user was actually asked to make.

**Which pack is measured, not guessed.** `for_deck` scores the whole deck
against each candidate with the same `choose` the page selector uses, and takes
the pack with the lowest total. "Which house style can carry this material" is a
question the fit function already answers; asking it once per pack is the whole
implementation.

**Material that will not fit is split, never dropped.** `fit` refuses an
archetype that cannot carry every item precisely so that losing a paragraph is
not on the menu. That refusal has to be honoured here too, so a page with more
material than any archetype holds becomes two pages.
"""

from __future__ import annotations

import functools
import json
import os
from typing import Iterable, Optional, Sequence

from app.design.bind import build_page
from app.design.material import MaterialShape, shape_from
from app.design.pack import ColorRoles, Pack
from app.design.repair import repair
from app.design.schema import DesignDoc, Page, PackRef
from app.design.select import choose
from app.design.vocabulary import ensure

PACK_DIR = os.path.join(os.path.dirname(__file__), "packs")

# The exhibit kinds a slide's content blocks can carry, and the material kind
# each becomes. Anything not named here is prose.
EXHIBIT_KINDS = {
    "chart": "chart",
    "table": "table",
    "metrics": "metrics",
    "statCallout": "metrics",
    "diagram": "diagram",
}
FIGURE_KINDS = {"image"}

# A page is split rather than truncated, but not indefinitely: material that
# still will not place after this many splits is a caller error worth
# reporting, not something to keep halving.
MAX_SPLITS = 4


class Unplaceable(RuntimeError):
    """No archetype in this pack can carry this material, even split."""


@functools.lru_cache(maxsize=16)
def load(pack_id: str) -> Pack:
    """One of the packs shipped with the service, by id."""
    path = os.path.join(PACK_DIR, f"{pack_id}.json")
    if not os.path.exists(path):
        raise KeyError(
            f"no pack {pack_id!r}; this service ships " + ", ".join(available())
        )
    with open(path) as handle:
        pack = Pack.model_validate(json.load(handle))
    # Every shipped pack was recovered from sampled *design* pages, so none of
    # them has a cover, a divider or a closing page. A deck needs all three.
    return ensure(pack)


def available() -> tuple[str, ...]:
    """Every pack id this service ships, in a stable order."""
    if not os.path.isdir(PACK_DIR):
        return ()
    return tuple(sorted(
        name[: -len(".json")] for name in os.listdir(PACK_DIR)
        if name.endswith(".json")
    ))


# ── The user's design direction, over the pack's composition ─────────────


def _palette(design_system: Optional[dict]) -> dict[str, str]:
    colors = ((design_system or {}).get("palette") or {}).get("colors") or []
    return {
        str(entry.get("name")): str(entry.get("value"))
        for entry in colors
        if isinstance(entry, dict) and entry.get("name") and entry.get("value")
    }


def _faces(design_system: Optional[dict]) -> tuple[str, str]:
    """The heading and body faces the direction asked for, if it named any."""
    typography = (design_system or {}).get("typography") or {}
    fonts = typography.get("fonts") or {}
    heading = str(fonts.get("majorFont") or fonts.get("heading") or "")
    body = str(fonts.get("minorFont") or fonts.get("body") or "")
    return heading, body


def overlay(pack: Pack, design_system: Optional[dict]) -> Pack:
    """The pack's composition, wearing the design direction's colours.

    Only the roles the direction actually names are moved. A palette with no
    `secondary` leaves the pack's own `accent_alt` alone rather than
    collapsing two roles onto one hue, which is how an accent stops reading
    as an accent.
    """
    palette = _palette(design_system)
    if not palette and not any(_faces(design_system)):
        return pack

    replacements: dict[str, str] = {}
    if palette.get("primary"):
        replacements["accent"] = palette["primary"]
    if palette.get("accent"):
        replacements["accent_alt"] = palette["accent"]
    if palette.get("background"):
        replacements["ground"] = palette["background"]
    if palette.get("text"):
        replacements["ink"] = palette["text"]
    if palette.get("gray"):
        replacements["ink_muted"] = palette["gray"]

    colors = (pack.colors.model_copy(update=replacements)
              if replacements else pack.colors)
    heading_face, body_face = _faces(design_system)
    return pack.model_copy(update={
        "colors": colors if isinstance(colors, ColorRoles) else pack.colors,
        "display_face": heading_face or pack.display_face,
        "body_face": body_face or pack.body_face,
    })


# ── A slide dict, read as material ───────────────────────────────────────


def _runs_text(block: dict) -> list[str]:
    out = []
    for run in block.get("runs") or ():
        if isinstance(run, dict):
            text = str(run.get("text") or "").strip()
            if text:
                out.append(text)
    return out


def _role_of(slide: dict, index: int, total: int) -> str:
    """What kind of page this is, from what the slide says about itself."""
    declared = str(slide.get("role") or "").strip()
    if declared in ("cover", "section", "content", "exhibit", "closing",
                    "appendix"):
        return declared
    if index == 0:
        return "cover"
    if total > 1 and index == total - 1:
        return "closing"
    return "content"


def material_from(slide: dict, *, index: int = 0, total: int = 1) -> MaterialShape:
    """Gather one designed slide into the description a layout is chosen from.

    Reads the shape `design_slide_content` already produces, so the tool did
    not have to be forked to reach this path. The heading is the slide's own
    `heading` when it states one, and otherwise the first single-run text
    block - which is what the placeholder-shaped output puts a title in.
    """
    contents = [c for c in (slide.get("contents") or ()) if isinstance(c, dict)]

    heading = str(slide.get("heading") or slide.get("title") or "").strip()
    paragraphs: list[str] = []
    exhibits: list[dict] = []
    figures: list[dict] = []

    heading_taken = bool(heading)
    for block in contents:
        kind = str(block.get("type") or "")
        if kind == "text":
            lines = _runs_text(block)
            if not lines:
                continue
            if not heading_taken and len(lines) == 1:
                heading = lines[0]
                heading_taken = True
                continue
            paragraphs.extend(lines)
        elif kind in EXHIBIT_KINDS:
            payload = dict(block)
            payload["kind"] = EXHIBIT_KINDS[kind]
            exhibits.append(payload)
        elif kind in FIGURE_KINDS:
            figures.append(dict(block))
        else:
            # An unknown block still carries copy worth keeping rather than
            # discarding: dropping it silently is the failure this whole
            # layer exists to end.
            paragraphs.extend(_runs_text(block))

    return shape_from(
        _role_of(slide, index, total),
        heading,
        paragraphs,
        exhibits=exhibits,
        figures=figures,
        citations=int(slide.get("citationCount") or 0),
    )


def _split(shape: MaterialShape) -> tuple[MaterialShape, MaterialShape]:
    """Halve a page's prose, keeping its heading on the first part."""
    texts = [item.text for item in shape.items
             if item.kind == "text" and item.role_hint != "heading"]
    exhibits = [dict(item.payload or {}) for item in shape.items
                if item.kind in ("chart", "table", "diagram", "metrics")]
    figures = [dict(item.payload or {}) for item in shape.items
               if item.kind == "figure"]
    cut = max(1, len(texts) // 2)
    first = shape_from(shape.role, shape.heading, texts[:cut],
                       exhibits=exhibits, citations=shape.citations)
    second = shape_from("content", "", texts[cut:], figures=figures)
    return first, second


def _place(shape: MaterialShape, pack: Pack,
           previous: Optional[str]) -> list[tuple[MaterialShape, object]]:
    """This material, bound to archetypes, split as many times as it takes."""
    pending = [shape]
    placed: list[tuple[MaterialShape, object]] = []
    splits = 0
    last = previous
    while pending:
        current = pending.pop(0)
        selection = choose(current, pack, previous_archetype=last)
        if selection is not None:
            placed.append((current, selection))
            last = selection.archetype.id
            continue
        if splits >= MAX_SPLITS or len(
                [i for i in current.items if i.kind == "text"]) < 2:
            raise Unplaceable(
                f"no archetype in pack {pack.id!r} can carry a page of "
                f"{current.total_chars} characters in "
                f"{len(current.items)} items, and it cannot be split further"
            )
        splits += 1
        head, tail = _split(current)
        pending.insert(0, tail)
        pending.insert(0, head)
    return placed


def compose(slides: Sequence[dict], pack: Pack, *, title: str = "") -> DesignDoc:
    """Every designed slide, laid out in this pack and repaired.

    Repair runs here rather than being offered as a tool. The validator this
    replaces was a tool the model could decline to call, and it was invoked
    zero times across every observed run.
    """
    ordered = sorted(
        [s for s in slides if isinstance(s, dict)],
        key=lambda s: int(s.get("slideIndex") or 0),
    )
    pages: list[Page] = []
    previous: Optional[str] = None
    for index, slide in enumerate(ordered):
        shape = material_from(slide, index=index, total=len(ordered))
        for part, selection in _place(shape, pack, previous):
            page = build_page(f"p{len(pages) + 1}", part, selection, pack,
                              considered=tuple(a.id for a in pack.archetypes))
            previous = selection.archetype.id
            pages.append(page)

    doc = DesignDoc(pack=PackRef(id=pack.id, version=pack.version),
                    canvas=pack.canvas, grid=pack.grid,
                    pages=tuple(pages), title=title)
    healed = tuple(repair(doc, page, pack).page for page in doc.pages)
    return doc.model_copy(update={"pages": healed})


# ── Which pack ───────────────────────────────────────────────────────────


# What a planned slide's `contentType` says about the kind of page it is.
# Anything not named here is an ordinary content page.
ROLE_FOR_CONTENT_TYPE = {
    "title": "cover",
    "conclusion": "closing",
    "section": "section",
}


def slides_from_plan(structure: Optional[dict]) -> list[dict]:
    """The plan, in the shape a designed slide has.

    Enough to choose a pack before a word of copy has been written, which is
    when the choice has to be made: the pack governs how every page is
    composed, so it cannot wait until the pages exist.
    """
    planned = (structure or {}).get("slides") or []
    out: list[dict] = []
    for index, slide in enumerate(planned):
        if not isinstance(slide, dict):
            continue
        points = [str(p) for p in (slide.get("keyPoints") or []) if str(p).strip()]
        contents: list[dict] = []
        if points:
            contents.append({"type": "text",
                             "runs": [{"text": p} for p in points]})
        content_type = str(slide.get("contentType") or "")
        if content_type in ("chart", "table", "dashboard"):
            kind = "table" if content_type == "table" else "chart"
            contents.append({"type": kind, "kind": kind})
        out.append({
            "slideIndex": int(slide.get("slideIndex", index)),
            "heading": str(slide.get("title") or ""),
            "role": ROLE_FOR_CONTENT_TYPE.get(content_type, "content"),
            "contents": contents,
        })
    return out


def for_plan(structure: Optional[dict],
             candidates: Optional[Iterable[str]] = None) -> Optional[Pack]:
    """The pack that best carries the deck this plan describes."""
    slides = slides_from_plan(structure)
    if not slides:
        return None
    return for_deck(slides, candidates)


def for_deck(slides: Sequence[dict],
             candidates: Optional[Iterable[str]] = None) -> Optional[Pack]:
    """The shipped pack that can carry this deck at the lowest total cost.

    Measured with the same `choose` a page uses, so this answers "which house
    style fits this material" with the fit function rather than with a guess
    about the topic.
    """
    ids = tuple(candidates) if candidates is not None else available()
    ordered = sorted(
        [s for s in slides if isinstance(s, dict)],
        key=lambda s: int(s.get("slideIndex") or 0),
    )
    shapes = [material_from(s, index=i, total=len(ordered))
              for i, s in enumerate(ordered)]

    best: Optional[Pack] = None
    best_cost = float("inf")
    for pack_id in ids:
        try:
            pack = load(pack_id)
        except (KeyError, ValueError):
            continue
        total = 0.0
        previous: Optional[str] = None
        for shape in shapes:
            selection = choose(shape, pack, previous_archetype=previous)
            if selection is None:
                # A pack that cannot carry a page is not disqualified - the
                # page will be split - but it is charged for the trouble.
                total += 10.0
                continue
            total += selection.cost
            previous = selection.archetype.id
        if total < best_cost:
            best, best_cost = pack, total
    return best
