"""
Tool: edit_deck_with_code

The agent writes Python, the code is checked, and if it is safe it runs in a
sandbox and its edit to the deck is kept.

This is the most powerful tool in the set, and by this codebase's own logic
(`draw_svg.py:9-10`) it therefore owes the longest stated guard-rails. Every
other builder is a typed vocabulary: the model says what it wants and the code
decides how. This one hands the model the how as well, so what keeps it honest
is not the shape of an argument - it is three deterministic gates and a vision
check afterwards.

**What crosses the boundary is a file, not an object.** The script never
touches the live `Presentation`: that is the same object graph the renderer,
the database session and the decrypted provider keys live beside, and an
object reference is not a boundary. The parent writes the deck out, the script
opens it, edits it and saves it, and the parent adopts the result only if
`adopt.problem` finds nothing wrong with it.

**The three gates**, in the order they apply:

1. `sandbox.gate` - the syntax tree. Nothing dangerous can be *expressed*: no
   import outside an allowlist (and in particular no `app.*`), no `eval`, no
   dunder attribute, no absolute path.
2. `sandbox.runner` - the process. A scrubbed environment with no keys in it,
   `setrlimit` on CPU, memory, file size and process count, a wall clock, and
   no network.
3. `sandbox.adopt` - the artefact. Opens as a deck, no macro part, no OLE
   object, no external relationship, within a declared slide count.

**The risk this does not remove**, stated because it is the one that matters:
free-form code plus after-the-fact vision checking means a script can produce
a figure that renders perfectly and is *wrong* - invented numbers that look
computed. `generate_image.py:23-32` calls that the single worst thing this
system could emit, and a vision model grading a rendered page cannot tell a
real figure from a plausible one. So a script that asserts numbers has to
declare where they came from, and one that declares nothing is refused at this
boundary - the same rule that already makes an unsupported figure a reported
gap rather than a silent claim.
"""

from __future__ import annotations

import os
import time
from typing import Optional

from app.agent.registry import ToolParameter, ToolRegistry, ToolResult
from app.agent.sandbox import adopt, gate, grounding, runner
from app.observability.logging import get_logger

logger = get_logger(__name__)

# The deck as the script sees it.
#
# This tool returns no figure block, deliberately. A figure belongs to
# build_chart, build_diagram, build_mermaid or draw_svg - all four are
# on-brand by construction and go through the importer's own checks - and
# adding a fifth, unconstrained way to produce one would make the ranking in
# `renderer/svg/sources.py` a lie. What this tool does is edit the deck.
DECK_NAME = runner.DECK_NAME

WHEN_NOT_TO = (
    "Do NOT use this when a build_* tool already has the shape. build_chart "
    "is the answer to a quantity, build_diagram to a structure, build_table "
    "to a grid and draw_svg to a bespoke figure - all four are on-brand by "
    "construction, validated against the deck's own design, and none of them "
    "can go wrong the way a script can. Reach for this only when the fix is "
    "not a matter of what is on the page but where it sits: two shapes "
    "overlap, a region needs to move, a slide validation flagged is easier "
    "to repair in place than to redraft from scratch. None of the typed "
    "builders can reposition something already on a slide - each of them "
    "only adds. Writing a chart by hand here, when build_chart would have "
    "drawn it, is strictly worse in every respect."
)


