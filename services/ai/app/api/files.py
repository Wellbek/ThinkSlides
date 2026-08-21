"""
File download endpoints for generated files - the rendered PPTX plus,
since #187, the individual chart/image assets extracted from it.
"""

import mimetypes
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.session import get_db
from app.db.models import GeneratedFile

router = APIRouter()

PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _media_type_for(file_type: str | None, filename: str | None) -> str:
    """The download's Content-Type, by file_type first and extension second.

    generated_files now holds more than PPTX rows (#187 added per-slide
    chart/image assets), so this can no longer hardcode the presentation
    media type for every row.
    """
    if file_type == "pptx":
        return PPTX_MEDIA_TYPE
    guessed, _ = mimetypes.guess_type(filename or "")
    return guessed or "application/octet-stream"


@router.get("/{file_id}")
async def download_file(
    file_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Download a generated file (deck or individual asset) by ID."""
    result = await db.execute(
        select(GeneratedFile).where(GeneratedFile.id == uuid.UUID(file_id))
    )
    file_record = result.scalar_one_or_none()

    if not file_record:
        raise HTTPException(status_code=404, detail="File not found")

    if not os.path.exists(file_record.file_path):
        raise HTTPException(status_code=404, detail="File no longer exists on disk")

    default_filename = "presentation.pptx" if file_record.file_type == "pptx" else os.path.basename(file_record.file_path)
    filename = file_record.original_filename or default_filename

    return FileResponse(
        path=file_record.file_path,
        filename=filename,
        media_type=_media_type_for(file_record.file_type, filename),
    )