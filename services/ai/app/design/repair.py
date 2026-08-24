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
from typing import Callable, Literal, Optional

from app.design.geometry import check_page, resolve_page
from app.design.pack import Pack
from app.design.schema import DesignDoc, Frame, Page, PatchOp, TextBlock

Severity = Literal[1, 2, 3, 4, 5]

# How far a block may exceed its frame before it is a finding. Below this it
# is inside the headroom the fitter already reserves.
OVERFLOW_TOLERANCE = 0.02
MAX_ROUNDS = 3
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
    from app.renderer.typeset.shape import layout

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
            result = layout(
                block.text, resolved.rect.width * 72.0,
                resolved.rect.height * 72.0, pack.face_for(role),
                role.size_pt, line_height_pt=role.line_height_pt,
                columns=block.columns,
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
                    patch=PatchOp(
                        op="set_columns",
                        args={"frame_id": frame.id,
                              "columns": min(3, block.columns + 1)},
                        reason="try more columns; kept only if the page improves",
                    ),
                ))
    return findings


def geometry_findings(doc: DesignDoc, page: Page) -> list[Finding]:
    """Frames that overlap ambiguously or escape the page."""
    return [
        Finding(frame_id=_frame_in(problem), property="geometry",
                observed=problem, expected="a placeable frame", severity=4)
        for problem in check_page(doc, page)
    ]


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
            if message:
                findings.append(Finding(
                    frame_id=frame.id, property="legibility",
                    observed=f"{role.size_pt:.1f}pt", expected=message,
                    severity=5,
                ))
    return findings


def diagnose(doc: DesignDoc, page: Page, pack: Pack) -> list[Finding]:
    """Everything measurably wrong with this page, worst first."""
    findings = (
        geometry_findings(doc, page)
        + overflow_findings(doc, page, pack)
        + legibility_findings(page, pack)
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


def _set_ground(page: Page, args: dict) -> Page:
    target, ground = args.get("frame_id"), str(args.get("ground") or "")

    def apply(frame: Frame) -> Frame:
        return frame if frame.id != target else frame.model_copy(
            update={"ground": ground or None}
        )

    return page.model_copy(update={"frames": _map_frames(page.frames, apply)})


def _set_z(page: Page, args: dict) -> Page:
    target, z = args.get("frame_id"), int(args.get("z", 0))

    def apply(frame: Frame) -> Frame:
        return frame if frame.id != target else frame.model_copy(update={"z": z})

    return page.model_copy(update={"frames": _map_frames(page.frames, apply)})


PATCHES: dict[str, PatchFn] = {
    "set_columns": _set_columns,
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


def _frame_in(problem: str) -> str:
    """Pull the first quoted frame id out of a geometry message."""
    parts = problem.split("'")
    return parts[1] if len(parts) > 1 else ""
