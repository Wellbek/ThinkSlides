from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.session import get_db
from app.db.models import Template, DesignSystemBundle
from app.agent.tools.extract_template_design_system import ExtractTemplateDesignSystemTool
from app.observability.logging import get_logger
import uuid
import os
import shutil

logger = get_logger(__name__)
router = APIRouter()


@router.get("")
async def list_templates(db: AsyncSession = Depends(get_db)):
    """List all templates for the current organisation."""
    # TODO: Extract org_id from JWT context
    org_id = await _get_default_org_id(db)

    result = await db.execute(
        select(Template)
        .where(Template.organisation_id == org_id)
        .order_by(Template.updated_at.desc())
    )
    templates = result.scalars().all()

    return [
        {
            "id": str(t.id),
            "name": t.name,
            "description": t.description,
            "tags": t.tags or [],
            "version": t.version,
            "isDefault": t.is_default,
            "originalFileName": t.original_filename,
            "createdAt": t.created_at.isoformat(),
        }
        for t in templates
    ]


@router.post("")
async def upload_template(
    name: str = Form(...),
    description: str = Form(""),
    tags: str = Form(""),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload a new template and extract its design system."""
    org_id = await _get_default_org_id(db)

    # Save uploaded file
    template_dir = os.environ.get("TEMPLATE_DIR", "/app/templates")
    os.makedirs(template_dir, exist_ok=True)

    file_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename or "template.pptx")[1]
    file_path = os.path.join(template_dir, f"{file_id}{ext}")

    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)

    file_size = len(content)

    # Create template record
    template = Template(
        organisation_id=org_id,
        name=name,
        description=description,
        tags=[t.strip() for t in tags.split(",") if t.strip()],
        file_path=file_path,
        original_filename=file.filename,
        file_size_bytes=file_size,
        mime_type=file.content_type or "application/vnd.openxmlformats-officedocument.presentationml.template",
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)

    # Extract design system
    try:
        extractor = ExtractTemplateDesignSystemTool()
        design_system = await extractor.run(file_path)

        bundle = DesignSystemBundle(
            template_id=template.id,
            palette=design_system.get("palette", {}),
            typography=design_system.get("typography", {}),
            spacing=design_system.get("spacing", {}),
            layouts=design_system.get("layouts", []),
            slide_dimensions=design_system.get("slideDimensions", {}),
            master_elements=design_system.get("masterElements", []),
            embedded_media=design_system.get("embeddedMedia", []),
        )
        db.add(bundle)
        await db.commit()

        logger.info(
            "template_design_system_extracted",
            template_id=str(template.id),
            layout_count=len(design_system.get("layouts", [])),
        )
    except Exception as e:
        logger.error("design_system_extraction_failed", template_id=str(template.id), error=str(e))

    return {
        "id": str(template.id),
        "name": template.name,
        "version": template.version,
        "designSystemBundleId": str(bundle.id) if "bundle" in dir() else None,
    }


@router.delete("/{template_id}")
async def delete_template(
    template_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a template and its design system bundles."""
    result = await db.execute(
        select(Template).where(Template.id == uuid.UUID(template_id))
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Remove file
    if os.path.exists(template.file_path):
        os.remove(template.file_path)

    await db.delete(template)
    await db.commit()

    return {"deleted": True}


async def _get_default_org_id(db: AsyncSession) -> uuid.UUID:
    """Get default organisation ID. In production this comes from JWT."""
    from app.db.models import Organisation
    result = await db.execute(select(Organisation).limit(1))
    org = result.scalar_one_or_none()
    if org:
        return org.id
    # Create default org
    org = Organisation(name="Default", slug="default")
    db.add(org)
    await db.commit()
    await db.refresh(org)
    return org.id