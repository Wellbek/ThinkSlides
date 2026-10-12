"""
Slide compile: one structured answer per slide, every slide at once (#323).

Drafting used to be a conversation per slide, run one slide after another: a
turn to choose an exhibit tool, a turn to call design_slide_content (which made
a call of its own to write the copy), a turn to validate, a turn to end. Four
or five calls a slide, in sequence, each carrying the whole run's system
prompt - 28 of a run's 32 calls and 202k of its 208k input tokens (R-001,
R-005).

Once the plan exists, a slide is a function of its plan entry, the evidence
and the design: what to build is mostly already decided by its contentType.
So each outstanding slide is now asked for once, in one answer that carries
everything the slide is: the build_* calls its exhibits need and one
``write_slide`` call with its copy. The answers are checked by the same code a
drafted slide goes through - the builders' own validation, the grounding
refusal, design_slide_content's finishing and validate_slide_json - and a
slide that fails a check is asked once more with what failed (D-3; section
14.3 of the plan). Slides are independent, so they are asked concurrently.

A slide compile could not produce is left outstanding, and the drafting phase
after this one picks it up the old way. Compile is the fast path, not the
only one.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import TYPE_CHECKING, AsyncIterator

from app.agent import pipeline
from app.agent.redact import redact_binary
from app.agent.registry import ToolResult
from app.agent.tools._today import today_line
from app.agent.tools.build_blocks import BLOCK_BUILDER_TOOLS
from app.agent.tools.build_visuals import BUILD_VISUAL_TOOLS
from app.agent.tools.design_slide_content import compose_prompt
from app.observability import trace
from app.observability.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.agent.loop import AgentLoop

logger = get_logger(__name__)

WRITE_SLIDE = "write_slide"

# One retry with the checks' messages, then the slide is left to drafting.
# The plan's escalation rule: a compile output failing a gate twice moves on.
MAX_ATTEMPTS = 2

# The tools a compiled slide may build from. The geometry tools never apply
# (compile runs only on a design language, where the page is composed in
# code), and the rendered-deck tools have no deck to work on yet.
_EXHIBIT_TOOLS = frozenset({
    *BUILD_VISUAL_TOOLS,
    *BLOCK_BUILDER_TOOLS,
    "generate_image",
    "select_source_image",
})

_WRITE_SLIDE_SCHEMA = {
    "type": "function",
    "function": {
        "name": WRITE_SLIDE,
        "description": (
            "The slide's copy: its heading, body, notes and apparatus, as one "
            "JSON object in the shape the request gives. Call it exactly once, "
            "after the build_* calls for this slide's exhibits."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "slide": {
                    "type": "object",
                    "description": "The slide JSON, in the shape given in the request.",
                },
            },
            "required": ["slide"],
        },
    },
}


def applies(loop: "AgentLoop") -> bool:
    """Whether compile drafts this run's slides.

    Only on a design language: there the page is composed in code once the
    material exists, so a slide's whole content is its copy and its exhibits.
    A template run picks a layout per slide first and fills its placeholders,
    which is a decision the drafting conversation still makes.

    And only where the drafting conversation has nothing more to offer.
    """
    if not loop.working_state.pack_id:
        return False
    # Compile offers what the drafting conversation offers, less the script
    # that edits a rendered deck. Where the conversation would offer that
    # script - one slide sent back with a deck already rendered - a position
    # fix may be the right answer, and the conversation is kept for it. Every
    # first pass, and every round that redrafts several slides, has no such
    # script on offer (see AgentLoop._withheld_tools).
    return "edit_deck_with_code" in loop._withheld_tools()


def outstanding(loop: "AgentLoop") -> list[int]:
    """Slides still to be written that compile has not tried this round."""
    tried = loop.working_state.compile_tried
    return [i for i in pipeline._slides_outstanding(loop) if i not in tried]


def offered_tools(loop: "AgentLoop") -> list[dict]:
    """The exhibit builders this run offers, and write_slide."""
    names = (_EXHIBIT_TOOLS - loop._withheld_tools())
    if loop.image_connection is None:
        names -= {"generate_image"}
    if not (loop.context.get("imageCatalog") or []):
        names -= {"select_source_image"}
    schemas = [
        schema for schema in loop.registry.get_function_schemas()
        if schema.get("function", {}).get("name") in names
    ]
    return schemas + [_WRITE_SLIDE_SCHEMA]


def system_prompt(loop: "AgentLoop", context: dict) -> str:
    """What every slide's request shares, so the provider can cache it.

    Static instructions first, then the deck: its plan and its evidence. Only
    the slide's own request differs between the calls, and it comes last.
    """
    ws = loop.working_state
    parts = [
        "You are ThinkSlides, writing one page of a presentation whose "
        "outline is settled. You are asked for one slide, and you answer "
        "with tool calls only: the build_* calls its exhibits need, then "
        "exactly one write_slide call carrying its copy. Nothing you write "
        "outside those calls reaches the deck.",
        "",
        today_line(),
        "",
    ]
    if not loop._has_grounding():
        from app.agent.loop import _UNSOURCED_RULES

        parts.extend(_UNSOURCED_RULES)
    parts.extend(loop._visuals_lines(loop._withheld_tools(), compiling=True))
    parts.extend(loop._research_lines(context))
    # The user's answers are not restated here: they are in the conversation
    # that follows this prompt, as the user gave them.
    parts.extend([
        "## The deck's outline",
        "Every slide of the deck, so this one fits the argument around it "
        "rather than repeating a neighbour:",
        "```json",
        json.dumps(ws.presentation_structure or {}, indent=2),
        "```",
    ])
    return "\n".join(parts)


def slide_request(loop: "AgentLoop", index: int, context: dict) -> str:
    """This slide's own part of the request: what to do, and the copy's shape."""
    from app.agent.loop import _project_design_system

    ws = loop.working_state
    plan = (ws.presentation_structure or {}).get("slides") or []
    planned = dict(plan[index]) if index < len(plan) else {}
    prompt, _ = compose_prompt(
        planned,
        index,
        ws.selected_layouts.get(index) or {},
        _project_design_system("design_slide_content", context.get("design_system") or {}),
        loop.detail_level,
        redact_binary(ws.selected_images.get(index) or {}),
        redact_binary(ws.prepared_visuals.get(index) or []),
        ws.deck_format,
        compiling=True,
    )
    return (
        loop.slide_directive_for(index, compiling=True)
        + "\n\n---\n\n"
        + prompt
        + "\n\nThe JSON object above is write_slide's \"slide\" argument."
    )


async def compile_all(loop: "AgentLoop", context: dict, chat_messages: list[dict],
                      session_id: uuid.UUID, phase: pipeline.Phase,
                      ) -> AsyncIterator[tuple[str, object]]:
    """Compile every outstanding slide concurrently, streaming their events.

    Each slide runs as its own task; their events are merged into this one
    stream in the order they happen. A provider that cannot be reached ends
    the phase the way a failed model turn ends any other.
    """
    indices = outstanding(loop)
    if not indices:
        return
    loop.working_state.compile_tried.update(indices)
    shared = system_prompt(loop, context)
    tools = offered_tools(loop)
    limit = asyncio.Semaphore(max(1, loop.compile_concurrency))
    queue: asyncio.Queue = asyncio.Queue()

    async def one(index: int) -> None:
        async with limit:
            try:
                async for item in compile_slide(
                    loop, index, shared, tools, context, chat_messages,
                    session_id, phase,
                ):
                    await queue.put(item)
            except Exception as e:  # a slide's failure must not lose the others
                logger.error("slide_compile_failed", slide_index=index, error=str(e))

    tasks = [asyncio.create_task(one(i)) for i in indices]
    pending = set(tasks)
    failed = False
    try:
        while pending or not queue.empty():
            if queue.empty():
                getter = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait(
                    pending | {getter}, return_when=asyncio.FIRST_COMPLETED)
                pending -= done
                if getter not in done:
                    getter.cancel()
                    continue
                item = getter.result()
            else:
                item = queue.get_nowait()
            kind, payload = item
            if kind == "provider_failed":
                failed = True
            elif kind == "event":
                yield kind, payload
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
    if failed:
        yield "provider_failed", True


async def compile_slide(loop: "AgentLoop", index: int, shared: str, tools: list[dict],
                        context: dict, chat_messages: list[dict],
                        session_id: uuid.UUID, phase: pipeline.Phase,
                        ) -> AsyncIterator[tuple[str, object]]:
    """Ask for one slide, check what came back, and ask once more if it failed."""
    from app.agent.loop import _event, _iso_now, _provider_failure_message

    messages = [{"role": "system", "content": shared}, *chat_messages,
                {"role": "user", "content": slide_request(loop, index, context)}]
    with trace.span("step", f"compile slide {index + 1}",
                    reason="one structured answer per slide", slideIndex=index) as span:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            response = None
            try:
                async for kind, payload in loop._with_retry_notices(
                    loop._chat_with_retry(messages=messages, tools=tools), phase.label,
                ):
                    if kind == "event":
                        yield "event", payload
                    else:
                        response = payload
            except Exception as e:
                logger.error("llm_call_failed", error=str(e), phase=phase.name,
                             slide_index=index)
                yield "event", _event("error", phase.label, {
                    "message": _provider_failure_message(e),
                    "code": "provider_unavailable",
                    "timestamp": _iso_now(),
                }, step=phase.name)
                yield "provider_failed", True
                return

            assistant = (response or {}).get("message") or {}
            calls = (response or {}).get("tool_calls") or []
            failures: list[str] = []
            results: list[tuple[str, ToolResult]] = []
            async for kind, payload in _apply(loop, index, calls, context,
                                              session_id, phase):
                if kind == "event":
                    yield "event", payload
                else:
                    results, failures = payload
            if span is not None:
                span.set(attempts=attempt, failures=len(failures))
            if not failures:
                return
            if attempt == MAX_ATTEMPTS:
                logger.warning("slide_compile_gave_up", slide_index=index,
                               failures=failures[:4])
                return
            # The same conversation, continued: what was asked, what came back,
            # what each call returned, and what must change.
            messages.append({**assistant, "role": "assistant", "tool_calls": calls})
            for call_id, result in results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(
                        redact_binary(result.data) if result.success
                        else {"error": result.error}),
                })
            messages.append({"role": "user", "content": (
                "Not all of that went through:\n"
                + "\n".join(f"- {f}" for f in failures)
                + "\n\nAnswer again for this slide: rebuild any exhibit that "
                "failed or should change, and end with write_slide carrying the "
                "slide's whole copy, written for the exhibits it now has."
            )})


async def _apply(loop: "AgentLoop", index: int, calls: list[dict], context: dict,
                 session_id: uuid.UUID, phase: pipeline.Phase,
                 ) -> AsyncIterator[tuple[str, object]]:
    """Run one answer's calls through the loop's own dispatcher.

    Exhibits first, in the order given, then the copy, then the slide check.
    Yields events, then ``("result", (results, failures))``: every call's
    result by call id, for the retry's transcript, and a line per thing that
    did not go through.
    """
    offered = {s["function"]["name"] for s in offered_tools(loop)}
    results: list[tuple[str, ToolResult]] = []
    failures: list[str] = []
    written = None

    for call in calls:
        fn = call.get("function") or {}
        name = fn.get("name", "")
        call_id = call.get("id") or str(uuid.uuid4())
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = None
        if not isinstance(args, dict):
            result = ToolResult(success=False, error="The arguments were not a JSON object.")
        elif name == WRITE_SLIDE:
            written = (call_id, args.get("slide"))
            continue
        elif name not in offered:
            result = ToolResult(success=False, error=(
                f"{name} is not offered here: answer with build_* calls and "
                "write_slide only."))
        else:
            # This answer is for this slide; an exhibit addressed elsewhere
            # would land on a page another task is writing.
            args["slide_index"] = index
            result = None
            async for kind, payload in loop._dispatch_tool(
                name, args, call_id, context=context, session_id=session_id,
                current_phase=phase.label, step=phase.name,
            ):
                if kind == "event":
                    yield "event", payload
                else:
                    result = payload
        results.append((call_id, result))
        if not result.success:
            failures.append(f"{name}: {result.error}")

    if written is None:
        failures.append("write_slide was not called, so the slide has no copy.")
        yield "result", (results, failures)
        return
    call_id, slide = written
    if not isinstance(slide, dict):
        result = ToolResult(success=False, error="write_slide's slide was not a JSON object.")
    else:
        plan = (loop.working_state.presentation_structure or {}).get("slides") or []
        outline = dict(plan[index]) if index < len(plan) else {}
        result = None
        async for kind, payload in loop._dispatch_tool(
            "design_slide_content",
            {"slide_outline": json.dumps(outline), "slide_index": index,
             "written": {**slide, "slideIndex": index}},
            call_id, context=context, session_id=session_id,
            current_phase=phase.label, step=phase.name, chosen_by="compile",
        ):
            if kind == "event":
                yield "event", payload
            else:
                result = payload
    results.append((call_id, result))
    if not result.success:
        failures.append(f"write_slide: {result.error}")
        yield "result", (results, failures)
        return

    stored = next((s for s in loop.working_state.accumulated_slides
                   if s.get("slideIndex") == index), None)
    check = None
    async for kind, payload in loop._dispatch_tool(
        "validate_slide_json", {"slide_json": json.dumps(stored or {})},
        str(uuid.uuid4()), context=context, session_id=session_id,
        current_phase=phase.label, step=phase.name, chosen_by="loop",
    ):
        if kind == "event":
            yield "event", payload
        else:
            check = payload
    if check is not None and not check.success:
        failures.append(f"the slide check: {check.error}")
    yield "result", (results, failures)
