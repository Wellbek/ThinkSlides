"""
Finding what is wrong with a page, and fixing only that.

Two things separate this from the loop it replaces. The existing pipeline
has `render -> validate -> draft`, and its unit of repair is the *whole
slide*: a page with one overset paragraph is redrafted from scratch, which
changes everything including the parts that were right. And its validator
was a tool the model could decline to call, which it did - `validate_slide_json`
was invoked zero times across every observed run, so density, title-register
and apparatus checks were all inert in practice.

Here a finding names a frame, a patch names an operation on that frame, and
the loop keeps a revision only if the page measurably improved. It can
therefore only ratchet upward, which is the property that makes it safe to
run unattended.

The patch vocabulary is closed on purpose. Nothing outside it can be
applied, so a hallucinated repair is rejected at parse time rather than
producing a page nobody can account for.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Optional, Sequence

from app.design.geometry import check_page, resolve_page
from app.design.pack import Pack
from app.design.schema import (
    DesignDoc, Exhibit, Figure, Frame, Page, PatchOp, TextBlock,
)

Severity = Literal[1, 2, 3, 4, 5]

# How far a block may exceed its frame before it is a finding. Below this it
# is inside the headroom the fitter already reserves.
OVERFLOW_TOLERANCE = 0.02
MAX_ROUNDS = 3
# The smallest a band may be nudged down to. `GridPlace` refuses zero, and a
# frame shrunk to nothing is a frame that should have been dropped.
MIN_BAND_HEIGHT = 0.01
# A round has to earn its place: a repair that improves the page by less
# than this is noise, and applying it costs a render for nothing.
MIN_IMPROVEMENT = 0.01


@dataclass(frozen=True)
class Finding:
    """One located defect, with the evidence and a proposed operation."""

    frame_id: str
    property: str
    observed: str
    expected: str
    severity: Severity = 3
    patch: Optional[PatchOp] = None

    def describe(self) -> str:
        return (f"{self.frame_id}: {self.property} is {self.observed}, "
                f"expected {self.expected}")


def _text_blocks(frame: Frame):
    for item in frame.content:
        if isinstance(item, TextBlock):
            yield item


def overflow_findings(doc: DesignDoc, page: Page, pack: Pack) -> list[Finding]:
    """Every text block that does not fit the frame it is bound to."""
    from app.design.setting import set_copy

    findings: list[Finding] = []
    for resolved in resolve_page(doc, page):
        frame = resolved.frame
        for block in _text_blocks(frame):
            # Read the role off the block, which is what the binder wrote and
            # what the renderer will use. Re-deriving it here from the frame
            # and a hardcoded "text" measured a pull-quote at body size: 16pt
            # against the 32.4pt it is actually drawn at, so a block that
            # overflowed its frame by 60% reported no finding at all and the
            # repair loop had nothing to act on.
            role = pack.type_roles.get(block.type_role)
            if role is None:
                continue
            result = set_copy(
                block.text, role, pack,
                resolved.rect.width * 72.0, resolved.rect.height * 72.0,
                columns=block.columns, column_gap=block.column_gap,
            )
            share = result.overflow_pt / max(1.0, resolved.rect.height * 72.0)
            if share > OVERFLOW_TOLERANCE:
                findings.append(Finding(
                    frame_id=frame.id,
                    property="overflow",
                    observed=f"{share:.0%} past the frame",
                    expected="inside it",
                    severity=5 if share > 0.25 else 4,
                    # Setting the copy in more columns is *tried* rather
                    # than assumed to help: a narrower measure produces
                    # disproportionately more lines, so splitting a 500pt
                    # column in three takes a block from 360pt deep to 405pt.
                    # The monotone gate rejects it when that happens, which
                    # is the gate doing its job rather than a wasted round.
                    patch=_overflow_patch(pack, frame, block),
                ))
    return findings


def _next_smaller_role(pack: Pack, current: str,
                       minimum_pt: float = 0.0) -> Optional[str]:
    """The largest rung of this pack's ramp below the one in use, and not
    below `minimum_pt`."""
    here = pack.type_roles.get(current)
    if here is None:
        return None
    smaller = [
        (role.size_pt, name) for name, role in pack.type_roles.items()
        if minimum_pt <= role.size_pt < here.size_pt
    ]
    return max(smaller)[1] if smaller else None


def designed_floor_pt(pack: Pack, frame: Frame) -> float:
    """The smallest size copy in this frame may be set at.

    Read from the rung the frame was *designed* for, not the one its copy has
    been stepped down to. Checked against the rung in use, body copy set one
    rung smaller as a caption was held to the caption floor, passed, and could
    be stepped down again: a deck's prose ended at 12.6pt against a body
    floor of 16.2pt (#348).
    """
    from app.design.roles import type_role_for
    from app.renderer.typeset.policy import floor_for

    return floor_for(pack, type_role_for(frame.role, "text", pack, frame.type_role))


def _overflow_patch(pack: Pack, frame: Frame, block) -> PatchOp:
    """What to try when a block does not fit the frame it is bound to.

    Setting the copy one rung smaller is the move a typesetter makes, and it
    is the one that actually helps: more columns produces disproportionately
    more lines, so splitting a 500pt column in three takes a block from 360pt
    deep to 405pt. Columns remain the fallback for a pack whose ramp has
    nothing below the rung already in use.

    Neither is applied on trust. The loop keeps a round only when the page's
    score falls, so a step down that costs more in legibility than it saves
    in overflow is reverted.
    """
    smaller = _next_smaller_role(pack, block.type_role,
                                 designed_floor_pt(pack, frame))
    if smaller is not None:
        return PatchOp(
            op="set_type_role",
            args={"frame_id": frame.id, "type_role": smaller},
            reason=f"set this copy at {smaller} so it fits; kept only if the "
                   "page improves and stays above the pack's legibility floor",
        )
    return PatchOp(
        op="set_columns",
        args={"frame_id": frame.id, "columns": min(3, block.columns + 1)},
        reason="try more columns; kept only if the page improves",
    )


def geometry_findings(doc: DesignDoc, page: Page) -> list[Finding]:
    """Frames that overlap ambiguously or escape the page.

    An ambiguous overlap carries its own fix. It has to: the renderer refuses
    to place a document with one, so a page that reaches here without a patch
    is a page the run cannot ship - and that is not hypothetical, it is how a
    six-slide deck composed seven pages and then rendered none of them. The
    packs are traced from real pages and some of those traces put two content
    frames on top of each other; until they are rebuilt (#275) the repair loop
    has to be able to say which one is in front.
    """
    findings = []
    for problem in check_page(doc, page):
        findings.append(Finding(
            frame_id=_frame_in(problem), property="geometry",
            observed=problem, expected="a placeable frame", severity=4,
            patch=_depth_patch(page, problem),
        ))
    return findings


def _depth_patch(page: Page, problem: str) -> Optional[PatchOp]:
    """Lift the second of two frames that overlap at the same depth.

    Only for overlap: a frame escaping the canvas is a different fault with a
    different answer, and inventing a patch for it would be the repair loop
    pretending to fix something.
    """
    if "overlap" not in problem:
        return None
    named = _frames_in(problem)
    if len(named) < 2:
        return None
    frame = page.frame_by_id(named[1])
    if frame is None:
        return None
    return PatchOp(
        op="set_z",
        args={"frame_id": named[1], "z": frame.z + 1},
        reason="two frames overlap at the same depth; lift the later one so "
               "the page says which is in front",
    )


def _frames_in(problem: str) -> list[str]:
    """Every quoted frame id in a geometry message, in order."""
    return problem.split("'")[1::2]


def legibility_findings(page: Page, pack: Pack) -> list[Finding]:
    """Type set below the floor this pack considers readable."""
    from app.renderer.typeset.policy import check

    findings = []
    for frame in page.all_frames():
        for block in _text_blocks(frame):
            role = pack.type_roles.get(block.type_role)
            if role is None:
                continue
            message = check(pack, block.type_role, role.size_pt)
            floor = designed_floor_pt(pack, frame)
            if message is None and role.size_pt < floor - 1e-6:
                message = (f"{block.type_role} at {role.size_pt:.1f}pt is below "
                           f"the {floor:.1f}pt this frame's copy is set at "
                           "no smaller than")
            if message:
                findings.append(Finding(
                    frame_id=frame.id, property="legibility",
                    observed=f"{role.size_pt:.1f}pt", expected=message,
                    severity=5,
                ))
    return findings


# A figure smaller than this share of the canvas is not a figure a reader can
# follow, whatever it contains. Stated as a share because a diagram's
# legibility is about how big it is on the page, not how many inches it is.
MIN_FIGURE_SHARE = 0.06

# How far a figure's slot may be from the figure's own shape before the crop
# or the letterbox is worth acting on. An octave either way.
MAX_ASPECT_DRIFT = 2.0


def figure_findings(doc: DesignDoc, page: Page, pack: Pack) -> list[Finding]:
    """Figures too small to read, and figures in a slot the wrong shape.

    The selector already prefers a slot whose aspect suits a figure. This is
    what is left when no slot suited it: the page was composed anyway, and a
    graph squeezed into a column is a graph nobody can follow.
    """
    findings: list[Finding] = []
    canvas_area = pack.canvas.w_in * pack.canvas.h_in
    if canvas_area <= 0:
        return findings
    for resolved in resolve_page(doc, page):
        frame = resolved.frame
        if frame.role != "figure":
            continue
        area = resolved.rect.width * resolved.rect.height
        share = area / canvas_area
        if share < MIN_FIGURE_SHARE:
            findings.append(Finding(
                frame_id=frame.id, property="figure_scale",
                observed=f"{share:.1%} of the page",
                expected=f"at least {MIN_FIGURE_SHARE:.0%}, or it is a figure "
                         f"nobody can read",
                severity=4,
            ))
        aspect = _declared_aspect(frame)
        if aspect and resolved.rect.height > 0:
            slot = resolved.rect.width / resolved.rect.height
            if slot > 0 and max(aspect / slot, slot / aspect) > MAX_ASPECT_DRIFT:
                findings.append(Finding(
                    frame_id=frame.id, property="figure_aspect",
                    observed=f"a {aspect:.1f}:1 figure in a {slot:.1f}:1 slot",
                    expected="a slot the figure fits, or letterboxing rather "
                             "than a crop",
                    severity=3,
                    patch=PatchOp(
                        op="set_fit", args={"frame_id": frame.id,
                                            "fit": "contain"},
                        reason="letterbox rather than crop; kept only if the "
                               "page improves",
                    ),
                ))
    return findings


def _declared_aspect(frame: Frame) -> Optional[float]:
    for item in frame.content:
        aspect = getattr(item, "aspect", None)
        if aspect:
            return float(aspect)
    return None


def diagnose(doc: DesignDoc, page: Page, pack: Pack) -> list[Finding]:
    """Everything measurably wrong with this page, worst first."""
    findings = (
        geometry_findings(doc, page)
        + overflow_findings(doc, page, pack)
        + legibility_findings(page, pack)
        + figure_findings(doc, page, pack)
    )
    return sorted(findings, key=lambda f: -f.severity)


def score(doc: DesignDoc, page: Page, pack: Pack) -> float:
    """How bad this page is. Lower is better; zero is clean.

    The loop's ratchet reads this and nothing else, so a repair that does not
    move it does not land.
    """
    return sum(f.severity for f in diagnose(doc, page, pack))


# ── patches ────────────────────────────────────────────────────────────────

PatchFn = Callable[[Page, dict], Page]


def _map_frames(frames: tuple[Frame, ...],
                fn: Callable[[Frame], Optional[Frame]]) -> tuple[Frame, ...]:
    out = []
    for frame in frames:
        updated = fn(frame)
        if updated is None:
            continue
        out.append(updated.model_copy(update={
            "children": _map_frames(updated.children, fn)
        }))
    return tuple(out)


def _set_columns(page: Page, args: dict) -> Page:
    target, columns = args.get("frame_id"), int(args.get("columns", 1))

    def apply(frame: Frame) -> Frame:
        if frame.id != target:
            return frame
        content = tuple(
            item.model_copy(update={"columns": columns})
            if isinstance(item, TextBlock) else item
            for item in frame.content
        )
        return frame.model_copy(update={"content": content})

    return page.model_copy(update={"frames": _map_frames(page.frames, apply)})


def _drop_content(page: Page, args: dict) -> Page:
    target = args.get("frame_id")

    def apply(frame: Frame) -> Optional[Frame]:
        if frame.id != target:
            return frame
        return None if not frame.ground else frame.model_copy(
            update={"content": ()}
        )

    return page.model_copy(update={"frames": _map_frames(page.frames, apply)})


def _set_type_role(page: Page, args: dict) -> Page:
    """Set one frame's text in a different rung of the pack's ramp.

    The floor is not checked here, and does not need to be: setting a block
    below what the pack considers legible raises a legibility finding, which
    raises the score, which the monotone gate then refuses. The mechanism
    that keeps a repair honest is the same one that keeps it legible.
    """
    frame_id = args["frame_id"]
    role = str(args["type_role"])

    def apply(frame: Frame) -> Frame:
        if frame.id != frame_id or not frame.content:
            return frame
        return frame.model_copy(update={"content": tuple(
            item.model_copy(update={"type_role": role})
            if isinstance(item, TextBlock) else item
            for item in frame.content
        )})

    return page.model_copy(update={"frames": _map_frames(page.frames, apply)})


def _set_fit(page: Page, args: dict) -> Page:
    """How a figure fills its slot: cropped, letterboxed, or stretched.

    The one thing that can actually be done about a figure in a slot the wrong
    shape, once the page is composed. Choosing a different slot is not a patch
    - it is a different page - so what is left is deciding whether the reader
    loses the edges of the figure or gains some white space, and white space
    is the lesser loss.
    """
    frame_id = args["frame_id"]
    fit = str(args.get("fit") or "contain")

    def apply(frame: Frame) -> Frame:
        if frame.id != frame_id or not frame.content:
            return frame
        return frame.model_copy(update={"content": tuple(
            item.model_copy(update={"fit": fit})
            if isinstance(item, (Figure, Exhibit)) else item
            for item in frame.content
        )})

    return page.model_copy(update={"frames": _map_frames(page.frames, apply)})


def _set_ground(page: Page, args: dict) -> Page:
    target, ground = args.get("frame_id"), str(args.get("ground") or "")

    def apply(frame: Frame) -> Frame:
        return frame if frame.id != target else frame.model_copy(
            update={"ground": ground or None}
        )

    return page.model_copy(update={"frames": _map_frames(page.frames, apply)})


def _move_band(page: Page, args: dict) -> Page:
    """Shift a frame's band, and optionally resize it.

    The one thing the vocabulary could not say. Every other operation changes
    what a frame *contains* or how it is *set* - its columns, its type role,
    its fit, its ground, its depth - and none of them can move it, so the
    smallest real fix a page ever needs ("this sits too high, drop it") had no
    expression and the only remedy was to redraft the whole slide.

    Deltas rather than coordinates, so a caller says "down a little" rather
    than naming a position it would have to derive. Clamped to the schema's
    own bounds, because a patch that cannot be constructed is a patch that
    silently does nothing.
    """
    target = args.get("frame_id")
    dy = float(args.get("dy") or 0.0)
    dh = float(args.get("dh") or 0.0)

    def apply(frame: Frame) -> Frame:
        # Only a band moves. A ground or a full-bleed panel is placed by
        # fraction of its parent and is not part of the stack.
        if frame.id != target or frame.place.kind != "grid":
            return frame
        place = frame.place
        if place.y is None or place.h is None:
            return frame
        return frame.model_copy(update={"place": place.model_copy(update={
            "y": round(max(-1.0, min(2.0, place.y + dy)), 4),
            "h": round(max(MIN_BAND_HEIGHT, min(2.0, place.h + dh)), 4),
        })})

    return page.model_copy(update={"frames": _map_frames(page.frames, apply)})


def _set_z(page: Page, args: dict) -> Page:
    target, z = args.get("frame_id"), int(args.get("z", 0))

    def apply(frame: Frame) -> Frame:
        return frame if frame.id != target else frame.model_copy(update={"z": z})

    return page.model_copy(update={"frames": _map_frames(page.frames, apply)})


PATCHES: dict[str, PatchFn] = {
    "move_band": _move_band,
    "set_columns": _set_columns,
    "set_type_role": _set_type_role,
    "set_fit": _set_fit,
    "drop_content": _drop_content,
    "set_ground": _set_ground,
    "set_z": _set_z,
}


def apply_patch(page: Page, patch: PatchOp) -> Page:
    """Apply one operation from the closed vocabulary.

    An unknown op is an error rather than a no-op: a repair loop that
    silently ignores instructions it does not understand reports progress it
    did not make.
    """
    handler = PATCHES.get(patch.op)
    if handler is None:
        raise ValueError(
            f"unknown patch operation {patch.op!r}; this system applies "
            + ", ".join(sorted(PATCHES))
        )
    return handler(page, patch.args)


@dataclass(frozen=True)
class RepairResult:
    """What the loop did, and whether it helped."""

    page: Page
    applied: tuple[PatchOp, ...] = ()
    before: float = 0.0
    after: float = 0.0
    rounds: int = 0
    findings: tuple[Finding, ...] = field(default_factory=tuple)

    @property
    def improved(self) -> bool:
        return self.after < self.before


def repair(doc: DesignDoc, page: Page, pack: Pack, *,
           max_rounds: int = MAX_ROUNDS) -> RepairResult:
    """Repair this page, keeping only changes that measurably help.

    Monotone by construction: each round is kept only if the page's score
    falls, and reverted otherwise. The loop can make a page better or leave
    it alone, and has no path to making it worse.
    """
    best = page
    best_score = score(doc, page, pack)
    original = best_score
    applied: list[PatchOp] = []
    rounds = 0

    for _ in range(max_rounds):
        findings = diagnose(doc, best, pack)
        patches = [f.patch for f in findings if f.patch is not None]
        if not patches:
            break
        rounds += 1
        candidate = best
        used: list[PatchOp] = []
        for patch in patches:
            try:
                candidate = apply_patch(candidate, patch)
                used.append(patch)
            except ValueError:
                continue
        if not used:
            break
        candidate_doc = doc.model_copy(update={
            "pages": tuple(candidate if p.id == page.id else p
                           for p in doc.pages)
        })
        candidate_score = score(candidate_doc, candidate, pack)
        if candidate_score <= best_score - MIN_IMPROVEMENT:
            best, best_score = candidate, candidate_score
            applied.extend(used)
        else:
            break

    return RepairResult(
        page=best.model_copy(update={
            "provenance": best.provenance.model_copy(update={
                "patched_by": tuple(applied)
            })
        }),
        applied=tuple(applied),
        before=original,
        after=best_score,
        rounds=rounds,
        findings=tuple(diagnose(doc, best, pack)),
    )


def apply_if_better(doc: DesignDoc, page: Page, pack: Pack,
                    patches: Sequence[PatchOp]) -> RepairResult:
    """Apply patches this page did not diagnose for itself, and keep the
    result only if it measurably improved.

    `repair` finds its own work. This exists for the work found *elsewhere* -
    by looking at the rendered page - so a verdict from outside the design
    layer lands under the same guarantee as one from inside it: the page gets
    better or it does not change. Without this a bounded revision loop can
    still end on its worst attempt, which is the failure a cap does not
    prevent.
    """
    before = score(doc, page, pack)
    candidate = page
    used: list[PatchOp] = []
    for patch in patches:
        try:
            candidate = apply_patch(candidate, patch)
            used.append(patch)
        except ValueError:
            continue
    if not used:
        return RepairResult(page=page, before=before, after=before)

    candidate_doc = doc.model_copy(update={
        "pages": tuple(candidate if p.id == page.id else p for p in doc.pages)
    })
    after = score(candidate_doc, candidate, pack)
    if after > before:
        return RepairResult(page=page, before=before, after=before)
    return RepairResult(
        page=candidate.model_copy(update={
            "provenance": candidate.provenance.model_copy(update={
                "patched_by": tuple(candidate.provenance.patched_by) + tuple(used)
            })
        }),
        applied=tuple(used), before=before, after=after, rounds=1,
        findings=tuple(diagnose(candidate_doc, candidate, pack)),
    )


def _frame_in(problem: str) -> str:
    """Pull the first quoted frame id out of a geometry message."""
    parts = problem.split("'")
    return parts[1] if len(parts) > 1 else ""
