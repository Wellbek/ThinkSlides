"""
ReAct Agent Loop

Implements the Reason-Act-Observation cycle:
1. LLM reasons about the current state (thought)
2. LLM selects a tool to call (act)
3. Tool executes and returns result (observation)
4. Cycle repeats until the task is complete

All intermediate steps are yielded as typed events for SSE streaming.
"""

from __future__ import annotations
from typing import AsyncIterator
from sqlalchemy.ext.asyncio import AsyncSession
from app import ai_settings
from app.agent import run_context
from app.agent.event_types import ALL_EVENT_TYPES
from app.agent.registry import get_registry, ToolResult
from app.agent.summaries import summarize_tool_result
from app.agent.tools._json_extract import extract_json_object
from app.agent.tools.build_visuals import BUILD_VISUAL_TOOLS
from app.agent.tools.build_blocks import BLOCK_BUILDER_TOOLS
from app.llm.provider import _is_rate_limit_error, build_chat_provider, get_llm_provider
from app.llm.retry_notices import (
    REASON_TRANSIENT,
    RetryNotice,
    publish_retry_notice,
    reset_retry_observer,
    set_retry_observer,
)
from app.observability.logging import get_logger
import asyncio
import json
import re
import time
import uuid
import os
import redis.asyncio as redis

logger = get_logger(__name__)

# Effort levels a message can be sent with. Simple answers from the chat model
# alone; deep additionally lets the agent research the topic with web_search.
# These mirror the orchestrator's handler.ModeSimple / handler.ModeDeep.
MODE_SIMPLE = "simple"
MODE_DEEP = "deep"

# Tools that only exist in deep mode. Removing them from the schema list is
# what actually enforces the mode - the model cannot call a tool it was never
# offered, which is more reliable than asking it not to in the prompt.
DEEP_ONLY_TOOLS = {"web_search"}

# The deck-writing tools, split at the two consultation checkpoints. A
# checkpoint that has not happened yet withholds everything from its own step
# onwards, so the model is never offered a tool it would only be refused for
# calling. See _tools_withheld_by_checkpoint.
PLANNING_TOOLS = frozenset({"plan_presentation_structure"})
DRAFTING_TOOLS = frozenset({
    "select_layout",
    "design_slide_content",
    "select_source_image",
    "finalize_and_render_pptx",
    "validate_rendered_output",
    *BUILD_VISUAL_TOOLS,
    *BLOCK_BUILDER_TOOLS,
})


def normalize_mode(mode: str | None) -> str:
    """Map any caller-supplied value onto a known mode, defaulting to simple."""
    return MODE_DEEP if mode == MODE_DEEP else MODE_SIMPLE


# How much content depth a generation should aim for. Mirrors the
# orchestrator's handler.DetailLevel* constants.
DETAIL_LEVEL_CONCISE = "concise"
DETAIL_LEVEL_DETAILED = "detailed"
DETAIL_LEVEL_COMPREHENSIVE = "comprehensive"
DETAIL_LEVELS = {DETAIL_LEVEL_CONCISE, DETAIL_LEVEL_DETAILED, DETAIL_LEVEL_COMPREHENSIVE}

# How much research a Deep Thinking run does, by content depth: (floor, cap)
# on the number of searches. The floor exists because one lookup returning a
# handful of links was being treated as the whole truth about a topic - a deck
# is only as trustworthy as the breadth behind it, and a single query answers
# one facet of a subject that has several. The cap stops the agent grinding
# through near-identical queries when the results stop improving.
RESEARCH_BUDGET = {
    DETAIL_LEVEL_CONCISE: (3, 6),
    DETAIL_LEVEL_DETAILED: (4, 9),
    DETAIL_LEVEL_COMPREHENSIVE: (5, 12),
}

# How many distinct sources a run aims to have gathered before it writes
# anything. Below this the agent is told to keep looking rather than plan.
RESEARCH_MIN_SOURCES = 6

# How many findings stay in the prompt. Large enough to hold every search a run
# is asked to make, since evicting the early ones would throw away the breadth
# the floor above exists to produce; snippets are trimmed instead so the
# context cost stays flat.
MAX_RESEARCH_IN_CONTEXT = 48
RESEARCH_SNIPPET_CHARS = 320

# Ways a user tells us to stop asking and just build the thing. Matched against
# what they actually typed, because the alternative - trusting the model to
# remember an instruction from six tool calls ago - is how a "don't ask me
# anything" run ends up interrogating someone anyway. Kept deliberately narrow:
# a false positive silently disables the consultation the user asked for.
_NO_QUESTIONS_PATTERNS = (
    r"\bdon'?t ask\b",
    r"\bdo not ask\b",
    r"\bno (?:more )?questions?\b",
    r"\bstop asking\b",
    r"\bwithout asking\b",
    r"\bjust (?:build|make|create|do|generate) it\b",
    r"\b(?:use )?your best judgement?\b",
    r"\bno need to ask\b",
    r"\bskip the questions?\b",
)
_NO_QUESTIONS_RE = re.compile("|".join(_NO_QUESTIONS_PATTERNS), re.IGNORECASE)


def user_declined_questions(chat_messages: list[dict]) -> bool:
    """True when the user has explicitly asked not to be interrupted."""
    return any(
        m.get("role") == "user" and _NO_QUESTIONS_RE.search(m.get("content") or "")
        for m in chat_messages
    )


# ── Deciding when a deck needs grounding it does not have ────────────────────
#
# In simple mode the agent has no web access and, unless documents were
# uploaded, nothing but its own memory. It still produces charts. A real run
# asked for "Starbucks FY2025" and charted revenue rising to $40.0B; the actual
# figure is ~$37B. The number was invented, and putting it in a chart with a
# labelled axis is what makes it look sourced.
#
# Hardening the prompt helps and does not guarantee anything (#72). Whether the
# request is *asking for* facts is a judgement call, so a model makes it: a
# single cheap classification call, separate from the agent loop, whose only
# job is to say whether this deck stands on real-world figures and which ones.
#
# It is deliberately not keyword matching. The first attempt was a regex over
# financial vocabulary, and it missed the most ordinary phrasing there is -
# "financial slides for starbucks" matched nothing, because a list of words is
# always a list of the words somebody thought of. The judgement generalises;
# the word list does not.
_GROUNDING_JUDGE_SYSTEM = """\
You decide one thing about a request for a slide deck: does the deck stand on \
real-world facts that ought to be looked up, rather than recalled?

Answer YES when the deck is about a real organisation, product, market, place, \
person or event, or when it asks for figures, results, trends, comparisons or \
anything time-anchored. These decks carry numbers, and a number recalled from \
memory is presented to the reader as a measurement.

Answer NO when the deck is conceptual, instructional, motivational, creative, \
process- or framework-shaped, or built entirely from material the user has \
supplied. Nothing in it would be checked against the world.

Reply with a JSON object and nothing else:
{"needs_research": true|false, "figures": ["short phrase", ...]}

"figures" names, in the user's own terms, up to four specific things this deck \
would need and cannot verify - "Starbucks FY2025 revenue", "US EV market size". \
Leave it empty when needs_research is false."""


def _judge_messages(chat_messages: list[dict]) -> list[dict]:
    """The conversation as the grounding judge sees it: the user's words only.

    Assistant turns are dropped deliberately. They contain the agent's own
    narration of the deck it is about to build, which reads as corroboration
    and pushes the judgement towards whatever it has already decided to do.
    """
    said = [
        (m.get("content") or "").strip()
        for m in chat_messages
        if m.get("role") == "user" and (m.get("content") or "").strip()
    ]
    return [
        {"role": "system", "content": _GROUNDING_JUDGE_SYSTEM},
        {"role": "user", "content": "\n\n".join(said[-6:])},
    ]


# The exact wording the agent is told to offer, so the answer can be recognised
# without guessing at free text. Matching stays deliberately loose around them:
# a user may well type their own "yes, search the web" instead of tapping.
DEEP_RESEARCH_YES = "Yes - research the web for current figures"
DEEP_RESEARCH_NO = "No - build it from what you already know"

_DEEP_RESEARCH_YES_RE = re.compile(
    "|".join((
        r"\bresearch the web\b",
        r"\bsearch the web\b",
        r"\bweb search\b",
        r"\bdeep (?:thinking|research|mode)\b",
        r"\b(?:enable|turn on|switch on|use) deep\b",
        r"\blook (?:it |them )?up (?:online|on the web)\b",
    )),
    re.IGNORECASE,
)
_DEEP_RESEARCH_NO_RE = re.compile(
    "|".join((
        r"\bwhat you (?:already )?know\b",
        r"\bno (?:web )?(?:research|search)\b",
        r"\bwithout (?:web )?(?:research|searching)\b",
        r"\bskip the (?:research|search)\b",
        r"\bdon'?t (?:search|research)\b",
    )),
    re.IGNORECASE,
)


def user_answered_deep_research(chat_messages: list[dict]) -> bool | None:
    """
    Whether the user agreed to web research, from their latest say on it.

    None when they have not addressed it. Declines are checked first because
    "No - build it from what you already know" must never read as a yes.
    """
    for message in reversed(chat_messages):
        if message.get("role") != "user":
            continue
        content = message.get("content") or ""
        if _DEEP_RESEARCH_NO_RE.search(content):
            return False
        if _DEEP_RESEARCH_YES_RE.search(content):
            return True
    return None


def normalize_detail_level(level: str | None) -> str:
    """Map any caller-supplied value onto a known level, defaulting to detailed."""
    return level if level in DETAIL_LEVELS else DETAIL_LEVEL_DETAILED


# Substrings identifying errors worth retrying. Provider SDKs raise their own
# exception types but all surface an HTTP status; 429 and 5xx are transient,
# everything else (a bad key, an unknown model) will fail again identically.
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


