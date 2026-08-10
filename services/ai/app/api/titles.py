from fastapi import APIRouter
from pydantic import BaseModel
from app.api.title_generator import generate_chat_title

router = APIRouter()


class GenerateTitleRequest(BaseModel):
    message: str


class GenerateTitleResponse(BaseModel):
    title: str | None


@router.post("/generate", response_model=GenerateTitleResponse)
async def generate_title(req: GenerateTitleRequest):
    """
    Generate a title for a chat based on the first user message.

    Uses a cheap flash GLM model for fast, economical title generation.
    """
    if not req.message or len(req.message.strip()) == 0:
        return GenerateTitleResponse(title=None)

    title = await generate_chat_title(req.message)

    if not title:
        return GenerateTitleResponse(title=None)

    return GenerateTitleResponse(title=title)