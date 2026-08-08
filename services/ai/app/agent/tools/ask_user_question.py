"""
Tool: ask_user_question
Pauses the agent workflow and presents a question with preselected options to the user.
The workflow can only resume when the user provides an answer or additional context.
"""

from __future__ import annotations
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.agent.tools._json_extract import coerce_json_arg
from app.observability.logging import get_logger
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
                    type="array",
                    items={"type": "string"},
                    description=(
                        "2-5 concrete, mutually distinct suggested answers the user can pick "
                        "in one tap - not a placeholder list. Tailor them to what you actually "
                        "know about the request (e.g. for a Starbucks presentation with no "
                        "template, suggest 'Starbucks green' alongside other real options, not "
                        "generic labels)."
                    ),
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
        options: list[str] | str,
        context: str = "",
    ) -> ToolResult:
        start = time.time()
        try:
            # coerce_json_arg handles the now-expected case (a native list,
            # matching the "array" schema type below) and, defensively, a
            # JSON-encoded string some providers might still send. A plain
            # non-JSON string is treated as a single option rather than
            # dropped - the model occasionally sends just one choice.
            coerced = coerce_json_arg(options, None)
            if isinstance(coerced, list):
                option_list = [str(o) for o in coerced]
            elif isinstance(options, str) and options:
                option_list = [options]
            else:
                option_list = []

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