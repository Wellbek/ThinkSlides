"""
The order a deck gets built in.

The loop used to enforce its sequence by *withholding tool schemas* and
refusing out-of-order calls, then leaving the model to work out what to do with
whatever remained. That is a fence, not a route, and a model that cannot see
the route guesses: one observed run put three question pauses to a user in a
row, re-extracted the design system it already had three times, ran ten
searches and mined six documents that did not exist - none of which was
forbidden, and one of which had to be right.

So the sequence lives here, as data. Each phase declares what it needs to be
true before it starts, what makes it finished, and exactly which tools exist
while it runs. The loop walks to the first unfinished phase whose conditions
hold and runs that one. The model is never asked what happens next; it is asked
to do the work of the phase it is in - understand the request, word the
questions, author the designs, choose a layout, write the copy.

Two shapes of phase:

* ``driver="loop"`` - the loop makes the call itself. Every phase with a fixed
  shape is one of these: there is exactly one thing to do, so letting a model
  decide whether to do it only creates a way for it not to happen.
* ``driver="model"`` - the work genuinely varies (which queries this topic
  needs, which layout this slide wants), so the model chooses, from a toolset
  scoped to that phase alone.

Going backwards is normal and needs no special case: validation flagging a
slide makes ``draft`` unfinished again, so the walker returns to it, then to
``render``, then to ``validate``. The revision loop is the walk, not a
sub-loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from app.agent.tools.build_blocks import BLOCK_BUILDER_TOOLS
from app.agent.tools.build_visuals import BUILD_VISUAL_TOOLS

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.agent.loop import AgentLoop

LOOP_DRIVEN = "loop"
MODEL_DRIVEN = "model"

# Everything a single slide can be built from. Offered only while that slide is
# the one being drafted.
SLIDE_TOOLS = frozenset({
    "select_layout",
    "compose_slide_regions",
    "select_source_image",
    "design_slide_content",
    "validate_slide_json",
    # Available whenever it seems reasonable, not only as the last-resort
    # `amend` phase below - a script that opens the deck and fixes one
    # precise thing (an overlap, a region that needs to move) can be a
    # smaller, faster move than a full redraft of the slide's content, and
    # the model is left to judge which one a given revision actually needs.
    # `AgentLoop._withheld_tools` keeps this out of the offer until a deck
    # exists to edit and the operator has turned the capability on.
    "edit_deck_with_code",
    *BUILD_VISUAL_TOOLS,
    *BLOCK_BUILDER_TOOLS,
})

# The two tools that exist to decide *where things go* on a template's page.
# A run composing against a design language pack does not use them: the whole
# point of that path is that the model never emits geometry, it says what the
# page is about and the pack's archetypes decide the rest. Leaving them
# offered would be worse than useless - a model that can see a way to place a
# box will place one, and then two systems are deciding the same thing.
TEMPLATE_GEOMETRY_TOOLS = frozenset({
    "select_layout",
    "compose_slide_regions",
})


@dataclass(frozen=True)
class Phase:
    """One step of a run, and the conditions that move it along."""

    name: str
    # The phase label carried on every SSE event, which the interface groups
    # the activity feed by. Several phases share one: the user does not need
    # to know that scouting and researching are separately bookkept.
    label: str
    driver: str
    # What the model may call here. Empty for loop-driven phases - the loop
    # makes those calls itself, so there is nothing to offer.
    tools: frozenset[str] = frozenset()
    # Whether this phase happens at all on this run.
    applies: Callable[["AgentLoop"], bool] = lambda loop: True
    # Whether it is finished. Checked before the phase runs (to skip it) and
    # after (to move on), so a phase that is already satisfied costs nothing.
    done: Callable[["AgentLoop"], bool] = lambda loop: True
    # What the model is told to do, when the model is doing it.
    directive: Callable[["AgentLoop"], str] = lambda loop: ""
    # How many model turns this phase gets before the run gives up on it.
    max_turns: int = 0
    pauses: bool = False
    # What happens when the model answers in words and calls no tool, for a
    # phase it may decline - one whose honest answer can be "nothing to do".
    on_decline: Callable[["AgentLoop"], None] | None = None


# ── Conditions, named so the table below reads as prose ──────────────────


def _wants_research(loop: "AgentLoop") -> bool:
    """Whether this run can and should look things up."""
    return loop.mode_is_deep and loop.search_config is not None


def _asks_questions(loop: "AgentLoop") -> bool:
    """Whether this run still stops to ask the user things.

    Consultation is the default and stays the default. The only thing that
    turns it off is the user *confirming* they want it off: a prompt that says
    "don't ask me anything" is a request to be offered the skip, not the skip
    itself, so it earns one confirmation question and nothing more until it is
    answered. ``questions_declined`` is the separate case of a caller that
    cannot answer at all - the offline harness and the tests.
    """
    return not loop.questions_declined and not loop.working_state.autonomy_confirmed


def _slides_outstanding(loop: "AgentLoop") -> list[int]:
    """Slide indices still to be written, in the order they will be written.

    Both the first pass and a revision pass: after validation flags a slide,
    that slide is outstanding again, which is what walks the run back to
    drafting.
    """
    ws = loop.working_state
    if not ws.presentation_structure:
        return []
    total = int(ws.presentation_structure.get("totalSlides") or 0)
    designed = {s.get("slideIndex") for s in ws.accumulated_slides}
    missing = [i for i in range(total) if i not in designed]
    if missing:
        return missing

    # The slides the last validation flagged and this round has not redrafted
    # yet. A queue rather than "nothing changed since validation": that test
    # went false the moment the first flagged slide was redrafted, so a round
    # revised one slide and re-rendered with the rest untouched (#299). The
    # queue is emptied when validation settles, so a run at its cap does not
    # draft again after its last check.
    return list(ws.revision_queue)


def _compiles(loop: "AgentLoop") -> bool:
    from app.agent import compile as slide_compile

    return slide_compile.applies(loop)


def _compile_outstanding(loop: "AgentLoop") -> list[int]:
    from app.agent import compile as slide_compile

    return slide_compile.outstanding(loop)


def _wants_code_amendment(loop: "AgentLoop") -> bool:
    """Whether this run has a defect only a script could fix, with no slide
    left in `slides_needing_revision` for it to attach to.

    `edit_deck_with_code` itself is offered throughout drafting once a deck
    exists (see `SLIDE_TOOLS` and `AgentLoop._withheld_tools`) - it is
    reachable whenever it seems reasonable, not held back for this phase
    alone. What this phase catches is the case that leaves behind: a deck
    validation still calls unhappy after its revision budget, with nothing
    specific flagged for the draft phase to walk back to and offer the tool
    on. Without this backstop that state has no phase that applies to it at
    all, and the run would finish silently unclean.
    """
    ws = loop.working_state
    return bool(
        loop.code_amendment_enabled
        and ws.has_called_finalize
        and ws.last_rendered_path
        # Validation ran out of revision passes with something still wrong.
        # A clean deck needs no script, and a deck still inside its revision
        # budget should use the typed tools, which is what those passes are.
        and ws.at_validation_cap
        and not ws.validation_clean
        # During an amendment, not for findings the deck was handed over
        # with: the change is not the user asking for those to be fixed.
        and not _only_handover_findings(ws)
    )


def _only_handover_findings(ws) -> bool:
    """Whether every finding left is one this run does not act on: one the
    deck was handed over with, or one the change step has had its turn to
    correct."""
    return bool(ws.validation_issues) and not ws.actionable(ws.validation_issues)


def _change_directive(loop: "AgentLoop") -> str:
    """The change step's instruction: which tool answers which kind of
    request, the operations already on the deck, and the pictures to hand.

    Names only the tools this run offers - a directive that names a withheld
    tool is one the model resolves by trying it anyway.
    """
    ws = loop.working_state
    scripted = "edit_deck_with_code" not in loop._withheld_tools()
    lines = [
        "The deck is finished and the user has written again. Their request: "
        f"{ws.amend_request!r}",
        "",
        "Make exactly that change and nothing else.",
        "- About what a slide says or shows (copy, a chart, a figure, a "
        "visual): call revise_slides with the slides and the user's specifics.",
        "- Something added to pages (a logo, a watermark, a footer or page "
        "numbers, a QR code, a progress bar, a shape), one of the deck's "
        "colours changed everywhere, or a phrase linked: call "
        "apply_deck_operation, with every part of the request in one call. "
        "Say where by anchor and which pages; it keeps what it adds clear of "
        "each page's content and uses the deck's own colours and face, so do "
        "not read the deck first.",
    ]
    if scripted:
        lines.append(
            "- Only what neither can express - something already on a page "
            "moved, rotated or restyled: edit_deck_with_code, leaving every "
            "slide's copy, layout and design as they are. Choose coordinates "
            "from layout.json beside the deck - every page's shapes and free "
            "bands, the margins, the deck's colours and face and its type "
            "floors - in the same script, so place anything you add clear of "
            "the content in the deck's own colours without reading the deck "
            "first.")
    if ws.deck_operations:
        listed = "; ".join(_operation_line(op) for op in ws.deck_operations)
        lines.append(
            "The deck already carries these operations (give the same id to "
            f"replace one, or remove it by id): {listed}.")
    return ("\n".join(lines) + loop.asset_directive()
            + " If the request cannot be done to the deck, reply in words "
            "saying why, with no tool call, and the deck is left as it is."
            + _change_findings_directive(loop))


def _operation_line(op: dict) -> str:
    """One recorded operation, as the model would recognise it."""
    kind = op.get("kind")
    if kind == "recolour":
        return f"{op.get('id')}: {op.get('role')} recoloured to {op.get('to')}"
    if kind == "link":
        return f"{op.get('id')}: {op.get('phrase')!r} linked to {op.get('url')}"
    element = op.get("element") or {}
    what = element.get("text") or element.get("asset") or element.get("data") \
        or element.get("shape") or element.get("type")
    return (f"{op.get('id')}: {element.get('type')} {what!r} at the "
            f"{op.get('anchor')} of {op.get('pages')} pages"
            + (" as a watermark" if op.get("layer") == "watermark" else ""))


def _change_findings_directive(loop: "AgentLoop") -> str:
    """What the change's own edit left on the deck, for it to correct."""
    findings = loop.working_state.change_findings
    if not findings:
        return ""
    lines = "\n".join(
        f"  - {'Slide ' + str(f['slideIndex'] + 1) if isinstance(f.get('slideIndex'), int) else 'Deck'}: "
        f"{f.get('detail') or f.get('type') or 'a problem'}" for f in findings)
    return (
        "\n\nThat change is on the deck, and checking it found this, which was "
        f"not there before it:\n{lines}\nCorrect it with one more call: an "
        "operation replaced under its own id (another anchor, size or set of "
        "pages), or one more script if a script made it. What is there stays "
        "and is applied again after any render, so correct it rather than "
        "undoing the change. If it cannot be corrected, reply in words saying so."
    )


