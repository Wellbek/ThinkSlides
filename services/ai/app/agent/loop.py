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
from app.agent import pipeline
from app.agent.summaries import summarize_tool_result
from app.agent.tools._binding import slot_key
from app.agent.tools._json_extract import extract_json_object
from app.agent.tools.build_visuals import BUILD_VISUAL_TOOLS
from app.agent.tools.build_blocks import BLOCK_BUILDER_TOOLS
from app.connections import mcp_tools
from app.connections import store as connection_store
from app.agent.tools.generate_image import GenerateImageTool
from app.connections.catalog import KIND_FIGURE, KIND_IMAGE, KIND_MCP
from app.llm.provider import (
    AINotConfiguredError,
    _is_quota_exhausted,
    _is_rate_limit_error,
    build_chat_provider,
)
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

# How many searches the opening scout runs. Small on purpose: its job is to
# make the questions concrete, not to research the deck.
SCOUT_SEARCHES = 2

# The consultation is one pause, so everything it asks has to fit on one card.
MAX_CONSULT_QUESTIONS = 4

# The question every consulted run asks, in the same words every time. The
# model judges plenty about a request; whether the user wants to be asked is
# not one of the things it gets to decide on their behalf.
AUTONOMY_QUESTION = {
    "question": "Shall I decide the details myself and build it?",
    "options": [
        "Yes - decide audience, focus and design, and go ahead",
        "No - I'll answer a few questions first",
    ],
}

# What the two options look like coming back. Matched loosely, because a user
# may type their own version instead of tapping - and decline-first, because
# the answer arrives with the question echoed in front of it ("Shall I decide
# the details myself...? -> No, ..."), so a yes-pattern that matches the
# question text reads every refusal as consent. That is not hypothetical: it
# shipped, and a run that had been told "no" skipped the design choice.
_AUTONOMY_NO_RE = re.compile(
    "|".join((
        r"\bno\b[^.\n]*\b(?:answer|questions?|ask|choose|pick|decide)\b",
        r"\bi'?ll (?:answer|choose|pick|decide)\b",
        r"\bdon'?t decide\b",
        r"\bask me\b",
    )),
    re.IGNORECASE,
)

# Deliberately none of these can match the question itself, which reads "Shall
# I decide the details myself and build it?" - a pattern like "decide the
# details" would match the echo and turn every reply into a yes.
_AUTONOMY_YES_RE = re.compile(
    "|".join((
        r"\byes\b[^?\n]*\bdecide\b",
        r"\byou decide\b",
        r"\bdecide\b[^?\n]*\byourself\b",
        r"\bgo ahead\b[^?\n]*\bdecide\b",
    )),
    re.IGNORECASE,
)


# What the outline review's two options look like coming back. The card sends
# a single question's answer as the bare option text, so - unlike the autonomy
# question - there is no echoed question to match against by accident, and the
# free-text elaboration follows on its own line.
#
# Approval is checked first here, which is the opposite of the autonomy pair
# and for the same underlying reason: "go ahead as planned" is the exact,
# unambiguous label of one option, whereas the change patterns are loose
# enough to catch a user who typed their own words instead of tapping.
_OUTLINE_APPROVED_RE = re.compile(
    "|".join((
        r"\bgo ahead as planned\b",
        r"\bgo ahead\b(?![^.\n]*\bbut\b)",
        r"\b(?:looks?|sounds?) (?:good|right|fine)\b",
        r"\bbuild it\b",
    )),
    re.IGNORECASE,
)

_OUTLINE_CHANGE_RE = re.compile(
    "|".join((
        r"\bchange something\b",
        r"\bchange\b[^.\n]*\b(?:outline|structure|slides?|order|it)\b",
        r"\b(?:add|drop|remove|delete|cut|merge|split|reorder|rename|swap)\b"
        r"[^.\n]*\bslides?\b",
        r"\b(?:replan|re-?plan|rework|revise|redo)\b",
        r"\b(?:shorten|lengthen|expand|trim)\b[^.\n]*\b(?:deck|outline|it)\b",
        r"\binstead of\b",
    )),
    re.IGNORECASE,
)


def user_wants_outline_changed(chat_messages: list[dict]) -> bool:
    """Whether the reply to the outline review asked for the outline to change.

    Their latest word on it wins. Approval is matched first so that "go ahead
    as planned" cannot be dragged into a change by a stray word after it.

    Without this the flag the review phase completes on was set the moment the
    question was *asked*, so both answers finished the checkpoint identically:
    a user who tapped "Change something - I'll say what" and typed their change
    watched the unchanged outline get built, because nothing cleared the plan
    and the pipeline has no edge back to it from drafting.
    """
    for message in reversed(chat_messages):
        if message.get("role") != "user":
            continue
        content = message.get("content") or ""
        if not content.strip():
            continue
        if _OUTLINE_APPROVED_RE.search(content):
            return False
        if _OUTLINE_CHANGE_RE.search(content):
            return True
        return False
    return False


def user_confirmed_autonomy(chat_messages: list[dict]) -> bool:
    """Whether the user handed the remaining decisions to the agent.

    Their latest word on it wins, and a decline is checked first: the reply
    carries the question with it, and the question is full of the words a
    yes would be matched on.
    """
    for message in reversed(chat_messages):
        if message.get("role") != "user":
            continue
        content = message.get("content") or ""
        if _AUTONOMY_NO_RE.search(content):
            return False
        if _AUTONOMY_YES_RE.search(content):
            return True
    return False


# Asked when intake could not think of anything to ask and the user has not
# taken the skip. Deliberately about the deck rather than about the process:
# a question worth a round trip even when nothing is ambiguous.
FALLBACK_QUESTION = {
    "question": "How should I pitch this?",
    "options": [
        "Keep it high-level - the argument, not the detail",
        "Balanced - the argument with the evidence under it",
        "Go deep - the detail matters more than the brevity",
    ],
}

# What intake is asked to work out, before anything acts on the request.
_INTAKE_PROMPT = """\
You are reading a request for a slide deck, before any work starts on it.

Work out what is being asked for, what is genuinely unclear, and whether the \
deck stands on real-world facts that ought to be looked up rather than \
recalled. Uploaded documents in this session: {documents}. The user asked for \
{detail_level} depth.

Reply with a JSON object and nothing else:
{{
  "brief": "one or two sentences: what this deck is about and who it seems to \
be for, in plain words",
  "audience": "who it is for, or \"\" if they did not say",
  "slideCount": <number the user asked for, or 0>,
  "wantsAutonomy": true|false,
  "needsResearch": true|false,
  "figures": ["specific things the deck needs that cannot be recalled", ...],
  "searchQueries": ["two opening queries that would tell you what this topic \
actually contains", ...],
  "ambiguities": [
    {{"question": "something you genuinely cannot infer, asked plainly",
      "options": ["2-5 concrete answers drawn from THIS request", ...]}}
  ]
}}

"wantsAutonomy" is true only when the request already says, in one way or \
another, that you should get on with it without being asked - "just make it", \
"you decide", "don't ask me anything", "I don't mind", "whatever looks good", \
"surprise me". It is false by default, and a short request is not a hands-off \
one: somebody who typed two lines usually has plenty to say when asked. \
Getting this wrong in either direction costs the user something, so only say \
true when they have actually said so - it raises one question offering them \
the skip, and nothing is skipped until they take it.

"needsResearch" is true when the deck is about a real organisation, product, \
market, place, person or event, or asks for figures, results, trends or \
anything time-anchored - a number recalled from memory reaches the reader as a \
measurement. It is false for conceptual, instructional, creative or \
process-shaped decks, and for decks built entirely from supplied material.

"ambiguities" is what the user will actually be asked, and they are asked \
unless they take the skip - so give one to three questions that genuinely \
change the deck: the angle when the topic has several, the audience when the \
register depends on it, which parts of a broad subject to cover, how deep to \
go. Even a well-specified request usually leaves one of those open; find the \
one whose answer would change most. Never ask about visual style or colour - \
the user is shown three rendered designs to choose from later, and the colour \
is asked beside them - and never ask something the request already answers. \
Each entry needs real options a user could tap, drawn from this request and \
what you have read.
"""


# Tools whose whole purpose is to stop and ask. A successful call ends the run
# until the user answers, wherever it was made from.
PAUSING_TOOLS = frozenset({"ask_user_question", "propose_design_directions"})

# What the direction the user chose asks of every slide, keyed by its
# structure axis (see app/directions.py). The user picked by looking at the
# shape its points took, so the deck has to keep taking that shape - a
# preview the deck does not honour is worse than no preview.
_STRUCTURE_DIRECTIVE = {
    "list": (
        " This deck's direction sets its points as written prose, so reach for "
        "a block only where the content genuinely has a shape a paragraph "
        "cannot hold."
    ),
    "stats": (
        " This deck's direction leads with oversized figures: where a slide's "
        "points carry numbers, call build_stat_callouts rather than writing "
        "them into bullets."
    ),
    "cards": (
        " This deck's direction leads with accent-barred cards: where a slide "
        "has a set of parallel points, call build_accent_cards rather than "
        "writing them as a bulleted list."
    ),
    "bands": (
        " This deck's direction leads with ruled row bands: where a slide has "
        "a set of parallel points, call build_row_bands rather than writing "
        "them as a bulleted list."
    ),
}


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
    # An exhausted account answers 429, and every layer must agree that waiting
    # cannot fix it. The provider layer already declines to retry these; this
    # one used to see only the status and retry anyway, so a run with no credit
    # left spent 35 seconds backing off - 5s, 10s, 20s - before reporting the
    # failure the very first call already knew about.
    if _is_quota_exhausted(exc):
        return False
    status = _status_of(exc)
    if status in _RETRYABLE_STATUSES:
        return True
    if status is not None:
        return False
    # Some SDKs stringify the status instead of exposing it.
    text = str(exc).lower()
    return "rate limit" in text or "429" in text or "timeout" in text