def _status_of(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return status


def _is_retryable(exc: Exception) -> bool:
    status = _status_of(exc)
    if status in _RETRYABLE_STATUSES:
        return True
    if status is not None:
        return False
    # Some SDKs stringify the status instead of exposing it.
    text = str(exc).lower()
    return "rate limit" in text or "429" in text or "timeout" in text


def _retry_after_seconds(exc: Exception) -> float | None:
    """Honour the provider's own advice about when to try again."""
    match = re.search(r"try again in (\d+(?:\.\d+)?)s", str(exc))
    if match:
        # A small margin, because the window is measured on their clock.
        return float(match.group(1)) + 0.5
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers:
        raw = headers.get("retry-after") or headers.get("Retry-After")
        try:
            return float(raw) if raw else None
        except (TypeError, ValueError):
            return None
    return None

# Map tool names to logical phases
TOOL_PHASES = {
    "analyze_document": "content_analysis",
    "extract_key_data": "content_analysis",
    "extract_template_design_system": "content_analysis",
    "web_search": "content_analysis",
    "plan_presentation_structure": "structure_planning",
    "select_layout": "layout_selection",
    "design_slide_content": "content_generation",
    "validate_slide_json": "validation",
    "select_source_image": "content_generation",
    "build_chart": "content_generation",
    "build_table": "content_generation",
    "build_key_metrics": "content_generation",
    "finalize_and_render_pptx": "rendering",
    "validate_rendered_output": "validation",
    "ask_user_question": "user_input",  # Future: pause for user input
}


class WorkingState:
    """
    Maintains the working state of the agent loop across iterations.
    Tracks accumulated slides, current step, and any pending questions.
    Also tracks completed tool calls for context preservation on errors.
    """

    def __init__(self) -> None:
        self.accumulated_slides: list[dict] = []
        self.presentation_structure: dict | None = None
        # slide_index -> the select_layout result for that slide. Lets the
        # loop auto-supply design_slide_content's layout_info instead of
        # requiring the LLM to copy the full layout object it just received
        # back verbatim in the next call - the same class of JSON-escaping
        # mistake as available_layouts (see _run's tool-dispatch injection).
        self.selected_layouts: dict[int, dict] = {}
        # slide_index -> the resolved image dict ({source, data, altText})
        # chosen by select_source_image, keyed so design_slide_content can be
        # auto-supplied a pre-vetted image instead of re-selecting or (worse)
        # having to carry raw image bytes through its own LLM output.
        self.selected_images: dict[int, dict] = {}
        # slide_index -> content blocks built by build_chart/build_table/
        # build_key_metrics. Held here rather than asked back from the model
        # because these carry the deck's actual figures: routing validated
        # numbers through a second LLM response is how a chart ends up
        # disagreeing with the bullets beside it.
        self.prepared_visuals: dict[int, list[dict]] = {}
        # Marks a slide placed itself through draw_primitives. Kept apart from
        # prepared_visuals because they are not content blocks - they attach to
        # the slide's ``primitives`` list, not to its ``contents``.
        self.prepared_primitives: dict[int, list[dict]] = {}
        self.current_step_index: int = 0
        self.pending_question: dict | None = None
        self.is_paused_for_user: bool = False
        self.has_called_finalize: bool = False
        # Ground-truth output validation (see validate_rendered_output) must
        # run at least once after finalize before the loop is allowed to
        # exit - but bounded, so a deck with a genuinely unfixable overflow
        # (e.g. content that cannot fit no matter how it's redesigned)
        # cannot loop forever chasing a clean validation result.
        self.has_called_validate: bool = False
        # True only once a validation run came back with NO issues. The exit
        # gate requires this (or the retry cap), so a deck with a known
        # rendering defect can't be announced as finished.
        self.validation_clean: bool = False
        # Issues reported by the most recent validation run, replayed to the
        # model when it tries to stop without fixing them.
        self.validation_issues: list[dict] = []
        # Slide indices the last validation run actually complained about.
        # Only these get redesigned: a deck where one chart overflows does not
        # need its other slides rewritten, and regenerating them costs an LLM
        # call each and risks changing copy the user was happy with.
        self.slides_needing_revision: list[int] = []
        self.validation_attempts: int = 0
        self.MAX_VALIDATION_ATTEMPTS = 3
        # Checks that could not run at all (LibreOffice conversion error, a
        # provider failure in the redesign feeding them). Tracked separately
        # from real attempts so infrastructure trouble cannot exhaust the
        # revision budget, but still bounded so it cannot spin forever.
        self.validation_failures: int = 0
        self.MAX_VALIDATION_FAILURES = 3
        # Set once finalize_and_render_pptx succeeds - the loop always knows
        # the true rendered path with certainty, so validate_rendered_output
        # is auto-supplied this rather than trusting the LLM's own copy.
        self.last_rendered_path: str | None = None
        # slide_finalized payloads produced by the most recent render, held
        # back until validation has settled. A slide the validator is about to
        # send back for redesign is not finalized, so publishing its preview
        # the moment it renders shows the user a slide that then changes
        # underneath them. Re-rendering overwrites this list, so whatever is
        # eventually published always describes the last render.
        self.pending_slide_previews: list[dict] = []
        self.previews_published: bool = False
        # The finalize_and_render_pptx payload for the current render, held
        # back with the previews. The download card and the file's entry in the
        # sources list are driven off this, so neither appears until the deck
        # has actually passed validation.
        self.pending_render_result: dict | None = None
        self.completed_tools: dict[str, dict] = {}  # tool_name -> {args, result, timestamp}
        self.last_error: str | None = None
        self.answered_questions: list[dict] = []  # Track questions that have been answered
        # How many web searches this run has attempted, and how many distinct
        # sources they produced between them. Both are gates: the run may not
        # start planning off a single lookup (see RESEARCH_MIN_SEARCHES), and
        # may not keep searching forever either (see RESEARCH_MAX_SEARCHES).
        self.web_search_calls: int = 0
        self.researched_urls: set[str] = set()
        # Questions the agent has put to the user, by checkpoint. The planning
        # consultation is enforced rather than merely encouraged - see
        # AgentLoop._check_precondition - so it has to be remembered across the
        # pause/resume cycle that answering a question involves.
        self.consulted_before_planning: bool = False
        self.reviewed_outline_with_user: bool = False
        # Whether the user has been offered web research for a factual request
        # this run has no grounding for. Persisted like the others: the offer
        # pauses the run, and a resumed run that forgot it had asked would ask
        # again.
        self.offered_deep_research: bool = False

    # Above this share of the deck being flagged, redesigning everything is
    # simpler (and no more expensive) than steering slide-by-slide edits.
    FULL_REWRITE_ISSUE_RATIO = 0.6

    def compute_slides_needing_revision(self, issues: list[dict]) -> list[int]:
        """
        Which slides the next revision pass should touch.

        Returns just the flagged slides for a localised defect - one chart
        running off one slide should cost one redesign, not a regeneration of
        the whole deck. When most of the deck is flagged the distinction stops
        being worth enforcing, so an empty list is returned, which callers read
        as "no restriction".
        """
        flagged = sorted({
            issue["slideIndex"] for issue in issues
            if isinstance(issue.get("slideIndex"), int)
        })
        if not flagged:
            return []

        total = len(self.accumulated_slides) or (
            (self.presentation_structure or {}).get("totalSlides", 0)
        )
        if total and len(flagged) / total >= self.FULL_REWRITE_ISSUE_RATIO:
            return []
        return flagged

    def add_slide(self, slide: dict) -> None:
        """Add a slide to the accumulated list, or replace it if this slideIndex
        was already designed - the LLM may legitimately redesign a slide it got
        wrong, and that must overwrite rather than duplicate."""
        idx = slide.get("slideIndex")
        if idx is not None:
            for i, existing in enumerate(self.accumulated_slides):
                if existing.get("slideIndex") == idx:
                    self.accumulated_slides[i] = slide
                    return
        self.accumulated_slides.append(slide)

    def resolve_and_add_slide(self, slide_data: dict) -> None:
        """
        Add a designed slide, first resolving any image content item whose
        source is the "selected" sentinel. design_slide_content is told
        about a pre-vetted image (if any) via selected_image_context and
        asked to reference it as {"type": "image", "image": {"source":
        "selected"}} rather than inventing or copying raw image data into
        its own output - this is the server-side step that turns that
        sentinel into the real {source, data} the renderer can use, or
        drops the item entirely if nothing was actually selected for this
        slide (never ships a reference the renderer cannot resolve).
        """
        slide_index = slide_data.get("slideIndex")
        resolved = self.selected_images.get(slide_index)
        contents = slide_data.get("contents")

        if isinstance(contents, list):
            new_contents = []
            for item in contents:
                is_selected_sentinel = (
                    isinstance(item, dict)
                    and item.get("type") == "image"
                    and isinstance(item.get("image"), dict)
                    and item["image"].get("source") == "selected"
                )
                if is_selected_sentinel:
                    if resolved:
                        merged_image = dict(resolved)
                        placeholder_idx = item.get("placeholderIdx", item["image"].get("placeholderIdx"))
                        if placeholder_idx is not None:
                            merged_image["placeholderIdx"] = placeholder_idx
                        new_contents.append({**item, "image": merged_image})
                    continue
                new_contents.append(item)
            slide_data = {**slide_data, "contents": new_contents}

        # Attach anything build_chart/build_table/build_key_metrics prepared
        # for this slide. Any chart or table the model wrote inline is
        # dropped in favour of the validated one, so a slide cannot end up
        # carrying the same figures twice.
        prepared = self.prepared_visuals.get(slide_index)
        if prepared:
            # Keyed by (type, region), not by type alone: a composed slide
            # legitimately carries three stat-callout blocks bound to three
            # different regions, and keying on type would collapse them into
            # one and silently drop the other two.
            prepared_slots = {
                (block.get("type"), block.get("regionId")) for block in prepared
            }
            kept = [
                item for item in slide_data.get("contents", [])
                if not (isinstance(item, dict)
                        and (item.get("type"), item.get("regionId")) in prepared_slots)
            ]
            slide_data = {**slide_data, "contents": [*kept, *prepared]}

        marks = self.prepared_primitives.get(slide_index)
        if marks:
            slide_data = {**slide_data, "primitives": list(marks)}

        self.add_slide(slide_data)

    def get_slides_json(self) -> str:
        """The deck as JSON, in the order the slides are meant to be read.

        Slides accumulate in the order ``design_slide_content`` happened to be
        called, which is not the order they were planned in - a model that
        designs slide 5 before slide 2 (routinely, and always after a
        validation pass sends specific slides back) produced a deck whose
        narrative ran out of sequence: the closing "Implications" slide
        rendered second. Ordering here rather than at each consumer means the
        renderer, the markdown fallback and the validator all see the same
        deck.
        """
        return json.dumps(self.ordered_slides())

    def ordered_slides(self) -> list[dict]:
        """Accumulated slides sorted by their planned index.

        A slide with no index keeps its position relative to the slides
        around it, since a stable sort leaves equal keys in insertion order.
        """
        def key(slide: dict) -> int:
            index = slide.get("slideIndex")
            return index if isinstance(index, int) else 10**6

        return sorted(self.accumulated_slides, key=key)

    def record_completed_tool(self, name: str, args: dict, result: dict) -> None:
        """Record a successfully completed tool call."""
        import datetime
        self.completed_tools[name] = {
            "args": args,
            "result": result,
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }

    def has_completed_tool(self, name: str) -> bool:
        """Check if a tool has been successfully completed."""
        return name in self.completed_tools

    def get_tool_result(self, name: str) -> dict | None:
        """Get the result of a completed tool, if available."""
        return self.completed_tools.get(name, {}).get("result")

    def get_completed_work_summary(self) -> str:
        """Generate a summary of completed work for the LLM context."""
        if not self.completed_tools and not self.answered_questions:
            return ""

        lines = ["## Already Completed Work", ""]
        lines.append("The following tools have already been successfully executed in this session.")
        lines.append("You do NOT need to repeat these steps - you can use their results directly.")
        lines.append("")

        # Group by phase for clarity
        phase_tools = {}
        for tool_name, tool_data in self.completed_tools.items():
            phase = TOOL_PHASES.get(tool_name, "other")
            if phase not in phase_tools:
                phase_tools[phase] = []
            phase_tools[phase].append(tool_name)

        # Define phase descriptions
        phase_descriptions = {
            "content_analysis": "Content Analysis",
            "structure_planning": "Structure Planning",
            "layout_selection": "Layout Selection",
            "content_generation": "Content Generation",
            "validation": "Validation",
            "rendering": "Rendering",
            "other": "Other",
        }

        for phase, tool_names in sorted(phase_tools.items(), key=lambda x: x[0]):
            phase_desc = phase_descriptions.get(phase, phase)
            lines.append(f"### {phase_desc}")
            for tool_name in tool_names:
                lines.append(f"- {tool_name}")
            lines.append("")

        if self.presentation_structure:
            lines.append("### Current Presentation Structure")
            lines.append("```json")
            lines.append(json.dumps(self.presentation_structure, indent=2))
            lines.append("```")
            lines.append("")

        if self.accumulated_slides:
            lines.append(f"### Slides Already Designed ({len(self.accumulated_slides)} slides)")
            for i, slide in enumerate(self.ordered_slides()):
                contents = slide.get("contents") or [{}]
                runs = contents[0].get("runs") or [{}]
                title = runs[0].get("text", f"Slide {i+1}")
                layout = slide.get("layoutName", slide.get("layoutId", "Unknown"))
                lines.append(f"{i+1}. **{title}** ({layout})")
            lines.append("")

        if self.last_error:
            lines.append("### Last Error Encountered")
            lines.append(f"An error occurred previously: {self.last_error}")
            lines.append("Please review the completed work above and continue from where you left off.")
            lines.append("")

        # Add answered questions to prevent re-asking
        if self.answered_questions:
            lines.append("### Questions Already Answered")
            lines.append("The following questions have already been asked and answered by the user:")
            lines.append("")
            for q in self.answered_questions:
                question = q.get("question", "")
                answer = q.get("answer", "")
                lines.append(f"- **Question:** {question}")
                lines.append(f"  **Answer:** {answer}")
                lines.append("")
            lines.append("Do NOT ask these questions again.")

        return "\n".join(lines)

    def reset(self) -> None:
        """Reset the working state for a new session."""
        self.accumulated_slides = []
        self.presentation_structure = None
        self.selected_layouts = {}
        self.selected_images = {}
        self.current_step_index = 0
        self.pending_question = None
        self.is_paused_for_user = False
        self.has_called_finalize = False
        self.has_called_validate = False
        self.validation_clean = False
        self.validation_issues = []
        self.slides_needing_revision = []
        self.validation_attempts = 0
        self.validation_failures = 0
        self.last_rendered_path = None
        self.pending_slide_previews = []
        self.previews_published = False
        self.pending_render_result = None
        self.completed_tools = {}
        self.last_error = None
        self.answered_questions = []
        self.web_search_calls = 0
        self.researched_urls = set()
        self.consulted_before_planning = False
        self.reviewed_outline_with_user = False
        self.offered_deep_research = False


class AgentLoop:
    """
    ReAct agent loop that orchestrates presentation generation.

    The loop:
    1. Loads context (design system bundle, ingested documents, chat history)
    2. Sends system prompt + context + user message to LLM
    3. LLM responds with a thought and optionally a tool call
    4. If tool call: execute the tool, yield events, feed result back
    5. Repeat until LLM signals completion (no tool call)
    """

    MAX_ITERATIONS = 25

    # Transient provider failures (5xx, timeouts) are a wait, not a failure.
    # Rate limits are retried in the provider layer instead - see
    # _chat_with_retry for why they must not be retried in both places.
    MAX_LLM_RETRIES = 3
    RETRY_BASE_SECONDS = 5.0

    # How often the loop looks for retry notices while a call is in flight.
    # Fast enough that a countdown appears promptly, cheap enough to be
    # invisible next to calls measured in seconds.
    NOTICE_POLL_SECONDS = 0.2

    def __init__(
        self,
        db: AsyncSession,
        mode: str | None = None,
        org_id: uuid.UUID | None = None,
        detail_level: str | None = None,
        include_sources: bool | None = None,
    ) -> None:
        """
        ``mode`` is the effort level the user picked in the composer. Pass None
        to inherit the mode of the run being resumed (see _load_working_state),
        which is what the answer-a-question path does - the resume request has
        no composer of its own to read a mode from.

        ``org_id`` selects whose configured models and search backend this run
        uses. None falls back to the environment configuration, keeping
        deployments that predate the settings API working unchanged.

        ``detail_level`` is the content depth chosen in the composer, and
        follows the same None-means-inherit-on-resume convention as ``mode``.

        ``include_sources`` is the composer's "cite sources in the document"
        toggle, and follows the same convention again - the resume request has
        no composer to read it from, so None inherits it from the paused run.
        """
        self.db = db
        self.registry = get_registry()
        # Replaced in _configure() once the organisation's settings are known.
        self.llm = get_llm_provider()
        self.working_state = WorkingState()
        # Set once _load_context runs; referenced by _save_working_state so the
        # design system and research accumulated mid-run survive a pause/resume
        # cycle (issue #20) the same way WorkingState fields already do.
        self.context: dict = {}
        self._resumed_design_system: dict | None = None
        self._resumed_research: list | None = None
        self._resumed_image_catalog: list | None = None
        self.redis_client = None
        self.mode = normalize_mode(mode) if mode is not None else None
        self.detail_level = normalize_detail_level(detail_level) if detail_level is not None else None
        self.org_id = org_id
        self.include_sources = include_sources
        self.search_config: ai_settings.SearchConfig | None = None
        self._search_token = None
        self._provider_token = None
        self._citation_tokens: list = []
        # The density budget's two channels: the design system the budget is
        # read from, and how much material stood behind each slide when it was
        # written. Both are published on the run context because the validator
        # cannot be handed them as tool arguments - see run_context.
        self._design_system_token = None
        self._material_token = None
        # Set from the conversation in _run: the consultation checkpoints are
        # enforced, so the user's "just build it" has to be able to lift them.
        self.questions_declined: bool = False
        # Loaded once in _configure. The organisation's saved AI settings are
        # read by three separate decisions (chat provider, deep mode, the
        # research offer); loading them once keeps those decisions consistent
        # with each other and off the database.
        self._settings_row = None
        # Set in _load_context: whether the session has uploaded documents.
        # Grounding-in-waiting - they have not been read yet on a fresh run, so
        # _has_grounding is still False, but offering to search the web instead
        # of reading the user's own file would be the wrong question.
        self._session_has_documents: bool = False

    async def _configure(self) -> None:
        """Resolve the organisation's settings into this run's provider and mode.

        Deep mode is downgraded to simple when no search backend is usable, so
        a stale client that asks for Deep Thinking on an unconfigured
        organisation still produces a presentation rather than an error.
        """
        if self.mode is None:
            self.mode = MODE_SIMPLE
        if self.detail_level is None:
            self.detail_level = DETAIL_LEVEL_DETAILED
        if self.include_sources is None:
            self.include_sources = False

        row = None
        if self.org_id is not None:
            try:
                row = await ai_settings.load_row(self.db, self.org_id)
            except Exception as e:  # noqa: BLE001 - never fail a run over settings
                logger.error(
                    "settings_load_failed", organisation_id=str(self.org_id), error=str(e)
                )
        self._settings_row = row

        credentials = ai_settings.resolve_chat_credentials(row)
        if credentials is not None:
            try:
                self.llm = build_chat_provider(credentials)
                # Tools make their own LLM calls (slide content, planning, key
                # data, image prompts) and cannot be passed a provider through
                # the registry. Publishing it on the run context is what makes
                # the Settings choice apply to the whole generation rather than
                # only to the loop's own reasoning.
                self._provider_token = run_context.set_chat_provider(self.llm)
            except ValueError as e:
                logger.error(
                    "configured_chat_provider_unusable",
                    provider=credentials.provider,
                    error=str(e),
                )

        if self.mode == MODE_DEEP:
            config, reason = ai_settings.resolve_search_config(row)
            if config is None:
                logger.info("deep_mode_downgraded", reason=reason)
                self.mode = MODE_SIMPLE
            else:
                self.search_config = config
                self._search_token = run_context.set_search_config(config)

        logger.info(
            "agent_run_configured",
            mode=self.mode,
            organisation_id=str(self.org_id) if self.org_id else None,
            chat_provider=credentials.provider if credentials else None,
            search_backend=self.search_config.backend if self.search_config else None,
        )

    async def _get_redis_client(self) -> redis.Redis:
        """Get or create Redis client."""
        if self.redis_client is None:
            redis_url = os.environ.get("REDIS_URL", "redis://redis:6379")
            self.redis_client = redis.from_url(redis_url, decode_responses=False)
        return self.redis_client

    async def _save_working_state(self, session_id: uuid.UUID) -> None:
        """Save working state to Redis for resume after user input."""
        state_dict = {
            "accumulated_slides": self.working_state.accumulated_slides,
            "presentation_structure": self.working_state.presentation_structure,
            "selected_layouts": self.working_state.selected_layouts,
            "selected_images": self.working_state.selected_images,
            "current_step_index": self.working_state.current_step_index,
            "completed_tools": self.working_state.completed_tools,
            "has_called_finalize": self.working_state.has_called_finalize,
            "has_called_validate": self.working_state.has_called_validate,
            "validation_attempts": self.working_state.validation_attempts,
            "validation_failures": self.working_state.validation_failures,
            "validation_clean": self.working_state.validation_clean,
            "validation_issues": self.working_state.validation_issues,
            "slides_needing_revision": self.working_state.slides_needing_revision,
            "last_rendered_path": self.working_state.last_rendered_path,
            "pending_slide_previews": self.working_state.pending_slide_previews,
            "previews_published": self.working_state.previews_published,
            "pending_render_result": self.working_state.pending_render_result,
            "last_error": self.working_state.last_error,
            "pending_question": self.working_state.pending_question,
            "answered_questions": self.working_state.answered_questions,
            "web_search_calls": self.working_state.web_search_calls,
            "researched_urls": sorted(self.working_state.researched_urls),
            # Without these, the run that resumes after answering the planning
            # question would find the checkpoint unmet and ask all over again.
            "consulted_before_planning": self.working_state.consulted_before_planning,
            "reviewed_outline_with_user": self.working_state.reviewed_outline_with_user,
            "offered_deep_research": self.working_state.offered_deep_research,
            # Carried so a run paused on a question resumes at the same effort
            # level - the resume request has no mode of its own.
            "mode": self.mode,
            "include_sources": self.include_sources,
            # Issue #20: without these, resuming after ask_user_question forced
            # a re-extraction of the design system and lost all accumulated web
            # research, since only WorkingState (not context) was persisted.
            "design_system": self.context.get("design_system"),
            "research": self.context.get("research"),
            "image_catalog": self.context.get("imageCatalog"),
            "detail_level": self.detail_level,
        }
        redis_client = await self._get_redis_client()
        await redis_client.set(
            f"working_state:{session_id}",
            json.dumps(state_dict),
            ex=3600,  # Expire after 1 hour
        )
        logger.info("saved_working_state", session_id=str(session_id))

    async def _load_working_state(self, session_id: uuid.UUID) -> bool:
        """Load working state from Redis. Returns True if state was restored."""
        redis_client = await self._get_redis_client()
        state_json = await redis_client.get(f"working_state:{session_id}")
        if not state_json:
            return False

        try:
            state_dict = json.loads(state_json)
            self.working_state.accumulated_slides = state_dict.get("accumulated_slides", [])
            self.working_state.presentation_structure = state_dict.get("presentation_structure")
            # JSON round-trips dict keys as strings; slide_index is looked up
            # as an int everywhere else, so convert back on load.
            self.working_state.selected_layouts = {
                int(k): v for k, v in state_dict.get("selected_layouts", {}).items()
            }
            self.working_state.selected_images = {
                int(k): v for k, v in state_dict.get("selected_images", {}).items()
            }
            self.working_state.current_step_index = state_dict.get("current_step_index", 0)
            self.working_state.completed_tools = state_dict.get("completed_tools", {})
            self.working_state.has_called_finalize = state_dict.get("has_called_finalize", False)
            self.working_state.has_called_validate = state_dict.get("has_called_validate", False)
            self.working_state.validation_attempts = state_dict.get("validation_attempts", 0)
            self.working_state.validation_failures = state_dict.get("validation_failures", 0)
            self.working_state.validation_clean = state_dict.get("validation_clean", False)
            self.working_state.validation_issues = state_dict.get("validation_issues", [])
            self.working_state.slides_needing_revision = state_dict.get("slides_needing_revision", [])
            self.working_state.last_rendered_path = state_dict.get("last_rendered_path")
            self.working_state.pending_slide_previews = state_dict.get("pending_slide_previews", [])
            self.working_state.previews_published = state_dict.get("previews_published", False)
            self.working_state.pending_render_result = state_dict.get("pending_render_result")
            self.working_state.last_error = state_dict.get("last_error")
            self.working_state.pending_question = state_dict.get("pending_question")
            self.working_state.answered_questions = state_dict.get("answered_questions", [])
            self.working_state.web_search_calls = state_dict.get("web_search_calls", 0)
            self.working_state.researched_urls = set(state_dict.get("researched_urls", []))
            self.working_state.consulted_before_planning = state_dict.get(
                "consulted_before_planning", False
            )
            self.working_state.offered_deep_research = state_dict.get(
                "offered_deep_research", False
            )
            self.working_state.reviewed_outline_with_user = state_dict.get(
                "reviewed_outline_with_user", False
            )
            # Only inherit the saved mode when the caller did not pick one, so
            # a resumed run keeps its effort level while a fresh message is
            # free to change it.
            if self.mode is None:
                self.mode = normalize_mode(state_dict.get("mode"))
            if self.include_sources is None:
                self.include_sources = bool(state_dict.get("include_sources", False))
            # Issue #20: stashed here since context itself is rebuilt fresh by
            # _load_context after this call - _run merges these in afterwards.
            self._resumed_design_system = state_dict.get("design_system")
            self._resumed_research = state_dict.get("research")
            self._resumed_image_catalog = state_dict.get("image_catalog")
            if self.detail_level is None:
                self.detail_level = normalize_detail_level(state_dict.get("detail_level"))
            logger.info("loaded_working_state", session_id=str(session_id), tools_completed=len(state_dict.get("completed_tools", {})))
            return True
        except Exception as e:
            logger.error("failed_to_load_working_state", session_id=str(session_id), error=str(e))
            return False

    async def _clear_working_state(self, session_id: uuid.UUID) -> None:
        """Clear saved working state from Redis."""
        redis_client = await self._get_redis_client()
        await redis_client.delete(f"working_state:{session_id}")
        logger.info("cleared_working_state", session_id=str(session_id))

    async def run(
        self,
        session_id: uuid.UUID,
    ) -> AsyncIterator[dict]:
        """
        Run the full agent loop for a session.
        Yields typed SSE events as dictionaries.
        Does NOT publish to Redis – that is the caller's responsibility.

        Thin wrapper around _run so the search configuration installed on the
        run context is torn down however the loop ends - including when the
        consumer stops iterating early.
        """
        try:
            async for event in self._run(session_id):
                yield event
        finally:
            if self._search_token is not None:
                run_context.reset_search_config(self._search_token)
                self._search_token = None
            if self._provider_token is not None:
                run_context.reset_chat_provider(self._provider_token)
                self._provider_token = None
            if self._design_system_token is not None:
                run_context.reset_design_system(self._design_system_token)
                self._design_system_token = None
            if self._material_token is not None:
                run_context.reset_slide_material(self._material_token)
                self._material_token = None
            # Reset in reverse, so nested sets unwind to what was there before.
            while self._citation_tokens:
                token = self._citation_tokens.pop()
                try:
                    if token.var is run_context._include_sources:
                        run_context.reset_include_sources(token)
                    else:
                        run_context.reset_research_findings(token)
                except ValueError:
                    # Set in a different context than the one tearing down.
                    pass

    async def _run(
        self,
        session_id: uuid.UUID,
    ) -> AsyncIterator[dict]:
        import_timestamp = _iso_now()

        # Try to load previous working state (from a paused question)
        state_loaded = await self._load_working_state(session_id)

        # Only reset if no previous state was found
        if not state_loaded:
            self.working_state.reset()

        # Resolve models, search backend, and the effective mode. Must run
        # after the working state load, which can supply the mode on a resume.
        await self._configure()

        # ── Phase 1: Load context ────────────────────
        yield _event("thought", "content_analysis", {
            "content": "Loading session context and design system...",
            "timestamp": import_timestamp,
        })

        context = await self._load_context(session_id)

        # Issue #20: restore design system / research accumulated before the
        # pause, so a resumed run does not re-extract the default design system
        # or lose grounding for facts it already looked up. A template's design
        # system (just loaded above) always wins, since it reflects the current
        # session rather than a potentially-stale resume snapshot.
        if state_loaded:
            if "design_system" not in context and self._resumed_design_system:
                context["design_system"] = self._resumed_design_system
            if self._resumed_research:
                context["research"] = self._resumed_research
            if self._resumed_image_catalog:
                context["imageCatalog"] = self._resumed_image_catalog

        self.context = context

        # ── Phase 2: Build messages ──────────────────
        chat_messages = await self._build_chat_messages(session_id)
        # Never un-declined: "just build it" said once holds for the run (and
        # lets a caller driving the loop directly switch the checkpoints off).
        self.questions_declined = self.questions_declined or user_declined_questions(
            chat_messages
        )

        # Did the user answer the web-research offer? Reading it here, before
        # the tool schemas are chosen, is what lets a "yes" actually put
        # web_search on the table for the rest of this run.
        research_answer = user_answered_deep_research(chat_messages)
        if research_answer is not None:
            self.working_state.offered_deep_research = True
        if research_answer is True and self.mode != MODE_DEEP:
            if await self._enable_deep_research():
                yield _event("thought", "content_analysis", {
                    "content": (
                        "Deep research enabled - I will search the web for the "
                        "figures this deck needs and cite what I find."
                    ),
                    "timestamp": _iso_now(),
                })
            else:
                # Nothing to search with. Say so rather than silently carrying
                # on as though the user's answer had been honoured.
                yield _event("thought", "content_analysis", {
                    "content": (
                        "No web search backend is configured for this workspace, "
                        "so I cannot research this. I will write the deck without "
                        "figures I cannot verify and say so on the slides."
                    ),
                    "timestamp": _iso_now(),
                })
        # ── The web-research offer, asked by the loop rather than the model ──
        #
        # This used to be a precondition: plan_presentation_structure was
        # rejected with an instruction to go and ask. It did not work. The
        # model would call the planner anyway, take the rejection as a failed
        # tool call - which is exactly what the interface then showed the user,
        # a red error the agent had thrown at itself - and often carry on
        # without ever asking. Preconditions are the right mechanism for
        # "you cannot do that yet"; they are the wrong one for "ask this
        # specific question", because getting it asked still depends on the
        # model choosing to comply.
        #
        # Whether the deck needs looking up is a judgement, and a model makes
        # it (_judge_needs_grounding). What the model does NOT get to decide is
        # whether the user is then asked: one question, two fixed answers that
        # have to be matched verbatim on the way back. So the loop asks it
        # directly, before the first tool call of the run, and pauses. Nothing
        # can skip it because nothing is consulted about it.
        offer, missing_figures = False, []
        async for kind, payload in self._with_retry_notices(
            self._should_offer_deep_research(chat_messages), "content_analysis"
        ):
            if kind == "event":
                yield payload
            else:
                offer, missing_figures = payload
        if offer:
            question = self._deep_research_question(missing_figures)
            self.working_state.offered_deep_research = True
            self.working_state.is_paused_for_user = True
            self.working_state.pending_question = question
            yield _event("question", "user_input", {
                "question": question["question"],
                "options": question["options"],
                "questions": question["questions"],
                "context": question["context"],
                "timestamp": _iso_now(),
            })
            await self._save_working_state(session_id)
            yield _event("done", "user_input", {
                "content": f"Waiting for user input: {question['question']}",
                "paused": True,
                "timestamp": _iso_now(),
            })
            return

        # Publish the citation settings for the tools, after the research
        # answer has been read - a run that just switched to deep research is
        # about to have findings worth citing. finalize_and_render_pptx reads
        # both when it puts the reference trail into the .pptx itself; the
        # findings are re-published as research accumulates, so the list is
        # whatever the run actually gathered by the time it renders.
        self._citation_tokens.append(
            run_context.set_include_sources(bool(self.include_sources))
        )
        self._citation_tokens.append(
            run_context.set_research_findings(context.get("research"))
        )

        # The density budget's channels, opened for the whole run. The design
        # system is republished when extraction returns a real one; the
        # material map starts empty and is filled a slide at a time as each
        # one is designed.
        if self._design_system_token is None:
            self._design_system_token = run_context.set_design_system(
                context.get("design_system")
            )
        if self._material_token is None:
            self._material_token = run_context.set_slide_material({})

        # ── Phase 3: ReAct loop ──────────────────────
        tool_schemas = self._tool_schemas_for_mode()
        current_phase = "content_analysis"

        # Function to rebuild messages with updated system prompt including completed work
        def rebuild_messages() -> list[dict]:
            system_prompt = self._build_system_prompt(context, self.working_state)
            return [{"role": "system", "content": system_prompt}] + chat_messages

        messages = rebuild_messages()

        for iteration in range(self.MAX_ITERATIONS):
            # Call LLM
            start_time = time.time()
            response = None
            try:
                async for kind, payload in self._with_retry_notices(
                    self._chat_with_retry(
                        messages=messages,
                        tools=tool_schemas if tool_schemas else None,
                    ),
                    current_phase,
                ):
                    if kind == "event":
                        yield payload
                    else:
                        response = payload
            except Exception as e:
                logger.error("llm_call_failed", error=str(e), iteration=iteration)
                yield _event("error", "content_analysis", {
                    "message": f"LLM call failed: {str(e)}",
                    "timestamp": _iso_now(),
                })
                break

            duration_ms = int((time.time() - start_time) * 1000)

            # Parse response
            assistant_message = response.get("message", {})
            tool_calls = response.get("tool_calls", [])

            # Yield thought event if there is content
            if assistant_message.get("content"):
                yield _event("thought", current_phase, {
                    "content": assistant_message["content"],
                    "durationMs": duration_ms,
                    "timestamp": _iso_now(),
                })

            # If no tool calls, check if we're actually done
            if not tool_calls:
                # MANDATORY: can only exit once finalize_and_render_pptx has
                # rendered a file AND validate_rendered_output has actually
                # checked it - unless the validation retry cap has been hit,
                # in which case further validation attempts are blocked (see
                # _check_precondition) and the loop must be allowed to exit
                # with whatever output it has rather than stall forever.
                # A validation run that FOUND problems does not satisfy the
                # gate. Requiring only that validation had run meant a deck
                # with a chart hanging off the page could still be announced
                # as complete and offered for download - the issues were
                # reported to the model and then simply not acted on. The
                # retry cap is still the escape hatch for a defect that
                # genuinely cannot be fixed.
                at_validation_cap = (
                    self.working_state.validation_attempts >= self.working_state.MAX_VALIDATION_ATTEMPTS
                    or self.working_state.validation_failures >= self.working_state.MAX_VALIDATION_FAILURES
                )
                validation_satisfied = self.working_state.validation_clean or at_validation_cap
                if not self.working_state.has_called_finalize or not validation_satisfied:
                    # Agent is trying to exit without completing the task
                    # Feed back an instruction to continue
                    logger.warning(
                        "agent_exited_early",
                        session_id=str(session_id),
                        iteration=iteration,
                        message="LLM responded without tool calls before finalize/validate completed",
                    )
                    if not self.working_state.has_called_finalize:
                        next_step_instruction = "You MUST continue using tools until you call finalize_and_render_pptx."
                    elif not self.working_state.has_called_validate:
                        next_step_instruction = (
                            "finalize_and_render_pptx has run, but you have not yet called "
                            "validate_rendered_output to check the actual rendered output. Call it now."
                        )
                    else:
                        issue_summary = self._describe_validation_issues()
                        targets = self.working_state.slides_needing_revision
                        if targets:
                            scope = (
                                "Edit ONLY slide(s) "
                                + ", ".join(str(i + 1) for i in targets)
                                + " with design_slide_content. Every other slide already "
                                "renders correctly and is carried over unchanged - do not "
                                "redesign them, and do not replan the deck."
                            )
                        else:
                            scope = (
                                "Most of the deck is affected, so redesign the slides with "
                                "design_slide_content as needed."
                            )
                        next_step_instruction = (
                            "validate_rendered_output found problems in the rendered file that you "
                            f"have not fixed yet:\n{issue_summary}\n"
                            f"Do NOT tell the user the presentation is ready. {scope} Shorten the "
                            "content, give an oversized chart or table a smaller region, or move it "
                            "onto its own slide - then call finalize_and_render_pptx again and "
                            "re-run validate_rendered_output."
                        )
                    # Add a system message forcing the agent to continue
                    messages.append({
                        "role": "system",
                        "content": (
                            f"You have NOT completed the task. {next_step_instruction} "
                            "Do not stop with just text responses. "
                            "If you need user input, use the ask_user_question tool (available in your tools). "
                            "Otherwise, continue with the next step in the presentation generation workflow."
                        ),
                    })
                    continue  # Skip the break and continue the loop

                # Safety net: the run is ending, so anything still staged is
                # final by definition. Normally validate_rendered_output has
                # already flushed these; this covers the paths where it hit
                # its retry cap or errored without reaching the flush.
                for preview_event in self._flush_slide_previews():
                    yield preview_event

                # Final thought (optional) - only if we actually called finalize
                yield _event("thought", "complete", {
                    "content": "Agent completed reasoning. Presentation finalized.",
                    "timestamp": _iso_now(),
                })
                # No done event – caller will handle final persistence and "done"
                break

            # Append assistant message to conversation, INCLUDING tool_calls (critical fix)
            assistant_msg_for_history = dict(assistant_message)
            if tool_calls:
                assistant_msg_for_history["tool_calls"] = tool_calls
            messages.append(assistant_msg_for_history)

            # Execute tool calls (parallel where independent)
            for tool_call in tool_calls:
                fn_name = tool_call.get("function", {}).get("name", "")
                fn_args_str = tool_call.get("function", {}).get("arguments", "{}")
                call_id = tool_call.get("id", str(uuid.uuid4()))

                # Update phase based on tool name
                if fn_name in TOOL_PHASES:
                    current_phase = TOOL_PHASES[fn_name]

                try:
                    fn_args = json.loads(fn_args_str)
                except json.JSONDecodeError:
                    fn_args = {}

                # Auto-supply large JSON blobs the loop already tracks in
                # context/working_state, rather than trusting the LLM to
                # reproduce them verbatim as an escaped JSON string - a
                # parameter shape LLMs reliably mis-escape once it's larger
                # than a couple of fields (observed failure: "Expecting
                # property name enclosed in double quotes", from the model
                # double-escaping the embedded quotes). The LLM does not need
                # to pass these at all; whatever it sends is overridden. Also
                # supplies the composer-chosen content depth the same way.
                design_system = context.get("design_system") or {}
                if fn_name == "select_layout":
                    fn_args["available_layouts"] = json.dumps(design_system.get("layouts", []))
                elif fn_name in ("analyze_document", "extract_key_data"):
                    fn_args["detail_level"] = self.detail_level
                elif fn_name == "plan_presentation_structure":
                    fn_args["design_system_summary"] = json.dumps(
                        _project_design_system(fn_name, design_system)
                    )
                    fn_args["detail_level"] = self.detail_level
                elif fn_name == "design_slide_content":
                    fn_args["design_system_context"] = json.dumps(
                        _project_design_system(fn_name, design_system)
                    )
                    fn_args["detail_level"] = self.detail_level
                    slide_index = fn_args.get("slide_index")
                    if slide_index in self.working_state.selected_layouts:
                        fn_args["layout_info"] = json.dumps(
                            self.working_state.selected_layouts[slide_index]
                        )
                    if slide_index in self.working_state.selected_images:
                        fn_args["selected_image_context"] = json.dumps(
                            self.working_state.selected_images[slide_index]
                        )
                    prepared = self.working_state.prepared_visuals.get(slide_index)
                    if prepared:
                        # What the visual actually shows, not just that one
                        # exists. Passing only the type produced copy about a
                        # different subject entirely - a Starbucks deck whose
                        # donut read Beverages/Food/Merchandise carried bullets
                        # about the "technology" and "healthcare" segments,
                        # because the model had a vague outline, no figures,
                        # and filled the gap by inventing some.
                        fn_args["prepared_visuals"] = json.dumps(prepared)
                elif fn_name == "finalize_and_render_pptx":
                    # Needed so generated tables/charts can be styled from
                    # the template's own brand colors instead of a generic
                    # default - see table_style.resolve_table_style.
                    fn_args["design_system_json"] = json.dumps(design_system)
                elif fn_name == "select_source_image":
                    # Metadata only (id + context) - never the raw image
                    # bytes/URL, so the LLM's decision never has to carry an
                    # image payload through its own output. See
                    # _resolve_selected_image below for how the chosen id is
                    # turned back into real data server-side.
                    catalog = context.get("imageCatalog") or []
                    fn_args["catalog_json"] = json.dumps([
                        {"id": entry.get("id"), "context": entry.get("context", "")}
                        for entry in catalog if entry.get("id")
                    ])
                elif fn_name == "validate_rendered_output":
                    # The loop knows the true rendered path and session_id
                    # with certainty - overriding whatever the LLM sends
                    # avoids validating a stale/wrong/hallucinated path.
                    if self.working_state.last_rendered_path:
                        fn_args["pptx_path"] = self.working_state.last_rendered_path
                    fn_args["session_id"] = str(session_id)
                    fn_args["detail_level"] = self.detail_level
                    # The content checks read the deck's own JSON, which the
                    # loop already holds - asking the model to hand it back
                    # would risk it being paraphrased on the way.
                    fn_args["slides_json"] = self.working_state.get_slides_json()

                # Narrate the validation step explicitly, server-authored so
                # it's always present and consistent rather than depending
                # on the LLM choosing to describe it.
                if fn_name == "validate_rendered_output":
                    narration = (
                        "Validating the rendered output with LibreOffice - checking the actual "
                        "rendered slides for text that overflows its placeholder or overlaps "
                        "other content..."
                    )
                    if self.detail_level == DETAIL_LEVEL_COMPREHENSIVE:
                        narration += " Also reviewing slide screenshots for visual polish."
                    yield _event("thought", "validation", {
                        "content": narration,
                        "timestamp": _iso_now(),
                    })

                # Yield tool_call event
                yield _event("tool_call", current_phase, {
                    "toolName": fn_name,
                    "toolArgs": fn_args,
                    "callId": call_id,
                    "timestamp": _iso_now(),
                })

                # Execute tool
                tool_start = time.time()
                tool_result = None
                precondition_error = self._check_precondition(fn_name, fn_args)
                if precondition_error:
                    tool_result = ToolResult(success=False, error=precondition_error)
                elif (
                    fn_name == "web_search"
                    and self.working_state.web_search_calls >= self._max_web_searches()
                ):
                    # Cap repeated web searches so the agent cannot loop on
                    # low-quality results. After the cap, return a gentle
                    # "use existing research" result instead of executing.
                    tool_result = ToolResult(
                        success=True,
                        data={
                            "query": fn_args.get("query", ""),
                            "results": [],
                            "result_count": 0,
                            "note": (
                                "Maximum number of web searches reached. Use the Research "
                                "Findings already in your context to write the content, and "
                                "do not call web_search again. Proceed to the next step."
                            ),
                        },
                    )
                else:
                    if fn_name == "finalize_and_render_pptx" and self.working_state.accumulated_slides:
                        # Trust the slides actually designed via design_slide_content
                        # over whatever the LLM re-typed as the argument string - the
                        # latter is a verbatim re-serialization of the whole deck and
                        # is the single biggest truncation/drift risk on multi-slide
                        # presentations.
                        fn_args["slides_json"] = self.working_state.get_slides_json()
                    if fn_name == "web_search":
                        self.working_state.web_search_calls += 1
                    # Tools make their own LLM calls, so a rate limit can strike
                    # inside one - narrate those waits the same way.
                    async for kind, payload in self._with_retry_notices(
                        self._execute_tool(fn_name, fn_args), current_phase
                    ):
                        if kind == "event":
                            yield payload
                        else:
                            tool_result = payload
                tool_duration = int((time.time() - tool_start) * 1000)

                # Extract sources from tool result data (URLs that can be displayed to user)
                sources = []
                if tool_result.success and tool_result.data:
                    sources = self._extract_sources(tool_result.data)

                # Yield tool_result event. The summary is what the feed leads
                # with; the raw result stays on the event behind a disclosure.
                yield _event("tool_result", current_phase, {
                    "toolName": fn_name,
                    "callId": call_id,
                    "result": tool_result.data if tool_result.success else tool_result.error,
                    "summary": summarize_tool_result(
                        fn_name, fn_args, tool_result.data, tool_result.success
                    ),
                    "durationMs": tool_duration,
                    "success": tool_result.success,
                    "tokenCount": tool_result.token_count,
                    "timestamp": _iso_now(),
                    "sources": sources if sources else None,
                })

                # Stage the per-slide previews produced by this render. They are
                # NOT published yet: validate_rendered_output runs next and may
                # send slides back for redesign, and a slide that is about to
                # change is not "finalized". _flush_slide_previews publishes
                # them once validation settles.
                if fn_name == "finalize_and_render_pptx" and tool_result.success:
                    self.working_state.has_called_finalize = True
                    self.working_state.last_rendered_path = tool_result.data.get("filePath")
                    preview_urls = tool_result.data.get("previewUrls", [])

                    # Preview page N is the Nth slide of the RENDERED deck, so
                    # it has to be matched against the same ordering the
                    # renderer was given - not the order the slides happened to
                    # be designed in.
                    rendered_order = self.working_state.ordered_slides()
                    self.working_state.pending_slide_previews = [
                        {
                            "slideIndex": slide_idx,
                            "slideData": (
                                rendered_order[slide_idx]
                                if slide_idx < len(rendered_order)
                                else None
                            ),
                            "previewUrl": preview_url,
                        }
                        for slide_idx, preview_url in enumerate(preview_urls)
                    ]
                    # A re-render supersedes anything published for the previous
                    # attempt, so the fresh set must be publishable again.
                    self.working_state.previews_published = False
                    self.working_state.pending_render_result = dict(tool_result.data or {})

                    # Working state is now cleared once validate_rendered_output
                    # has actually run (or hit its retry cap) below, not here -
                    # "presentation is complete" means checked, not just rendered.

                if fn_name == "validate_rendered_output" and precondition_error is None:
                    # precondition_error is None only when the tool actually
                    # executed - a call blocked for not having finalized yet,
                    # or blocked by the retry cap below, must not count as an
                    # attempt or be mistaken for "validation done".
                    self.working_state.has_called_validate = True

                    if tool_result.success:
                        # Only a completed check spends the revision budget. A
                        # run that never got to inspect anything (LibreOffice
                        # failed to convert, the provider rate-limited the
                        # redesign that preceded it) says nothing about the
                        # deck, and burning an attempt on it used to push a
                        # still-broken deck to the cap and out the door.
                        self.working_state.validation_attempts += 1
                        issues = (tool_result.data or {}).get("issues", [])
                        self.working_state.validation_issues = issues
                        self.working_state.validation_clean = not issues
                        self.working_state.slides_needing_revision = (
                            self.working_state.compute_slides_needing_revision(issues)
                        )
                        at_cap = (
                            self.working_state.validation_attempts
                            >= self.working_state.MAX_VALIDATION_ATTEMPTS
                        )
                        validation_done = not issues or at_cap
                    else:
                        self.working_state.validation_failures += 1
                        validation_done = (
                            self.working_state.validation_failures
                            >= self.working_state.MAX_VALIDATION_FAILURES
                        )
                    if validation_done:
                        # The deck will not change again this run, so the
                        # slides are genuinely final - publish their previews.
                        for preview_event in self._flush_slide_previews():
                            yield preview_event
                        await self._clear_working_state(session_id)

                # If ask_user_question was called, emit question event and pause
                if fn_name == "ask_user_question" and tool_result.success:
                    self.working_state.is_paused_for_user = True
                    self.working_state.pending_question = tool_result.data
                    # Which checkpoint this satisfied is decided by where the
                    # run is, not by what the model says it is doing: before a
                    # plan exists the question is the planning consultation,
                    # after it the outline review. The research offer is not in
                    # this list - the loop asks that one itself, before the
                    # first LLM call, and returns.
                    if self.working_state.presentation_structure:
                        self.working_state.reviewed_outline_with_user = True
                    else:
                        self.working_state.consulted_before_planning = True
                    yield _event("question", "user_input", {
                        "question": tool_result.data.get("question"),
                        "options": tool_result.data.get("options", []),
                        "questions": tool_result.data.get("questions", []),
                        "context": tool_result.data.get("context", ""),
                        "timestamp": _iso_now(),
                    })
                    # Save working state before pausing
                    await self._save_working_state(session_id)
                    # Yield done event to signal the workflow is paused
                    yield _event("done", "user_input", {
                        "content": f"Waiting for user input: {tool_result.data.get('question')}",
                        "paused": True,
                        "timestamp": _iso_now(),
                    })
                    # Stop the whole run, not just this tool-calls batch. A
                    # bare `break` here only exited the inner "for tool_call"
                    # loop - the outer "for iteration" loop then continued to
                    # its next pass and called the LLM again immediately,
                    # with the just-appended assistant message's tool_calls
                    # still unanswered (ask_user_question's own tool_call_id
                    # never got a paired tool-role response, since that
                    # append happens right after this block and was never
                    # reached). Strict providers (OpenAI) reject that
                    # outright: "An assistant message with 'tool_calls' must
                    # be followed by tool messages responding to each
                    # 'tool_call_id'" - visible as an error immediately after
                    # the question. Lenient providers might not reject it,
                    # but the run was never supposed to keep going here
                    # regardless - it must wait for the resume request.
                    return

                # Feed result back to conversation (tool role message)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(
                        tool_result.data if tool_result.success else {"error": tool_result.error}
                    ),
                })

                # Record completed tool for context preservation
                if tool_result.success:
                    self.working_state.record_completed_tool(fn_name, fn_args, tool_result.data or {})

                    # Special handling for tools that produce important state.
                    # Both tools return their payload directly as tool_result.data
                    # (not nested under a "structure"/"slide" key) - a mismatch
                    # here previously left presentation_structure/accumulated_slides
                    # empty for the whole run, which is why finalize_and_render_pptx
                    # had no tracked state to fall back on and the "Slides Already
                    # Designed" prompt recap never appeared.
                    if fn_name == "plan_presentation_structure":
                        if tool_result.data and "totalSlides" in tool_result.data:
                            self.working_state.presentation_structure = tool_result.data

                    if fn_name == "design_slide_content":
                        if tool_result.data and "slideIndex" in tool_result.data:
                            self.working_state.resolve_and_add_slide(tool_result.data)

                    # Recorded by slide_index so design_slide_content's
                    # layout_info can be auto-supplied later (see the
                    # tool-dispatch injection above) instead of requiring the
                    # LLM to copy this object verbatim into the next call.
                    if fn_name == "select_layout" and tool_result.data:
                        slide_index = fn_args.get("slide_index")
                        if slide_index is not None:
                            self.working_state.selected_layouts[slide_index] = tool_result.data

                    # Persist the design system into context so it survives
                    # message rebuilds and the LLM does not re-extract it.
                    # This mirrors how uploaded templates inject their design
                    # system, and applies to the built-in default too.
                    if fn_name == "extract_template_design_system" and tool_result.data:
                        context["design_system"] = tool_result.data
                        # And onto the run context, where the content designer
                        # and the validator read the density budget from it.
                        run_context.set_design_system(tool_result.data)

                    # Accumulate web_search results into context so they persist
                    # across rebuilds - the LLM can ground all slides on them
                    # without re-searching, and the sources stay available.
                    if fn_name == "web_search" and tool_result.data and tool_result.data.get("results"):
                        research = context.setdefault("research", [])
                        image_catalog = context.setdefault("imageCatalog", [])
                        for item in tool_result.data["results"]:
                            if item.get("url"):
                                self.working_state.researched_urls.add(item["url"])
                            research.append({
                                "query": tool_result.data.get("query", ""),
                                "title": item.get("title", ""),
                                "url": item.get("url", ""),
                                "snippet": item.get("snippet", ""),
                                # Best-effort, and usually absent: only some
                                # backends date a page. The reference list in
                                # the rendered deck prints it when present, so
                                # a reader can tell how current a figure is.
                                "published": item.get("published", ""),
                            })
                            # Candidate images a search backend found on this
                            # page (currently only Firecrawl, whose scraped
                            # markdown naturally ties an image to the page it
                            # came from) - reused, never generated. See
                            # select_source_image for the confidence gate.
                            for img_url in (item.get("images") or [])[:2]:
                                image_catalog.append({
                                    "id": f"web-{len(image_catalog)}-{img_url}",
                                    "sourceType": "web",
                                    "data": img_url,
                                    "context": f"{item.get('title', '')}: {item.get('snippet', '')}"[:300],
                                })
                        # Bound the research context to avoid unbounded prompt
                        # growth. The bound has to clear several searches' worth
                        # of results, or the breadth the run was just made to go
                        # and find is thrown away before it can be written from:
                        # at 15, a fourth search silently evicted the first one.
                        if len(research) > MAX_RESEARCH_IN_CONTEXT:
                            context["research"] = research[-MAX_RESEARCH_IN_CONTEXT:]
                        # Keep what the renderer will cite in step with what
                        # the run has actually found.
                        self._citation_tokens.append(
                            run_context.set_research_findings(context["research"])
                        )
                        if len(image_catalog) > 20:
                            context["imageCatalog"] = image_catalog[-20:]

                    # Catalog embedded images extracted from the user's own
                    # documents (PDF/DOCX) - see analyze_document. Reused,
                    # never generated.
                    if fn_name == "analyze_document" and tool_result.data and tool_result.data.get("images"):
                        image_catalog = context.setdefault("imageCatalog", [])
                        image_catalog.extend(tool_result.data["images"])
                        if len(image_catalog) > 20:
                            context["imageCatalog"] = image_catalog[-20:]

                    # Charts/tables/metric rows are attached to their slide
                    # here rather than handed back for the model to re-state.
                    if (fn_name in BUILD_VISUAL_TOOLS
                            or fn_name in BLOCK_BUILDER_TOOLS) and tool_result.data:
                        target = tool_result.data.get("slideIndex")
                        block = tool_result.data.get("content")
                        if target is not None and block:
                            slots = self.working_state.prepared_visuals.setdefault(target, [])
                            # One block per type *per region*: rebuilding the
                            # stat callouts in the right rail must not discard
                            # the ones in the left.
                            slot_key = (block.get("type"), block.get("regionId"))
                            slots[:] = [
                                existing for existing in slots
                                if (existing.get("type"),
                                    existing.get("regionId")) != slot_key
                            ]
                            slots.append(block)

                    # draw_primitives places marks rather than content blocks,
                    # so they attach to the slide's own primitives list.
                    if fn_name == "draw_primitives" and tool_result.data:
                        target = tool_result.data.get("slideIndex")
                        marks = tool_result.data.get("primitives")
                        if target is not None and marks:
                            self.working_state.prepared_primitives[target] = list(marks)

                    # Resolve select_source_image's chosen id back to the
                    # real image data (the LLM only ever saw lightweight
                    # metadata - see the tool-dispatch injection above) and
                    # stash it for design_slide_content to reference on the
                    # next call for this slide.
                    if fn_name == "select_source_image" and tool_result.data and tool_result.data.get("selected"):
                        slide_index = tool_result.data.get("slideIndex")
                        image_id = tool_result.data.get("imageId")
                        catalog_entry = next(
                            (e for e in context.get("imageCatalog", []) if e.get("id") == image_id),
                            None,
                        )
                        if slide_index is not None and catalog_entry:
                            self.working_state.selected_images[slide_index] = {
                                "source": "url" if catalog_entry.get("sourceType") == "web" else "base64",
                                "data": catalog_entry.get("data", ""),
                                "altText": (catalog_entry.get("context") or "")[:200],
                            }

                    # Update the system prompt in place so the LLM sees the
                    # latest completed-work summary, WITHOUT discarding the
                    # in-progress tool conversation. Previously this called
                    # rebuild_messages() which reset messages to
                    # [system, chat_messages] and re-appended only the latest
                    # tool result - that wiped all prior assistant tool_calls
                    # and tool results, causing the LLM to lose context and
                    # re-call tools (e.g. planning or extracting repeatedly).
                    # The tool result was already appended above (line 482), so
                    # we only need to refresh messages[0].
                    if messages and messages[0].get("role") == "system":
                        messages[0] = {
                            "role": "system",
                            "content": self._build_system_prompt(context, self.working_state),
                        }
                else:
                    # Record error for context
                    self.working_state.last_error = tool_result.error
                    # Refresh the system prompt in place (preserve history).
                    if messages and messages[0].get("role") == "system":
                        messages[0] = {
                            "role": "system",
                            "content": self._build_system_prompt(context, self.working_state),
                        }

                logger.info(
                    "tool_executed",
                    tool_name=fn_name,
                    success=tool_result.success,
                    duration_ms=tool_duration,
                )

        # If the loop exhausted all iterations without finalizing (and is not
        # paused waiting for user input), emit a clear error so the UI shows a
        # failure instead of hanging silently.
        if not self.working_state.has_called_finalize and not self.working_state.is_paused_for_user:
            logger.warning(
                "agent_iteration_cap_reached",
                iterations=self.MAX_ITERATIONS,
                session_id=str(session_id),
            )
            yield _event("error", "content_analysis", {
                "message": (
                    f"Generation did not complete within {self.MAX_ITERATIONS} steps. "
                    "Please try simplifying your request or try again."
                ),
                "code": "iteration_cap_reached",
                "timestamp": _iso_now(),
            })

    def _research_budget(self) -> tuple[int, int]:
        """(floor, cap) on this run's number of web searches."""
        return RESEARCH_BUDGET.get(
            self.detail_level or DETAIL_LEVEL_DETAILED,
            RESEARCH_BUDGET[DETAIL_LEVEL_DETAILED],
        )

    def _min_web_searches(self) -> int:
        return self._research_budget()[0]

    def _max_web_searches(self) -> int:
        return self._research_budget()[1]

    def _research_is_thin(self) -> bool:
        """True while a Deep Thinking run has not looked around enough yet.

        Counted in attempts rather than successes, so a search backend that is
        down or returning nothing cannot stall the run - each attempt spends
        the budget either way, and the cap is above the floor.
        """
        if self.mode != MODE_DEEP:
            return False
        ws = self.working_state
        if ws.web_search_calls >= self._max_web_searches():
            return False
        return (
            ws.web_search_calls < self._min_web_searches()
            or len(ws.researched_urls) < RESEARCH_MIN_SOURCES
        )

    def _has_grounding(self) -> bool:
        """
        Whether anything in this run could support a figure.

        Research results or an analysed document count; the model's own memory
        does not, which is the whole point - it is what produced a charted
        $40.0B for a company that reported ~$37B.
        """
        ws = self.working_state
        # Anything the web turned up, this run or before the pause.
        if ws.researched_urls or self.context.get("research"):
            return True
        # Anything read out of the user's own uploads.
        return any(
            ws.has_completed_tool(tool)
            for tool in ("analyze_document", "extract_key_data")
        )

    async def _judge_needs_grounding(
        self, chat_messages: list[dict]
    ) -> tuple[bool, list[str]]:
        """Ask a model whether this deck stands on facts it would have to look up.

        Returns ``(needs_research, figures)``. On any failure - the provider is
        down, the reply is not JSON - returns ``(False, [])``: an offer that
        cannot be judged is not made, because the alternative is interrupting
        every run with a question nothing supports.

        Goes through _chat_with_retry rather than calling the provider directly,
        so a transient 5xx or a rate-limit wait costs a retry instead of
        silently costing the checkpoint.
        """
        try:
            response = await self._chat_with_retry(
                messages=_judge_messages(chat_messages), tools=None
            )
        except Exception as e:  # noqa: BLE001 - a judgement is never worth a run
            logger.warning("grounding_judge_failed", error=str(e))
            return False, []

        content = ((response or {}).get("message") or {}).get("content") or ""
        verdict = extract_json_object(content)
        if not verdict:
            logger.warning("grounding_judge_unparseable", reply=content[:200])
            return False, []

        needs = bool(verdict.get("needs_research"))
        figures = [
            str(f).strip()
            for f in (verdict.get("figures") or [])
            if str(f).strip()
        ][:4]
        logger.info("grounding_judged", needs_research=needs, figures=figures)
        return needs, figures

    async def _should_offer_deep_research(
        self, chat_messages: list[dict]
    ) -> tuple[bool, list[str]]:
        """
        Whether to stop and ask before planning a factual deck with nothing
        behind it, and which figures to say are missing.

        Only in simple mode (deep mode already researches), only when the run
        has no grounding at all, and - the point that makes the offer honest -
        only when there is a search backend to switch on. Offering to research
        on a workspace with no search model configured is offering something
        that cannot happen. A user who said "just build it" is not interrupted,
        and neither is a session that uploaded a document: the answer there is
        to read the file, not to go looking on the web.

        The judgement itself is the last step, because it costs an LLM call and
        everything above it is free.
        """
        if self.mode == MODE_DEEP:
            return False, []
        if self.working_state.offered_deep_research or self.questions_declined:
            return False, []
        if self._has_grounding() or self._session_has_documents:
            return False, []
        # Too late to be useful once the deck is being written: the point of
        # the offer is to change where the numbers come from, not to interrupt
        # a run that already has them on slides.
        if self.working_state.accumulated_slides:
            return False, []
        config, reason = ai_settings.resolve_search_config(self._settings_row)
        if config is None:
            logger.info("deep_research_offer_skipped", reason=reason)
            return False, []
        needs, figures = await self._judge_needs_grounding(chat_messages)
        return needs, figures

    def _deep_research_question(self, figures: list[str]) -> dict:
        """The web-research offer, in the shape ask_user_question would return.

        Authored here rather than by the model. The offer has exactly one
        sensible wording and exactly two answers, both of which have to be
        matched verbatim afterwards, so there is nothing for a model to decide
        and everything for it to get wrong. What the judge does contribute is
        the specifics - naming the figures beats saying "some figures".
        """
        text = "This deck needs figures I have no source for. Research them on the web?"
        missing = ""
        if figures:
            missing = (
                " For this deck that means "
                + ", ".join(figures[:-1] + [f"and {figures[-1]}"] if len(figures) > 1
                            else figures)
                + "."
            )
        return {
            "question": text,
            "options": [DEEP_RESEARCH_YES, DEEP_RESEARCH_NO],
            "questions": [{"question": text, "options": [DEEP_RESEARCH_YES, DEEP_RESEARCH_NO]}],
            "context": (
                "You have asked for a deck built on facts and figures, and this run "
                "has nothing to base them on: web search is off and no document was "
                "uploaded." + missing + " I can still write it from memory, but anything "
                "I put in a chart would be a recollection presented as a measurement - "
                "which is how a wrong number ends up looking sourced. Turning on web "
                "research lets me look the figures up and cite where each one came from."
            ),
        }

    async def _enable_deep_research(self) -> bool:
        """
        Switch a running simple-mode run over to web research.

        Returns False when no search backend is configured for the
        organisation, in which case the run stays in simple mode and the model
        is told to say on the slide that the figures are unverified rather than
        chart them as though they were not.
        """
        config, reason = ai_settings.resolve_search_config(self._settings_row)
        if config is None:
            logger.info("deep_research_unavailable", reason=reason)
            return False

        self.search_config = config
        if self._search_token is not None:
            run_context.reset_search_config(self._search_token)
        self._search_token = run_context.set_search_config(config)
        self.mode = MODE_DEEP
        logger.info("deep_research_enabled_mid_run", backend=config.backend)
        return True

    def _check_precondition(self, fn_name: str, fn_args: dict) -> str | None:
        """Block a tool call that is out of order, returning a corrective
        error message the LLM sees as the tool's result (same mechanism as the
        web_search cap below). This is what actually stops the loop from
        "jumping around" - illegal transitions never reach the tool at all,
        instead of being merely discouraged in the system prompt.

        Deliberately not a rigid phase counter: tools may still interleave
        (e.g. select_layout/design_slide_content per slide, or ask_user_question
        at any point) - only the transitions that would silently corrupt state
        are blocked.
        """
        ws = self.working_state

        if fn_name == "plan_presentation_structure" and ws.accumulated_slides:
            return (
                "Presentation structure cannot be replanned - slide content has "
                "already been designed using the current structure. If the "
                "structure genuinely needs to change, ask the user with "
                "ask_user_question before restructuring; otherwise continue "
                "designing the remaining slides in the existing plan."
            )

        # ── The two consultation checkpoints ──────────────────────────────
        #
        # The prompt has asked for these all along and the model routinely
        # skipped both, going from "here is a topic" to a finished deck without
        # a word to the user. Enforcing them here is the difference between a
        # suggestion and a rule: the planning call simply does not go through
        # until the user has been consulted.
        if fn_name == "plan_presentation_structure" and self._research_is_thin():
            return (
                f"Not enough research yet: {ws.web_search_calls} search(es) so far, "
                f"turning up {len(ws.researched_urls)} distinct source(s). A deck is only "
                "as trustworthy as the breadth behind it, and one query answers one facet "
                f"of the topic. Run at least {self._min_web_searches()} web_search calls "
                f"covering DIFFERENT facets - the subject itself, the specific figures the "
                "deck needs, recent developments, and any counterpoint or context - until "
                f"you have {RESEARCH_MIN_SOURCES}+ distinct sources, then plan."
            )

        if (
            fn_name == "plan_presentation_structure"
            and not ws.consulted_before_planning
            and not self.questions_declined
        ):
            return (
                "Consult the user before planning. Call ask_user_question ONCE with a "
                "consolidated set of questions covering what you are actually unsure "
                "about - typically the audience, the angle or sections to focus on, the "
                "depth/slide count, and (when no template was uploaded) the visual style. "
                "Give every question 2-5 concrete options drawn from THIS request and the "
                "material you have read, not generic placeholders, so the user can answer "
                "in one tap. Plan the deck once they have answered."
            )

        if (
            fn_name in ("select_layout", "design_slide_content",
                        *BUILD_VISUAL_TOOLS, *BLOCK_BUILDER_TOOLS)
            and ws.presentation_structure
            and not ws.reviewed_outline_with_user
            and not self.questions_declined
        ):
            return (
                "Show the user the outline before drafting any slides. Call "
                "ask_user_question with the planned slide titles laid out in the call's "
                "top-level 'context' field (one line per slide, numbered), and ask whether "
                "to go ahead. Every option must be an answer rather than a prompt for one: "
                "'Go ahead as planned' plus the 2-3 specific adjustments most likely to be "
                "wanted for THIS deck, naming its actual slides (merge these two, add one "
                "on X, cut Y and go deeper on Z). Never offer 'No, I want changes' - it "
                "tells you nothing. Draft the slides once they have answered."
            )

        # The backstop for a user who declined research: the deck still gets
        # built, but a number nobody can check does not get an axis and a
        # legend. Text can be hedged on the slide; a chart cannot.
        if fn_name in BUILD_VISUAL_TOOLS and not self._has_grounding():
            return (
                "There is no source behind this figure - no research was run and no "
                "document was uploaded, so any number here would come from memory. "
                "A chart or table presents it as measured, which is worse than "
                "writing no visual at all. Put what you know in words on the slide, "
                "say plainly that the figures are unverified and what would confirm "
                "them, and carry on with the rest of the deck."
            )

        if fn_name in ("select_layout", "design_slide_content", "select_source_image"):
            if not ws.presentation_structure:
                return (
                    f"{fn_name} requires a presentation structure. Call "
                    "plan_presentation_structure first."
                )

        if fn_name == "design_slide_content":
            slide_index = fn_args.get("slide_index")

            # After a validation pass flagged specific slides, only those get
            # redesigned. Regenerating a slide that already renders correctly
            # costs an LLM call and risks rewriting copy the user was happy
            # with, to fix a problem it does not have.
            #
            # An index that has not been designed yet is always allowed: one of
            # the fixes we actively suggest is moving an oversized chart onto a
            # slide of its own, which means authoring a new one.
            already_designed = {
                slide.get("slideIndex") for slide in ws.accumulated_slides
            }
            if (
                ws.slides_needing_revision
                and slide_index is not None
                and slide_index not in ws.slides_needing_revision
                and slide_index in already_designed
            ):
                targets = ", ".join(str(i + 1) for i in ws.slides_needing_revision)
                return (
                    f"Slide {slide_index + 1} renders correctly and does not need "
                    f"redesigning. Validation only flagged slide(s) {targets} - edit "
                    "just those, then call finalize_and_render_pptx again. Every other "
                    "slide is carried over unchanged automatically."
                )

            if slide_index is not None and slide_index not in ws.selected_layouts:
                return (
                    f"No layout has been selected for slide {slide_index} yet. Call "
                    f"select_layout for slide_index={slide_index} first - its result "
                    "is passed to design_slide_content automatically."
                )

        if fn_name == "finalize_and_render_pptx":
            if not ws.presentation_structure:
                return (
                    "finalize_and_render_pptx requires a presentation structure. "
                    "Call plan_presentation_structure first."
                )
            total = ws.presentation_structure.get("totalSlides", 0)
            designed = {s.get("slideIndex") for s in ws.accumulated_slides}
            missing = [i for i in range(total) if i not in designed]
            if missing:
                return (
                    f"Cannot finalize yet: {len(missing)} of {total} planned slides "
                    f"have not been designed (missing slideIndex: {missing}). Call "
                    "design_slide_content for each missing slide first."
                )

        if fn_name == "validate_rendered_output":
            if not ws.has_called_finalize:
                return (
                    "validate_rendered_output checks the actual rendered .pptx file, which "
                    "does not exist yet. Call finalize_and_render_pptx first."
                )
            if ws.validation_attempts >= ws.MAX_VALIDATION_ATTEMPTS:
                return (
                    f"Validation retry limit reached ({ws.MAX_VALIDATION_ATTEMPTS} attempts). "
                    "Stop redesigning and finish now. The file is made available to the user "
                    "as it stands, so tell them plainly which slides still render incorrectly "
                    "and what is wrong with them - do not describe the deck as clean or "
                    "problem-free, and do not claim the issues were fixed."
                )
            if ws.validation_failures >= ws.MAX_VALIDATION_FAILURES:
                return (
                    f"Validation could not be completed after {ws.MAX_VALIDATION_FAILURES} "
                    "failed attempts. Finish now and report that the rendered output could "
                    "not be verified, rather than claiming the presentation is problem-free."
                )

        return None

    async def _execute_tool(self, name: str, args: dict) -> ToolResult:
        """Execute a registered tool by name."""
        entry = self.registry.get(name)
        if not entry:
            return ToolResult(success=False, error=f"Unknown tool: {name}")

        tool_def, fn = entry
        try:
            result = await fn(**args)
            return result
        except Exception as e:
            logger.error("tool_execution_failed", tool_name=name, error=str(e))
            return ToolResult(success=False, error=str(e))

    async def _load_context(self, session_id: uuid.UUID) -> dict:
        """Load session context including design system and ingested documents."""
        from sqlalchemy import func, select
        from app.db.models import ChatSession, DesignSystemBundle, IngestedDocument

        context: dict = {}

        result = await self.db.execute(
            select(ChatSession).where(ChatSession.id == session_id)
        )
        session = result.scalar_one_or_none()
        if not session:
            return context

        # Whether this session has anything of the user's own to read. Checked
        # before the run starts because the web-research offer must not fire on
        # a deck that already has a source attached, and analyze_document has
        # not run yet at that point.
        try:
            doc_count = await self.db.execute(
                select(func.count())
                .select_from(IngestedDocument)
                .where(IngestedDocument.session_id == session_id)
            )
            self._session_has_documents = bool(doc_count.scalar() or 0)
        except Exception as e:  # noqa: BLE001 - never fail a run over this count
            logger.error(
                "document_count_failed", session_id=str(session_id), error=str(e)
            )
            # Assume there is something to read rather than interrupting a run
            # that may not need interrupting.
            self._session_has_documents = True

        # Load design system if template is set
        if session.template_id:
            ds_result = await self.db.execute(
                select(DesignSystemBundle)
                .where(DesignSystemBundle.template_id == session.template_id)
                .order_by(DesignSystemBundle.version.desc())
                .limit(1)
            )
            bundle = ds_result.scalar_one_or_none()
            if bundle:
                context["design_system"] = {
                    "palette": bundle.palette,
                    "typography": bundle.typography,
                    "spacing": bundle.spacing,
                    "layouts": bundle.layouts,
                    "slideDimensions": bundle.slide_dimensions,
                    "masterElements": bundle.master_elements,
                    "embeddedMedia": bundle.embedded_media,
                    # Bundles written before this column existed default to
                    # {} here - table_style.py's resolvers already fall back
                    # to deriving from palette when dataViz is empty.
                    "dataViz": bundle.data_viz,
                    # Likewise: an empty density means "this template was
                    # never measured", and the budget holds such a slide to
                    # nothing rather than to a number it invented.
                    "density": bundle.density,
                    "pageSurvey": bundle.page_survey,
                    "layoutGrammar": bundle.layout_grammar,
                }

        return context

    async def _chat_with_retry(self, messages: list[dict], tools: list[dict] | None):
        """Call the chat model, retrying transient provider failures.

        Rate limits are deliberately NOT retried here. They are handled one
        level down, in the provider, which is where the provider's own
        "try again in 2.6s" advice is visible and where every tool's LLM call
        passes through as well. Retrying them in both places multiplied the two
        budgets (4 x 3 attempts) and made the wait reported to the user a
        fraction of the real one - the interface said "retrying in 5s" while the
        run actually sat there for a minute and a half.

        What is left here is the rest: a 5xx or a timeout, where the provider
        offers no advice and exponential backoff is a reasonable guess.
        """
        delay = self.RETRY_BASE_SECONDS
        for attempt in range(self.MAX_LLM_RETRIES + 1):
            try:
                return await self.llm.chat(messages=messages, tools=tools)
            except Exception as e:
                if (
                    attempt == self.MAX_LLM_RETRIES
                    or _is_rate_limit_error(e)
                    or not _is_retryable(e)
                ):
                    raise
                wait = _retry_after_seconds(e) or delay
                logger.warning(
                    "llm_call_retrying",
                    attempt=attempt + 1,
                    wait_seconds=round(wait, 2),
                    error=str(e)[:200],
                )
                publish_retry_notice(
                    RetryNotice(
                        reason=REASON_TRANSIENT,
                        attempt=attempt + 1,
                        max_attempts=self.MAX_LLM_RETRIES,
                        delay_seconds=wait,
                        detail=str(e)[:200],
                    )
                )
                await asyncio.sleep(wait)
                delay *= 2

    async def _with_retry_notices(
        self, awaitable, phase: str
    ) -> AsyncIterator[tuple[str, object]]:
        """Await ``awaitable``, narrating any provider back-off it runs into.

        Yields ``("event", <rate_limit event>)`` for each wait as it starts, and
        finally ``("result", <value>)``. The awaited work runs as a task so the
        loop can keep emitting while it is blocked - the whole point being that
        a 30-second rate-limit wait is announced when it begins rather than
        inferred afterwards from a gap in the timestamps.
        """
        queue: asyncio.Queue[RetryNotice] = asyncio.Queue()
        token = set_retry_observer(queue.put_nowait)
        # The observer is installed before the task is created so the task's
        # copied context carries it - that is what lets a notice raised inside
        # a tool's own LLM call several frames down reach this generator.
        task = asyncio.ensure_future(awaitable)
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=self.NOTICE_POLL_SECONDS)
                while not queue.empty():
                    yield "event", _retry_notice_event(queue.get_nowait(), phase)
                if done:
                    break
            yield "result", task.result()
        finally:
            reset_retry_observer(token)
            if not task.done():
                task.cancel()

    def _tools_withheld_by_checkpoint(self) -> set[str]:
        """The deck-writing tools a pending consultation puts out of reach.

        Rejecting these calls (which _check_precondition still does, as a
        backstop) came too late to be the whole answer: the model would try the
        planner anyway, and the rejection surfaced in the interface as a failed
        tool call - an error the agent appeared to have thrown at itself, right
        where the user was expecting a question. Withholding the schema is the
        same rule enforced one step earlier, where it costs nothing and shows
        nothing. The model is left holding ask_user_question and the reading
        tools, which is exactly what it should be doing.

        Safe to compute once per run: both flags are only ever set on the way
        into a pause, and a pause ends the run.
        """
        if self.questions_declined:
            return set()
        ws = self.working_state
        withheld: set[str] = set()
        if not ws.reviewed_outline_with_user:
            withheld.update(DRAFTING_TOOLS)
        if not ws.consulted_before_planning:
            withheld.update(PLANNING_TOOLS)
        return withheld

    def _tool_schemas_for_mode(self) -> list[dict]:
        """The tool schemas this run may call, by effort level and checkpoint."""
        schemas = self.registry.get_function_schemas()
        blocked = self._tools_withheld_by_checkpoint()
        if self.mode != MODE_DEEP:
            blocked |= DEEP_ONLY_TOOLS

        def name_of(schema: dict) -> str:
            return schema.get("function", {}).get("name", schema.get("name", ""))

        if not blocked:
            return schemas
        return [s for s in schemas if name_of(s) not in blocked]

    def _build_system_prompt(self, context: dict, working_state: WorkingState | None = None) -> str:
        """Build the system prompt with embedded design system context."""
        import datetime

        # Without this the model dates the deck from its training cutoff -
        # writing "this year" as 2023 and treating stale figures as current.
        today = datetime.datetime.now().astimezone()

        prompt_parts = [
            "You are ThinkSlides, an AI assistant that generates professional PowerPoint presentations.",
            "You follow a ReAct (Reason-Act-Observe) pattern to create presentations step by step.",
            "",
            f"The current date is {today.strftime('%A, %d %B %Y')} ({today.strftime('%Y-%m-%d')}). "
            "This is later than your training data. Treat it as authoritative for anything "
            'time-relative - "current", "latest", "this year", "recent", "up to date" - and never '
            "infer the present year from memory. When the deck needs recent figures, search for "
            "them rather than relying on what you remember.",
            "",
            "## CRITICAL RULES - YOU MUST FOLLOW",
            "",
            "1. You MUST always call tools until you complete the presentation.",
            "2. You MUST call finalize_and_render_pptx as the final step to generate the PPTX file.",
            "3. NEVER respond with only text and stop - this will cause the generation to fail.",
            "3b. NEVER write a download link or file path into your reply. The interface provides the "
            "download itself once the deck has passed validation; a link you compose points at a path "
            "inside the service container and is dead for the user. Say the deck is ready and describe "
            "it - do not link to it. Equally, do not announce that the presentation is complete while "
            "validate_rendered_output still reports unresolved issues; fix those first.",
            "4. THE DECK IS PLANNED WITH THE USER, NOT AT THEM. There are two consultation "
            "checkpoints, and the tool list enforces both - until the first has happened you "
            "are not given plan_presentation_structure, and until the second has you are not "
            "given select_layout/design_slide_content/build_*/finalize_and_render_pptx. If a "
            "tool you expected is missing, that is why: ask the outstanding question rather "
            "than looking for a way around it.\n"
            "   (a) BEFORE PLANNING: call ask_user_question once with a consolidated set of "
            "questions about what you are genuinely unsure of - target audience; scope/slide "
            "count; WHICH PARTS OF THE MATERIAL TO FOCUS ON when it covers more than fits; tone; "
            "how to resolve anything ambiguous or contradictory you found; HOW TO PRESENT THE "
            "DATA when the material has figures, time series, breakdowns or comparisons and it "
            "is not obvious whether they belong in a chart (and which kind), a table, or prose; "
            "and VISUAL STYLE / COLOR when no template was uploaded and the user has not said.\n"
            "   (b) BEFORE DRAFTING: call ask_user_question again with the planned outline "
            "spelled out - the slide titles, numbered, one per line, in the call's top-level "
            "'context' field (not inside a question object) - and ask whether to go ahead. "
            "EVERY OPTION MUST BE AN ANSWER, NOT A PROMPT FOR ONE: 'Go ahead as planned', "
            "'Merge slides 4 and 5 on trends', 'Add a slide on risks', 'Cut the history slide "
            "and go deeper on pricing' - naming the actual slides in THIS outline. An option "
            "like 'No, I want changes' tells you nothing and wastes the user's turn, so never "
            "offer one. This is their chance to redirect the deck while it is still cheap; do "
            "not skip it and do not bury it in a question about something else.\n"
            "   Beyond the two checkpoints, still ask whenever a new ambiguity turns up and a "
            "wrong assumption would produce a presentation the user did not want.",
            "5. ASK EVERYTHING AT ONCE. ask_user_question takes a 'questions' array - up to 4 "
            "{question, options} objects answered together in a single pause. Use it: four "
            "separate pauses for audience, focus, length and colour is an interrogation, and each "
            "one costs the user a round trip. EVERY question MUST carry 2-5 suggested options "
            "(a real array), concrete and mutually distinct, so the user can answer by tapping. "
            "OPTIONS MUST BE TAILORED TO THE ACTUAL REQUEST, never generic placeholders: for "
            "content focus, name the 2-4 candidate angles/sections you actually saw in the "
            "material (not 'Option A/B/C') and let the user pick or say 'cover them all'; for "
            "data presentation, offer the concrete alternatives for THAT data ('Line chart of "
            "revenue 2011-2025', 'Table of year-by-year figures', 'Keep it as bullets') rather "
            "than asking abstractly whether the user likes charts. A question whose options are "
            "missing or generic is rejected and has to be asked again, so write them properly "
            "the first time.",
            "6. When no template was uploaded you MUST settle the colour/style choice with the user "
            "before designing content - fold it into your consolidated question rather than "
            "defaulting silently, because an unanswered colour choice renders the deck in a generic "
            "preset that looks nothing like the subject's brand. Never ask about colors if "
            "the user gave a template, since its own colors are always used as-is and the question "
            "would be meaningless. When you do ask: if the topic clearly implies a real brand/entity "
            "with a known color (e.g. a presentation about a specific company), offer THAT SUBJECT'S "
            "OWN brand color as one option, named and given as its real hex code - derived from the "
            "company actually being presented, never carried over from another deck, another company, "
            "or an example in these instructions. State the brand and the hex together so the choice "
            "is checkable (the pattern is \"<Company> <colour name> (#RRGGBB)\"). If you are not "
            "confident of the brand's real hex, say so and offer the closest preset instead of "
            "inventing or reusing one. Offer that "
            "alongside 2-3 of these general presets as other options - Professional, Warm, Monochrome, "
            "Bold, Nature. Whatever is chosen, pass the SAME value (the hex code, or the preset's "
            "lowercase name) as color_theme to BOTH extract_template_design_system and "
            "finalize_and_render_pptx so the design system you see and what actually renders agree.",
            "7. EXCEPTION: if the user has explicitly said not to ask questions (e.g. 'don't ask me "
            "anything', 'just build it', 'use your best judgment', 'no more questions'), make the most "
            "reasonable assumption yourself and proceed without asking - briefly note any significant "
            "assumption in the relevant slide's notes instead. Both checkpoints lift automatically in "
            "that case; you do not need to ask permission to skip them.",
            "8. Both checkpoints are one pause each. Fold everything you need into that call rather "
            "than pausing repeatedly - but do not suppress a genuine question just because drafting "
            "has started: if a new ambiguity only becomes apparent partway through (e.g. conflicting "
            "figures in an uploaded document), ask then too rather than silently guessing.",
            "9. Do NOT ask about things you can already infer confidently from the conversation or "
            "uploaded documents, or about minor stylistic choices with no real stakes - that is noise, "
            "not the kind of question this rule is asking for. Every question you do ask should be one "
            "where the two answers lead to visibly different decks.",
            "10. Tool calls that are out of order (e.g. finalizing before every planned slide is "
            "designed) will be rejected with an explanation - read it and follow the corrective "
            "instruction rather than retrying the same call.",
            "",
            "The system will NOT accept completion until finalize_and_render_pptx is called.",
            "",
            "## Templates & Design System",
            "",
            "- If the user uploaded a template, its design system is already in your context below - use it directly and do NOT call extract_template_design_system.",
            "- If the user has NO template or asks for a 'default' template, call extract_template_design_system with NO template_path (or template_path='default'). It returns a built-in default design system. NEVER invent a filename like 'default_template.pptx' - it does not exist and will fail.",
            "- Do NOT ask the user to upload a template if they already said to use a default. Proceed with the built-in default.",
            "",
            "## Presentation Detail Level",
            "",
            f"The user has selected the **{self.detail_level or DETAIL_LEVEL_DETAILED}** detail level. "
            "analyze_document, extract_key_data, plan_presentation_structure, and "
            "design_slide_content are all told this automatically - you do not need to pass it "
            "yourself - but keep your own reasoning and notes consistent with it rather than "
            "defaulting to the shortest possible phrasing. Higher levels also mean more of the "
            "source material is actually considered (not just written more verbosely) and more "
            "willingness to use real tables/charts where the data supports them.",
            "",
        ]

        # The two effort levels differ in whether the agent may research the
        # topic on the web. Describing only the tools it actually has keeps the
        # prompt honest - in simple mode web_search is not in the schema list.
        if self.mode == MODE_DEEP:
            min_searches, max_searches = self._research_budget()
            prompt_parts.extend([
                "## Researching Facts & Citing Sources (Deep Thinking is ON)",
                "",
                "- When the topic involves real-world facts, statistics, history, or current information that you may not know precisely, call web_search FIRST and base your content on the returned results.",
                f"- RESEARCH THE TOPIC FROM SEVERAL ANGLES, NOT ONE. Run at least "
                f"{min_searches} searches (up to {max_searches}) with genuinely different "
                "queries before planning: the subject itself, the specific figures the deck "
                "needs, what has happened recently, and the context or counterpoint a "
                "sceptical audience would raise. One query returning a handful of links is "
                "not a body of research, and planning is blocked until you have looked "
                f"properly - aim for {RESEARCH_MIN_SOURCES}+ distinct sources.",
                "- Vary the wording between searches. Re-asking the same question in slightly "
                "different words returns the same pages and teaches you nothing new; if a "
                "query returns little, change what you are asking, not just how.",
                "- CORROBORATE ANYTHING THAT CARRIES WEIGHT. A headline figure, a market size, "
                "a growth rate or a superlative should appear in more than one independent "
                "source before you put it on a slide. Where sources disagree, say so on the "
                "slide or in its notes rather than silently picking one; where a number rests "
                "on a single source, attribute it in the slide's notes.",
                "- Prefer primary and recent sources (the company's own filing, the statistics "
                "office, the standards body) over aggregators repeating each other.",
                "- Do NOT call web_search for opinions, the user's own documents, or topics you already know well - avoid unnecessary calls.",
                "- The web_search results are shown to the user as sources, so prefer it whenever a claim benefits from a citation.",
                "",
            ])
        else:
            prompt_parts.extend([
                "## Sourcing Content (Deep Thinking is OFF)",
                "",
                "- You have no web access on this run. Write from the user's documents, the conversation, and your own knowledge.",
                "- Do NOT invent precise statistics, dates, or quotations you are not confident in. Prefer qualitative statements over fabricated numbers.",
                "- This applies with full force to build_chart, build_table and "
                "build_key_metrics. A chart makes a number look sourced, so charting a "
                "figure you actually guessed is worse than writing no chart at all. "
                "Only build a visual from figures present in the user's documents, the "
                "conversation, or facts you are genuinely confident of. If the request "
                "needs current results you do not have, say so on the slide and in its "
                "notes, and chart the shape of the question rather than inventing "
                "values for it.",
                "- If the request genuinely needs current or verifiable facts, say so in your slides' framing rather than guessing.",
                "",
            ])

        research_tool = ["web_search — Search the web for real facts and return cited sources"] \
            if self.mode == MODE_DEEP else []
        tool_lines = [
            "analyze_document — Extract and summarize content from uploaded documents",
            "extract_key_data — Pull specific data points from documents",
            "extract_template_design_system — Parse a template's design system, or get the built-in default (omit template_path)",
            *research_tool,
            "plan_presentation_structure — Plan the slide structure and outline",
            "select_layout — Choose the best layout for each slide based on content",
            "build_chart — Build a validated chart for a slide from typed arguments (categories, series, axis titles). Use this rather than writing chart JSON yourself",
            "build_table — Build a validated table for a slide from typed columns and rows",
            "build_key_metrics — Build a row of 2-4 headline figures for a slide",
            "design_slide_content — Generate a slide's title and copy (visuals come from the build_* tools)",
            "validate_slide_json — Validate slide JSON against the schema before rendering",
            "select_source_image — Reuse a confidently-matching image already extracted from the user's own source material (never generates new images)",
            "ask_user_question — Put up to 4 questions to the user at once, each with 2-5 "
            "suggested answers (pauses the workflow until they reply)",
            "finalize_and_render_pptx — Render the final PPTX file",
            "validate_rendered_output — Check the ACTUAL rendered output: overflow/overlap in the real geometry, plus placeholder copy, repeated points, bullets restating a chart, and package integrity (MUST be called after finalize_and_render_pptx, and is the true last step)",
        ]
        prompt_parts.extend([
            "## Building Visuals",
            "",
            "- Charts, tables and metric rows are built with the build_* tools, not "
            "written as JSON inside design_slide_content. Each one validates what you "
            "give it (series lengths against categories, plain numbers, rows against "
            "columns) and tells you exactly what is wrong if it does not fit, so a "
            "malformed visual is caught where you stated it rather than silently "
            "dropped at render time.",
            "- Call build_chart/build_table/build_key_metrics with the slide_index "
            "BEFORE calling design_slide_content for that slide.",
            "- Units belong in the series name or the axis title, never inside the "
            "numbers: \"Revenue (US$ billions)\" with values [29.1, 32.3], not "
            "[\"29.1B\", \"32.3B\"].",
            "- A visual REPLACES the prose it shows. Once a chart is attached, the "
            "slide's bullets should interpret it, not repeat its figures.",
            "- Only ever chart or tabulate figures that genuinely appear in the source "
            "material, the research results or the conversation. Never invent, "
            "estimate or round-trip a number you are not sure of.",
            "",
        ])
        prompt_parts.append("## Available Tools")
        prompt_parts.append("Use the provided function tools to accomplish each step:")
        prompt_parts.extend(f"{i}. {line}" for i, line in enumerate(tool_lines, 1))
        prompt_parts.append("")

        # Add completed work summary if available (critical for issue #7)
        if working_state and working_state.completed_tools:
            completed_summary = working_state.get_completed_work_summary()
            if completed_summary:
                prompt_parts.append(completed_summary)

        if context.get("design_system"):
            import json as _json
            prompt_parts.append("## Design System Context")
            prompt_parts.append("The following design system was extracted from the user's template.")
            prompt_parts.append("You MUST follow these design tokens when generating content:")
            prompt_parts.append("```json")
            prompt_parts.append(_json.dumps(context["design_system"], indent=2))
            prompt_parts.append("```")
            prompt_parts.append("")
        else:
            prompt_parts.append("## Design System Context")
            prompt_parts.append(
                "No user template is attached. Call extract_template_design_system with no "
                "template_path to load the built-in default design system before designing slides."
            )
            prompt_parts.append("")

        if context.get("research"):
            findings = context["research"]
            distinct_sources = len({r.get("url") for r in findings if r.get("url")})
            prompt_parts.append("## Research Findings (from web_search)")
            prompt_parts.append(
                f"{len(findings)} findings from {distinct_sources} distinct sources. Base your "
                "factual content on them, and prefer a figure that appears in more than one of "
                "them over one that appears in only a single source. Do NOT call web_search "
                "again for topics already covered below. These sources are shown to the user "
                "as citations."
            )
            for idx, r in enumerate(findings, 1):
                snippet = (r.get("snippet") or "")[:RESEARCH_SNIPPET_CHARS]
                prompt_parts.append(
                    f"{idx}. [{r.get('title', '')}]({r.get('url', '')}) - {snippet}"
                )
            prompt_parts.append("")

        research_step = [
            "Research the topic with web_search from several different angles "
            f"({self._min_web_searches()}+ distinct queries) before writing anything"
        ] if self.mode == MODE_DEEP else []
        workflow_steps = [
            "If documents are provided, analyze them first with analyze_document",
            "Extract key data with extract_key_data",
            "If no design system is in your context, get the built-in default with extract_template_design_system (no template_path)",
            *research_step,
            "CHECKPOINT 1 - consult the user: one ask_user_question call carrying every "
            "question you have (audience, focus, depth, style), each with tailored options. "
            "Skipped only if the user said not to ask (see CRITICAL RULES above)",
            "Plan the presentation structure with plan_presentation_structure",
            "CHECKPOINT 2 - review the outline: ask_user_question with the numbered slide "
            "titles in 'context' and options for going ahead or adjusting it",
            "For each slide, select a layout with select_layout",
            "If candidate source images exist and a slide's topic plausibly has a relevant one, call select_source_image for that slide before designing its content - otherwise skip it",
            "For any slide whose point is carried by numbers, build its visual FIRST: "
            "build_chart for a trend/comparison/breakdown, build_table for genuinely "
            "tabular data, build_key_metrics for a few standout figures. Each is "
            "validated and attached to that slide automatically, so the numbers are "
            "checked once where you state them",
            "Where a slide's content has a SHAPE, build that shape rather than "
            "flattening it into bullets: build_stat_callouts when the point is a "
            "figure, build_row_bands for many parallel points (an implication per "
            "industry, per capability), build_icon_cards for a set of parallel "
            "things, build_accent_cards plus build_legend when two kinds of thing "
            "are set against each other, build_banner for a one-line summary, "
            "build_image_card_row for things better shown than described. These "
            "attach to the slide like the visuals above. draw_primitives exists for "
            "the marks none of them covers, and is a last resort - a block carries "
            "meaning and sizes itself, a mark does neither",
            "Generate content for each slide with design_slide_content - it writes the "
            "title and copy; any visual or block already built for that slide is "
            "attached for you and must not be restated as bullets",
            "Validate all slides with validate_slide_json",
            "Render with finalize_and_render_pptx - THIS IS MANDATORY",
            "Then call validate_rendered_output to check the ACTUAL rendered file - THIS IS ALSO MANDATORY, and comes after finalize, not before",
            "If validate_rendered_output reports real issues (overflow, overlap, placeholder copy, a repeated point, bullets restating a chart) for a slide, call design_slide_content again for that slide with shorter/adjusted content reflecting the reported detail, then finalize_and_render_pptx and validate_rendered_output again - up to a few times; if issues remain after that, accept the output and finish rather than looping forever. Purely 'aesthetic' findings are optional polish, not something to chase indefinitely",
        ]
        prompt_parts.append("## Workflow")
        prompt_parts.extend(f"{i}. {step}" for i, step in enumerate(workflow_steps, 1))
        prompt_parts.extend([
            "",
            "Always think step by step. Explain your reasoning before each tool call.",
            "NEVER stop until you have called finalize_and_render_pptx.",
        ])

        return "\n".join(prompt_parts)

    def _describe_validation_issues(self, limit: int = 8) -> str:
        """Render the last validation run's issues as a slide-numbered list."""
        issues = self.working_state.validation_issues
        if not issues:
            return "  (no specific issues recorded)"

        lines = []
        for issue in issues[:limit]:
            slide_index = issue.get("slideIndex")
            where = f"Slide {slide_index + 1}" if isinstance(slide_index, int) else "Deck"
            lines.append(f"  - {where}: {issue.get('detail', issue.get('type', 'issue'))}")
        if len(issues) > limit:
            lines.append(f"  - ...and {len(issues) - limit} more")
        return "\n".join(lines)

    def _flush_slide_previews(self) -> list[dict]:
        """
        Publish the staged ``slide_finalized`` events for the current render.

        Previews are staged at render time and released only once the deck is
        settled, so the UI never shows a slide that validation is about to send
        back for redesign. Idempotent: repeated calls after a flush emit
        nothing until the next render stages a fresh set.
        """
        if self.working_state.previews_published:
            return []

        events = [
            _event("slide_finalized", "rendering", {**preview, "timestamp": _iso_now()})
            for preview in self.working_state.pending_slide_previews
        ]

        render_result = self.working_state.pending_render_result
        if render_result:
            if self.working_state.validation_clean:
                # Only a deck that actually renders correctly is offered for
                # download. This event - not the finalize_and_render_pptx
                # tool_result - is what persists the file and emits
                # file_generated (see api/generate.py).
                events.append(_event("deck_finalized", "validation", {
                    "renderResult": render_result,
                    "validationClean": True,
                    "timestamp": _iso_now(),
                }))
            else:
                # Exhausted the revision budget with defects still present.
                # The deck IS still handed over - once the agent has stopped
                # trying, withholding the file only leaves the user with
                # nothing, and its own closing message tells them to download
                # it. What must not happen is passing it off as clean, so the
                # unresolved issues are published alongside it and
                # validationClean stays false for anything reading the event.
                events.append(_event("validation_unresolved", "validation", {
                    "issues": self.working_state.validation_issues,
                    "issueCount": len(self.working_state.validation_issues),
                    "attempts": self.working_state.validation_attempts,
                    "timestamp": _iso_now(),
                }))
                events.append(_event("deck_finalized", "validation", {
                    "renderResult": render_result,
                    "validationClean": False,
                    "issues": self.working_state.validation_issues,
                    "timestamp": _iso_now(),
                }))

        if events:
            self.working_state.previews_published = True
        return events

    def _extract_sources(self, data) -> list[dict]:
        """Extract source URLs from tool result data for display in chat.

        Walks dicts/lists looking for URL-valued fields. When a sibling
        ``title``/``name``/``media`` field is present on the same object, it is
        used as the source title; otherwise the URL's domain is used.
        """
        sources: list[dict] = []

        # Keys whose string value is treated as a source URL.
        url_keys = [
            "url", "source", "link", "href", "file_url", "download_url",
            "source_url", "web_url", "website", "reference", "citation"
        ]
        # Sibling keys that may hold a human-readable title for the URL.
        title_keys = ["title", "name", "media", "source_name", "publisher"]

        def add_url(url: str, owner: dict | None) -> None:
            if not isinstance(url, str) or not self._is_valid_url(url):
                return
            if any(s.get("url") == url for s in sources):
                return
            title = self._extract_domain(url)
            if owner:
                for tk in title_keys:
                    tv = owner.get(tk)
                    if isinstance(tv, str) and tv.strip():
                        title = tv.strip()
                        break
            sources.append({"url": url, "title": title, "type": "web"})

        def walk(obj) -> None:
            if isinstance(obj, dict):
                for key in url_keys:
                    if key in obj and obj[key]:
                        add_url(obj[key], obj)
                # Recurse into nested structures to find more URL fields.
                for value in obj.values():
                    if isinstance(value, (dict, list)):
                        walk(value)
            elif isinstance(obj, list):
                for item in obj:
                    if isinstance(item, (dict, list)):
                        walk(item)

        walk(data)

        # De-duplicate by URL, preserving order.
        seen_urls = set()
        unique_sources = []
        for s in sources:
            if s["url"] not in seen_urls:
                seen_urls.add(s["url"])
                unique_sources.append(s)

        return unique_sources

    def _is_valid_url(self, url: str) -> bool:
        """Check if string is a valid URL."""
        from urllib.parse import urlparse

        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc]) and result.scheme in ("http", "https")
        except Exception:
            return False

    def _extract_domain(self, url: str) -> str:
        """Extract domain name from URL for display."""
        from urllib.parse import urlparse

        try:
            parsed = urlparse(url)
            domain = parsed.netloc
            # Remove www. prefix
            if domain.startswith("www."):
                domain = domain[4:]
            return domain
        except Exception:
            return url

    async def _build_chat_messages(self, session_id: uuid.UUID) -> list[dict]:
        """Build the chat message history for the LLM."""
        from sqlalchemy import select
        from app.db.models import ChatMessage

        result = await self.db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at)
        )
        messages = result.scalars().all()

        return [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.content
        ]


