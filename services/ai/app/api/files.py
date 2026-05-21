"""
File download endpoints for generated PPTX files.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.session import get_db
from app.db.models import GeneratedFile
import uuid

router = APIRouter()


@router.get("/{file_id}")
async def download_file(
    file_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Download a generated file by ID."""
    result = await db.execute(
        select(GeneratedFile).where(GeneratedFile.id == uuid.UUID(file_id))
    )
    file_record = result.scalar_one_or_none()

    if not file_record:
        raise HTTPException(status_code=404, detail="File not found")

    import os
    if not os.path.exists(file_record.file_path):
        raise HTTPException(status_code=404, detail="File no longer exists on disk")

    return FileResponse(
        path=file_record.file_path,
        filename=file_record.original_filename or "presentation.pptx",
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )