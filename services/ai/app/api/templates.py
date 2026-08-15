import json
import os
import shutil
import uuid
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
import asyncio

from app.db.session import get_db
from app.db.models import Template, DesignSystemBundle
from app.agent.tools.extract_template_design_system import ExtractTemplateDesignSystemTool
from app.llm import get_llm_provider
from app.api.auth import get_current_user
from app.config import settings

router = APIRouter()

async def stream_analysis(file_path: str, name: str, org_id: str, db: AsyncSession,
                          page_range: str = ""):
    """SSE generator with database persistence."""
    yield f"event: start\ndata: {json.dumps({'status': 'processing', 'message': 'Starting template analysis...'})}\n\n"
    await asyncio.sleep(0.1)

    yield f"event: extracting\ndata: {json.dumps({'progress': 0, 'message': 'Extracting design system...'})}\n\n"

    # Run extraction with periodic heartbeat SSE events to prevent proxy
    # timeouts during long vision-LLM calls (PDF analysis can take 30+ s).
    tool = ExtractTemplateDesignSystemTool()
    extract_task = asyncio.create_task(
        tool.run(file_path, page_range=page_range or "")
    )

    try:
        while not extract_task.done():
            yield f"event: heartbeat\ndata: {json.dumps({'status': 'extracting', 'message': 'Still analyzing template...'})}\n\n"
            done, _ = await asyncio.wait({extract_task}, timeout=8.0)
            if done:
                break
    except Exception as e:
        extract_task.cancel()
        yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
        return

    # Retrieve result (may raise if the task failed internally)
    try:
        result = extract_task.result()
    except Exception as e:
        yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
        return

    if not result.success:
        yield f"event: error\ndata: {json.dumps({'error': result.error})}\n\n"
        return
    design_system = result.data

    yield f"event: extracting\ndata: {json.dumps({'progress': 100, 'message': 'Design system extracted'})}\n\n"
    yield f"event: analyzing\ndata: {json.dumps({'progress': 0, 'message': 'LLM analyzing template...'})}\n\n"

    # LLM analysis (streaming if your LLM supports it)
    prompt = f"""Analyze this design system in 2-3 sentences:\n{json.dumps(design_system, indent=2)}"""
    llm = get_llm_provider()
    response = await llm.chat(
        messages=[{"role": "user", "content": prompt}],
        tools=None,
    )
    full_analysis = response.get("message", {}).get("content", "")
    # Simulate streaming chunks (modify as needed)
    words = full_analysis.split()
    for i in range(0, len(words), 2):
        chunk = " ".join(words[i:i+2]) + " "
        yield f"event: analysis_chunk\ndata: {json.dumps({'chunk': chunk, 'accumulated': full_analysis[:i+2]})}\n\n"
        await asyncio.sleep(0.1)

    # Save to database — move file from temp dir to shared templates volume.
    # Use shutil.move() instead of os.rename() because the temp uploads
    # directory (/app/temp_uploads) and the templates directory
    # (/app/templates — Docker named volume) are on different filesystems.
    # os.rename() would raise OSError: Invalid cross-device link.
    template_id = str(uuid.uuid4())
    file_id = str(uuid.uuid4())
    ext = os.path.splitext(file_path)[1]
    template_dir = settings.template_dir
    os.makedirs(template_dir, exist_ok=True)
    saved_path = os.path.join(template_dir, f"{file_id}{ext}")
    shutil.move(file_path, saved_path)

    new_template = Template(
        id=template_id,
        organisation_id=org_id,
        name=name,
        file_path=saved_path,
        version=1,
        is_default=False
    )
    db.add(new_template)

    # Map camelCase extraction keys to snake_case DB columns.
    # The extraction tool returns: palette, typography, spacing, layouts,
    # slideDimensions, masterElements, embeddedMedia, sourceType.
    # The DB model has: palette, typography, spacing, layouts,
    # slide_dimensions, master_elements, embedded_media, raw_xml_snapshot.
    bundle = DesignSystemBundle(
        template_id=template_id,
        version=1,
        palette=design_system.get("palette", {}),
        typography=design_system.get("typography", {}),
        spacing=design_system.get("spacing", {}),
        layouts=design_system.get("layouts", []),
        slide_dimensions=design_system.get("slideDimensions", {}),
        master_elements=design_system.get("masterElements", []),
        embedded_media=design_system.get("embeddedMedia", []),
        data_viz=design_system.get("dataViz", {}),
        density=design_system.get("density", {}),
        page_survey=design_system.get("pageSurvey", {}),
        raw_xml_snapshot=json.dumps(design_system),
    )
    db.add(bundle)
    await db.commit()

    yield f"event: done\ndata: {json.dumps({'template_id': template_id, 'name': name, 'design_system': design_system, 'analysis': full_analysis})}\n\n"


@router.post("/upload-stream")
async def upload_template_stream(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    org_id: Optional[str] = Form(None),
    # Which pages of the upload are the template - "20-35", "1,4,9-12".
    # Omitted means the whole document. A user continuing the style of one
    # chapter of a deck should not have to hand us the other 150 pages and
    # hope the sampler agrees with them about which part mattered.
    pages: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    if not file.filename.endswith(('.pptx', '.potx', '.pdf')):
        raise HTTPException(400, "Only PPTX, POTX, or PDF files allowed")

    if name is None:
        name = file.filename

    # Resolve org_id: explicit form field > JWT claim > error
    resolved_org_id = org_id or user.get("org_id", "")
    if not resolved_org_id or resolved_org_id == "dev-org":
        raise HTTPException(
            status_code=400,
            detail="organisation_id is required. Please belong to an organisation before uploading templates.",
        )

    # Use a temp subdirectory within the upload_dir (not a shared volume —
    # temp files are private to this container and will be moved to the
    # shared templates volume after successful analysis).
    temp_dir = os.path.join(settings.upload_dir, "temp_uploads")
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, f"{uuid.uuid4()}_{file.filename}")
    try:
        with open(temp_path, "wb") as f:
            content = await file.read()
            f.write(content)
    except Exception:
        # Clean up temp file on write failure
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise

    return StreamingResponse(
        stream_analysis(temp_path, name, resolved_org_id, db, pages or ""),
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
    result = await tool.run(file_path, page_range=str(data.get("pages") or ""))
    if not result.success:
        raise HTTPException(500, result.error)
    return result.data
