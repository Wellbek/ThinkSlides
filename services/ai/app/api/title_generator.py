"""
Chat Title Generation

Uses a cheap flash GLM model to generate concise titles from user queries.
"""

from typing import Optional
from app.config import settings
from app.llm.provider import get_llm_provider
from app.observability.logging import get_logger

logger = get_logger(__name__)


async def generate_chat_title(first_user_message: str) -> Optional[str]:
    """
    Generate a concise title for a chat based on the first user message.

    Uses the cheap flash GLM model for fast, economical title generation.

    Args:
        first_user_message: The content of the first user message.

    Returns:
        A generated title (max 50 characters) or None if generation fails.
    """
    if not first_user_message or len(first_user_message.strip()) == 0:
        return None

    try:
        # Use a specific prompt for title generation
        messages = [
            {
                "role": "system",
                "content": "You are a title generator. Generate a concise, descriptive title (max 50 characters) for a chat conversation based on the user's first message. Return ONLY the title, nothing else."
            },
            {
                "role": "user",
                "content": f"Generate a title for this chat: {first_user_message}"
            }
        ]

        # Create a provider with the flash model
        from app.llm.provider import ZhipuProvider

        class FlashTitleProvider(ZhipuProvider):
            def __init__(self):
                from zhipuai import ZhipuAI
                self.client = ZhipuAI(api_key=settings.zhipu_api_key)
                self.model = settings.zhipu_flash_model  # Use flash model

        provider = FlashTitleProvider()
        result = await provider.chat(
            messages=messages,
            temperature=0.7,
            max_tokens=100,  # We only need a short title
        )

        title = result.get("message", {}).get("content", "").strip()

        # Clean up the title - remove quotes, limit length
        title = title.strip('"\'').strip()

        # Ensure title is not empty and not too long
        if not title or len(title) == 0:
            return None

        if len(title) > 50:
            title = title[:47] + "..."

        logger.info("title_generated", title=title, original_message_length=len(first_user_message))
        return title

    except Exception as e:
        logger.error("title_generation_failed", error=str(e))
        return None