class EditDeckWithCodeTool:
    """Agent-written Python, gated deterministically and run in a sandbox."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="edit_deck_with_code",
            description=(
                "Write a short Python script that edits the deck directly "
                "with python-pptx, for something the build_* tools cannot "
                "express. The script is checked before it runs and the deck "
                "it produces is checked before it is kept. " + WHEN_NOT_TO
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="code", type="string",
                    description=(
                        f"A Python script. The deck is {DECK_NAME!r} in the "
                        "directory the script runs in - open it with "
                        "python-pptx, edit it, and save it back to the same "
                        "name. Use relative paths only. You may import pptx, "
                        "math, statistics, json, csv, re, datetime, decimal, "
                        "random, textwrap, itertools, matplotlib, numpy and "
                        "PIL. You may not import anything else, and in "
                        "particular not os, sys, subprocess, socket or this "
                        "service's own modules. No eval, no exec, no "
                        "__import__. There is no network and no filesystem "
                        "outside your own directory. Pictures the user supplied are "
                        "listed in the instructions you were given and sit "
                        "beside the deck under those names; open them with "
                        "PIL or add them with python-pptx's add_picture."
                    ),
                ),
                ToolParameter(
                    name="intent", type="string",
                    description=(
                        "One line saying what this script does to the deck, "
                        "in the terms a reader of the slide would use - "
                        "'lay the four regions out as a 2x2 with the totals "
                        "in the middle', not 'add shapes'."
                    ),
                ),
                ToolParameter(
                    name="inputs", type="string",
                    description=(
                        "Where every number this script puts on the page came "
                        "from: a document, a search result, or the "
                        "conversation. Required whenever the script writes a "
                        "figure, because a chart drawn by code is a stronger "
                        "claim than a sentence - it tells the reader the "
                        "numbers were measured, not typed. A script that "
                        "asserts numbers with nothing declared here is "
                        "refused. Write 'none' only when the script places "
                        "no figures at all."
                    ),
                ),
                ToolParameter(
                    name="slide_index", type="integer", required=False,
                    description=(
                        "0-based index of the slide this edits, when it edits "
                        "one. Omit for a change across the whole deck."
                    ),
                ),
            ],
        )

    @classmethod
    async def run(cls, code: str, intent: str = "", inputs: str = "",
                  slide_index: Optional[int] = None) -> ToolResult:
        start = time.time()
        source = str(code or "")

        # Gate one: what the script can say.
        problem = gate.check(source)
        if problem:
            logger.info("sandbox_code_refused", reason=problem[:200])
            return ToolResult(
                success=False,
                error=(f"{problem}\n\nRewrite it within those limits, or use "
                       "a build_* tool if one has the shape."),
                data={"outcome": "refused", "source": source,
                      "reason": problem},
                duration_ms=int((time.time() - start) * 1000),
            )

        ungrounded = grounding.problem(source, inputs)
        if ungrounded:
            logger.info("sandbox_code_ungrounded")
            return ToolResult(
                success=False, error=ungrounded,
                data={"outcome": "refused", "source": source,
                      "reason": ungrounded},
                duration_ms=int((time.time() - start) * 1000),
            )

        deck = _current_deck()
        if deck is None:
            return ToolResult(
                success=False,
                error=("There is no rendered deck to edit yet. Build the "
                       "slides first and call finalize_and_render_pptx; this "
                       "tool edits what that produced."),
                duration_ms=int((time.time() - start) * 1000),
            )

        # Gate two: what the process can reach.
        result = runner.run(source, deck=deck, assets=_assets())
        if not result.ok:
            logger.info("sandbox_code_failed")
            return ToolResult(
                success=False,
                error=(f"{result.failure}\n\nFix the script and call this "
                       "again - you have the deck's state and the error."),
                data={"outcome": "failed", "source": source,
                      "reason": result.failure, "stdout": result.stdout},
                duration_ms=int((time.time() - start) * 1000),
            )

        edited = result.outputs.get(DECK_NAME)
        # Gate three: what the artefact is.
        rejected = adopt.problem(edited or b"", max_slides=_slide_cap())
        if rejected:
            logger.warning("sandbox_deck_rejected", reason=rejected[:200])
            return ToolResult(
                success=False,
                error=(f"{rejected}\n\nThe edit was discarded and the deck is "
                       "as it was. Edit the deck you were given and save it "
                       f"back to {DECK_NAME!r}."),
                data={"outcome": "failed", "source": source,
                      "reason": rejected},
                duration_ms=int((time.time() - start) * 1000),
            )

        _adopt_deck(edited)
        logger.info("sandbox_deck_adopted", bytes=len(edited or b""),
                    slide_index=slide_index,
                    # Whether the child actually got its own network
                    # namespace. It does not inside a container with dropped
                    # capabilities - `unshare` needs CAP_SYS_ADMIN, which the
                    # hardening removes - so what stops a script reaching
                    # Postgres or Redis there is the AST gate refusing to let
                    # it name `socket`, `urllib` or `http` at all. Recorded on
                    # every run rather than assumed, because a sandbox whose
                    # isolation silently did not apply is worse than one that
                    # never claimed it.
                    network_namespace=result.networked)
        return ToolResult(
            success=True,
            data={
                "outcome": "ran",
                "source": source,
                "slideIndex": slide_index,
                "stdout": result.stdout,
                "summary": (
                    "The script ran and its edit was kept"
                    + (f" (slide {slide_index + 1})" if slide_index is not None
                       else "")
                    + f": {intent.strip() or 'the deck was edited'}. "
                    "validate_rendered_output judges the result."
                ),
            },
            duration_ms=int((time.time() - start) * 1000),
        )


def replay(path: str, log: list[dict]) -> list[str]:
    """Apply recorded edits again to a freshly rendered deck.

    A render rebuilds the file from the slides, which drops every script's
    edit. Replaying them through the same three gates keeps a requested change
    (a logo on every page, a watermark) across revision rounds. Returns the
    problems met, one per edit that could not be reapplied; the rest stand.
    """
    problems: list[str] = []
    for entry in log:
        source = str(entry.get("source") or "")
        if gate.check(source):
            problems.append(f"{entry.get('intent') or 'an edit'}: refused by the gate")
            continue
        with open(path, "rb") as handle:
            current = handle.read()
        result = runner.run(source, deck=current, assets=_assets())
        if not result.ok:
            problems.append(f"{entry.get('intent') or 'an edit'}: {result.failure}")
            continue
        edited = result.outputs.get(DECK_NAME) or b""
        rejected = adopt.problem(edited, max_slides=_slide_cap_for(current))
        if rejected:
            problems.append(f"{entry.get('intent') or 'an edit'}: {rejected}")
            continue
        temporary = f"{path}.editing"
        with open(temporary, "wb") as handle:
            handle.write(edited)
        os.replace(temporary, path)
    return problems


def _assets() -> dict[str, bytes]:
    """The pictures the user supplied, as the script finds them."""
    from app.agent import run_context

    return dict(run_context.get_assets())


def _slide_cap_for(deck: bytes) -> int:
    import io

    from pptx import Presentation

    try:
        return len(Presentation(io.BytesIO(deck)).slides) + 10
    except Exception:   # pragma: no cover
        return 200


def _deck_path() -> Optional[str]:
    """Where this run's rendered deck is, or None before there is one.

    A path rather than bytes in a context variable: `finalize_and_render_pptx`
    writes a real file and the loop records `last_rendered_path` from its
    result, so this reads the same thing the download and the vision check
    read. Holding a second copy in memory would be two sources of truth for
    the same deck, and the one that got stale would be whichever was not
    looked at.
    """
    from app.agent import run_context

    path = run_context.get_rendered_path()
    return path if path and os.path.exists(path) else None


def _current_deck() -> Optional[bytes]:
    """The rendered deck as it stands, or None when there is not one yet."""
    path = _deck_path()
    if path is None:
        return None
    with open(path, "rb") as handle:
        return handle.read()


def _adopt_deck(data: bytes) -> None:
    """Write the script's edit over the run's deck.

    Only ever called after `adopt.problem` has returned None, so what lands
    here has already opened as a presentation, carries no macro and no OLE
    part, and is within the run's slide count. Written whole rather than
    patched: a half-written deck is one nobody can reason about.
    """
    path = _deck_path()
    if path is None:   # pragma: no cover - checked before the script ran
        return
    temporary = f"{path}.editing"
    with open(temporary, "wb") as handle:
        handle.write(data)
    os.replace(temporary, path)


def _slide_cap() -> int:
    """How many slides this run's deck may have after an edit.

    Read from the deck the run actually has rather than fixed, and with room
    above it: a script that legitimately splits a page adds one, and a script
    that returns five hundred slides is not editing anything.
    """
    deck = _current_deck()
    if not deck:
        return 200
    try:
        import io

        from pptx import Presentation

        return len(Presentation(io.BytesIO(deck)).slides) + 10
    except Exception:   # pragma: no cover - adopt.problem reports it properly
        return 200
