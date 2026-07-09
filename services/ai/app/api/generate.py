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
import asyncio
import redis.asyncio as redis
import datetime
import time
import traceback

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


class SimplePresentationRequest(BaseModel):
    title: str
    slide1_title: str = "Introduction"
    slide1_content: list[str] = []
    slide2_title: str = "Conclusion"
    slide2_content: list[str] = []
    template_path: str = ""


class AnswerQuestionRequest(BaseModel):
    answer: str
    selected_option: str = ""


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

    async def run_agent_with_handler():
        try:
            await _run_agent(sid, user_msg.id, assistant_msg.id)
        except Exception as e:
            logger.error("_run_agent failed", error=str(e), traceback=traceback.format_exc())

    asyncio.create_task(run_agent_with_handler())

    # Return immediately – the real work happens in the background
    return {
        "message_id": str(assistant_msg.id),
        "session_id": str(sid),
    }


async def _run_agent(
    session_id: uuid.UUID,
    user_message_id: uuid.UUID,
    assistant_message_id: uuid.UUID,
    answer: str = "",
    question_text: str = "",
):
    from app.db.session import async_session
    import datetime
    import traceback

    logger.info("_run_agent STARTED for session %s (answer=%s)", session_id, len(answer) > 0)

    overall_start_time = time.time()

    # Get or create session start time for timer tracking
    session_start_time_key = f"session_start_time:{session_id}"
    session_start_time = await redis_client.get(session_start_time_key)
    if not session_start_time:
        # First message in this session - set the start time
        session_start_time = str(overall_start_time)
        await redis_client.set(session_start_time_key, session_start_time, ex=86400)  # 24 hours
        logger.info("Set session start time for %s", session_id)
    else:
        session_start_time = float(session_start_time)
    # If this is a resume (answer provided), record the answered question
    if answer and question_text:
        try:
            async with async_session() as db:
                # Get the working state to add answered question
                redis_key = f"working_state:{session_id}"
                state_json = await redis_client.get(redis_key)
                if state_json:
                    state_dict = json.loads(state_json)
                    answered_questions = state_dict.get("answered_questions", [])
                    answered_questions.append({
                        "question": question_text,
                        "answer": answer,
                        "timestamp": datetime.datetime.utcnow().isoformat(),
                    })
                    state_dict["answered_questions"] = answered_questions
                    await redis_client.set(
                        redis_key,
                        json.dumps(state_dict),
                        ex=3600,
                    )
                    logger.info("Recorded answered question in working state for session %s", session_id)
        except Exception as e:
            logger.error("Failed to record answered question: %s", str(e))

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

                    # Emit timer update every 5 events
                    if event_count % 5 == 0:
                        elapsed_seconds = time.time() - float(session_start_time)
                        elapsed_ms = int(elapsed_seconds * 1000)
                        timer_event = {
                            "type": "timer_update",
                            "elapsedMs": elapsed_ms,
                            "elapsedSeconds": elapsed_seconds,
                            "eventCount": event_count,
                        }
                        await redis_client.publish(
                            f"agent:events:{session_id}", json.dumps(timer_event)
                        )

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

                    # Capture question event for user-facing assistant message
                    if event.get("type") == "question":
                        question_text = event.get("question", "")
                        options = event.get("options", [])
                        context = event.get("context", "")
                        question_content = f"Question: {question_text}\n"
                        if options:
                            question_content += "\nOptions:\n"
                            for i, opt in enumerate(options, 1):
                                question_content += f"{i}. {opt}\n"
                        if context:
                            question_content += f"\nContext: {context}\n"
                        final_content = question_content

                        # Store the question as a proper assistant message with metadata
                        # This ensures it appears in chat history and can be tracked
                        question_msg = ChatMessage(
                            session_id=session_id,
                            role="assistant",
                            content=question_content,
                            attachments=json.dumps({
                                "type": "question",
                                "question": question_text,
                                "options": options,
                                "context": context,
                            }),
                        )
                        db.add(question_msg)
                        await db.commit()
                        await db.refresh(question_msg)
                        logger.info(
                            "Stored question as assistant message for session %s",
                            session_id
                        )

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

                overall_duration_ms = int((time.time() - overall_start_time) * 1000)
                total_elapsed_ms = int((time.time() - float(session_start_time)) * 1000)

                done_event = {
                    "type": "done",
                    "message_id": str(assistant_message_id),
                    "content": final_content,
                    "files": generated_files if generated_files else None,
                    "overallDurationMs": overall_duration_ms,
                    "totalElapsedMs": total_elapsed_ms,
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
            overall_duration_ms = int((time.time() - overall_start_time) * 1000)
            total_elapsed_ms = int((time.time() - float(session_start_time)) * 1000)

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
                    "overallDurationMs": overall_duration_ms,
                    "totalElapsedMs": total_elapsed_ms,
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


@router.post("/sessions/{session_id}/simple-fallback")
async def simple_fallback(
    session_id: str,
    req: SimplePresentationRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Simple fallback endpoint for generating a basic 2-slide PPTX without the full agent loop.
    Useful for quick tests and when the agent loop is failing.
    """
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session_id")

    from app.renderer.pptx_renderer import PPTXRenderer
    import os
    import time

    logger.info("simple_fallback: generating 2-slide presentation for session %s", session_id)

    # Build simple slide structure
    slides = [
        {
            "slideIndex": 0,
            "title": req.title,
            "layoutType": "Title Slide",
            "content": {
                "title": req.title,
                "subtitle": "Generated by ThinkSlides",
            },
        },
        {
            "slideIndex": 1,
            "title": req.slide1_title,
            "layoutType": "Content Slide",
            "content": {
                "title": req.slide1_title,
                "points": req.slide1_content or ["Point 1", "Point 2", "Point 3"],
            },
        },
        {
            "slideIndex": 2,
            "title": req.slide2_title,
            "layoutType": "Content Slide",
            "content": {
                "title": req.slide2_title,
                "points": req.slide2_content or ["Summary point 1", "Summary point 2"],
            },
        },
    ]

    # Determine output path
    output_dir = os.environ.get("OUTPUT_DIR", "/app/output")
    os.makedirs(output_dir, exist_ok=True)

    output_id = str(uuid.uuid4())
    output_path = os.path.join(output_dir, f"{output_id}.pptx")

    # Render the PPTX
    template_path = req.template_path if req.template_path and os.path.exists(req.template_path) else None
    renderer = PPTXRenderer(template_path=template_path)
    result_path = renderer.render(
        slides=slides,
        metadata={"title": req.title, "author": "ThinkSlides"},
        output_path=output_path,
    )

    file_size = os.path.getsize(result_path) if os.path.exists(result_path) else 0

    # Save to database
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
            "sid": str(sid),
            "ftype": "pptx",
            "fpath": result_path,
            "fname": f"{req.title}.pptx",
            "fsize": file_size,
            "fmeta": json.dumps({"slideCount": len(slides), "generator": "simple_fallback"}),
        },
    )
    await db.commit()

    # Emit file_generated event
    file_event = {
        "type": "file_generated",
        "phase": "rendering",
        "fileId": file_id,
        "fileName": f"{req.title}.pptx",
        "fileUrl": f"/api/files/{file_id}",
        "fileSizeBytes": file_size,
        "fileType": "pptx",
        "slideCount": len(slides),
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }
    await redis_client.publish(
        f"agent:events:{session_id}", json.dumps(file_event, default=str)
    )

    # Emit done event
    done_event = {
        "type": "done",
        "content": f"Generated {len(slides)} slides: {req.title}",
        "files": [file_event],
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }
    await redis_client.publish(
        f"agent:events:{session_id}", json.dumps(done_event, default=str)
    )

    logger.info(
        "simple_fallback: completed for session %s, file_id=%s, size=%d bytes",
        session_id, file_id, file_size,
    )

    return {
        "fileId": file_id,
        "fileName": f"{req.title}.pptx",
        "fileSizeBytes": file_size,
        "slideCount": len(slides),
        "fileUrl": f"/api/files/{file_id}",
    }


@router.post("/sessions/{session_id}/answer-question")
async def answer_question(
    session_id: str,
    req: AnswerQuestionRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Resume the agent workflow after a question was asked.
    The user's answer is added as a new message and the agent loop continues.
    """
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

    # Store the user's answer as a new message
    answer_content = req.selected_option if req.selected_option else req.answer
    user_msg = ChatMessage(
        session_id=sid,
        role="user",
        content=answer_content,
        attachments=[],
    )
    db.add(user_msg)
    await db.commit()
    await db.refresh(user_msg)

    # Store assistant placeholder for the response
    assistant_msg = ChatMessage(
        session_id=sid,
        role="assistant",
        content="",
    )
    db.add(assistant_msg)
    await db.commit()
    await db.refresh(assistant_msg)

    # Get the pending question from working state
    question_text = ""
    try:
        state_json = await redis_client.get(f"working_state:{session_id}")
        if state_json:
            state_dict = json.loads(state_json)
            pending_question = state_dict.get("pending_question", {})
            question_text = pending_question.get("question", "")
    except Exception as e:
        logger.warning("Failed to get pending question from working state: %s", str(e))

    # Resume the agent loop with answer and question text
    asyncio.create_task(_run_agent(sid, user_msg.id, assistant_msg.id, answer_content, question_text))

    logger.info(
        "answer_question: resumed workflow for session %s with answer: %s",
        session_id, answer_content[:100],
    )

    return {
        "message_id": str(assistant_msg.id),
        "session_id": str(sid),
        "answer": answer_content,
    }
