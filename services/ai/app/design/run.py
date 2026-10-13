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

import dataclasses
import functools
import json
import os
from typing import Iterable, Optional, Sequence

from app.design.bind import build_page
from app.design.material import MaterialShape, shape_from
from app.design.pack import ColorRoles, Pack
from app.design.repair import overflow_findings, repair
from app.design.schema import DesignDoc, Frame, Page, PackRef, TextBlock
from app.design.select import choose, fit, readable_somewhere
from app.design.vocabulary import ensure, remeasured
from app.observability.logging import get_logger

logger = get_logger(__name__)

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

# A page is split rather than truncated, but not indefinitely. The bound comes
# from the material rather than being a fixed number: a page can never need
# more parts than it has paragraphs, and a fixed cap of four refused eighteen
# paragraphs that simply needed five pages. Termination does not rest on this
# anyway - each split strictly reduces the item count of both halves, and a
# part too small to divide reports rather than looping.
MIN_SPLITS = 4


def natural_aspect(block: dict) -> Optional[float]:
    """The shape a figure wants, read from the figure itself.

    An SVG says so in its viewBox, which is why a Mermaid graph is rendered
    to SVG *before* the page is composed rather than after: a figure whose
    shape is unknown until it is drawn cannot be given a slot that suits it.
    """
    drawn = (block or {}).get("diagram") or {}
    if str(drawn.get("format") or "").lower() != "svg":
        return None
    try:
        from xml.etree import ElementTree

        from app.renderer.svg.importer import read_viewbox

        view = read_viewbox(ElementTree.fromstring(str(drawn.get("svg") or "")))
        return view.aspect if view.aspect > 0 else None
    except Exception:
        # A figure that will not parse has no shape to declare, and the
        # renderer refuses it by name later. Nothing is guessed here.
        return None


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
    # And its slot capacities were traced before its frames were final, so
    # they are measured again from the frames it actually has - after any
    # frame laid across two grounds is seated on one (#396).
    from app.design.grounds import seated

    return ensure(remeasured(seated(pack)))


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


# What each block builder calls its entries, and which of an entry's fields
# carry copy. Written out rather than walked generically: a generic walk over
# an arbitrary dict picks up ids, tones and icon names as though they were
# prose, and a heading made of the word "benefit" is worse than none.
BLOCK_TEXT_FIELDS = {
    "accentCard": ("accentCards", ("heading", "body")),
    "iconCard": ("iconCards", ("heading", "body")),
    "rowBand": ("rowBands", ("label", "prose")),
}

# The two that nest their payload one level down rather than holding a list at
# the top: `{"type": "banner", "banner": {"text": ...}}`.
NESTED_BLOCK_TEXT = {
    "banner": ("banner", "text"),
}


def _block_text(block: dict) -> list[str]:
    """The copy inside a typed block payload, for a block with no `runs`."""
    kind = str(block.get("type") or "")

    nested = NESTED_BLOCK_TEXT.get(kind)
    if nested:
        holder, field = nested
        text = str((block.get(holder) or {}).get(field) or "").strip()
        return [text] if text else []

    if kind == "imageCardRow":
        cards = (block.get("imageCardRow") or {}).get("cards") or ()
        return [line for card in cards if isinstance(card, dict)
                for line in [" - ".join(
                    part for part in (str(card.get("title") or "").strip(),
                                      str(card.get("caption") or "").strip())
                    if part)]
                if line]

    named = BLOCK_TEXT_FIELDS.get(kind)
    if not named:
        return []
    key, fields = named
    out: list[str] = []
    for entry in block.get(key) or ():
        if not isinstance(entry, dict):
            continue
        parts: list[str] = []
        for field in fields:
            value = entry.get(field)
            if isinstance(value, (list, tuple)):
                parts.extend(str(item).strip() for item in value
                             if str(item or "").strip())
            elif str(value or "").strip():
                parts.append(str(value).strip())
        if parts:
            out.append(" - ".join(parts))
    return out


