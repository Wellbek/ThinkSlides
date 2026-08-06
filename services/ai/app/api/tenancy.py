"""
Resolving which organisation a request belongs to.

The Go orchestrator owns authentication; it forwards the caller's identity to
this service as ``X-Org-Id`` / ``X-User-Id`` headers. Requests that arrive
without them (direct calls in development, the background job worker) fall back
to a JWT if one is present, and finally to the single default organisation -
the same behaviour the rest of the service already had.
"""

from __future__ import annotations

import uuid

from fastapi import Depends, Request
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Organisation, User
from app.db.session import get_db
from app.observability.logging import get_logger

logger = get_logger(__name__)


async def get_or_create_default_org_id(db: AsyncSession) -> uuid.UUID:
    """The single-tenant fallback organisation."""
    result = await db.execute(select(Organisation).limit(1))
    org = result.scalar_one_or_none()
    if org:
        return org.id
    org = Organisation(name="Default", slug="default")
    db.add(org)
    await db.commit()
    await db.refresh(org)
    return org.id


async def get_or_create_default_user_id(db: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    """The single-tenant fallback user."""
    result = await db.execute(select(User).limit(1))
    user = result.scalar_one_or_none()
    if user:
        return user.id
    user = User(
        organisation_id=org_id,
        email="admin@thinkslides.dev",
        password_hash="$2b$12$placeholder_not_for_production",
        display_name="Admin",
        role="admin",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user.id


def _org_id_from_jwt(request: Request) -> uuid.UUID | None:
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    try:
        claims = jwt.decode(
            header.split(" ", 1)[1],
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        return None
    return _parse_uuid(claims.get("org_id"))


def _parse_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


async def resolve_org_id(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> uuid.UUID:
    """FastAPI dependency yielding the calling organisation's id."""
    from_header = _parse_uuid(request.headers.get("X-Org-Id"))
    if from_header is not None:
        return from_header

    from_jwt = _org_id_from_jwt(request)
    if from_jwt is not None:
        return from_jwt

    return await get_or_create_default_org_id(db)
