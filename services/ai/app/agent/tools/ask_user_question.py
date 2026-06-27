"""
Tool: ask_user_question
Pauses the agent workflow and presents a question with preselected options to the user.
The workflow can only resume when the user provides an answer or additional context.
"""

from __future__ import annotations
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.observability.logging import get_logger
import json
import time

logger = get_logger(__name__)


class AskUserQuestionTool:
    """Pause the agent workflow and ask the user a question with options."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="ask_user_question",
            description=(
                "Pause the presentation generation workflow and ask the user a question "
                "with preselected options. Use this when you need clarification or user input "
                "to proceed. The workflow will pause and resume only after the user responds."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="question",
                    type="string",
                    description="The question to ask the user",
                ),
                ToolParameter(
                    name="options",
                    type="string",
                    description='JSON array of option strings, e.g., ["Option A", "Option B", "Option C"]',
                ),
                ToolParameter(
                    name="context",
                    type="string",
                    description="Additional context about why this question is being asked",
                    required=False,
                ),
            ],
        )

    @classmethod
    async def run(
        cls,
        question: str,
        options: str,
        context: str = "",
    ) -> ToolResult:
        start = time.time()
        try:
            # Parse options
            try:
                option_list = json.loads(options) if options else []
            except json.JSONDecodeError:
                option_list = [options] if options else []

            duration_ms = int((time.time() - start) * 1000)

            logger.info(
                "ask_user_question",
                question=question,
                option_count=len(option_list),
                context=context,
            )

            return ToolResult(
                success=True,
                data={
                    "question": question,
                    "options": option_list,
                    "context": context,
                    "type": "user_question",
                },
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.error("ask_user_question_failed", error=str(e))
            return ToolResult(success=False, error=str(e), duration_ms=int((time.time() - start) * 1000))