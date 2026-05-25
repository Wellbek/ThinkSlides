from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.session import get_db
from app.db.models import ChatSession, ChatMessage, AgentEvent
from app.agent.loop import AgentLoop
from app.observability.logging import get_logger
from pydantic import BaseModel
from typing import Optional
import uuid

logger = get_logger(__name__)

# Carry the full path prefix on the router itself so this endpoint resolves
# regardless of how api/__init__.py mounts it. Combined with main.py's
# `prefix="/api/v1"`, the final URL is /api/v1/sessions/{id}/messages,
# matching the orchestrator's AIServiceClient.SendMessage call.
router = APIRouter(tags=["sessions"])


class SendMessageRequest(BaseModel):
    content: str
    attachments: list = []


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    req: SendMessageRequest,
    db: AsyncSession = Depends(get_db),
):
    """Send a message to a chat session and trigger agent generation."""
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session_id")

    result = await db.execute(
        select(ChatSession).where(ChatSession.id == sid)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Store user message
    user_msg = ChatMessage(
        session_id=sid,
        role="user",
        content=req.content,
        attachments=req.attachments,
    )
    db.add(user_msg)
    await db.commit()
    await db.refresh(user_msg)

    # Store assistant placeholder
    assistant_msg = ChatMessage(
        session_id=sid,
        role="assistant",
        content="",
    )
    db.add(assistant_msg)
    await db.commit()
    await db.refresh(assistant_msg)

    # Enqueue agent generation job
    # In production this goes through the orchestrator Redis queue.
    # For direct AI-service invocation, run agent loop in background.
    import asyncio
    asyncio.create_task(_run_agent(db, sid, user_msg.id, assistant_msg.id))

    return {
        "message_id": str(assistant_msg.id),
        "session_id": str(sid),
    }


async def _run_agent(
    db: AsyncSession,
    session_id: uuid.UUID,
    user_message_id: uuid.UUID,
    assistant_message_id: uuid.UUID,
):
    """Run the agent loop and persist events."""
    try:
        loop = AgentLoop(db=db)
        async for event in loop.run(session_id=session_id):
            # Persist each agent event
            agent_event = AgentEvent(
                session_id=session_id,
                message_id=assistant_message_id,
                event_type=event.get("type", "unknown"),
                phase=event.get("phase"),
                content=event.get("content", {}),
                tool_name=event.get("toolName"),
                tool_args=event.get("toolArgs"),
                tool_result=event.get("toolResult"),
                duration_ms=event.get("durationMs"),
                token_count=event.get("tokenCount"),
            )
            db.add(agent_event)
            await db.commit()

            logger.info(
                "agent_event",
                event_type=event.get("type"),
                phase=event.get("phase"),
                session_id=str(session_id),
            )
    except Exception as e:
        logger.error("agent_loop_failed", session_id=str(session_id), error=str(e))
        # Persist error event
        error_event = AgentEvent(
            session_id=session_id,
            message_id=assistant_message_id,
            event_type="error",
            phase="content_analysis",
            content={"message": str(e)},
        )
        db.add(error_event)
        await db.commit()