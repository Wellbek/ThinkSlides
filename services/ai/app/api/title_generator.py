"""
Chat Title Generation

Uses a cheap flash GLM model to generate concise titles from user queries.
"""

from typing import Optional
import httpx
from app.agent.tools._today import today_line
from app.config import settings
from app.observability.logging import get_logger

logger = get_logger(__name__)

# Zhipu GLM chat completions endpoint (same default the zhipuai SDK uses).
ZHIPU_CHAT_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"


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
                "content": (
                    f"{today_line()} "
                    "You are a title generator. Generate a concise, descriptive title "
                    "(max 50 characters) for a chat conversation based on the user's "
                    "first message. Return ONLY the title, nothing else."
                )
            },
            {
                "role": "user",
                "content": f"Generate a title for this chat: {first_user_message}"
            }
        ]

        # GLM flash models are reasoning models: with thinking enabled they
        # emit reasoning_content and leave `content` empty (and the reasoning
        # is slow enough to trip the SDK's request timeout). The zhipuai SDK
        # pinned here (v2.0.1) does not support the `thinking` parameter, so
        # call the HTTP API directly with thinking disabled. This returns a
        # short, direct title in `content` quickly and deterministically.
        payload = {
            "model": settings.zhipu_flash_model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 100,
            "thinking": {"type": "disabled"},
        }
        headers = {
            "Authorization": f"Bearer {settings.zhipu_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(ZHIPU_CHAT_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        title = data["choices"][0]["message"].get("content", "").strip()

        # Clean up the title - remove quotes, limit length
        title = title.strip('"\'').strip()

        # Ensure title is not empty and not too long
        if not title or len(title) == 0:
            return None

        if len(title) > 50:
            title = title[:47] + "..."

        logger.info("title_generated", title=title, original_message_length=len(first_user_message))
        return title

    except httpx.HTTPStatusError as e:
        logger.error(
            "title_generation_failed",
            error=f"HTTP {e.response.status_code}: {e.response.text[:200]}",
        )
        return None
    except Exception as e:
        logger.error("title_generation_failed", error=f"{type(e).__name__}: {e}")
        return None