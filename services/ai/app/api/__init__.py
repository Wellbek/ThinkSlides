from fastapi import APIRouter
from app.api.templates import router as templates_router
from app.api.sessions import router as sessions_router
from app.api.generate import router as generate_router
from app.api.files import router as files_router
from app.api.health import router as health_router
from app.api.titles import router as titles_router

router = APIRouter()
router.include_router(health_router, tags=["health"])
router.include_router(templates_router, prefix="/templates", tags=["templates"])
router.include_router(sessions_router, prefix="/sessions", tags=["sessions"])
router.include_router(generate_router, prefix="/generate", tags=["generate"])
router.include_router(files_router, prefix="/files", tags=["files"])
router.include_router(titles_router, prefix="/titles", tags=["titles"])