def page_role(slide: dict, index: int, total: int) -> str:
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
            aspect = natural_aspect(block)
            if aspect:
                # Declared before placement, so the selector can prefer a slot
                # the figure actually fits. Without it a wide graph lands in a
                # tall slot and is cropped or letterboxed, and the cost of
                # that is discovered at render when nothing can act on it.
                payload["aspect"] = aspect
            exhibits.append(payload)
        elif kind in FIGURE_KINDS:
            figures.append(dict(block))
        else:
            # An unknown block still carries copy worth keeping rather than
            # discarding: dropping it silently is the failure this whole
            # layer exists to end.
            #
            # `_runs_text` alone was not enough for that. The block builders
            # write their copy into their own typed payload - `accentCards`,
            # `rowBands`, `iconCards` - and none of them carries a `runs`
            # key, so every one of them reached here and contributed nothing.
            # A page built entirely of accent cards arrived at the selector as
            # a page with a heading and no material.
            paragraphs.extend(_runs_text(block) or _block_text(block))

    return shape_from(
        page_role(slide, index, total),
        heading,
        paragraphs,
        exhibits=exhibits,
        figures=figures,
        citations=int(slide.get("citationCount") or 0),
    )


# What takes a figure slot: every exhibit, and an image.
_VISUAL_KINDS = frozenset({"chart", "table", "diagram", "metrics", "figure"})


