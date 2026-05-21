"""
Python Pydantic schemas for API request/response, mirroring TypeScript.
"""

from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional


class CreateSessionRequest(BaseModel):
    title: Optional[str] = None
    template_id: Optional[str] = None
    brief: Optional[str] = None


class CreateSessionResponse(BaseModel):
    session_id: str
    title: str
    created_at: str


class SendMessageRequest(BaseModel):
    content: str
    attachments: list[dict] = Field(default_factory=list)


class SendMessageResponse(BaseModel):
    message_id: str
    session_id: str


class SessionListItem(BaseModel):
    id: str
    title: str
    template_badge: Optional[str] = None
    slide_count: int = 0
    updated_at: str
    created_at: str


class AuthLoginRequest(BaseModel):
    email: str
    password: str


class AuthLoginResponse(BaseModel):
    token: str
    user: dict