def _project_design_system(fn_name: str, design_system: dict) -> dict:
    """
    Narrow the design system to the keys a given tool actually reads.

    The whole bundle used to be serialised into every call. For
    design_slide_content that meant the full layout catalog - every
    placeholder's EMU geometry for every layout, a few thousand characters -
    rode along on each slide, in the tool-call arguments AND again in the
    echoed tool result, despite that tool only ever reading the fonts and the
    palette. On a token-per-minute budget that is what pushes a normal deck
    into rate-limit territory partway through, and a 429 mid-revision is what
    strands a deck with an unfixed slide.
    """
    if not design_system:
        return {}

    if fn_name == "design_slide_content":
        typography = design_system.get("typography", {}) or {}
        palette = design_system.get("palette", {}) or {}
        return {
            "typography": {
                key: typography[key]
                for key in ("titleFont", "bodyFont")
                if key in typography
            },
            # The prompt only ever renders the first five swatches.
            "palette": {"colors": (palette.get("colors") or [])[:5]},
        }

    if fn_name == "plan_presentation_structure":
        # Only the layout NAMES are surfaced to the planner; the geometry is
        # select_layout's business.
        return {
            "layouts": [
                {
                    "layoutId": layout.get("layoutId"),
                    "layoutName": layout.get("layoutName"),
                }
                for layout in design_system.get("layouts", []) or []
            ]
        }

    return design_system


def _event(event_type: str, phase: str, data: dict) -> dict:
    """
    Create a typed agent event dict.

    Rejects a name that is in neither half of the shared contract. An
    unrecognised name is not a harmless typo: every event is published to
    Redis, and one the browser was never told to listen for is dropped in
    silence. Failing here is how that gets noticed.
    """
    if event_type not in ALL_EVENT_TYPES:
        raise ValueError(
            f"unknown agent event type {event_type!r}. Add it to "
            f"app/agent/event_types.py and to AGENT_EVENT_TYPES (or "
            f"INTERNAL_AGENT_EVENT_TYPES) in packages/schemas/src/agent-events.ts, "
            f"or the browser will never receive it."
        )
    return {
        "type": event_type,
        "phase": phase,
        **data,
    }


def _retry_notice_event(notice: RetryNotice, phase: str) -> dict:
    """Turn a provider back-off into the event the interface counts down."""
    return _event("rate_limit", phase, {
        "reason": notice.reason,
        "attempt": notice.attempt,
        "maxAttempts": notice.max_attempts,
        "retryInSeconds": round(notice.delay_seconds, 1),
        "provider": notice.provider,
        "model": notice.model,
        "message": notice.message(),
        "timestamp": _iso_now(),
    })


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()