def _split(shape: MaterialShape) -> tuple[MaterialShape, MaterialShape]:
    """Halve a page's prose and its exhibits, keeping its heading on both parts.

    Exhibits are divided as well as prose. Every archetype in most packs has
    one exhibit slot, so a page carrying a diagram *and* a table could never
    be placed however its paragraphs were divided: both exhibits stayed on
    the first part, that part ran out of prose to split, and the whole deck
    dropped to the stock renderer (#351).
    """
    texts = [item.text for item in shape.items
             if item.kind == "text" and item.role_hint != "heading"]
    # Exhibits and images compete for the same figure slots, so they are
    # divided together: an image beside a chart on a pack with one figure
    # slot is as unplaceable as two charts.
    visuals = [item for item in shape.items if item.kind in _VISUAL_KINDS]
    # A page with one paragraph and two visuals still divides - at the
    # visuals, with the paragraph staying beside the first of them. One
    # paragraph and one visual divides too, the visual continuing on its own:
    # that is only asked for when no arrangement holds the two together.
    cut = max(1, len(texts) // 2) if len(texts) > 1 or len(visuals) < 2 else 1
    if len(visuals) > 1:
        shown = max(1, len(visuals) // 2)
    elif len(texts) == 1 and visuals:
        shown = 0
    else:
        shown = len(visuals)

    def exhibits(part):
        return [dict(i.payload or {}) for i in part if i.kind != "figure"]

    def figures(part):
        return [dict(i.payload or {}) for i in part if i.kind == "figure"]

    first = shape_from(shape.role, shape.heading, texts[:cut],
                       exhibits=exhibits(visuals[:shown]),
                       figures=figures(visuals[:shown]),
                       citations=shape.citations)
    # The continuation keeps the heading. Dropping it left the second page
    # with nothing whose role is "heading", and an archetype that requires a
    # headline then refuses it - so a page that had to be split could not be
    # placed at all. Repeating the heading on a continued page is also what a
    # publication does.
    second = shape_from("content", shape.heading, texts[cut:],
                        exhibits=exhibits(visuals[shown:]),
                        figures=figures(visuals[shown:]))
    return first, second


# What a slot names when it can take a chart, table or diagram. "figure" is
# one of them: every measured pack declares its exhibit slots that way, and
# `select._accepts` places any exhibit in one.
_EXHIBIT_SLOT_KINDS = frozenset({"figure", "chart", "table", "diagram"})


def exhibits_per_page(pack: Pack) -> int:
    """The most exhibits or images any content page in this pack can carry.

    Told to the drafting turn, because a page drafted with more exhibits than
    any arrangement holds is split onto continuation pages: the deck keeps its
    design but gains pages nobody planned.
    """
    return max((
        sum(1 for slot in archetype.slots if _EXHIBIT_SLOT_KINDS & set(slot.accepts))
        for archetype in pack.archetypes
        if archetype.role in ("content", "exhibit")), default=0)


def _divisible(shape: MaterialShape) -> bool:
    """Whether `_split` has anything to divide.

    Only the body counts: a heading is not a paragraph the page can be
    divided at, and counting it meant a shape with one paragraph left looked
    divisible, split into itself plus nothing, and went round again until the
    cap.
    """
    body = [i for i in shape.items
            if i.kind == "text" and i.role_hint != "heading"]
    visuals = [i for i in shape.items if i.kind in _VISUAL_KINDS]
    return len(body) >= 2 or len(visuals) >= 2 or (body and visuals)


def _place(shape: MaterialShape, pack: Pack,
           previous: Optional[str],
           avoid: frozenset[str] = frozenset(),
           plain: bool = True,
           split_to_fit: bool = False,
           index: int = 0,
           ) -> list[tuple[MaterialShape, object]]:
    """This material, bound to archetypes, split as many times as it takes.

    ``avoid`` names archetypes this page has already been through. It is how a
    page is *placed again* rather than redrafted: the arrangement that came
    out wrong is taken off the menu and the next-best one is chosen from the
    same material. `choose` has always accepted a candidate list; nothing here
    passed it one.

    An avoid set that would leave nothing to choose from is ignored rather
    than obeyed. Refusing to place a page at all is a worse outcome than
    placing it the way we already did.
    """
    candidates = [a for a in pack.archetypes if a.id not in avoid] or None
    pending = [shape]
    placed: list[tuple[MaterialShape, object]] = []
    splits = 0
    ceiling = max(MIN_SPLITS,
                  len([i for i in shape.items if i.kind != "figure"]))
    last = previous
    while pending:
        current = pending.pop(0)
        selection = choose(current, pack, previous_archetype=last,
                           candidates=candidates)
        if selection is None and current.role != "content":
            # An opener carrying a page of copy is a content page in
            # practice, and a pack whose real cover is a full-bleed
            # photograph - WTI's is - has nowhere to put that copy. Falling
            # back is better than refusing: the page still gets composed, in
            # the archetype that can actually hold it.
            current = dataclasses.replace(current, role="content")
            selection = choose(current, pack, previous_archetype=last,
                               candidates=candidates)
        if (selection is not None and split_to_fit and splits < ceiling
                and _divisible(current)
                and not _holds_copy(current, selection, pack, index)):
            # The page count is free, so copy that does not fit at a readable
            # size gains a page rather than smaller type (#368).
            splits += 1
            head, tail = _split(current)
            pending.insert(0, tail)
            pending.insert(0, head)
            continue
        if selection is not None:
            placed.append((current, selection))
            last = selection.archetype.id
            continue
        pages = [a for a in (candidates or pack.archetypes)
                 if a.role in ("content", "exhibit")]
        hopeless = bool(pages) and not all(
            readable_somewhere(item, pack, pages) for item in current.items)
        indivisible = splits >= ceiling or not _divisible(current)
        if hopeless:
            # No arrangement of the pack can show this exhibit legibly. The
            # pack's plain page has a figure frame the width of the grid and
            # most of its height, so it is the next place to try: a six-column
            # table forced into a short slot came out at 8pt and still ran
            # into the prose below it (#357). A readable table on a plainer
            # page beats an unreadable one on a designed page. Never in a
            # preview: a specimen shows what the language itself can do.
            from app.design.vocabulary import PLAIN_ID, plain_content

            offered = (plain and PLAIN_ID not in avoid
                       and all(a.id != PLAIN_ID for a in pack.archetypes))
            fallback = plain_content(pack) if offered else None
            if fallback is not None:
                selection = choose(
                    current, pack.model_copy(update={"archetypes": (fallback,)}),
                    previous_archetype=last)
                if selection is not None:
                    logger.warning("exhibit_placed_on_plain_page",
                                   pack=pack.id)
                    placed.append((current, selection))
                    last = selection.archetype.id
                    continue
            # Splitting only helps if the exhibit could stand legibly on the
            # plain page once the prose around it is gone. If even that page
            # is too small for it - a forty-row table - more parts would only
            # repeat the heading, so the page is set cramped as it is.
            if fallback is None or not all(
                    readable_somewhere(item, pack, [fallback])
                    for item in current.items):
                indivisible = True
        if indivisible:
            # Nothing left to divide. Placing it cramped is better than
            # refusing it: a refusal takes the whole deck to the stock
            # renderer (#351). A page whose exhibit fits nowhere but which can
            # still be divided is split instead, until the exhibit stands on
            # a part the plain page can hold (#357).
            selection = choose(current, pack, previous_archetype=last,
                               candidates=candidates, legible=False)
            if selection is not None:
                logger.warning("exhibit_placed_below_legible_size",
                               pack=pack.id,
                               archetype=selection.archetype.id)
                placed.append((current, selection))
                last = selection.archetype.id
                continue
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


def _place_plainly(shape: MaterialShape, pack: Pack, previous: Optional[str],
                   index: int, refused: Unplaceable
                   ) -> list[tuple[MaterialShape, object]]:
    """This page on the pack's plain arrangement, split as it needs.

    Raises what the pack's own arrangements raised when even the plain page
    cannot carry it, so the caller's last resort still applies.
    """
    from app.design.vocabulary import plain_content

    plain = pack.model_copy(update={"archetypes": (plain_content(pack),)})
    try:
        placed = _place(shape, plain, previous)
    except Unplaceable:
        raise refused
    logger.warning("page_placed_on_plain_fallback", pack=pack.id,
                   slide_index=index, reason=str(refused))
    return placed


def _holds_copy(shape: MaterialShape, selection, pack: Pack, index: int) -> bool:
    """Whether this placement keeps every block of copy inside its frame,
    once repair has done what it can.

    The selector charges overflow as a cost, so the cheapest arrangement
    left after a `replace` can still be one whose only prose slot is a
    caption a third the size of the copy; repair cannot set copy below its
    designed floor, and the page ran off its bottom edge (#380).
    """
    page = build_page("probe", shape, selection, pack, considered=(),
                      source_slide=index)
    doc = DesignDoc(pack=PackRef(id=pack.id, version=pack.version),
                    canvas=pack.canvas, grid=pack.grid, pages=(page,))
    healed = repair(doc, page, pack).page
    return not overflow_findings(doc, healed, pack)


def _alternatives(shape: MaterialShape, pack: Pack, previous: Optional[str],
                  ruled_out: frozenset[str], index: int = 0) -> tuple[str, ...]:
    """The other arrangements that could carry this material legibly.

    What a `replace` verdict would have to choose from. Without it the loop
    learns that a page cannot move only by spending a render on it: probes
    for #376 saw a second `replace` swap two pages between the only two
    arrangements that held them, and a third hand back both unchanged.

    An arrangement counts only if the copy fits it too: one it overflows is
    not somewhere the page can go, and offering it spends a round (#380).
    """
    others = []
    for archetype in pack.archetypes:
        if archetype.id in ruled_out:
            continue
        selection = fit(shape, archetype, pack, previous_archetype=previous)
        if selection is not None and _holds_copy(shape, selection, pack, index):
            others.append(archetype.id)
    return tuple(others)


def compose(slides: Sequence[dict], pack: Pack, *, title: str = "",
            skip_unplaceable: bool = False,
            avoid: Optional[dict[int, frozenset[str]]] = None,
            pages: Optional[str] = None) -> DesignDoc:
    """Every designed slide, laid out in this pack and repaired.

    Repair runs here rather than being offered as a tool. The validator this
    replaces was a tool the model could decline to call, and it was invoked
    zero times across every observed run.

    ``avoid`` names, per slide index, the archetypes that slide has already
    been composed on and should not be given again. That is the second rung of
    the revision ladder: a page whose arrangement came out wrong is placed
    again from the same material rather than redrafted from scratch.

    ``skip_unplaceable`` drops a page that no archetype can carry instead of
    raising, and exists for **previews only**. A real deck that silently loses
    a slide is worse than one that fails loudly - the user gets a file, ships
    it, and finds out in the room - so the default stays strict. A specimen
    deck is the opposite case: three cards showing two pages each is a
    comparison the user can still make, and three showing nothing is not.
    
    ``pages`` is the type-size policy's question, whether the user fixed the
    page count (#368). ``"free"``: copy that does not fit its frame at the
    readable floor is split onto a continuation page. ``"fixed"``: it may be
    set below the floor, down to ``RELAXED_FLOOR_PT``, and each such page
    records how far (`Provenance.below_floor_pt`). ``None`` keeps neither,
    for previews and the language gate.
    """
    if pages == "fixed":
        from app.design.repair import relaxed_floor

        with relaxed_floor():
            return _compose(slides, pack, title=title,
                            skip_unplaceable=skip_unplaceable, avoid=avoid,
                            split_to_fit=False)
    return _compose(slides, pack, title=title, skip_unplaceable=skip_unplaceable,
                    avoid=avoid, split_to_fit=pages == "free")


def _compose(slides: Sequence[dict], pack: Pack, *, title: str,
             skip_unplaceable: bool,
             avoid: Optional[dict[int, frozenset[str]]],
             split_to_fit: bool) -> DesignDoc:
    ordered = sorted(
        [s for s in slides if isinstance(s, dict)],
        key=lambda s: int(s.get("slideIndex") or 0),
    )
    pages: list[Page] = []
    previous: Optional[str] = None
    for index, slide in enumerate(ordered):
        shape = material_from(slide, index=index, total=len(ordered))
        avoided = (avoid or {}).get(index, frozenset())
        try:
            try:
                placed = _place(shape, pack, previous, avoided,
                                plain=not skip_unplaceable,
                                split_to_fit=split_to_fit, index=index)
                if avoided and not all(_holds_copy(part, selection, pack, index)
                                       for part, selection in placed):
                    # A re-placement must not make the page worse. One whose
                    # copy runs out of its frames on the new arrangement keeps
                    # the arrangement it had, which the render then reports
                    # as unmovable, so the finding goes on to a redraft
                    # (#380: on the last round it shipped off the page).
                    logger.warning("replacement_would_overflow", pack=pack.id,
                                   slide_index=index,
                                   archetypes=[sel.archetype.id for _, sel in placed])
                    placed = _place(shape, pack, previous,
                                    plain=not skip_unplaceable,
                                    split_to_fit=split_to_fit, index=index)
            except Unplaceable:
                if not avoided:
                    raise
                # A re-placement found nothing new that holds the page. The
                # arrangement it was on held it a render ago, and placing it
                # the way we already did beats downgrading it to the plain
                # page because a revision was asked for.
                placed = _place(shape, pack, previous,
                                plain=not skip_unplaceable,
                                split_to_fit=split_to_fit, index=index)
        except Unplaceable as refused:
            if not skip_unplaceable:
                # One page the pack cannot carry is set on the pack's plainest
                # page instead of taking every other page down with it: a
                # deck rendered on stock placeholders because of its fourth
                # slide is the failure #351 found, and a worse outcome for the
                # user than one plain page in an otherwise designed deck
                # (#322). Raises only if even the plain page refuses it.
                placed = _place_plainly(shape, pack, previous, index, refused)
            else:
                placed = []
        if not placed:
            # A preview only. Logged rather than swallowed: a preview that quietly shows two
            # pages instead of three has still found a defect in the pack, and
            # nobody looking at the card can tell.
            logger.info("specimen_page_skipped", pack=pack.id,
                        slide_index=index, chars=shape.total_chars,
                        items=len(shape.items))
            continue
        for part, selection in placed:
            page = build_page(f"p{len(pages) + 1}", part, selection, pack,
                              considered=tuple(a.id for a in pack.archetypes),
                              source_slide=index)
            others = _alternatives(part, pack, previous,
                                   frozenset(avoided) | {selection.archetype.id},
                                   index)
            page = page.model_copy(update={"provenance": page.provenance.model_copy(
                update={"alternatives": others})})
            previous = selection.archetype.id
            pages.append(page)

    doc = DesignDoc(pack=PackRef(id=pack.id, version=pack.version),
                    canvas=pack.canvas, grid=pack.grid,
                    pages=tuple(pages), title=title)
    healed = tuple(_with_floor_record(repair(doc, page, pack).page, pack)
                   for page in doc.pages)
    return doc.model_copy(update={"pages": _one_headline_rung(healed, pack)})


def _with_floor_record(page: Page, pack: Pack) -> Page:
    """This page, saying how far below its designed floor any copy was set.

    Only a page composed with the floor relaxed can have any (#368); the
    record is what the closing message and the run's checks report.
    """
    from app.design.repair import _text_blocks, unrelaxed_floor_pt

    below = [
        role.size_pt
        for frame in page.all_frames()
        for block in _text_blocks(frame)
        if (role := pack.type_roles.get(block.type_role)) is not None
        and role.size_pt < unrelaxed_floor_pt(pack, frame) - 1e-6
    ]
    if not below:
        return page
    return page.model_copy(update={"provenance": page.provenance.model_copy(
        update={"below_floor_pt": round(min(below), 1)})})


# The page roles whose headlines read as one series. Openers and dividers set
# theirs at display size on purpose.
SERIES_ROLES = ("content", "exhibit")


def _one_headline_rung(pages: Sequence[Page], pack: Pack) -> tuple[Page, ...]:
    """Headlines designed on one rung stay on one rung after repair.

    Repair fits each page on its own, and a headline that overran its lines
    was stepped down the ramp on that page and no other: a Kubernetes deck set
    five of its seven content headlines at 31.6pt regular and two at 45pt
    bold, so the deck read as assembled rather than designed (#357). A reader
    takes the headline style as the deck's voice.

    Only what repair changed is evened out. Headings are grouped by the rung
    their arrangement set them on - a measured pack deliberately sets some in
    a pull-quote and some at headline size, and that difference is the house
    style - and each group takes the smallest rung repair stepped any of its
    members down to, which every member is known to fit. A group is never
    taken to body size or below: a heading that had to be set as body copy
    to fit is one broken page, not the deck's new voice.
    """
    body = pack.type_roles.get("body")
    floor = body.size_pt if body else 0.0

    def heading_blocks(page: Page):
        return [block for frame in page.all_frames()
                if frame.id == page.provenance.heading_frame
                for block in frame.content if isinstance(block, TextBlock)]

    def size(rung: str) -> float:
        role = pack.type_roles.get(rung)
        return role.size_pt if role else 0.0

    target: dict[str, str] = {}
    for page in pages:
        designed = page.provenance.heading_rung
        if page.role not in SERIES_ROLES or not designed:
            continue
        for block in heading_blocks(page):
            if size(block.type_role) <= floor or size(block.type_role) >= size(designed):
                continue
            current = target.get(designed, designed)
            if size(block.type_role) < size(current):
                target[designed] = block.type_role
    if not target:
        return tuple(pages)

    def lower(frame: Frame, heading: str, rung: str) -> Frame:
        content = tuple(
            block.model_copy(update={"type_role": rung})
            if frame.id == heading and isinstance(block, TextBlock)
            and size(block.type_role) > size(rung)
            else block
            for block in frame.content)
        return frame.model_copy(update={
            "content": content,
            "children": tuple(lower(child, heading, rung)
                              for child in frame.children)})

    out = []
    for page in pages:
        rung = target.get(page.provenance.heading_rung)
        if page.role in SERIES_ROLES and rung and page.provenance.heading_frame:
            page = page.model_copy(update={"frames": tuple(
                lower(f, page.provenance.heading_frame, rung) for f in page.frames)})
        out.append(page)
    return tuple(out)


def _bound_rung(frame: Frame, pack: Pack, item) -> str:
    """The rung this item is set on in this frame - bind's own answer."""
    from app.design.bind import _type_role

    return _type_role(frame, pack, item)


def headline_capacity(pack: Pack) -> int:
    """The longest headline every content arrangement sets at headline size.

    What drafting is told, so a headline is written to fit rather than
    stepped down the ramp after the fact - and, since headlines designed on
    one rung are evened out after repair, rather than taking every headline
    in the deck down with it. Measured where the selector actually puts a
    heading, and only where that is the pack's `headline` rung: a measured
    pack may set a page's assertion in a pull-quote or a standfirst, whose
    capacity says nothing about a headline. The tightest such slot, because
    a headline has to fit whichever arrangement its page lands on.
    """
    from app.design.select import fit

    if "headline" not in pack.type_roles:
        return 0
    probe = shape_from("content", "A headline", [
        "A paragraph of ordinary length, as a content page carries beside "
        "its headline, long enough to be placed as body copy."])
    capacities = []
    for archetype in pack.archetypes:
        if archetype.role not in SERIES_ROLES:
            continue
        selection = fit(probe, archetype, pack)
        if selection is None:
            continue
        frames = {f.id: f for frame in archetype.frames for f in frame.walk()}
        for placement in selection.placements:
            frame = frames.get(placement.slot.frame_id)
            if (placement.item.role_hint == "heading" and placement.slot.max_chars
                    and frame is not None
                    and _bound_rung(frame, pack, placement.item) == "headline"):
                capacities.append(placement.slot.max_chars)
    return min(capacities) if capacities else 0


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


# What a frame's copy is called when the writer is asked to cut it. Frames the
# system fills itself - a source line, footnotes, a folio - are not the
# writer's to shorten and are never reported.
_FIELD_FOR_FRAME = {
    "headline": "the headline", "standfirst": "the standfirst",
    "column": "the body copy", "body": "the body copy",
    "pullquote": "the pull quote", "caption": "the caption",
    "eyebrow": "the eyebrow", "statRow": "the figures",
}


def copy_over_budget(doc: DesignDoc, pack: Pack) -> dict[int, list[dict]]:
    """Per designed slide and per field, copy still running past its frames.

    Repair no longer sets copy below the size its frame was designed for
    (#348), so copy that does not fit stays too long - and that is the
    writer's to fix, with a number for the field that is over: `chars` is how
    much it carries, `fits` about how much its frames hold at the size they
    are set in. Measured by typesetting, like everything else here.
    """
    from app.design.geometry import resolve_page
    from app.design.setting import set_copy

    totals: dict[int, dict[str, dict]] = {}
    for page in doc.pages:
        slide = page.provenance.source_slide
        if slide is None:
            continue
        for resolved in resolve_page(doc, page):
            field = _FIELD_FOR_FRAME.get(resolved.frame.role)
            if field is None:
                continue
            for block in resolved.frame.content:
                if not isinstance(block, TextBlock) or not block.text:
                    continue
                role = pack.type_roles.get(block.type_role)
                if role is None:
                    continue
                height_pt = resolved.rect.height * 72.0
                result = set_copy(block.text, role, pack,
                                  resolved.rect.width * 72.0, height_pt,
                                  columns=block.columns,
                                  column_gap=block.column_gap)
                if result.overflow_pt <= 0.02 * height_pt:
                    continue
                share = height_pt / (height_pt + result.overflow_pt)
                entry = totals.setdefault(slide, {}).setdefault(
                    field, {"field": field, "chars": 0, "fits": 0})
                entry["chars"] += len(block.text)
                entry["fits"] += int(len(block.text) * share)
    return {slide: list(fields.values()) for slide, fields in totals.items()}
