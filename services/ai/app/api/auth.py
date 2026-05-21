"""
Authentication endpoints for the AI service.

In the full architecture, auth is handled by the Go orchestrator.
This module provides a lightweight verification utility
for when the AI service receives direct requests.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from app.config import settings

security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    """
    Verify JWT token and return user claims.
    Returns a default user if no auth is configured (development mode).
    """
    if credentials is None:
        # Development mode — no auth
        return {
            "user_id": "dev-user",
            "org_id": "dev-org",
            "role": "admin",
        }

    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        return {
            "user_id": payload.get("user_id", ""),
            "org_id": payload.get("org_id", ""),
            "role": payload.get("role", "member"),
        }
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )