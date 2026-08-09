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
from app.agent.registry import get_registry, ToolResult
from app.llm.provider import build_chat_provider, get_llm_provider
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


def normalize_mode(mode: str | None) -> str:
    """Map any caller-supplied value onto a known mode, defaulting to simple."""
    return MODE_DEEP if mode == MODE_DEEP else MODE_SIMPLE


# How much content depth a generation should aim for. Mirrors the
# orchestrator's handler.DetailLevel* constants.
DETAIL_LEVEL_CONCISE = "concise"
DETAIL_LEVEL_DETAILED = "detailed"
DETAIL_LEVEL_COMPREHENSIVE = "comprehensive"
DETAIL_LEVELS = {DETAIL_LEVEL_CONCISE, DETAIL_LEVEL_DETAILED, DETAIL_LEVEL_COMPREHENSIVE}


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
    "generate_image_prompt": "content_generation",
    "finalize_and_render_pptx": "rendering",
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
        self.current_step_index: int = 0
        self.pending_question: dict | None = None
        self.is_paused_for_user: bool = False
        self.has_called_finalize: bool = False
        self.completed_tools: dict[str, dict] = {}  # tool_name -> {args, result, timestamp}
        self.last_error: str | None = None
        self.answered_questions: list[dict] = []  # Track questions that have been answered
        self.web_search_calls: int = 0  # Cap repeated web_search calls per run
        self.MAX_WEB_SEARCH_CALLS = 3

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

    def get_slides_json(self) -> str:
        """Get accumulated slides as JSON string."""
        return json.dumps(self.accumulated_slides)

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
            for i, slide in enumerate(self.accumulated_slides):
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
        self.current_step_index = 0
        self.pending_question = None
        self.is_paused_for_user = False
        self.has_called_finalize = False
        self.completed_tools = {}
        self.last_error = None
        self.answered_questions = []
        self.web_search_calls = 0


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

    # A rate limit is a wait, not a failure. Three retries covers the usual
    # per-minute window without letting a genuinely broken key hang the run.
    MAX_LLM_RETRIES = 3
    RETRY_BASE_SECONDS = 5.0

    def __init__(
        self,
        db: AsyncSession,
        mode: str | None = None,
        org_id: uuid.UUID | None = None,
        detail_level: str | None = None,
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
        self.redis_client = None
        self.mode = normalize_mode(mode) if mode is not None else None
        self.detail_level = normalize_detail_level(detail_level) if detail_level is not None else None
        self.org_id = org_id
        self.search_config: ai_settings.SearchConfig | None = None
        self._search_token = None
        self._provider_token = None

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

        row = None
        if self.org_id is not None:
            try:
                row = await ai_settings.load_row(self.db, self.org_id)
            except Exception as e:  # noqa: BLE001 - never fail a run over settings
                logger.error(
                    "settings_load_failed", organisation_id=str(self.org_id), error=str(e)
                )

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
            "current_step_index": self.working_state.current_step_index,
            "completed_tools": self.working_state.completed_tools,
            "has_called_finalize": self.working_state.has_called_finalize,
            "last_error": self.working_state.last_error,
            "pending_question": self.working_state.pending_question,
            "answered_questions": self.working_state.answered_questions,
            "web_search_calls": self.working_state.web_search_calls,
            # Carried so a run paused on a question resumes at the same effort
            # level - the resume request has no mode of its own.
            "mode": self.mode,
            # Issue #20: without these, resuming after ask_user_question forced
            # a re-extraction of the design system and lost all accumulated web
            # research, since only WorkingState (not context) was persisted.
            "design_system": self.context.get("design_system"),
            "research": self.context.get("research"),
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
            self.working_state.current_step_index = state_dict.get("current_step_index", 0)
            self.working_state.completed_tools = state_dict.get("completed_tools", {})
            self.working_state.has_called_finalize = state_dict.get("has_called_finalize", False)
            self.working_state.last_error = state_dict.get("last_error")
            self.working_state.pending_question = state_dict.get("pending_question")
            self.working_state.answered_questions = state_dict.get("answered_questions", [])
            self.working_state.web_search_calls = state_dict.get("web_search_calls", 0)
            # Only inherit the saved mode when the caller did not pick one, so
            # a resumed run keeps its effort level while a fresh message is
            # free to change it.
            if self.mode is None:
                self.mode = normalize_mode(state_dict.get("mode"))
            # Issue #20: stashed here since context itself is rebuilt fresh by
            # _load_context after this call - _run merges these in afterwards.
            self._resumed_design_system = state_dict.get("design_system")
            self._resumed_research = state_dict.get("research")
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

        self.context = context

        # ── Phase 2: Build messages ──────────────────
        chat_messages = await self._build_chat_messages(session_id)

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
            try:
                response = await self._chat_with_retry(
                    messages=messages,
                    tools=tool_schemas if tool_schemas else None,
                )
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
                # MANDATORY: Can only exit if finalize_and_render_pptx was called
                if not self.working_state.has_called_finalize:
                    # Agent is trying to exit without completing the task
                    # Feed back an instruction to continue
                    logger.warning(
                        "agent_exited_early",
                        session_id=str(session_id),
                        iteration=iteration,
                        message="LLM responded without tool calls before finalize was called",
                    )
                    # Add a system message forcing the agent to continue
                    messages.append({
                        "role": "system",
                        "content": (
                            "You have NOT completed the task. You MUST continue using tools "
                            "until you call finalize_and_render_pptx. Do not stop with just text responses. "
                            "If you need user input, use the ask_user_question tool (available in your tools). "
                            "Otherwise, continue with the next step in the presentation generation workflow."
                        ),
                    })
                    continue  # Skip the break and continue the loop

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
                elif fn_name == "plan_presentation_structure":
                    fn_args["design_system_summary"] = json.dumps(design_system)
                    fn_args["detail_level"] = self.detail_level
                elif fn_name == "design_slide_content":
                    fn_args["design_system_context"] = json.dumps(design_system)
                    fn_args["detail_level"] = self.detail_level
                    slide_index = fn_args.get("slide_index")
                    if slide_index in self.working_state.selected_layouts:
                        fn_args["layout_info"] = json.dumps(
                            self.working_state.selected_layouts[slide_index]
                        )
                elif fn_name == "finalize_and_render_pptx":
                    # Needed so generated tables/charts can be styled from
                    # the template's own brand colors instead of a generic
                    # default - see table_style.resolve_table_style.
                    fn_args["design_system_json"] = json.dumps(design_system)

                # Yield tool_call event
                yield _event("tool_call", current_phase, {
                    "toolName": fn_name,
                    "toolArgs": fn_args,
                    "callId": call_id,
                    "timestamp": _iso_now(),
                })

                # Execute tool
                tool_start = time.time()
                precondition_error = self._check_precondition(fn_name, fn_args)
                if precondition_error:
                    tool_result = ToolResult(success=False, error=precondition_error)
                elif fn_name == "finalize_and_render_pptx" and self.working_state.accumulated_slides:
                    # Trust the slides actually designed via design_slide_content
                    # over whatever the LLM re-typed as the argument string - the
                    # latter is a verbatim re-serialization of the whole deck and
                    # is the single biggest truncation/drift risk on multi-slide
                    # presentations.
                    fn_args["slides_json"] = self.working_state.get_slides_json()
                    tool_result = await self._execute_tool(fn_name, fn_args)
                elif fn_name == "web_search":
                    # Cap repeated web searches so the agent cannot loop on
                    # low-quality results. After the cap, return a gentle
                    # "use existing research" result instead of executing.
                    if self.working_state.web_search_calls >= self.working_state.MAX_WEB_SEARCH_CALLS:
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
                        self.working_state.web_search_calls += 1
                        tool_result = await self._execute_tool(fn_name, fn_args)
                else:
                    tool_result = await self._execute_tool(fn_name, fn_args)
                tool_duration = int((time.time() - tool_start) * 1000)

                # Extract sources from tool result data (URLs that can be displayed to user)
                sources = []
                if tool_result.success and tool_result.data:
                    sources = self._extract_sources(tool_result.data)

                # Yield tool_result event
                yield _event("tool_result", current_phase, {
                    "toolName": fn_name,
                    "callId": call_id,
                    "result": tool_result.data if tool_result.success else tool_result.error,
                    "durationMs": tool_duration,
                    "success": tool_result.success,
                    "tokenCount": tool_result.token_count,
                    "timestamp": _iso_now(),
                    "sources": sources if sources else None,
                })

                # If a slide was finalized, emit slide_finalized for each slide
                if fn_name == "finalize_and_render_pptx" and tool_result.success:
                    self.working_state.has_called_finalize = True
                    preview_urls = tool_result.data.get("previewUrls", [])

                    # Emit slide_finalized events for each slide with preview URL
                    for slide_idx, preview_url in enumerate(preview_urls):
                        slide_data = None
                        if slide_idx < len(self.working_state.accumulated_slides):
                            slide_data = self.working_state.accumulated_slides[slide_idx]

                        yield _event("slide_finalized", "rendering", {
                            "slideIndex": slide_idx,
                            "slideData": slide_data,
                            "previewUrl": preview_url,
                            "timestamp": _iso_now(),
                        })

                    # Clear saved working state - presentation is complete
                    await self._clear_working_state(session_id)

                # If ask_user_question was called, emit question event and pause
                if fn_name == "ask_user_question" and tool_result.success:
                    self.working_state.is_paused_for_user = True
                    self.working_state.pending_question = tool_result.data
                    yield _event("question", "user_input", {
                        "question": tool_result.data.get("question"),
                        "options": tool_result.data.get("options", []),
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
                            self.working_state.add_slide(tool_result.data)

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

                    # Accumulate web_search results into context so they persist
                    # across rebuilds - the LLM can ground all slides on them
                    # without re-searching, and the sources stay available.
                    if fn_name == "web_search" and tool_result.data and tool_result.data.get("results"):
                        research = context.setdefault("research", [])
                        for item in tool_result.data["results"]:
                            research.append({
                                "query": tool_result.data.get("query", ""),
                                "title": item.get("title", ""),
                                "url": item.get("url", ""),
                                "snippet": item.get("snippet", ""),
                            })
                        # Bound the research context to avoid unbounded prompt growth.
                        if len(research) > 15:
                            context["research"] = research[-15:]

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

        if fn_name in ("select_layout", "design_slide_content", "generate_image_prompt"):
            if not ws.presentation_structure:
                return (
                    f"{fn_name} requires a presentation structure. Call "
                    "plan_presentation_structure first."
                )

        if fn_name == "design_slide_content":
            slide_index = fn_args.get("slide_index")
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
        from sqlalchemy import select
        from app.db.models import ChatSession, DesignSystemBundle

        context: dict = {}

        result = await self.db.execute(
            select(ChatSession).where(ChatSession.id == session_id)
        )
        session = result.scalar_one_or_none()
        if not session:
            return context

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
                }

        return context

    async def _chat_with_retry(self, messages: list[dict], tools: list[dict] | None):
        """Call the chat model, retrying transient rate limits.

        A 429 part-way through a generation used to abandon the whole run, and
        with it every slide already designed - which on a low-tier API key
        happens routinely, since one presentation makes dozens of calls in a
        couple of minutes. Providers tell us how long to wait; when they do not,
        exponential backoff is a reasonable guess.
        """
        delay = self.RETRY_BASE_SECONDS
        for attempt in range(self.MAX_LLM_RETRIES + 1):
            try:
                return await self.llm.chat(messages=messages, tools=tools)
            except Exception as e:
                if attempt == self.MAX_LLM_RETRIES or not _is_retryable(e):
                    raise
                wait = _retry_after_seconds(e) or delay
                logger.warning(
                    "llm_call_retrying",
                    attempt=attempt + 1,
                    wait_seconds=round(wait, 2),
                    error=str(e)[:200],
                )
                await asyncio.sleep(wait)
                delay *= 2

    def _tool_schemas_for_mode(self) -> list[dict]:
        """The tool schemas this run may call, filtered by effort level."""
        schemas = self.registry.get_function_schemas()
        if self.mode == MODE_DEEP:
            return schemas

        def name_of(schema: dict) -> str:
            return schema.get("function", {}).get("name", schema.get("name", ""))

        return [s for s in schemas if name_of(s) not in DEEP_ONLY_TOOLS]

    def _build_system_prompt(self, context: dict, working_state: WorkingState | None = None) -> str:
        """Build the system prompt with embedded design system context."""
        prompt_parts = [
            "You are ThinkSlides, an AI assistant that generates professional PowerPoint presentations.",
            "You follow a ReAct (Reason-Act-Observe) pattern to create presentations step by step.",
            "",
            "## CRITICAL RULES - YOU MUST FOLLOW",
            "",
            "1. You MUST always call tools until you complete the presentation.",
            "2. You MUST call finalize_and_render_pptx as the final step to generate the PPTX file.",
            "3. NEVER respond with only text and stop - this will cause the generation to fail.",
            "4. DEFAULT TO ASKING, NOT GUESSING, at every real decision point. This includes at least: "
            "target audience; desired scope/slide count; tone; WHICH PARTS OF THE SOURCE MATERIAL TO "
            "FOCUS ON OR EMPHASIZE when it covers more than can reasonably fit; how to resolve ambiguous "
            "or conflicting information in what was provided; and VISUAL STYLE / COLOR PREFERENCE "
            "whenever no template was uploaded (so the built-in default design system will be used) and "
            "the user hasn't already indicated one. Call ask_user_question rather than guessing whenever "
            "a wrong assumption would produce a presentation the user did not want. This is the default; "
            "only skip asking when you are genuinely confident in the answer, not merely when an answer "
            "is possible.",
            "5. EVERY ask_user_question call MUST include well-chosen suggested options via the "
            "'options' parameter (a real array, 2-5 items) - concrete, mutually distinct choices the "
            "user can pick in one tap, not just an open-ended question with nothing to react to. "
            "OPTIONS MUST BE TAILORED TO THE ACTUAL REQUEST, never generic placeholders: for content "
            "focus, name the 2-4 candidate angles/sections you actually saw in the material (not "
            "'Option A/B/C') and let the user pick or say 'cover them all'.",
            "6. Color/style questions ONLY when no template was uploaded - never ask about colors if "
            "the user gave a template, since its own colors are always used as-is and the question "
            "would be meaningless. When you do ask: if the topic clearly implies a real brand/entity "
            "with a known color (e.g. a presentation about a specific company), offer that brand's "
            "actual color as one option using its real hex code (e.g. 'Starbucks green (#00704A)'), "
            "alongside 2-3 of these general presets as other options - Professional, Warm, Monochrome, "
            "Bold, Nature. Whatever is chosen, pass the SAME value (the hex code, or the preset's "
            "lowercase name) as color_theme to BOTH extract_template_design_system and "
            "finalize_and_render_pptx so the design system you see and what actually renders agree.",
            "7. EXCEPTION: if the user has explicitly said not to ask questions (e.g. 'don't ask me "
            "anything', 'just build it', 'use your best judgment', 'no more questions'), make the most "
            "reasonable assumption yourself and proceed without asking - briefly note any significant "
            "assumption in the relevant slide's notes instead.",
            "8. Prefer asking before planning, batching multiple uncertainties into ONE consolidated "
            "ask_user_question rather than asking one at a time. But do not suppress a genuine question "
            "just because planning has already started - if a new ambiguity only becomes apparent "
            "partway through (e.g. conflicting figures in an uploaded document), ask then too rather "
            "than silently guessing.",
            "9. Do NOT ask about things you can already infer confidently from the conversation or "
            "uploaded documents, or about minor stylistic choices with no real stakes - that is noise, "
            "not the kind of question this rule is asking for.",
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
            "plan_presentation_structure and design_slide_content are told this automatically - "
            "you do not need to pass it yourself - but keep your own reasoning and notes "
            "consistent with it rather than defaulting to the shortest possible phrasing.",
            "",
        ]

        # The two effort levels differ in whether the agent may research the
        # topic on the web. Describing only the tools it actually has keeps the
        # prompt honest - in simple mode web_search is not in the schema list.
        if self.mode == MODE_DEEP:
            prompt_parts.extend([
                "## Researching Facts & Citing Sources (Deep Thinking is ON)",
                "",
                "- When the topic involves real-world facts, statistics, history, or current information that you may not know precisely, call web_search FIRST and base your content on the returned results.",
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
            "design_slide_content — Generate slide content (text, image prompts, tables, charts)",
            "validate_slide_json — Validate slide JSON against the schema before rendering",
            "generate_image_prompt — Create prompts for AI-generated images",
            "ask_user_question — Ask the user for clarification or input (pauses workflow)",
            "finalize_and_render_pptx — Render the final PPTX file (MUST be called last)",
        ]
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
            prompt_parts.append("## Research Findings (from web_search)")
            prompt_parts.append(
                "Base your factual content on these findings. Do NOT call web_search again for "
                "topics already covered below. These sources are shown to the user as citations."
            )
            for idx, r in enumerate(context["research"], 1):
                prompt_parts.append(
                    f"{idx}. [{r.get('title', '')}]({r.get('url', '')}) - {r.get('snippet', '')}"
                )
            prompt_parts.append("")

        research_step = ["For factual topics, research with web_search before writing content"] \
            if self.mode == MODE_DEEP else []
        workflow_steps = [
            "If documents are provided, analyze them first with analyze_document",
            "Extract key data with extract_key_data",
            "If no design system is in your context, get the built-in default with extract_template_design_system (no template_path)",
            *research_step,
            "If anything about the request is genuinely unsure/ambiguous, ask ONE consolidated question with ask_user_question before planning, unless the user already said not to ask (see CRITICAL RULES above)",
            "Plan the presentation structure with plan_presentation_structure",
            "For each slide, select a layout with select_layout",
            "Generate content for each slide with design_slide_content",
            "Validate all slides with validate_slide_json",
            "Render with finalize_and_render_pptx - THIS IS MANDATORY",
        ]
        prompt_parts.append("## Workflow")
        prompt_parts.extend(f"{i}. {step}" for i, step in enumerate(workflow_steps, 1))
        prompt_parts.extend([
            "",
            "Always think step by step. Explain your reasoning before each tool call.",
            "NEVER stop until you have called finalize_and_render_pptx.",
        ])

        return "\n".join(prompt_parts)

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


def _event(event_type: str, phase: str, data: dict) -> dict:
    """Create a typed agent event dict."""
    return {
        "type": event_type,
        "phase": phase,
        **data,
    }


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()