def _change_declined(loop: "AgentLoop") -> None:
    """A change answered in words leaves the deck as it was handed over, and
    what validation said of it then still stands: checking it again spent a
    vision call per page on a deck nobody touched (probe-rotate-exhibit)."""
    ws = loop.working_state
    ws.amend_done = True
    if not ws.amend_edited and not ws.amend_redrafted:
        # Nothing was changed. Declined after a script edited the deck or
        # revise_slides redrafted some of it, the deck carries that change,
        # and what validation found after it stands.
        ws.restore_handover()


def _validation_settled(loop: "AgentLoop") -> bool:
    """Clean or out of budget, and nothing has changed the file since.

    A script edit reopens it for one more look, even at the cap: the check
    schedules no revision there, but it is what the closing message reports,
    and the `amend` backstop's edit otherwise shipped unchecked.
    """
    ws = loop.working_state
    # During an amendment a deck that carries only what it was handed over
    # with is as settled as it is going to be: the change broke nothing.
    return ((ws.validation_clean or ws.at_validation_cap
             or _only_handover_findings(ws))
            and not ws.edited_since_validation)


PIPELINE: tuple[Phase, ...] = (
    Phase(
        name="intake",
        label="content_analysis",
        driver=LOOP_DRIVEN,
        done=lambda loop: loop.working_state.brief is not None,
        directive=lambda loop: "Read the request and say what it is asking for.",
    ),
    Phase(
        name="scout",
        label="content_analysis",
        driver=LOOP_DRIVEN,
        # Two queries, before the questions, so the options the user is offered
        # come from the actual material rather than from adjectives. Not the
        # deck's research: that happens after the answers narrow the topic.
        applies=lambda loop: _wants_research(loop) and loop.brief_needs_research,
        done=lambda loop: loop.working_state.scouted,
        directive=lambda loop: "Look up enough to make the questions concrete.",
    ),
    Phase(
        name="consult",
        label="user_input",
        driver=LOOP_DRIVEN,
        applies=_asks_questions,
        done=lambda loop: loop.working_state.consulted_before_planning,
        pauses=True,
        directive=lambda loop: "Put the outstanding questions to the user, in one pause.",
    ),
    Phase(
        name="design",
        label="user_input",
        driver=LOOP_DRIVEN,
        # Always: every deck needs a design settled. What varies is whether the
        # user is the one settling it - a run they have handed over takes the
        # first direction rather than skipping the question and rendering on
        # whatever the default happened to be.
        done=lambda loop: loop.working_state.offered_directions,
        directive=lambda loop: (
            "Offer the user three rendered design directions to choose between."
        ),
        pauses=True,
    ),
    Phase(
        name="research",
        label="content_analysis",
        driver=MODEL_DRIVEN,
        tools=frozenset({"web_search"}),
        # The substantial round, now that the audience, angle and depth are
        # settled. Searching before that spends the budget on facets the user
        # was about to rule out.
        applies=lambda loop: _wants_research(loop) and not loop.working_state.amending,
        # Enough sources, the search budget spent, or the model has stopped
        # asking - a topic with little written about it must not strand a run.
        done=lambda loop: (
            not loop._research_is_thin()
            or loop.working_state.phase_turns.get("research", 0) >= 6
        ),
        directive=lambda loop: (
            f"Research the topic. {loop.working_state.web_search_calls} search(es) so far "
            f"returning {len(loop.working_state.researched_urls)} distinct source(s); this "
            f"phase ends at {loop._min_web_searches()} searches and "
            f"{loop.research_min_sources} sources. Ask a genuinely different question each "
            "time - the subject itself, the figures the deck needs, what has happened "
            "recently, the counterpoint a sceptic would raise. A rephrasing of the last "
            "query returns the last query's pages."
        ),
        max_turns=10,
    ),
    Phase(
        name="plan",
        label="structure_planning",
        driver=LOOP_DRIVEN,
        done=lambda loop: bool(loop.working_state.presentation_structure),
        directive=lambda loop: "Lay the deck out with plan_presentation_structure.",
    ),
    Phase(
        name="review",
        label="user_input",
        driver=LOOP_DRIVEN,
        applies=_asks_questions,
        done=lambda loop: loop.working_state.reviewed_outline_with_user,
        pauses=True,
        directive=lambda loop: "Show the user the outline and ask whether to build it.",
    ),
    Phase(
        name="compile",
        label="content_generation",
        driver=LOOP_DRIVEN,
        # Every outstanding slide in one answer each, all at once (#323). A
        # slide it could not produce falls through to drafting below.
        applies=lambda loop: _compiles(loop),
        done=lambda loop: not _compile_outstanding(loop),
        directive=lambda loop: "Write every outstanding slide, one answer each.",
    ),
    Phase(
        name="draft",
        label="content_generation",
        driver=MODEL_DRIVEN,
        tools=SLIDE_TOOLS,
        done=lambda loop: not _slides_outstanding(loop),
        directive=lambda loop: loop.slide_directive(),
        # Per slide, not for the whole phase: the loop re-enters this phase
        # once per outstanding slide.
        max_turns=6,
    ),
    Phase(
        name="change",
        label="content_generation",
        driver=MODEL_DRIVEN,
        tools=frozenset({"apply_deck_operation", "edit_deck_with_code", "revise_slides"}),
        # A message that arrived after the deck was finished: the deck exists
        # and is kept, and this is the request to change it (#376).
        applies=lambda loop: loop.working_state.amending,
        done=lambda loop: loop.working_state.amend_done,
        directive=_change_directive,
        max_turns=4,
        on_decline=_change_declined,
    ),
    Phase(
        name="render",
        label="rendering",
        driver=LOOP_DRIVEN,
        done=lambda loop: loop.working_state.render_is_current,
        directive=lambda loop: "Render the deck with finalize_and_render_pptx.",
    ),
    Phase(
        name="validate",
        label="validation",
        driver=LOOP_DRIVEN,
        done=_validation_settled,
        directive=lambda loop: "Check the rendered file with validate_rendered_output.",
    ),
    Phase(
        name="amend",
        label="validation",
        driver=MODEL_DRIVEN,
        tools=frozenset({"edit_deck_with_code"}),
        # The backstop, not the primary path - see `_wants_code_amendment`.
        # `edit_deck_with_code` is offered throughout drafting already; this
        # only fires for the state that leaves nothing else to apply to.
        applies=_wants_code_amendment,
        done=lambda loop: loop.working_state.code_amendment_done,
        directive=lambda loop: (
            "validate_rendered_output reported something the build_* tools "
            "cannot fix. Write one short script with edit_deck_with_code to "
            "correct it, or reply in words, with no tool call, if a script "
            "would not help."
        ),
        max_turns=4,
        on_decline=lambda loop: setattr(loop.working_state, "code_amendment_done", True),
    ),
)

PHASES_BY_NAME = {phase.name: phase for phase in PIPELINE}


def next_phase(loop: "AgentLoop") -> Phase | None:
    """The first phase that applies to this run and is not finished."""
    for phase in PIPELINE:
        if phase.applies(loop) and not phase.done(loop):
            return phase
    return None


def phase_after(name: str) -> Phase | None:
    """The phase following ``name`` in the pipeline, for progress reporting."""
    names = [phase.name for phase in PIPELINE]
    if name not in names:
        return None
    position = names.index(name) + 1
    return PIPELINE[position] if position < len(PIPELINE) else None
