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
from app.agent.registry import get_registry, ToolResult
from app.llm.provider import get_llm_provider
from app.observability.logging import get_logger
from app.config import settings
import json
import time
import uuid
import os
import redis.asyncio as redis

logger = get_logger(__name__)

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
        """Add a slide to the accumulated list."""
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

        # Special handling for specific tool results
        if "plan_presentation_structure" in self.completed_tools:
            result = self.completed_tools["plan_presentation_structure"]["result"]
            if isinstance(result, dict) and "structure" in result:
                lines.append("### Presentation Structure (Already Planned)")
                lines.append("```json")
                lines.append(json.dumps(result["structure"], indent=2))
                lines.append("```")
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
                title = slide.get("contents", [{}])[0].get("runs", [{}])[0].get("text", f"Slide {i+1}")
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

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.registry = get_registry()
        self.llm = get_llm_provider()
        self.working_state = WorkingState()
        self.redis_client = None

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
            "current_step_index": self.working_state.current_step_index,
            "completed_tools": self.working_state.completed_tools,
            "has_called_finalize": self.working_state.has_called_finalize,
            "last_error": self.working_state.last_error,
            "pending_question": self.working_state.pending_question,
            "answered_questions": self.working_state.answered_questions,
            "web_search_calls": self.working_state.web_search_calls,
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
            self.working_state.current_step_index = state_dict.get("current_step_index", 0)
            self.working_state.completed_tools = state_dict.get("completed_tools", {})
            self.working_state.has_called_finalize = state_dict.get("has_called_finalize", False)
            self.working_state.last_error = state_dict.get("last_error")
            self.working_state.pending_question = state_dict.get("pending_question")
            self.working_state.answered_questions = state_dict.get("answered_questions", [])
            self.working_state.web_search_calls = state_dict.get("web_search_calls", 0)
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
        """
        import_timestamp = _iso_now()

        # Try to load previous working state (from a paused question)
        state_loaded = await self._load_working_state(session_id)

        # Only reset if no previous state was found
        if not state_loaded:
            self.working_state.reset()

        # ── Phase 1: Load context ────────────────────
        yield _event("thought", "content_analysis", {
            "content": "Loading session context and design system...",
            "timestamp": import_timestamp,
        })

        context = await self._load_context(session_id)

        # ── Phase 2: Build messages ──────────────────
        chat_messages = await self._build_chat_messages(session_id)

        # ── Phase 3: ReAct loop ──────────────────────
        tool_schemas = self.registry.get_function_schemas()
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
                response = await self.llm.chat(
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

                # Yield tool_call event
                yield _event("tool_call", current_phase, {
                    "toolName": fn_name,
                    "toolArgs": fn_args,
                    "callId": call_id,
                    "timestamp": _iso_now(),
                })

                # Execute tool
                tool_start = time.time()
                if fn_name == "web_search":
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
                    # Break the loop - workflow will resume on next user message
                    break

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

                    # Special handling for tools that produce important state
                    if fn_name == "plan_presentation_structure":
                        if tool_result.data and "structure" in tool_result.data:
                            self.working_state.presentation_structure = tool_result.data["structure"]

                    if fn_name == "design_slide_content":
                        if tool_result.data and "slide" in tool_result.data:
                            self.working_state.add_slide(tool_result.data["slide"])

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
                }

        return context

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
            "4. If you need clarification, use the ask_user_question tool (if available).",
            "5. If you don't have enough information, make reasonable assumptions and proceed.",
            "",
            "The system will NOT accept completion until finalize_and_render_pptx is called.",
            "",
            "## Templates & Design System",
            "",
            "- If the user uploaded a template, its design system is already in your context below - use it directly and do NOT call extract_template_design_system.",
            "- If the user has NO template or asks for a 'default' template, call extract_template_design_system with NO template_path (or template_path='default'). It returns a built-in default design system. NEVER invent a filename like 'default_template.pptx' - it does not exist and will fail.",
            "- Do NOT ask the user to upload a template if they already said to use a default. Proceed with the built-in default.",
            "",
            "## Researching Facts & Citing Sources",
            "",
            "- When the topic involves real-world facts, statistics, history, or current information that you may not know precisely, call web_search FIRST and base your content on the returned results.",
            "- Do NOT call web_search for opinions, the user's own documents, or topics you already know well - avoid unnecessary calls.",
            "- The web_search results are shown to the user as sources, so prefer it whenever a claim benefits from a citation.",
            "",
            "## Available Tools",
            "Use the provided function tools to accomplish each step:",
            "1. analyze_document — Extract and summarize content from uploaded documents",
            "2. extract_key_data — Pull specific data points from documents",
            "3. extract_template_design_system — Parse a template's design system, or get the built-in default (omit template_path)",
            "4. web_search — Search the web for real facts and return cited sources",
            "5. plan_presentation_structure — Plan the slide structure and outline",
            "6. select_layout — Choose the best layout for each slide based on content",
            "7. design_slide_content — Generate slide content (text, image prompts, tables, charts)",
            "8. validate_slide_json — Validate slide JSON against the schema before rendering",
            "9. generate_image_prompt — Create prompts for AI-generated images",
            "10. ask_user_question — Ask the user for clarification or input (pauses workflow)",
            "11. finalize_and_render_pptx — Render the final PPTX file (MUST be called last)",
            "",
        ]

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
            import json as _json2
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

        prompt_parts.extend([
            "## Workflow",
            "1. If documents are provided, analyze them first with analyze_document",
            "2. Extract key data with extract_key_data",
            "3. If no design system is in your context, get the built-in default with extract_template_design_system (no template_path)",
            "4. For factual topics, research with web_search before writing content",
            "5. Plan the presentation structure with plan_presentation_structure",
            "6. For each slide, select a layout with select_layout",
            "7. Generate content for each slide with design_slide_content",
            "8. Validate all slides with validate_slide_json",
            "9. Render with finalize_and_render_pptx - THIS IS MANDATORY",
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