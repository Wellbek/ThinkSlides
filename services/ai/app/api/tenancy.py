"""
Resolving which organisation a request belongs to.

The Go orchestrator owns authentication; it forwards the caller's identity to
this service as ``X-Org-Id`` / ``X-User-Id`` headers. Requests that arrive
without them (direct calls in development, the background job worker) fall back
to a JWT if one is present, and finally to the single default organisation -
the same behaviour the rest of the service already had.

An identity that names a workspace which no longer exists is refused here
rather than carried into the request. A token outlives its database - a reset
volume, a deleted organisation - and the identity in it stays cryptographically
valid, so nothing downstream had any reason to doubt it. What that produced was
a workspace that read as "nothing configured" (a SELECT for an absent
organisation returns no rows) and then answered the setup it prompted for with
a bare 500, because writing the settings row violated a foreign key nobody had
checked.
"""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Organisation, User
from app.db.session import get_db
from app.observability.logging import get_logger

logger = get_logger(__name__)

# 401 rather than 404: the request is not asking for something missing, it is
# arriving with an identity that is no longer real. The frontend already
# clears the token and returns to the login page on a 401, which is exactly
# the recovery this needs.
STALE_ORGANISATION_DETAIL = (
    "Your session belongs to a workspace that no longer exists. "
    "Sign in again to continue."
)


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


async def _organisation_exists(db: AsyncSession, org_id: uuid.UUID) -> bool:
    result = await db.execute(
        select(Organisation.id).where(Organisation.id == org_id)
    )
    return result.scalar_one_or_none() is not None


async def resolve_org_id(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> uuid.UUID:
    """FastAPI dependency yielding the calling organisation's id.

    A claimed organisation is checked against the database before it is
    returned. One indexed lookup buys the guarantee that every endpoint behind
    this dependency agrees about who is calling - and, more to the point, that
    a stale session is told so instead of reading as an empty workspace it
    cannot then write to.
    """
    claimed = _parse_uuid(request.headers.get("X-Org-Id")) or _org_id_from_jwt(request)
    if claimed is not None:
        if await _organisation_exists(db, claimed):
            return claimed
        logger.warning("stale_organisation_identity", organisation_id=str(claimed))
        raise HTTPException(status_code=401, detail=STALE_ORGANISATION_DETAIL)

    return await get_or_create_default_org_id(db)
