"""
Tool: ask_user_question

Pauses the agent workflow and puts one or more questions to the user, each with
suggested answers they can pick in a single tap. The workflow resumes only once
they reply.

A run may ask several things at once. Consulting someone properly needs more
than one answer - who the deck is for, which angle to take, how long it should
be, what it should look like - and asking those one at a time meant a pause,
a round trip and a fresh LLM call for each, which in practice made the agent
skip the consultation altogether. The single-question form is still accepted,
both for the model that sends it and for the questions already persisted in
older sessions.
"""

from __future__ import annotations
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.agent.tools._json_extract import coerce_json_arg
from app.observability.logging import get_logger
import time

logger = get_logger(__name__)

# Enough to settle a deck's direction in one exchange, not so many that the
# card becomes a form to fill in.
MAX_QUESTIONS = 4
MAX_OPTIONS = 5
MIN_OPTIONS = 2


def _normalize_options(raw) -> list[str]:
    """Coerce whatever the model sent for one question's options into a list."""
    coerced = coerce_json_arg(raw, None)
    if isinstance(coerced, list):
        options = [str(o).strip() for o in coerced if str(o).strip()]
    elif isinstance(raw, str) and raw.strip():
        # A single choice sent as a bare string, rather than dropped.
        options = [raw.strip()]
    else:
        options = []
    # Duplicates read as a mistake to the user and give them nothing to choose
    # between, so collapse them while keeping the model's ordering.
    seen: set[str] = set()
    unique: list[str] = []
    for option in options:
        key = option.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(option)
    return unique[:MAX_OPTIONS]


class AskUserQuestionTool:
    """Pause the agent workflow and ask the user one or more questions."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="ask_user_question",
            description=(
                "Pause the presentation workflow and consult the user. Ask everything "
                "you are unsure about in ONE call by passing several questions - the "
                "audience, the angle to focus on, the depth, the visual style - rather "
                "than pausing repeatedly. Every question must carry 2-5 concrete "
                "suggested answers tailored to this specific request. The workflow "
                "resumes only after the user responds."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="questions",
                    type="array",
                    required=False,
                    items={
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string",
                                "description": "One thing you need to know, asked plainly.",
                            },
                            "options": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "2-5 concrete, mutually distinct answers the user can "
                                    "pick in one tap, tailored to what you actually know "
                                    "about this request - never generic placeholders like "
                                    "'Option A'. For a focus question, name the real "
                                    "candidate angles you saw in the material; for a "
                                    "colour question about a known brand, give that "
                                    "brand's own colour with its real hex code."
                                ),
                            },
                        },
                        "required": ["question", "options"],
                    },
                    description=(
                        f"Up to {MAX_QUESTIONS} questions to put to the user at once, each "
                        "with its own suggested answers."
                    ),
                ),
                ToolParameter(
                    name="question",
                    type="string",
                    required=False,
                    description=(
                        "A single question, when there is genuinely only one thing to "
                        "ask. Prefer 'questions' whenever there is more than one."
                    ),
                ),
                ToolParameter(
                    name="options",
                    type="array",
                    required=False,
                    items={"type": "string"},
                    description="Suggested answers for the single-question form.",
                ),
                ToolParameter(
                    name="context",
                    type="string",
                    required=False,
                    description=(
                        "Why you are asking, and anything the user needs in front of them "
                        "to answer - the planned slide outline, the conflicting figures "
                        "you found. Shown above the questions."
                    ),
                ),
            ],
        )

    @classmethod
    async def run(
        cls,
        questions: list | str | None = None,
        question: str = "",
        options: list[str] | str | None = None,
        context: str = "",
    ) -> ToolResult:
        start = time.time()

        def elapsed() -> int:
            return int((time.time() - start) * 1000)

        try:
            items = coerce_json_arg(questions, None)
            normalized: list[dict] = []
            hoisted_context = ""

            if isinstance(items, list):
                for item in items[:MAX_QUESTIONS]:
                    if isinstance(item, dict):
                        text = str(item.get("question", "")).strip()
                        item_options = _normalize_options(item.get("options"))
                        # Models routinely attach the context to the question
                        # it belongs to rather than to the call. Observed with
                        # the outline review, where the outline itself - the
                        # whole point of the question - ended up on the inner
                        # object and was dropped, leaving the user asked to
                        # approve a plan they could not see.
                        if not hoisted_context and item.get("context"):
                            hoisted_context = str(item["context"]).strip()
                    else:
                        # A bare list of strings: the model sent the questions
                        # without their options.
                        text = str(item).strip()
                        item_options = []
                    if text:
                        normalized.append({"question": text, "options": item_options})

            if not normalized and question.strip():
                normalized.append(
                    {"question": question.strip(), "options": _normalize_options(options)}
                )

            if not normalized:
                return ToolResult(
                    success=False,
                    error=(
                        "No question was provided. Pass 'questions' as a list of "
                        "{question, options} objects (or a single 'question' with "
                        "'options')."
                    ),
                    duration_ms=elapsed(),
                )

            thin = [q["question"] for q in normalized if len(q["options"]) < MIN_OPTIONS]
            if thin:
                return ToolResult(
                    success=False,
                    error=(
                        "Every question needs at least "
                        f"{MIN_OPTIONS} concrete suggested answers, and these have fewer: "
                        + "; ".join(f'"{q}"' for q in thin)
                        + ". Give each one 2-5 real, mutually distinct choices drawn from "
                        "this request and the material you have read - the user should be "
                        "able to answer by tapping, not by writing a brief. Then call "
                        "ask_user_question again."
                    ),
                    duration_ms=elapsed(),
                )

            logger.info(
                "ask_user_question",
                question_count=len(normalized),
                option_counts=[len(q["options"]) for q in normalized],
                context=context,
            )

            return ToolResult(
                success=True,
                data={
                    "questions": normalized,
                    "context": context.strip() or hoisted_context,
                    # The first question also fills the single-question fields,
                    # which the chat-message rendering and any session saved
                    # before multi-question support still read.
                    "question": normalized[0]["question"],
                    "options": normalized[0]["options"],
                    "type": "user_question",
                },
                duration_ms=elapsed(),
            )
        except Exception as e:
            logger.error("ask_user_question_failed", error=str(e))
            return ToolResult(success=False, error=str(e), duration_ms=elapsed())
