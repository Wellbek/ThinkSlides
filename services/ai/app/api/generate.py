from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
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
            generated_files = []
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

                    # Track generated files from tool_result events
                    if event.get("type") == "tool_result" and event.get("toolName") == "finalize_and_render_pptx":
                        tool_result_data = event.get("toolResult") or event.get("result")
                        generated_file_info = {}
                        if tool_result_data and isinstance(tool_result_data, dict):
                            generated_file_info = tool_result_data
                        elif isinstance(tool_result_data, str):
                            try:
                                generated_file_info = json.loads(tool_result_data)
                            except (json.JSONDecodeError, TypeError):
                                generated_file_info = {}

                        if generated_file_info and generated_file_info.get("success", True):
                            # Save to generated_files table
                            file_path = generated_file_info.get("filePath", "")
                            file_name = generated_file_info.get("fileName", "presentation.pptx")
                            file_size = generated_file_info.get("fileSizeBytes", 0)
                            slide_count = generated_file_info.get("slideCount", 0)
                            file_type = "pptx"

                            try:
                                file_id = str(uuid.uuid4())
                                await db.execute(
                                    text(
                                        """INSERT INTO generated_files 
                                           (id, organisation_id, session_id, file_type, file_path, 
                                            original_filename, file_size_bytes, metadata, created_at)
                                           SELECT :fid, organisation_id, :sid, :ftype, :fpath,
                                                  :fname, :fsize, :fmeta, NOW()
                                           FROM chat_sessions WHERE id = :sid"""
                                    ),
                                    {
                                        "fid": file_id,
                                        "sid": str(session_id),
                                        "ftype": file_type,
                                        "fpath": file_path,
                                        "fname": file_name,
                                        "fsize": file_size,
                                        "fmeta": json.dumps({"slideCount": slide_count}),
                                    },
                                )
                                await db.commit()

                                # Emit file_generated SSE event
                                file_event = {
                                    "type": "file_generated",
                                    "phase": "rendering",
                                    "fileId": file_id,
                                    "fileName": file_name,
                                    "fileUrl": f"/api/files/{file_id}",
                                    "fileSizeBytes": file_size,
                                    "fileType": file_type,
                                    "slideCount": slide_count,
                                    "timestamp": datetime.datetime.utcnow().isoformat(),
                                }
                                pub_result = await redis_client.publish(
                                    f"agent:events:{session_id}", json.dumps(file_event, default=str)
                                )
                                logger.info(
                                    "_run_agent: published file_generated event for session %s, file_id=%s, subscribers=%d",
                                    session_id, file_id, pub_result,
                                )
                                generated_files.append(file_event)
                            except Exception as db_err:
                                logger.error(
                                    "_run_agent: failed to save generated file for session %s: %s",
                                    session_id, str(db_err),
                                )

                logger.info(
                    "_run_agent: loop finished, got %d events for session %s",
                    event_count, session_id
                )

                # FALLBACK: ensure we have text to show
                if not final_content:
                    if generated_files:
                        file_names = ", ".join(f["fileName"] for f in generated_files)
                        final_content = f"Presentation generated: {file_names}"
                    else:
                        final_content = "Presentation generation completed. Check the preview panel for your slides."

                # Save directly into the assistant placeholder
                await db.execute(
                    text("UPDATE chat_messages SET content = :content WHERE id = :id"),
                    {"content": final_content, "id": assistant_message_id},
                )
                await db.commit()
                logger.info("_run_agent: saved assistant message for %s", session_id)

                done_event = {
                    "type": "done",
                    "message_id": str(assistant_message_id),
                    "content": final_content,
                    "files": generated_files if generated_files else None,
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
                    text("UPDATE chat_messages SET content = :content WHERE id = :id"),
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
