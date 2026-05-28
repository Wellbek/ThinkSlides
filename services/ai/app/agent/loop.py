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
import asyncio
import redis.asyncio as redis

logger = get_logger(__name__)


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
        self.redis = None

    async def run(
        self,
        session_id: uuid.UUID,
    ) -> AsyncIterator[dict]:
        """
        Run the full agent loop for a session.
        Yields typed SSE events as dictionaries.
        """
        self.session_id = session_id  # Store for broadcasting
        
        # Initialize Redis client
        redis_client = None
        try:
            redis_client = await redis.from_url(settings.redis_url, decode_responses=True)
        except ImportError:
            logger.warning("redis_not_installed", error="redis package not installed")
        except Exception as e:
            logger.warning("redis_init_failed", error=str(e))
        
        async def broadcast(event: dict):
            """Broadcast event to Redis for orchestrator SSE hub."""
            if redis_client:
                try:
                    await redis_client.publish(
                        f"agent:events:{session_id}",
                        json.dumps(event)
                    )
                except Exception as e:
                    logger.warning("redis_publish_failed", error=str(e))
        
        import_timestamp = _iso_now()

        # ── Phase 1: Load context ────────────────────
        event = _event("thought", "content_analysis", {
            "content": "Loading session context and design system...",
            "timestamp": import_timestamp,
        })
        yield event
        await broadcast(event)

        context = await self._load_context(session_id)

        # ── Phase 2: Build messages ──────────────────
        system_prompt = self._build_system_prompt(context)
        chat_messages = await self._build_chat_messages(session_id)

        messages = [{"role": "system", "content": system_prompt}] + chat_messages

        # ── Phase 3: ReAct loop ──────────────────────
        tool_schemas = self.registry.get_function_schemas()

        for iteration in range(self.MAX_ITERATIONS):
            event = _event("thought", "content_analysis", {
                "content": f"Agent iteration {iteration + 1}",
                "timestamp": _iso_now(),
            })
            yield event
            await broadcast(event)

            # Call LLM
            start_time = time.time()
            try:
                response = await self.llm.chat(
                    messages=messages,
                    tools=tool_schemas if tool_schemas else None,
                )
            except Exception as e:
                logger.error("llm_call_failed", error=str(e), iteration=iteration)
                event = _event("error", "content_analysis", {
                    "message": f"LLM call failed: {str(e)}",
                    "timestamp": _iso_now(),
                })
                yield event
                await broadcast(event)
                break

            duration_ms = int((time.time() - start_time) * 1000)

            # Parse response
            assistant_message = response.get("message", {})
            tool_calls = response.get("tool_calls", [])

            # Yield thought event
            if assistant_message.get("content"):
                event = _event("thought", "content_analysis", {
                    "content": assistant_message["content"],
                    "timestamp": _iso_now(),
                })
                yield event
                await broadcast(event)

            # If no tool calls, agent is done reasoning
            if not tool_calls:
                event = _event("thought", "complete", {
                    "content": "Agent completed reasoning.",
                    "timestamp": _iso_now(),
                })
                yield event
                await broadcast(event)
                
                # Broadcast final done event
                done_event = _event("done", "complete", {
                    "session_id": str(session_id),
                    "timestamp": _iso_now(),
                })
                yield done_event
                await broadcast(done_event)
                break

            # Add assistant message to conversation
            messages.append(assistant_message)

            # Execute tool calls (parallel where independent)
            for tool_call in tool_calls:
                fn_name = tool_call.get("function", {}).get("name", "")
                fn_args_str = tool_call.get("function", {}).get("arguments", "{}")
                call_id = tool_call.get("id", str(uuid.uuid4()))

                try:
                    fn_args = json.loads(fn_args_str)
                except json.JSONDecodeError:
                    fn_args = {}

                # Yield tool_call event
                event = _event("tool_call", "content_analysis", {
                    "toolName": fn_name,
                    "toolArgs": fn_args,
                    "callId": call_id,
                    "timestamp": _iso_now(),
                })
                yield event
                await broadcast(event)

                # Execute tool
                tool_start = time.time()
                tool_result = await self._execute_tool(fn_name, fn_args)
                tool_duration = int((time.time() - tool_start) * 1000)

                # Yield tool_result event
                result_event = _event("tool_result", "content_analysis", {
                    "toolName": fn_name,
                    "callId": call_id,
                    "result": tool_result.data if tool_result.success else tool_result.error,
                    "durationMs": tool_duration,
                    "tokenCount": tool_result.token_count,
                    "timestamp": _iso_now(),
                })
                yield result_event
                await broadcast(result_event)

                # If a slide was finalized, emit slide_finalized
                if fn_name == "finalize_and_render_pptx" and tool_result.success:
                    slide_event = _event("slide_finalized", "content_generation", {
                        "slideIndex": fn_args.get("slide_index", 0),
                        "slideData": tool_result.data,
                        "timestamp": _iso_now(),
                    })
                    yield slide_event
                    await broadcast(slide_event)

                # Feed result back to conversation
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(
                        tool_result.data if tool_result.success else {"error": tool_result.error}
                    ),
                })

                logger.info(
                    "tool_executed",
                    tool_name=fn_name,
                    success=tool_result.success,
                    duration_ms=tool_duration,
                )
        
        # Clean up Redis connection
        if redis_client:
            await redis_client.close()

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

    def _build_system_prompt(self, context: dict) -> str:
        """Build the system prompt with embedded design system context."""
        prompt_parts = [
            "You are SlideAgent, an AI assistant that generates professional PowerPoint presentations.",
            "You follow a ReAct (Reason-Act-Observe) pattern to create presentations step by step.",
            "",
            "## Available Tools",
            "Use the provided function tools to accomplish each step:",
            "1. analyze_document — Extract and summarize content from uploaded documents",
            "2. extract_key_data — Pull specific data points from documents",
            "3. extract_template_design_system — Parse a template's design system (palette, fonts, layouts)",
            "4. plan_presentation_structure — Plan the slide structure and outline",
            "5. select_layout — Choose the best layout for each slide based on content",
            "6. design_slide_content — Generate slide content (text, image prompts, tables, charts)",
            "7. validate_slide_json — Validate slide JSON against the schema before rendering",
            "8. generate_image_prompt — Create prompts for AI-generated images",
            "9. finalize_and_render_pptx — Render the final PPTX file",
            "",
        ]

        if context.get("design_system"):
            import json as _json
            prompt_parts.append("## Design System Context")
            prompt_parts.append("The following design system was extracted from the user's template.")
            prompt_parts.append("You MUST follow these design tokens when generating content:")
            prompt_parts.append("```json")
            prompt_parts.append(_json.dumps(context["design_system"], indent=2))
            prompt_parts.append("```")
            prompt_parts.append("")

        prompt_parts.extend([
            "## Workflow",
            "1. If documents are provided, analyze them first with analyze_document",
            "2. Extract key data with extract_key_data",
            "3. Plan the presentation structure with plan_presentation_structure",
            "4. For each slide, select a layout with select_layout",
            "5. Generate content for each slide with design_slide_content",
            "6. Validate all slides with validate_slide_json",
            "7. Render with finalize_and_render_pptx",
            "",
            "Always think step by step. Explain your reasoning before each tool call.",
        ])

        return "\n".join(prompt_parts)

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