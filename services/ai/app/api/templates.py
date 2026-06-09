import json
import os
import uuid
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional, List
import asyncio

from app.db.session import get_db
from app.db.models import Template, DesignSystemBundle 
from app.agent.tools.extract_template_design_system import ExtractTemplateDesignSystemTool
from app.llm import get_llm_provider

router = APIRouter()

async def stream_analysis(file_path: str, name: str, org_id: str, db: AsyncSession):
    """SSE generator with database persistence."""
    yield f"event: start\ndata: {json.dumps({'status': 'processing', 'message': 'Starting template analysis...'})}\n\n"
    await asyncio.sleep(0.1)

    yield f"event: extracting\ndata: {json.dumps({'progress': 0, 'message': 'Extracting design system...'})}\n\n"
    try:
        tool = ExtractTemplateDesignSystemTool()
        result = await tool.run(file_path)
        if not result.success:
            raise Exception(result.error)
        design_system = result.data
    except Exception as e:
        yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
        return

    yield f"event: extracting\ndata: {json.dumps({'progress': 100, 'message': 'Design system extracted'})}\n\n"
    yield f"event: analyzing\ndata: {json.dumps({'progress': 0, 'message': 'LLM analyzing template...'})}\n\n"

    # LLM analysis (streaming if your LLM supports it)
    prompt = f"""Analyze this design system in 2-3 sentences:\n{json.dumps(design_system, indent=2)}"""
    llm = get_llm_client()
    # Replace with async streaming if available
    full_analysis = await llm.complete(prompt)
    # Simulate streaming chunks (modify as needed)
    words = full_analysis.split()
    for i in range(0, len(words), 2):
        chunk = " ".join(words[i:i+2]) + " "
        yield f"event: analysis_chunk\ndata: {json.dumps({'chunk': chunk, 'accumulated': full_analysis[:i+2]})}\n\n"
        await asyncio.sleep(0.1)

    # Save to database
    template_id = str(uuid.uuid4())
    file_id = str(uuid.uuid4())
    ext = os.path.splitext(file_path)[1]
    saved_path = f"/app/templates/{file_id}{ext}"
    os.rename(file_path, saved_path)

    new_template = Template(
        id=template_id,
        organisation_id=org_id,
        name=name,
        file_path=saved_path,
        version=1,
        is_default=False
    )
    db.add(new_template)

    bundle = DesignSystemBundle(
        template_id=template_id,
        bundle=design_system,
        version=1,
        is_active=True
    )
    db.add(bundle)
    await db.commit()

    yield f"event: done\ndata: {json.dumps({'template_id': template_id, 'name': name, 'design_system': design_system, 'analysis': full_analysis})}\n\n"


@router.post("/upload-stream")
async def upload_template_stream(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    # org_id would be extracted from JWT in a real implementation
):
    if not file.filename.endswith(('.pptx', '.potx', '.pdf')):
        raise HTTPException(400, "Only PPTX, POTX, or PDF files allowed")

    if name is None:
        name = file.filename

    # Get organisation ID from current user (simplified – you'll need to implement JWT extraction)
    # For now, fetch a default org or use a placeholder
    # Ideally: user = await get_current_user(...); org_id = user.organisation_id
    org_id = "00000000-0000-0000-0000-000000000000"  # TODO: replace with actual org ID

    temp_dir = "/app/temp_uploads"
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, f"{uuid.uuid4()}_{file.filename}")
    with open(temp_path, "wb") as f:
        content = await file.read()
        f.write(content)

    return StreamingResponse(
        stream_analysis(temp_path, name, org_id, db),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/extract")
async def extract_design_system(data: dict):
    """Synchronous endpoint for orchestrator to extract design system from a file path."""
    file_path = data.get("file_path")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(400, "Invalid file path")
    tool = ExtractTemplateDesignSystemTool()
    result = await tool.run(file_path)
    if not result.success:
        raise HTTPException(500, result.error)
    return result.data