from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.session import get_db
from app.db.models import ChatSession, ChatMessage, AgentEvent, GeneratedFile
from app.api.tenancy import get_or_create_default_org_id, get_or_create_default_user_id
from pydantic import BaseModel
from typing import Optional
import uuid

router = APIRouter()


class CreateSessionRequest(BaseModel):
    title: Optional[str] = None
    template_id: Optional[str] = None
    brief: Optional[str] = None


@router.get("")
async def list_sessions(db: AsyncSession = Depends(get_db)):
    """List all chat sessions for the current user."""
    result = await db.execute(
        select(ChatSession)
        .order_by(ChatSession.updated_at.desc())
        .limit(100)
    )
    sessions = result.scalars().all()

    return [
        {
            "id": str(s.id),
            "title": s.title,
            "slideCount": s.slide_count,
            "updatedAt": s.updated_at.isoformat(),
            "createdAt": s.created_at.isoformat(),
        }
        for s in sessions
    ]


@router.post("")
async def create_session(
    req: CreateSessionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a new chat session."""
    org_id = await get_or_create_default_org_id(db)
    user_id = await get_or_create_default_user_id(db, org_id)

    template_uuid = uuid.UUID(req.template_id) if req.template_id else None

    session = ChatSession(
        organisation_id=org_id,
        user_id=user_id,
        title=req.title or "New Chat",
        template_id=template_uuid,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    return {
        "session_id": str(session.id),
        "title": session.title,
        "created_at": session.created_at.isoformat(),
    }


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get full session detail including messages, events, and files."""
    sid = uuid.UUID(session_id)
    result = await db.execute(
        select(ChatSession).where(ChatSession.id == sid)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    msg_result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == sid)
        .order_by(ChatMessage.created_at)
    )
    messages = msg_result.scalars().all()

    evt_result = await db.execute(
        select(AgentEvent)
        .where(AgentEvent.session_id == sid)
        .order_by(AgentEvent.created_at)
    )
    events = evt_result.scalars().all()

    file_result = await db.execute(
        select(GeneratedFile)
        .where(GeneratedFile.session_id == sid)
    )
    files = file_result.scalars().all()

    return {
        "id": str(session.id),
        "title": session.title,
        "templateBadge": None,
        "slideCount": session.slide_count,
        "messages": [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "attachments": m.attachments or [],
                "createdAt": m.created_at.isoformat(),
            }
            for m in messages
        ],
        "events": [
            {
                "id": str(e.id),
                "eventType": e.event_type,
                "phase": e.phase,
                "content": e.content,
                "toolName": e.tool_name,
                "createdAt": e.created_at.isoformat(),
            }
            for e in events
        ],
        "generatedFiles": [
            {
                "id": str(f.id),
                "fileType": f.file_type,
                "fileName": f.original_filename or "presentation.pptx",
                "fileSizeBytes": f.file_size_bytes or 0,
                "downloadUrl": f"/api/v1/files/{f.id}",
                "createdAt": f.created_at.isoformat(),
            }
            for f in files
        ],
        "createdAt": session.created_at.isoformat(),
        "updatedAt": session.updated_at.isoformat(),
    }


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a chat session and all associated data."""
    sid = uuid.UUID(session_id)
    result = await db.execute(
        select(ChatSession).where(ChatSession.id == sid)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    await db.delete(session)
    await db.commit()

    return {"deleted": True}