def _schema_name(schema: dict) -> str:
    """The tool name out of a function-calling schema, either shape."""
    return schema.get("function", {}).get("name", schema.get("name", ""))


_HEX_IN_TEXT = re.compile(r"#[0-9A-Fa-f]{6}")


def _normalize_query(query) -> str:
    """A search query reduced to what makes it a different question.

    Case, punctuation and spacing do not change which pages come back, so
    "German dog breeds?" and "german  dog breeds" are the same search.
    """
    return " ".join(re.sub(r"[^\w\s]", " ", str(query or "").casefold()).split())


def _provider_failure_message(exc: Exception) -> str:
    """What to tell the user when the model provider would not answer.

    The raw exception is a status code and a vendor error body - one observed
    failure reached the interface as `LLM call failed: Error code: 429, with
    error text {"error":{"code":"1113","message":"余额不足或无可用资源包"}}`,
    which names neither the cause nor the fix. The two cases worth separating
    are the ones the user can act on.
    """
    if _is_quota_exhausted(exc):
        return (
            "The AI provider rejected the request: this account is out of credit. "
            "Top it up, or switch provider in the workspace's AI settings, and try "
            "again - nothing was generated."
        )
    if _is_rate_limit_error(exc):
        return (
            "The AI provider is rate-limiting this workspace and did not recover "
            "after several waits. Try again in a few minutes."
        )
    return f"The AI provider could not be reached: {str(exc)[:300]}"


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
    "build_chart_grid": "content_generation",
    "build_diagram": "content_generation",
    "build_table": "content_generation",
    "build_key_metrics": "content_generation",
    "generate_image": "content_generation",
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
        # slide_index -> the region tree compose_slide_regions resolved for it.
        # Held here for the same reason the visuals are: the geometry was
        # validated once, and routing it back through a model's JSON output to
        # be restated is how a validated tree acquires a typo.
        self.prepared_regions: dict[int, list[dict]] = {}
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
        # The direction pause is its own phase rather than another entry
        # in answered_questions: the loop's bookkeeping was written for a
        # single consultation, and "do not ask these again" would suppress
        # a second pause that has not happened yet.
        self.offered_directions: bool = False
        self.chosen_direction: dict | None = None
        # What was offered, so the reply can be resolved back to a bundle.
        self.proposed_directions: list[dict] = []
        # What the run understood the request to be, from the intake step: the
        # brief, the audience, what is genuinely ambiguous, whether any of it
        # needs looking up. Every later phase reads it, so it is computed once
        # and persisted rather than re-derived from the conversation.
        self.brief: dict | None = None
        # Whether the opening couple of searches have run. Separate from the
        # research budget because they answer a different question: not "what
        # goes in the deck" but "what should I be asking the user".
        self.scouted: bool = False
        # The offer to take over, and its answer. Asked only when the request
        # implied it, answered by the user, and only "yes" turns the
        # consultation off - see pipeline._asks_questions.
        self.autonomy_asked: bool = False
        self.autonomy_confirmed: bool = False
        # Which phase the run is in, so a resume picks up where it stopped
        # rather than re-deriving it from a handful of flags.
        self.phase: str | None = None
        # Bumped whenever a slide is written or rewritten, and recorded at each
        # render. Unequal means the deck on disk is older than the deck in
        # memory, which is what sends a revised run back through rendering
        # instead of straight to validation.
        self.design_serial: int = 0
        self.rendered_serial: int = -1
        # design_serial as it stood when validation last reported. Slides are
        # only outstanding for revision while nothing has been rewritten since
        # then - otherwise the fix would be flagged as still needing fixing.
        self.validated_serial: int = -1
        # Model turns spent in each phase since it last made progress. A phase
        # that stops making any is finished with, one way or another.
        self.phase_turns: dict[str, int] = {}

    @property
    def at_validation_cap(self) -> bool:
        """Whether the deck has spent its revision budget, either way it can."""
        return (
            self.validation_attempts >= self.MAX_VALIDATION_ATTEMPTS
            or self.validation_failures >= self.MAX_VALIDATION_FAILURES
        )

    @property
    def render_is_current(self) -> bool:
        """Whether the rendered file reflects every slide as it now stands."""
        return self.has_called_finalize and self.rendered_serial == self.design_serial

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
        # Any write makes the rendered file stale - see render_is_current.
        self.design_serial += 1
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

        # A composed page's geometry, if compose_slide_regions resolved one.
        # It wins over any regions the model copied from the grammar into its
        # own output: the composed tree is the one whose parts the exhibits
        # were actually bound to, and two trees on one slide would leave every
        # binding pointing at whichever survived.
        composed = self.prepared_regions.get(slide_index)
        if composed:
            slide_data = {**slide_data, "regions": composed}

        # Attach anything build_chart/build_table/build_key_metrics prepared
        # for this slide. Any chart or table the model wrote inline is
        # dropped in favour of the validated one, so a slide cannot end up
        # carrying the same figures twice.
        prepared = self.prepared_visuals.get(slide_index)
        if prepared:
            # Keyed by type, region *and* slot, not by type alone: a composed
            # slide legitimately carries three stat-callout blocks bound to
            # three different regions, and a dashboard page carries two charts
            # in two panels. Keying on anything coarser collapses them into one
            # and silently drops the rest. Read from _binding so this and the
            # store the blocks came from cannot drift apart.
            prepared_slots = {slot_key(block) for block in prepared}
            kept = [
                item for item in slide_data.get("contents", [])
                if not (isinstance(item, dict) and slot_key(item) in prepared_slots)
            ]
            slide_data = {**slide_data, "contents": [*kept, *prepared]}

        marks = self.prepared_primitives.get(slide_index)
        if marks:
            slide_data = {**slide_data, "primitives": list(marks)}

        slide_data = _credit_generated_images(slide_data)

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
        self.offered_directions = False
        self.chosen_direction = None
        self.proposed_directions = []
        self.brief = None
        self.scouted = False
        self.autonomy_asked = False
        self.autonomy_confirmed = False
        self.phase = None
        self.design_serial = 0
        self.rendered_serial = -1
        self.validated_serial = -1
        self.phase_turns = {}


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
        # A fork, not the global singleton: run-scoped tools discovered from one
        # organisation's MCP server must not still be registered when another
        # organisation's run starts, in a service that is shared.
        self.registry = get_registry().fork()
        # Built in _configure() from the workspace's settings. Deliberately not
        # a process-wide environment provider: that default is what let a run
        # start on a model nobody had chosen.
        self.llm = None
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
        # Discovered MCP servers, and which phases their tools are offered in.
        # Both empty on a workspace with no connections, which is the only
        # difference that makes to a run.
        self.mcp_servers: list = []
        self.mcp_tools_by_phase: dict[str, frozenset[str]] = {}
        # The workspace's image provider, when it has one. None is the ordinary
        # case, and generate_image is not registered at all in that case.
        self.image_connection = None
        self._image_token = None
        # The workspace's figure provider, when it has one. None means every
        # chart is drawn by matplotlib, which is also the default when it is set.
        self.figure_connection = None
        self._figure_token = None

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
        if credentials is None:
            raise AINotConfiguredError()
        try:
            self.llm = build_chat_provider(credentials)
        except ValueError as e:
            logger.error(
                "configured_chat_provider_unusable",
                provider=credentials.provider,
                error=str(e),
            )
            raise AINotConfiguredError(
                f"The AI model saved in Settings cannot be used: {e}. Open "
                "Settings and correct the provider, model or API key."
            ) from e
        # Tools make their own LLM calls (slide content, planning, key data,
        # image prompts) and cannot be passed a provider through the registry.
        # Publishing it on the run context is what makes the Settings choice
        # apply to the whole generation rather than only to the loop's own
        # reasoning - and require_chat_provider is what makes its absence an
        # error rather than a silent fallback.
        self._provider_token = run_context.set_chat_provider(self.llm)

        if self.mode == MODE_DEEP:
            config, reason = ai_settings.resolve_search_config(row)
            if config is None:
                logger.info("deep_mode_downgraded", reason=reason)
                self.mode = MODE_SIMPLE
            else:
                self.search_config = config
                self._search_token = run_context.set_search_config(config)

        await self._connect_mcp_servers()
        await self._enable_image_generation()
        await self._enable_figure_rendering()

        logger.info(
            "agent_run_configured",
            mode=self.mode,
            organisation_id=str(self.org_id) if self.org_id else None,
            chat_provider=credentials.provider if credentials else None,
            search_backend=self.search_config.backend if self.search_config else None,
            mcp_servers=len(self.mcp_servers),
        )

    async def _connect_mcp_servers(self) -> None:
        """Discover this workspace's MCP tools and scope them to their phases.

        Runs once, at configure time, so the schemas exist before the first
        phase that could offer them. Every handshake happens concurrently: N
        servers should cost roughly one round trip, not N, or a workspace with
        ten connections waits a minute before its first slide.

        Nothing here can fail a run. A workspace with no connections gets two
        empty collections, and a server that will not answer gets recorded with
        its reason and skipped - #183 is what reports that to the user.
        """
        if self.org_id is None:
            return
        try:
            connections = await connection_store.resolve_enabled(
                self.db, self.org_id, kind=KIND_MCP
            )
        except Exception as e:  # noqa: BLE001 - never fail a run over connections
            logger.error("connections_load_failed",
                         organisation_id=str(self.org_id), error=str(e))
            return

        if not connections:
            return

        self.mcp_servers = await mcp_tools.discover(connections)
        self.mcp_tools_by_phase = mcp_tools.register(self.registry, self.mcp_servers)

        by_slug = {server.slug: server for server in self.mcp_servers}
        await connection_store.record_health(self.db, self.org_id, {
            connection.id: (
                by_slug[connection.slug].error,
                len(by_slug[connection.slug].tools),
            )
            for connection in connections
            if connection.slug in by_slug
        })

    async def _enable_image_generation(self) -> None:
        """Register generate_image, but only where there is a provider for it.

        Absent an image connection the tool does not exist and the drafting
        prompt keeps saying image generation does not exist - which is then
        true. A tool that is registered but always fails teaches a model to keep
        trying it, and the prompt's flat statement is the right thing to say
        when there is nothing behind it.

        Same conditional shape as the document tools, which are described only
        when the session actually has something to read.
        """
        if self.org_id is None:
            return
        try:
            available = await connection_store.resolve_enabled(
                self.db, self.org_id, kind=KIND_IMAGE
            )
        except Exception as e:  # noqa: BLE001 - never fail a run over connections
            logger.error("image_connection_load_failed",
                         organisation_id=str(self.org_id), error=str(e))
            return
        if not available:
            return

        self.image_connection = available[0]
        self._image_token = run_context.set_image_connection(self.image_connection)
        GenerateImageTool.register(self.registry)
        # Offered while a slide is being drafted, which is the only phase where
        # a slide exists to put an image on.
        self.mcp_tools_by_phase.setdefault("draft", frozenset())
        self.mcp_tools_by_phase["draft"] = (
            self.mcp_tools_by_phase["draft"] | {"generate_image"}
        )
        logger.info("image_generation_enabled",
                    provider=self.image_connection.connection_id)

    async def _enable_figure_rendering(self) -> None:
        """Make a figure provider available to build_chart's renderer preference.

        Unlike image generation this gates no tool: build_chart always exists,
        and the connection only decides whether ``renderer='figure'`` is honoured
        or falls back to matplotlib. So there is nothing conditional to register -
        just a connection to publish.
        """
        if self.org_id is None:
            return
        try:
            available = await connection_store.resolve_enabled(
                self.db, self.org_id, kind=KIND_FIGURE
            )
        except Exception as e:  # noqa: BLE001 - never fail a run over connections
            logger.error("figure_connection_load_failed",
                         organisation_id=str(self.org_id), error=str(e))
            return
        if not available:
            return
        self.figure_connection = available[0]
        self._figure_token = run_context.set_figure_connection(self.figure_connection)
        logger.info("figure_rendering_enabled",
                    provider=self.figure_connection.connection_id)

    def _degraded_connections(self) -> list[dict]:
        """The servers this run could not reach, for the one warning it sends.

        Named rather than counted. "One connection is degraded" tells a user
        nothing they can act on; "Company wiki: the server refused the
        credential" tells them which credential to go and fix.
        """
        return [
            {"label": server.label, "reason": server.error}
            for server in self.mcp_servers if not server.ok
        ]

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
            "offered_directions": self.working_state.offered_directions,
            "chosen_direction": self.working_state.chosen_direction,
            "proposed_directions": self.working_state.proposed_directions,
            "reviewed_outline_with_user": self.working_state.reviewed_outline_with_user,
            "offered_deep_research": self.working_state.offered_deep_research,
            # The pipeline's own state: what the run understood, how far it
            # scouted, whether the user handed it the wheel, and where it was.
            "brief": self.working_state.brief,
            "scouted": self.working_state.scouted,
            "autonomy_asked": self.working_state.autonomy_asked,
            "autonomy_confirmed": self.working_state.autonomy_confirmed,
            "phase": self.working_state.phase,
            "design_serial": self.working_state.design_serial,
            "rendered_serial": self.working_state.rendered_serial,
            "validated_serial": self.working_state.validated_serial,
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
            self.working_state.offered_directions = state_dict.get(
                "offered_directions", False
            )
            self.working_state.chosen_direction = state_dict.get("chosen_direction")
            self.working_state.proposed_directions = state_dict.get(
                "proposed_directions", []
            )
            self.working_state.offered_deep_research = state_dict.get(
                "offered_deep_research", False
            )
            self.working_state.reviewed_outline_with_user = state_dict.get(
                "reviewed_outline_with_user", False
            )
            self.working_state.brief = state_dict.get("brief")
            self.working_state.scouted = state_dict.get("scouted", False)
            self.working_state.autonomy_asked = state_dict.get("autonomy_asked", False)
            self.working_state.autonomy_confirmed = state_dict.get(
                "autonomy_confirmed", False
            )
            self.working_state.phase = state_dict.get("phase")
            # A state saved before the pipeline existed has neither serial. The
            # deck it describes was rendered from the slides it carries, so
            # treating them as equal is right: a resumed run does not re-render
            # a deck nothing has changed since.
            self.working_state.design_serial = state_dict.get("design_serial", 0)
            self.working_state.rendered_serial = state_dict.get(
                "rendered_serial",
                0 if state_dict.get("has_called_finalize") else -1,
            )
            self.working_state.validated_serial = state_dict.get("validated_serial", -1)
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
        except AINotConfiguredError as e:
            # A workspace with no model configured cannot generate anything.
            # Said once, plainly, with a code the interface can act on - rather
            # than let each of a dozen LLM calls discover it separately.
            logger.warning(
                "run_blocked_unconfigured",
                session_id=str(session_id),
                organisation_id=str(self.org_id) if self.org_id else None,
            )
            yield _event("error", "content_analysis", {
                "message": str(e),
                "code": "ai_not_configured",
                "timestamp": _iso_now(),
            })
        finally:
            if self._search_token is not None:
                run_context.reset_search_config(self._search_token)
                self._search_token = None
            if self._provider_token is not None:
                run_context.reset_chat_provider(self._provider_token)
                self._provider_token = None
            if self._image_token is not None:
                run_context.reset_image_connection(self._image_token)
                self._image_token = None
            if self._figure_token is not None:
                run_context.reset_figure_connection(self._figure_token)
                self._figure_token = None
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

        # Said once, at the top, and never again for this run. A connection that
        # would not answer is not retried mid-run - retrying is how a six-second
        # timeout becomes a minute of them - but it must not vanish silently
        # either. A user who cannot tell "no relevant results" from "the tool
        # was never there" is worse off than one told the tool is down.
        degraded = self._degraded_connections()
        if degraded:
            yield _event("connection_degraded", "content_analysis", {
                "connections": degraded,
                "timestamp": import_timestamp,
            })

        # ── Phase 1: Load context ────────────────────
        # Narrated only on a fresh run. On a resume the user has just answered
        # a question and wants to see the answer being acted on, not the same
        # "loading..." line they already saw before they were asked.
        if not state_loaded:
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
        # Deliberately NOT read from the user's words any more. "Don't ask me
        # anything" is a request to be offered the skip, and the offer is one
        # question the user answers - a phrase alone cannot switch off the
        # consultation, because a model reading the phrase wrongly would take
        # a decision the user never made. It feeds intake's wantsAutonomy
        # instead (see _phase_intake), which is what raises the confirmation.
        #
        # The flag survives for callers that genuinely cannot answer anything:
        # the offline harness and the tests set it directly.

        # Did the user answer the web-research offer? Reading it here, before
        # the tool schemas are chosen, is what lets a "yes" actually put
        # web_search on the table for the rest of this run.
        # Did the user pick a design direction? Reading it here, before the
        # first LLM call, is what makes the choice govern the run: the
        # planner's density target, the layout selection, the copy and the
        # renderer all read context["design_system"], and a choice resolved
        # any later would arrive after the decisions it was meant to make.
        chosen = self._resolve_direction_choice(chat_messages)
        if chosen is not None:
            # The colour was the second question on the same card, so its
            # answer arrives with the direction's. Applied here, to the one
            # direction that was actually picked - the specimens were drawn
            # before it existed.
            chosen, recoloured = self._apply_colour_choice(chosen, chat_messages)
            self.working_state.chosen_direction = chosen
            context["design_system"] = chosen.get("designSystem") or {}
            run_context.set_design_system(context["design_system"])
            self._clean_up_direction_previews(session_id, chosen.get("id"))
            yield _event("thought", "content_analysis", {
                "content": (
                    f"Building in the {chosen.get('name') or 'chosen'} direction: "
                    f"{chosen.get('rationale') or ''}".strip()
                    + (f" Recoloured to {recoloured}, as you asked."
                       if recoloured else "")
                ),
                "timestamp": _iso_now(),
            })

        # A request to change the outline, read before the walk for the same
        # reason the direction is: the walker decides where to resume from the
        # state it finds, so an answer acted on later would arrive after the
        # phase it was meant to redirect.
        async for event in self._reopen_outline_if_asked(chat_messages):
            yield event

        # The one question every consulted run asks. Read here, before the walk,
        # because it decides whether the design pause happens at all.
        if user_confirmed_autonomy(chat_messages):
            self.working_state.autonomy_confirmed = True

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

        # ── The run walks its pipeline ────────────────────────────────────
        #
        # Each pass takes the first phase that applies and is not finished, and
        # runs it. Nothing chooses the order: see app/agent/pipeline.py.
        #
        # Progress is measured rather than assumed. A phase that runs and
        # changes nothing observable has stalled - the model refusing to call
        # the one tool it was left, a provider returning empty - and two of
        # those in a row ends the run with an honest error instead of spinning
        # to the iteration cap.
        stalls = 0
        while True:
            phase = pipeline.next_phase(self)
            if phase is None:
                break

            self.working_state.phase = phase.name
            before = self._progress_marker()
            paused = False
            failed = False

            async for kind, payload in self._run_phase(
                phase, context, chat_messages, session_id
            ):
                if kind == "event":
                    yield payload
                elif kind == "paused":
                    paused = True
                elif kind == "provider_failed":
                    failed = True

            if paused:
                return
            if failed:
                return

            if self._progress_marker() != before:
                stalls = 0
                # Progress earns a fresh budget, so "six turns" means six per
                # slide drafted rather than six for the whole phase. Only real
                # progress: refunding turns to a phase that just ran out of
                # them and changed nothing is how the walk loops forever.
                self.working_state.phase_turns.pop(phase.name, None)
            elif not phase.done(self):
                stalls += 1
                logger.warning(
                    "phase_made_no_progress",
                    phase=phase.name,
                    stalls=stalls,
                    session_id=str(session_id),
                )
                if stalls >= 2:
                    yield _event("error", phase.label, {
                        "message": (
                            f"The run could not get past the {phase.name} step. "
                            "Nothing was generated - please try again, or simplify "
                            "the request."
                        ),
                        "code": "phase_stalled",
                        "timestamp": _iso_now(),
                    })
                    return

        # Every phase is finished, so the deck is rendered and checked. The
        # closing word is the model's - it is the only part of the ending that
        # is not mechanical.
        for preview_event in self._flush_slide_previews():
            yield preview_event
        async for event in self._closing_message(context, chat_messages):
            yield event
    # ── The phases the loop drives itself ─────────────────────────────────

    async def _ensure_design_system(
        self, context: dict, session_id: uuid.UUID
    ) -> AsyncIterator[dict]:
        """Load a design system if the run has none.

        Not a phase: it is a precondition of several, it never pauses, and an
        uploaded template means it has already happened.
        """
        if context.get("design_system"):
            return
        async for kind, payload in self._call_tool(
            "extract_template_design_system",
            {"template_path": ""},
            context=context,
            session_id=session_id,
            phase=pipeline.PHASES_BY_NAME["intake"],
        ):
            if kind == "event":
                yield payload

    async def _phase_intake(
        self, context: dict, chat_messages: list[dict], session_id: uuid.UUID
    ) -> AsyncIterator[tuple[str, object]]:
        """Understand the request, once, before anything acts on it.

        Every later phase reads this: the scout's queries, the questions the
        user is asked, the brief the directions are drawn for, the planner's
        subject. It also decides whether the deck stands on facts that ought to
        be looked up - the judgement that used to be its own separate call.
        """
        yield "event", _event("thought", "content_analysis", {
            "content": "Reading the request...",
            "timestamp": _iso_now(),
        })

        documents = "yes" if self._session_has_documents else "no"
        prompt = _INTAKE_PROMPT.format(
            documents=documents,
            detail_level=self.detail_level or DETAIL_LEVEL_DETAILED,
        )
        content = None
        async for kind, payload in self._ask_model(
            prompt, chat_messages, phase_label="content_analysis"
        ):
            if kind == "result":
                content = payload
            else:
                yield kind, payload
        if content is None:
            return

        brief = extract_json_object(content) or {}
        # A user who typed "just build it" has implied it in as many words, so
        # the judgement does not get to disagree - it only gets to notice the
        # cases the phrase list never thought of.
        if user_declined_questions(chat_messages):
            brief["wantsAutonomy"] = True
        # A brief that did not parse still has to leave the phase finished, or
        # the run stalls on it. An empty one degrades to "ask the user about
        # everything", which is the safe direction to fail in.
        brief.setdefault("brief", (chat_messages[-1].get("content") if chat_messages else "") or "")
        brief.setdefault("ambiguities", [])
        brief.setdefault("searchQueries", [])
        brief.setdefault("needsResearch", False)
        self.working_state.brief = brief

        summary = str(brief.get("brief") or "").strip()
        if summary:
            yield "event", _event("thought", "content_analysis", {
                "content": summary,
                "timestamp": _iso_now(),
            })

    async def _phase_scout(
        self, context: dict, chat_messages: list[dict], session_id: uuid.UUID
    ) -> AsyncIterator[tuple[str, object]]:
        """A couple of searches, so the questions can be about real material.

        Not the deck's research - that comes after the answers narrow the
        topic. This exists so the user is offered angles the material actually
        contains instead of a list of adjectives.
        """
        queries = [
            str(q).strip()
            for q in (self.working_state.brief or {}).get("searchQueries") or []
            if str(q).strip()
        ][:SCOUT_SEARCHES]
        for query in queries:
            async for kind, payload in self._call_tool(
                "web_search",
                {"query": query},
                context=context,
                session_id=session_id,
                phase=pipeline.PHASES_BY_NAME["scout"],
            ):
                if kind == "event":
                    yield kind, payload
        self.working_state.scouted = True

    async def _phase_consult(
        self, context: dict, chat_messages: list[dict], session_id: uuid.UUID
    ) -> AsyncIterator[tuple[str, object]]:
        """The one pause every consulted run makes, and everything in it.

        It always carries the same first question - shall the agent decide the
        rest itself - so a user who wants to be left alone can say so once,
        here, rather than having it guessed from their phrasing. Anything the
        intake found genuinely unclear rides along in the same pause; a request
        that was already specific is left as a single question.

        The web-research offer joins it too, when the run has a search backend
        but was not sent in Deep Thinking mode. It used to be a pause of its
        own, which is a round trip spent on a question that fits here.
        """
        brief = self.working_state.brief or {}
        ws = self.working_state

        # A user who asked to be left alone is offered exactly that, once, and
        # on its own: mixing it with the questions it would cancel asks them to
        # answer both sides of the same decision. Their answer is read on the
        # way back in (see user_confirmed_autonomy) - "yes" ends the
        # consultation and settles the design without another round trip,
        # "no" falls through to the questions below.
        if brief.get("wantsAutonomy") and not ws.autonomy_asked:
            ws.autonomy_asked = True
            async for item in self._ask_and_pause(
                [dict(AUTONOMY_QUESTION)],
                context="",
                phase=pipeline.PHASES_BY_NAME["consult"],
                run_context_=context,
                session_id=session_id,
                records_consultation=False,
            ):
                yield item
            return

        questions: list[dict] = []

        if self._can_offer_research():
            figures = [
                str(f) for f in (self.working_state.brief or {}).get("figures") or []
            ][:4]
            questions.append(self._deep_research_question(figures)["questions"][0])

        for item in brief.get("ambiguities") or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("question") or "").strip()
            options = [str(o).strip() for o in item.get("options") or [] if str(o).strip()]
            if text and len(options) >= 2:
                questions.append({"question": text, "options": options[:5]})
            if len(questions) >= MAX_CONSULT_QUESTIONS:
                break

        if not questions:
            # Intake found nothing to ask, and the run is still going to ask:
            # the consultation is not skipped because a model could not think
            # of a question, only because the user said to skip it. This is
            # the floor, not the expected case.
            questions.append(dict(FALLBACK_QUESTION))

        async for item in self._ask_and_pause(
            questions,
            context="" if len(questions) == 1 else (
                "A few things worth settling before I build this."
            ),
            phase=pipeline.PHASES_BY_NAME["consult"],
            run_context_=context,
            session_id=session_id,
        ):
            yield item

    async def _phase_design(
        self, context: dict, chat_messages: list[dict], session_id: uuid.UUID
    ) -> AsyncIterator[tuple[str, object]]:
        """Settle the deck's design - by asking, or by deciding.

        A user who handed the run over is not asked, but the deck still needs a
        design, and "not asked" used to mean "rendered on whatever the default
        happened to be". It takes the first direction instead: the light one,
        which is the same thing they would have been offered first.
        """
        async for event in self._ensure_design_system(context, session_id):
            yield "event", event

        if not pipeline._asks_questions(self):
            async for event in self._settle_design_unasked(context):
                yield "event", event
            return

        brief = self.working_state.brief or {}
        tool_result = None
        async for kind, payload in self._call_tool(
            "propose_design_directions",
            {"brief": str(brief.get("brief") or "")},
            context=context,
            session_id=session_id,
            phase=pipeline.PHASES_BY_NAME["design"],
        ):
            if kind == "event":
                yield kind, payload
            else:
                tool_result = payload

        if tool_result is not None and tool_result.success:
            async for event in self._pause_for_user(
                "propose_design_directions", tool_result, session_id
            ):
                yield "event", event
            yield "paused", True

    async def _settle_design_unasked(self, context: dict) -> AsyncIterator[dict]:
        """Take the first direction, for a run that is not stopping to ask.

        An uploaded template is left exactly as it is: the user already chose a
        design by attaching one, and replacing it would be the run overruling
        the clearest instruction it has.
        """
        from app import directions as direction_model

        current = context.get("design_system") or {}
        if str(current.get("sourceType") or "default") != "default":
            self.working_state.offered_directions = True
            return

        brand = _HEX_IN_TEXT.search(self._settled_answers())
        chosen = direction_model.default_directions(
            brand.group(0).upper() if brand else None
        )[0]

        self.working_state.offered_directions = True
        self.working_state.chosen_direction = chosen
        context["design_system"] = chosen.get("designSystem") or {}
        self._citation_tokens.append(
            run_context.set_design_system(context["design_system"])
        )
        yield _event("thought", "user_input", {
            "content": (
                f"Building in the {chosen.get('name')} direction: "
                f"{chosen.get('rationale')}"
            ),
            "timestamp": _iso_now(),
        })

    async def _phase_plan(
        self, context: dict, chat_messages: list[dict], session_id: uuid.UUID
    ) -> AsyncIterator[tuple[str, object]]:
        """Lay out the deck."""
        async for event in self._ensure_design_system(context, session_id):
            yield "event", event

        brief = self.working_state.brief or {}
        # The tool's own parameter names, not paraphrases of them: the loop is
        # the caller now, so a mismatch here is a stalled run rather than a
        # model that tries again differently.
        summary = "\n".join(filter(None, [
            str(brief.get("brief") or ""),
            self._settled_answers(),
            self._research_summary(),
        ]))
        async for kind, payload in self._call_tool(
            "plan_presentation_structure",
            {
                "content_summary": summary,
                "target_slide_count": int(brief.get("slideCount") or 0),
                "audience": str(brief.get("audience") or "") or "general",
            },
            context=context,
            session_id=session_id,
            phase=pipeline.PHASES_BY_NAME["plan"],
        ):
            if kind == "event":
                yield kind, payload

    async def _phase_review(
        self, context: dict, chat_messages: list[dict], session_id: uuid.UUID
    ) -> AsyncIterator[tuple[str, object]]:
        """Show the outline and ask whether to go ahead.

        The one pause that happens even when the user handed over every other
        decision: seeing the slide titles before they are written is the
        cheapest moment to redirect a deck.
        """
        structure = self.working_state.presentation_structure or {}
        slides = structure.get("slides") or []
        outline = "\n".join(
            f"{i + 1}. {s.get('title') or s.get('heading') or 'Untitled'}"
            for i, s in enumerate(slides)
        )
        async for item in self._ask_and_pause(
            [{
                "question": "Shall I build this outline?",
                "options": [
                    "Go ahead as planned",
                    "Change something - I'll say what",
                ],
            }],
            context=f"Here is the outline I have planned:\n{outline}",
            phase=pipeline.PHASES_BY_NAME["review"],
            run_context_=context,
            session_id=session_id,
        ):
            yield item

    async def _phase_render(
        self, context: dict, chat_messages: list[dict], session_id: uuid.UUID
    ) -> AsyncIterator[tuple[str, object]]:
        """Render the deck as it currently stands."""
        structure = self.working_state.presentation_structure or {}
        tool_result = None
        async for kind, payload in self._call_tool(
            "finalize_and_render_pptx",
            {
                # slides_json is filled in by the dispatcher from the slides
                # actually designed - see _dispatch_tool.
                "slides_json": self.working_state.get_slides_json(),
                "session_id": str(session_id),
                "presentation_metadata": json.dumps({
                    "title": structure.get("title") or "",
                    "subtitle": structure.get("subtitle") or "",
                }),
            },
            context=context,
            session_id=session_id,
            phase=pipeline.PHASES_BY_NAME["render"],
        ):
            if kind == "event":
                yield kind, payload
            else:
                tool_result = payload
        if tool_result is not None and tool_result.success:
            # This render reflects the slides as they are now; the next slide
            # written makes it stale again. See WorkingState.render_is_current.
            self.working_state.rendered_serial = self.working_state.design_serial

    async def _phase_validate(
        self, context: dict, chat_messages: list[dict], session_id: uuid.UUID
    ) -> AsyncIterator[tuple[str, object]]:
        """Check the file that was actually produced."""
        async for kind, payload in self._call_tool(
            "validate_rendered_output",
            # pptx_path, slides_json and detail_level are supplied by the
            # dispatcher, which knows the real rendered path with certainty.
            {"pptx_path": self.working_state.last_rendered_path or "",
             "session_id": str(session_id)},
            context=context,
            session_id=session_id,
            phase=pipeline.PHASES_BY_NAME["validate"],
        ):
            if kind == "event":
                yield kind, payload

    # ── Shared phase mechanics ────────────────────────────────────────────

    async def _ask_and_pause(
        self,
        questions: list[dict],
        *,
        context: str,
        phase: pipeline.Phase,
        run_context_: dict,
        session_id: uuid.UUID,
        records_consultation: bool = True,
    ) -> AsyncIterator[tuple[str, object]]:
        """Put a set of questions to the user through the real tool, and stop.

        Through the tool rather than around it, so the options are normalised
        and validated exactly as a model-authored question would be.
        """
        tool_result = None
        async for kind, payload in self._call_tool(
            "ask_user_question",
            {"questions": questions, "context": context},
            context=run_context_,
            session_id=session_id,
            phase=phase,
        ):
            if kind == "event":
                yield kind, payload
            else:
                tool_result = payload

        if tool_result is not None and tool_result.success:
            async for event in self._pause_for_user(
                "ask_user_question", tool_result, session_id,
                records_consultation=records_consultation,
            ):
                yield "event", event
            yield "paused", True

    def _research_summary(self) -> str:
        """The findings this run gathered, as lines the planner can read."""
        findings = (self.context or {}).get("research") or []
        if not findings:
            return ""
        lines = ["What the research turned up:"]
        for item in findings[:MAX_RESEARCH_IN_CONTEXT]:
            snippet = (item.get("snippet") or "")[:RESEARCH_SNIPPET_CHARS]
            lines.append(f"- {item.get('title', '')}: {snippet}")
        return "\n".join(lines)

    def _settled_answers(self) -> str:
        """Everything the user has told this run, as plain lines."""
        return "\n".join(
            f"{a.get('question', '')} {a.get('answer', '')}".strip()
            for a in self.working_state.answered_questions
        )

    def _can_offer_research(self) -> bool:
        """Whether it is worth asking the user about researching the topic.

        Only when there is a backend to research with, the deck stands on
        facts, and the run was not already sent in Deep Thinking mode - where
        the answer is already yes.
        """
        return (
            not self.mode_is_deep
            and not self.working_state.offered_deep_research
            and self.brief_needs_research
            and self.search_config is not None
        )

    async def _model_turns(
        self,
        phase: pipeline.Phase,
        context: dict,
        chat_messages: list[dict],
        session_id: uuid.UUID,
    ) -> AsyncIterator[tuple[str, object]]:
        """Let the model work inside one phase, with only that phase's tools.

        The model chooses what to do here because the work genuinely varies -
        which queries this topic needs, which layout this slide wants. What it
        cannot do is leave: the phase ends when the phase's own exit condition
        says so, or when it runs out of turns.
        """
        # The phase's declared toolset, plus whatever this workspace's MCP
        # servers said they belong in. Added rather than merged into the phase
        # table because the second half is not known until the run starts.
        offered = phase.tools | self.mcp_tools_by_phase.get(phase.name, frozenset())
        schemas = [
            schema for schema in self.registry.get_function_schemas()
            if _schema_name(schema) in offered
        ]
        messages = [
            {"role": "system", "content": self._build_system_prompt(
                context, self.working_state
            )}
        ] + chat_messages

        while True:
            if phase.done(self):
                return
            spent = self.working_state.phase_turns.get(phase.name, 0)
            if spent >= phase.max_turns:
                # Out of turns, not out of work. The walker decides what that
                # means: a phase whose exit condition tolerates falling short
                # (research on a thin topic) moves on, and one that does not
                # stalls and ends the run honestly.
                logger.info(
                    "phase_turns_exhausted", phase=phase.name, turns=spent
                )
                return
            self.working_state.phase_turns[phase.name] = spent + 1

            response = None
            try:
                async for kind, payload in self._with_retry_notices(
                    self._chat_with_retry(messages=messages, tools=schemas or None),
                    phase.label,
                ):
                    if kind == "event":
                        yield "event", payload
                    else:
                        response = payload
            except Exception as e:
                logger.error("llm_call_failed", error=str(e), phase=phase.name)
                yield "event", _event("error", phase.label, {
                    "message": _provider_failure_message(e),
                    "code": "provider_unavailable",
                    "timestamp": _iso_now(),
                })
                yield "provider_failed", True
                return

            assistant_message = (response or {}).get("message") or {}
            tool_calls = (response or {}).get("tool_calls") or []

            if assistant_message.get("content"):
                yield "event", _event("thought", phase.label, {
                    "content": assistant_message["content"],
                    "timestamp": _iso_now(),
                })

            if not tool_calls:
                # Nothing was called and the phase is unfinished. Say what is
                # outstanding and give it another turn; the walker's stall
                # detection ends the run if this repeats.
                messages.append({
                    "role": "system",
                    "content": (
                        "You did not call a tool. This step is not finished: "
                        f"{phase.directive(self)}"
                    ),
                })
                continue

            messages.append({**assistant_message, "tool_calls": tool_calls})

            for tool_call in tool_calls:
                fn_name = tool_call.get("function", {}).get("name", "")
                call_id = tool_call.get("id", str(uuid.uuid4()))
                try:
                    fn_args = json.loads(
                        tool_call.get("function", {}).get("arguments", "{}")
                    )
                except json.JSONDecodeError:
                    fn_args = {}

                tool_result = None
                async for kind, payload in self._dispatch_tool(
                    fn_name,
                    fn_args,
                    call_id,
                    context=context,
                    session_id=session_id,
                    current_phase=phase.label,
                ):
                    if kind == "event":
                        yield "event", payload
                    else:
                        tool_result = payload

                if fn_name in PAUSING_TOOLS and tool_result.success:
                    async for event in self._pause_for_user(
                        fn_name, tool_result, session_id
                    ):
                        yield "event", event
                    yield "paused", True
                    return

                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(
                        tool_result.data if tool_result.success
                        else {"error": tool_result.error}
                    ),
                })

            messages[0] = {
                "role": "system",
                "content": self._build_system_prompt(context, self.working_state),
            }

    async def _closing_message(
        self, context: dict, chat_messages: list[dict]
    ) -> AsyncIterator[dict]:
        """The model's own account of what it built, once everything is done."""
        prompt = self._build_system_prompt(context, self.working_state)
        async for kind, payload in self._ask_model(
            prompt, chat_messages, phase_label="complete"
        ):
            if kind == "event":
                yield payload
            elif kind == "result" and str(payload).strip():
                yield _event("thought", "complete", {
                    "content": str(payload).strip(),
                    "timestamp": _iso_now(),
                })

    # ── Running one phase ─────────────────────────────────────────────────

    def _progress_marker(self) -> tuple:
        """Everything a phase could move. Unchanged means it moved nothing."""
        ws = self.working_state
        return (
            ws.brief is not None,
            ws.scouted,
            ws.consulted_before_planning,
            ws.offered_directions,
            ws.web_search_calls,
            len(ws.researched_urls),
            bool(ws.presentation_structure),
            ws.reviewed_outline_with_user,
            len(ws.accumulated_slides),
            ws.design_serial,
            ws.rendered_serial,
            ws.validation_attempts,
            ws.validation_failures,
        )

    async def _run_phase(
        self,
        phase: pipeline.Phase,
        context: dict,
        chat_messages: list[dict],
        session_id: uuid.UUID,
    ) -> AsyncIterator[tuple[str, object]]:
        """Run one phase, whoever drives it.

        Yields ``("event", …)`` throughout, plus ``("paused", True)`` when the
        run is waiting on the user and ``("provider_failed", True)`` when the
        model could not be reached.
        """
        if phase.driver == pipeline.LOOP_DRIVEN:
            handler = getattr(self, f"_phase_{phase.name}")
            async for item in handler(context, chat_messages, session_id):
                yield item
        else:
            async for item in self._model_turns(
                phase, context, chat_messages, session_id
            ):
                yield item

    async def _call_tool(
        self,
        fn_name: str,
        fn_args: dict,
        *,
        context: dict,
        session_id: uuid.UUID,
        phase: pipeline.Phase,
    ) -> AsyncIterator[tuple[str, object]]:
        """Make a tool call the loop decided on, as if the model had made it.

        The same dispatcher either way, so a loop-driven phase gets the
        auto-supplied arguments, the preconditions, the caches and the
        working-state bookkeeping without restating any of it.
        """
        async for kind, payload in self._dispatch_tool(
            fn_name,
            fn_args,
            str(uuid.uuid4()),
            context=context,
            session_id=session_id,
            current_phase=phase.label,
        ):
            yield kind, payload

    async def _ask_model(
        self, prompt: str, chat_messages: list[dict], *, phase_label: str
    ) -> AsyncIterator[tuple[str, object]]:
        """One tool-free call for a phase whose output is text, not an action.

        Yields ``("event", …)`` for any provider back-off, then
        ``("result", <content str>)`` - or ``("provider_failed", True)`` when
        the provider could not be reached, which the caller turns into the end
        of the run.
        """
        messages = [{"role": "system", "content": prompt}] + chat_messages
        response = None
        try:
            async for kind, payload in self._with_retry_notices(
                self._chat_with_retry(messages=messages, tools=None), phase_label
            ):
                if kind == "event":
                    yield "event", payload
                else:
                    response = payload
        except Exception as e:
            logger.error("phase_llm_call_failed", phase=phase_label, error=str(e))
            yield "event", _event("error", phase_label, {
                "message": _provider_failure_message(e),
                "code": "provider_unavailable",
                "timestamp": _iso_now(),
            })
            yield "provider_failed", True
            return
        yield "result", ((response or {}).get("message") or {}).get("content") or ""

    # ── What the pipeline table reads off a run ───────────────────────────
    #
    # Named properties rather than reaching into the loop's internals, so
    # pipeline.py stays a description of the sequence and nothing else.

    @property
    def mode_is_deep(self) -> bool:
        return self.mode == MODE_DEEP

    @property
    def research_min_sources(self) -> int:
        return RESEARCH_MIN_SOURCES

    @property
    def brief_needs_research(self) -> bool:
        """Whether intake judged this deck to stand on facts worth looking up."""
        return bool((self.working_state.brief or {}).get("needsResearch"))

    def slide_directive(self) -> str:
        """What to do with the one slide the drafting phase is currently on."""
        from app.agent.pipeline import _slides_outstanding

        outstanding = _slides_outstanding(self)
        if not outstanding:
            return "Every slide is drafted."
        index = outstanding[0]
        plan = (self.working_state.presentation_structure or {}).get("slides") or []
        planned = plan[index] if index < len(plan) else {}
        title = planned.get("title") or planned.get("heading") or ""
        revising = index in self.working_state.slides_needing_revision

        if revising:
            return (
                f"Revise slide {index + 1}"
                + (f' ("{title}")' if title else "")
                + " - it is the only slide being changed; every other slide is carried "
                "over unchanged. Validation reported:\n"
                + self._describe_validation_issues()
                + "\nShorten the copy, give an oversized visual a smaller region, or move "
                "it onto its own slide, then call design_slide_content for this slide."
            )
        return (
            f"Draft slide {index + 1} of {len(plan) or index + 1}"
            + (f': "{title}"' if title else "")
            + ". Call select_layout for slide_index="
            + f"{index}, then any build_* tool its point needs (a chart, table or metric "
            "row for figures; a block for content with a shape), then "
            f"design_slide_content for slide_index={index}. This slide only."
            + self._structure_directive()
        )

    def _structure_directive(self) -> str:
        """The block the chosen direction leads with, as an instruction.

        The user picked a direction by looking at what its points *looked
        like* - bullets, a row of figures, ruled bands. A deck that then
        writes bullets whatever they chose makes the preview a lie, so the
        shape they saw is named here, where the slide is actually built.
        """
        structure = str(
            ((self.working_state.chosen_direction or {}).get("designSystem") or {})
            .get("structure") or ""
        )
        return _STRUCTURE_DIRECTIVE.get(structure, "")

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
        """Refuse a call that would corrupt the run, with a corrective message
        the model sees as the tool's result.

        Ordering between phases is not enforced here any more - the pipeline
        decides what runs when, and a rule that also refused the loop's own
        calls would deadlock it (a run whose user took the autonomy option
        never offers design directions, and planning must not wait on a pause
        that will never happen). What is left are the invariants a phase cannot
        express: state that would be corrupted, and figures nothing stands
        behind.
        """
        ws = self.working_state

        # extract_key_data mines a document. With none in the session it was
        # being called against web snippets that the model can already read in
        # its own context - six times in one run, each reporting that it had
        # found nothing.
        if (
            fn_name == "extract_key_data"
            and not self._session_has_documents
            and not ws.has_completed_tool("analyze_document")
        ):
            return (
                "There are no uploaded documents in this session, so there is nothing "
                "for extract_key_data to mine. Research findings are already in your "
                "context in full - read them there and move on to the next step."
            )

        if fn_name == "plan_presentation_structure" and ws.accumulated_slides:
            return (
                "Presentation structure cannot be replanned - slide content has "
                "already been designed using the current structure. If the "
                "structure genuinely needs to change, ask the user with "
                "ask_user_question before restructuring; otherwise continue "
                "designing the remaining slides in the existing plan."
            )

        # The backstop for a run with nothing behind its figures: the deck still
        # gets built, but a number nobody can check does not get an axis and a
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

    def _answer_from_context(
        self, fn_name: str, fn_args: dict, context: dict
    ) -> ToolResult | None:
        """A call the loop can answer from what the run already holds.

        Distinct from _check_precondition on purpose: this is not an
        out-of-order call to be corrected, it is a re-read of something already
        read, and the honest answer is the value rather than an error. Rejecting
        it would put a red failed tool call in the feed for a step that did
        nothing wrong.

        Two calls qualify. The design system is re-extracted on every resume -
        three times in one observed run - and the result was written straight
        over ``context["design_system"]``, which on a run past the direction
        pause is the design the *user chose*: a tidy-up was silently discarding
        it. A search already run is the other: re-asking a question returns the
        page it returned last time, and a run told to research from several
        angles will otherwise spend its whole budget circling one.
        """
        if fn_name == "web_search":
            return self._search_already_run(fn_args, context)

        if fn_name != "extract_template_design_system":
            return None

        design_system = context.get("design_system")
        if not design_system:
            return None

        # A colour named after the fact can still be applied - but never over a
        # direction the user picked or a template they uploaded, both of which
        # already answer the question a colour would be re-asking.
        settled = bool(self.working_state.chosen_direction) or (
            str(design_system.get("sourceType") or "default") != "default"
        )
        if str(fn_args.get("color_theme") or "").strip() and not settled:
            return None

        return ToolResult(success=True, data=design_system, duration_ms=0)

    def _search_already_run(self, fn_args: dict, context: dict) -> ToolResult | None:
        """This run's findings for a query it has already asked.

        Returned rather than re-fetched, and it does not spend a search from the
        budget: the backend would hand back the same pages, several seconds
        later, for a slot the run is supposed to spend on a different facet of
        the topic.
        """
        query = _normalize_query(fn_args.get("query"))
        if not query:
            return None

        found = [
            {
                "title": entry.get("title", ""),
                "url": entry.get("url", ""),
                "snippet": entry.get("snippet", ""),
                "published": entry.get("published", ""),
            }
            for entry in context.get("research") or []
            if _normalize_query(entry.get("query")) == query
        ]
        if not found:
            return None

        return ToolResult(
            success=True,
            data={
                "query": fn_args.get("query", ""),
                "results": found,
                "result_count": len(found),
                "note": (
                    "You have already run this search - these are its results, and they "
                    "are in your context in full. Ask a different question about the "
                    "topic or move on to the next step."
                ),
            },
            duration_ms=0,
        )

    async def _dispatch_tool(
        self,
        fn_name: str,
        fn_args: dict,
        call_id: str,
        *,
        context: dict,
        session_id: uuid.UUID,
        current_phase: str,
    ) -> AsyncIterator[tuple[str, object]]:
        """Run one tool call and record everything it changes about the run.

        Yields ``("event", <event>)`` as it goes and ``("result", ToolResult)``
        last. Everything a tool call implies - the arguments the loop supplies
        rather than trusts the model to retype, the preconditions, the caches,
        the working-state bookkeeping - lives here, so a phase the loop drives
        itself gets exactly the same treatment as one the model drove.

        Deliberately not included: appending the tool-role message, refreshing
        the system prompt, and pausing. Those belong to whoever is driving, and
        a loop-driven phase has no message thread to append to.
        """
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
        if fn_name == "propose_design_directions":
            # The session the specimens belong to, and the uploaded
            # template if there is one - neither of which the model
            # knows, and the second of which is the first direction.
            fn_args["session_id"] = str(session_id)
            fn_args["design_system"] = design_system
            # Everything the user has already told us, so all three
            # directions respect it. Passed rather than trusted to the
            # model's own summary: the answers are on the working
            # state, and a paraphrase is where a settled colour goes
            # missing.
            answered = self.working_state.answered_questions
            if answered and not str(fn_args.get("answers") or "").strip():
                fn_args["answers"] = "\n".join(
                    f"{a.get('question', '')} {a.get('answer', '')}".strip()
                    for a in answered
                )
        elif fn_name == "select_layout":
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
            yield "event", _event("thought", "validation", {
                "content": narration,
                "timestamp": _iso_now(),
            })

        # Yield tool_call event
        yield "event", _event("tool_call", current_phase, {
            "toolName": fn_name,
            "toolArgs": fn_args,
            "callId": call_id,
            "timestamp": _iso_now(),
        })

        # Execute tool
        tool_start = time.time()
        tool_result = None
        precondition_error = self._check_precondition(fn_name, fn_args)
        cached_result = (
            None if precondition_error
            else self._answer_from_context(fn_name, fn_args, context)
        )
        if precondition_error:
            tool_result = ToolResult(success=False, error=precondition_error)
        elif cached_result is not None:
            # Already held by the run - answered rather than re-run. See
            # _answer_from_context.
            tool_result = cached_result
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
                    yield "event", payload
                else:
                    tool_result = payload
        tool_duration = int((time.time() - tool_start) * 1000)

        # Extract sources from tool result data (URLs that can be displayed to user)
        sources = []
        if tool_result.success and tool_result.data:
            sources = self._extract_sources(tool_result.data)

        # Yield tool_result event. The summary is what the feed leads
        # with; the raw result stays on the event behind a disclosure.
        yield "event", _event("tool_result", current_phase, {
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
                self.working_state.validated_serial = self.working_state.design_serial
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
                    yield "event", preview_event
                await self._clear_working_state(session_id)


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
            #
            # A direction the user picked is never written over: past
            # that pause, context["design_system"] is their decision,
            # and the planner, the layouts, the copy and the renderer
            # all read it from here.
            if (
                fn_name == "extract_template_design_system"
                and tool_result.data
                and not self.working_state.chosen_direction
            ):
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
                    or fn_name in BLOCK_BUILDER_TOOLS
                    or fn_name == "generate_image") and tool_result.data:
                target = tool_result.data.get("slideIndex")
                block = tool_result.data.get("content")
                if target is not None and block:
                    slots = self.working_state.prepared_visuals.setdefault(target, [])
                    # One block per type, per region, *per slot*: rebuilding
                    # the stat callouts in the right rail must not discard the
                    # ones in the left, and a dashboard's bridge chart must not
                    # discard the mix chart beside it. Region alone was enough
                    # while a slide carried one chart; on a page with no region
                    # tree two charts both bind to the same nothing, and the
                    # second silently replaced the first - the tool reported
                    # success and the exhibit never reached the page.
                    key = slot_key(block)
                    slots[:] = [
                        existing for existing in slots
                        if slot_key(existing) != key
                    ]
                    slots.append(block)

            # compose_slide_regions resolves geometry rather than content,
            # so it attaches to the slide's own regions list. Recomposing
            # replaces the tree outright: two compositions on one slide is
            # the model changing its mind, not adding to the page.
            if fn_name == "compose_slide_regions" and tool_result.data:
                target = tool_result.data.get("slideIndex")
                tree = tool_result.data.get("regions")
                if target is not None and tree:
                    self.working_state.prepared_regions[target] = list(tree)

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

        else:
            self.working_state.last_error = tool_result.error

        logger.info(
            "tool_executed",
            tool_name=fn_name,
            success=tool_result.success,
            duration_ms=tool_duration,
        )
        yield "result", tool_result

    async def _pause_for_user(
        self,
        fn_name: str,
        tool_result: ToolResult,
        session_id: uuid.UUID,
        records_consultation: bool = True,
    ) -> AsyncIterator[dict]:
        """Put a question to the user and stop the run until they answer.

        Both pausing tools return the same payload shape, so the pause itself is
        one path. What differs is the bookkeeping: which checkpoint the answer
        will satisfy when the run resumes.

        The design pause is deliberately NOT recorded in ``answered_questions`` -
        that list is replayed to the model as "do not ask these again", and a
        second pause suppressed by the first one having happened is exactly the
        bug that shape invites.
        """
        data = tool_result.data or {}
        ws = self.working_state
        ws.is_paused_for_user = True
        ws.pending_question = data

        if fn_name == "propose_design_directions":
            ws.offered_directions = True
            ws.proposed_directions = [
                d for d in (data.get("directions") or []) if isinstance(d, dict)
            ]
            waiting = "Waiting for the user to choose a design direction"
        else:
            # Which checkpoint this satisfied is decided by where the run is,
            # not by what the model says it is doing: before a plan exists the
            # question is the consultation, after it the outline review.
            if ws.presentation_structure:
                ws.reviewed_outline_with_user = True
            elif records_consultation:
                # The offer to take over is not the consultation: answering
                # "no" to it leaves every real question still unasked.
                ws.consulted_before_planning = True
            waiting = f"Waiting for user input: {data.get('question')}"

        yield _event("question", "user_input", {
            "question": data.get("question"),
            "options": data.get("options", []),
            "questions": data.get("questions", []),
            "context": data.get("context", ""),
            "timestamp": _iso_now(),
        })
        await self._save_working_state(session_id)
        # The caller returns on seeing this: the run is over until the answer
        # arrives as a new request. A `break` here once left the just-appended
        # assistant tool_calls unanswered, which strict providers reject
        # outright ("must be followed by tool messages responding to each
        # tool_call_id") right where the user was expecting a question.
        yield _event("done", "user_input", {
            "content": waiting,
            "paused": True,
            "timestamp": _iso_now(),
        })

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
                    "archetypes": bundle.archetypes,
                }

        return context

    async def _reopen_outline_if_asked(
        self, chat_messages: list[dict]
    ) -> AsyncIterator[dict]:
        """Walk the run back to planning when the outline review asked for it.

        The review phase completes on ``reviewed_outline_with_user``, which is
        set when the question is *put* rather than when it is answered - so
        both options finished the checkpoint and the walk went on to drafting
        either way. Clearing the plan is what actually reopens it: the ``plan``
        phase completes on ``presentation_structure`` being set, so emptying it
        makes planning outstanding again, and the user's words are already in
        the history the planner is given.

        Everything derived from the outline goes with it: a layout chosen for
        slide 4 of the old plan is not a layout for slide 4 of the new one.

        Scoped to a deck that has not been drafted yet, which is exactly where
        the review checkpoint sits - nothing walks back to ``review`` once
        drafting has started, so an outline answer arriving against a part-built
        deck is not this checkpoint. A change asked for *mid-draft* is #109 and
        is deliberately left alone here rather than silently throwing away
        finished slides.
        """
        ws = self.working_state
        if not ws.reviewed_outline_with_user or not ws.presentation_structure:
            return
        if ws.accumulated_slides:
            return
        if not user_wants_outline_changed(chat_messages):
            return

        ws.presentation_structure = None
        ws.selected_layouts = {}
        ws.slides_needing_revision = []
        # The checkpoint reopens with the plan: the user has to see what their
        # change produced, or the second outline ships less reviewed than the
        # first one did.
        ws.reviewed_outline_with_user = False
        logger.info("outline_reopened_by_user")
        yield _event("thought", "structure_planning", {
            "content": "Replanning the outline around what you asked to change.",
            "timestamp": _iso_now(),
        })

    def _resolve_direction_choice(self, chat_messages: list[dict]) -> dict | None:
        """
        Which direction the user picked, out of the ones they were offered.

        The card sends the direction's name *and* its id, so the id is what
        this matches on and the name is only the fallback - two directions can
        be named similarly, and a user typing a reply of their own will send
        neither. A reply that matches nothing leaves the run on whatever
        design system it already had, which is the safe direction: an
        unrecognised answer must not silently pick one.
        """
        ws = self.working_state
        if not ws.offered_directions or ws.chosen_direction is not None:
            return None
        offered = [d for d in (ws.proposed_directions or []) if isinstance(d, dict)]
        if not offered:
            return None

        said = " ".join(
            str(m.get("content") or "") for m in chat_messages
            if m.get("role") == "user"
        ).lower()
        if not said.strip():
            return None
        for direction in offered:
            if f"direction: {str(direction.get('id') or '').lower()}" in said:
                return direction
        for direction in offered:
            name = str(direction.get("name") or "").strip().lower()
            if name and name in said:
                return direction
        return None

    def _apply_colour_choice(
        self, chosen: dict, chat_messages: list[dict]
    ) -> tuple[dict, str | None]:
        """
        The colour the user picked, applied to the direction they picked.

        Both answers come off one card, so the colour necessarily lands after
        the specimens were drawn - the previews are honest about ground, type,
        motif and structure, and this is where the hue catches up. Read from
        the newest reply only: a hex from earlier in the conversation was
        already settled before the directions were authored, and all three
        were built from it.

        "As proposed" carries no hex, so it resolves to no change - which is
        the same path as a reply that answered the direction and ignored the
        colour.
        """
        from app import directions as direction_model

        latest = next(
            (str(m.get("content") or "") for m in reversed(chat_messages)
             if m.get("role") == "user"),
            "",
        )
        found = _HEX_IN_TEXT.search(latest)
        if not found:
            return chosen, None
        wanted = found.group(0).upper()

        recoloured = direction_model.recolour(chosen, wanted)
        if recoloured is chosen or recoloured == chosen:
            # An uploaded template, an unreadable pairing, or the colour it was
            # already in. Saying nothing beats claiming a change that did not
            # happen.
            logger.info("direction_recolour_skipped",
                        direction=chosen.get("id"), wanted=wanted)
            return chosen, None
        logger.info("direction_recoloured", direction=chosen.get("id"),
                    color=wanted)
        return recoloured, wanted

    def _clean_up_direction_previews(self, session_id: uuid.UUID, keep: str | None) -> None:
        """
        Drop the specimens of the directions that were not chosen.

        They are throwaway by design and there are nine of them per run; the
        chosen one is kept because the card that offered it stays in the
        transcript and would otherwise show a broken image.
        """
        try:
            from app.renderer import specimen

            specimen.cleanup_directions(str(session_id), keep=keep)
        except Exception as e:  # pragma: no cover - never fail a run over this
            logger.warning("direction_preview_cleanup_failed", error=str(e))

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

    def _next_step(self) -> str:
        """What this run does next, read off the pipeline rather than guessed.

        The model is only ever asked to work inside one phase, so this is the
        phase's own directive. It is the last thing in the prompt because it is
        the only part that changes as the run moves.
        """
        phase = pipeline.next_phase(self)
        if phase is None:
            return (
                "The deck is rendered and validated. Tell the user what you built, in "
                "a few sentences - no download link, no file path."
            )
        return phase.directive(self) or (
            f"The {phase.name} step is running. Wait for it to finish."
        )

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
            "4. THE DECK IS PLANNED WITH THE USER, NOT AT THEM. The run stops to consult "
            "them, to let them pick a design, and to show them the outline before it is "
            "written. You do not schedule those pauses and you cannot skip them - they are "
            "steps of the run, and the step you are on is stated at the end of this prompt. "
            "Do that step.",
            "4b. Do not restate a question in prose. The question card carries it, with its "
            "options, and a numbered copy above it is the same interrogation twice. Say what "
            "you are about to do in a sentence, then do it.",
            "5. WHEN YOU DO ASK SOMETHING, ASK IT PROPERLY. Every question carries 2-5 "
            "suggested options (a real array), concrete and mutually distinct, so the user "
            "can answer by tapping. OPTIONS MUST BE TAILORED TO THE ACTUAL REQUEST, never "
            "generic placeholders: for content focus, name the 2-4 candidate angles you "
            "actually saw in the material (not 'Option A/B/C'); for data presentation, offer "
            "the concrete alternatives for THAT data ('Line chart of revenue 2011-2025', "
            "'Table of year-by-year figures', 'Keep it as bullets') rather than asking "
            "abstractly whether the user likes charts.",
            "6. THE LOOK AND THE COLOUR ARE BOTH SETTLED BY propose_design_directions. Never ask "
            "the user to choose between 'professional', 'warm', 'bold' in words - they cannot see "
            "what those mean, and it spends a pause on a question the specimen slides answer "
            "better. Do not ask about colour in the consultation either: that call carries the "
            "colour question itself, next to the rendered directions, with options drawn from the "
            "subject. If the user names a colour of their own accord, pass it to "
            "propose_design_directions as brand_color and all three directions are built from it. "
            "When the user uploaded a template, colour is not asked at all: its own palette is "
            "used as-is, and it is offered back to them as the first direction.",
            "7. EXCEPTION: if the user has explicitly said not to ask questions (e.g. 'don't ask me "
            "anything', 'just build it', 'use your best judgment', 'no more questions'), make the most "
            "reasonable assumption yourself and proceed without asking - briefly note any significant "
            "assumption in the relevant slide's notes instead. All three checkpoints lift automatically "
            "in that case; you do not need to ask permission to skip them.",
            "8. Each checkpoint is one pause. Fold everything you need into that call rather "
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
            + ("analyze_document, extract_key_data, " if self._session_has_documents else "")
            + "plan_presentation_structure and "
            "design_slide_content are told this automatically - you do not need to pass it "
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
        # Described only when the session actually has something to read - a
        # tool list that names a tool the run has withheld is the prompt and
        # the loop disagreeing, which the model resolves by trying it anyway.
        document_tools = [
            "analyze_document — Extract and summarize content from uploaded documents",
            "extract_key_data — Pull specific data points out of a document that "
            "analyze_document has read",
        ] if self._session_has_documents else []
        image_tools = [
            "generate_image — Generate a grounded illustration for a cover, a "
            "divider or a conceptual idea. It must illustrate something the "
            "deck established (you name which points or sources), and must "
            "never carry figures, axes or labels. Prefer build_diagram for "
            "structure and build_chart for quantity"
        ] if self.image_connection is not None else []
        tool_lines = [
            *document_tools,
            "extract_template_design_system — Parse a template's design system, or get the built-in default (omit template_path). Call it once; if a design system is already in your context below, it is already loaded",
            *research_tool,
            "propose_design_directions — Author three whole design systems for this deck and "
            "render them as specimen slides for the user to choose between (pauses the "
            "workflow; this is how the deck's look is decided)",
            "plan_presentation_structure — Plan the slide structure and outline",
            "select_layout — Choose the best layout for each slide based on content",
            "compose_slide_regions — Divide one slide into named areas so it can carry "
            "more than one exhibit, when none of the template's own compositions fits. "
            "Returns the region ids to pass as build_* region_id",
            "build_chart — Build a validated chart for a slide from typed arguments (categories, series, axis titles). Use this rather than writing chart JSON yourself",
            "build_chart_grid — Build a grid of small charts, one per segment, all on one shared scale. Use it to say whether a pattern holds everywhere or one segment is carrying it",
            *image_tools,
            "build_diagram — Draw a structural graphic: process flow, 2x2 matrix, "
            "funnel, timeline, pyramid, value chain, Venn or org chart. Use it when "
            "the point is a STRUCTURE rather than a quantity - a chart shows how "
            "much, a diagram shows how it fits together",
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
            "- A slide showing ONE thing needs no regions: build the exhibit and "
            "let it take the page. A slide with two or more genuinely different "
            "things to show, and no template composition that fits, is composed "
            "first with compose_slide_regions and then each exhibit is built with "
            "the region_id it belongs in. Give two exhibits of the same type "
            "different slot names so both survive.",
            "- Composing a page is not a reason to fill it. Four exhibits "
            "answering the same question read worse than one answering it once, "
            "and an exhibit built from figures the material does not have is the "
            "worst outcome available. Compose because the content needs the room.",
            "- Units belong in the series name or the axis title, never inside the "
            "numbers: \"Revenue (US$ billions)\" with values [29.1, 32.3], not "
            "[\"29.1B\", \"32.3B\"].",
            "- Pick the chart type by the question the slide asks, not by habit. "
            "A bar chart is the answer to \"how do these compare\" and to nothing "
            "else, and a deck whose every exhibit is a bar chart has asked one "
            "question of everything: "
            "line for change over time; bar for comparison across categories, or "
            "barh when the category names are long; stacked for what a total is "
            "made of; stacked100 for what changed underneath a total that barely "
            "moved; waterfall for where the movement between two totals came from "
            "(the revenue or cost bridge); slope for who moved up and who moved "
            "down between two dates; dumbbell for the size of a gap - plan against "
            "actual, us against peers; bullet for whether a measure is hitting its "
            "target; heatmap for the hot spots in a dense matrix; combo for a "
            "volume and a rate that only mean something together; donut or pie for "
            "parts of one whole; scatter for a relationship between two measures.",
            "- Do not state a second axis, a target or a waterfall subtotal by "
            "leaving it to be worked out from the numbers. Say which series is the "
            "rate, what each target is, which bars are totals. A chart that "
            "guesses is wrong in a way that looks fine.",
            "- A visual REPLACES the prose it shows. Once a chart is attached, the "
            "slide's bullets should interpret it, not repeat its figures.",
            "- Only ever chart or tabulate figures that genuinely appear in the source "
            "material, the research results or the conversation. Never invent, "
            "estimate or round-trip a number you are not sure of.",
            "",
        ])
        prompt_parts.append("## Tools")
        prompt_parts.append(
            "What each tool does. You are only offered the ones the current step needs, "
            "so a tool you expected but cannot see belongs to a step that is not this one:"
        )
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

        prompt_parts.extend([
            "",
            "Explain your reasoning in a sentence before each tool call.",
            "",
            # Last, because it is the only part of this prompt that changes as
            # the run moves, and the workflow above describes a whole run rather
            # than this moment in one.
            "## Next Step",
            "",
            self._next_step(),
            "",
            "Do that, and only that. It is derived from what this run has actually "
            "completed. The tools you have been given are the ones this step needs; "
            "re-running something already done is not progress.",
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


def _credit_generated_images(slide_data: dict) -> dict:
    """Put a visible credit on any slide carrying a generated image.

    As a footnote, not as bespoke chrome: the apparatus is already compiled,
    measured and rendered on both the template and the default path, so a credit
    that goes through it cannot be dropped by whichever path a deck happens to
    take. A credit only the default renderer drew would be missing on exactly
    the decks that matter most - the ones built on a customer's own template.

    This is not optional. A picture is the easiest thing on a slide to mistake
    for a photograph of something real, and the credit is the reader's only way
    to tell.
    """
    contents = slide_data.get("contents")
    if not isinstance(contents, list):
        return slide_data

    credits = []
    for item in contents:
        if not isinstance(item, dict):
            continue
        if str(item.get("provenance") or "") != "generated":
            continue
        line = str(item.get("credit") or "").strip()
        if line and line not in credits:
            credits.append(line)
    if not credits:
        return slide_data

    existing = [
        str(note).strip() for note in (slide_data.get("footnotes") or [])
        if str(note or "").strip()
    ]
    added = [line for line in credits if line not in existing]
    if not added:
        return slide_data
    return {**slide_data, "footnotes": [*existing, *added]}


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