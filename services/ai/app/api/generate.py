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
import json
import redis.asyncio as redis
import datetime

logger = get_logger(__name__)

# Carry the full path prefix on the router itself so this endpoint resolves
# regardless of how api/__init__.py mounts it. Combined with main.py's
# `prefix="/api/v1"`, the final URL is /api/v1/sessions/{id}/messages,
# matching the orchestrator's AIServiceClient.SendMessage call.
router = APIRouter(tags=["sessions"])

# Redis client (adjust URL as needed)
redis_client = redis.from_url("redis://redis:6379", decode_responses=True)


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
    asyncio.create_task(_run_agent(sid, user_msg.id, assistant_msg.id))

    # Return immediately – the real work happens in the background
    return {
        "message_id": str(assistant_msg.id),
        "session_id": str(sid),
    }


async def _run_agent(
    session_id: uuid.UUID,
    user_message_id: uuid.UUID,
    assistant_message_id: uuid.UUID,
):
    from app.db.session import async_session
    import datetime
    import traceback

    logger.info("_run_agent STARTED for session %s", session_id)

    try:
        async with async_session() as db:
            logger.info("_run_agent: got db session for %s", session_id)
            
            final_content = ""
            try:
                logger.info("_run_agent: creating AgentLoop for %s", session_id)
                loop = AgentLoop(db=db)
                
                logger.info("_run_agent: starting loop.run() for %s", session_id)
                event_count = 0
                
                async for event in loop.run(session_id=session_id):
                    event_count += 1
                    logger.info(
                        "_run_agent: got event #%d type=%s for session %s",
                        event_count, event.get("type"), session_id
                    )
                    
                    # Persist event
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

                    # Publish to Redis
                    event_json = json.dumps(event, default=str)
                    pub_result = await redis_client.publish(
                        f"agent:events:{session_id}", event_json
                    )
                    logger.info(
                        "_run_agent: published event type=%s to Redis, subscribers=%d",
                        event.get("type"), pub_result
                    )

                    # Capture any final content emitted
                    if event.get("type") == "message_complete":
                        final_content = event.get("content", "")

                logger.info(
                    "_run_agent: loop finished, got %d events for session %s",
                    event_count, session_id
                )

                # FALLBACK: ensure we have text to show
                if not final_content:
                    final_content = "Presentation generation completed. Check the preview panel for your slides."

                # Save directly into the assistant placeholder
                await db.execute(
                    "UPDATE chat_messages SET content = :content WHERE id = :id",
                    {"content": final_content, "id": assistant_message_id},
                )
                await db.commit()
                logger.info("_run_agent: saved assistant message for %s", session_id)

                done_event = {
                    "type": "done",
                    "message_id": str(assistant_message_id),
                    "content": final_content,
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                }
                pub_result = await redis_client.publish(
                    f"agent:events:{session_id}", json.dumps(done_event)
                )
                logger.info(
                    "_run_agent: published DONE event for session %s, subscribers=%d",
                    session_id, pub_result
                )

            except Exception as inner_e:
                logger.error(
                    "_run_agent: INNER EXCEPTION for session %s: %s\n%s",
                    session_id, str(inner_e), traceback.format_exc()
                )
                raise  # re-raise to be caught by outer handler

    except Exception as e:
        logger.error(
            "_run_agent: OUTER EXCEPTION for session %s: %s\n%s",
            session_id, str(e), traceback.format_exc()
        )
        
        # Try to save fallback and publish done event even on error
        try:
            async with async_session() as db:
                fallback = f"An error occurred: {str(e)}"
                await db.execute(
                    "UPDATE chat_messages SET content = :content WHERE id = :id",
                    {"content": fallback, "id": assistant_message_id},
                )
                await db.commit()

                done_event = {
                    "type": "done",
                    "message_id": str(assistant_message_id),
                    "content": fallback,
                    "error": str(e),
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                }
                await redis_client.publish(
                    f"agent:events:{session_id}", json.dumps(done_event)
                )
                logger.info("_run_agent: published ERROR done event for %s", session_id)
        except Exception as final_e:
            logger.error(
                "_run_agent: CRITICAL - couldn't even save fallback: %s", str(final_e)
            )