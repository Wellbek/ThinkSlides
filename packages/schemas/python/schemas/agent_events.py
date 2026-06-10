"""
Python Pydantic schemas for agent events, mirroring the TypeScript schema.
"""

from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, Literal, Union
from enum import Enum


class AgentEventPhase(str, Enum):
    content_analysis = "content_analysis"
    structure_planning = "structure_planning"
    layout_selection = "layout_selection"
    content_generation = "content_generation"
    validation = "validation"
    rendering = "rendering"
    complete = "complete"


class AgentEventType(str, Enum):
    thought = "thought"
    tool_call = "tool_call"
    tool_result = "tool_result"
    slide_finalized = "slide_finalized"
    error = "error"
    progress = "progress"


class ThoughtEvent(BaseModel):
    type: Literal["thought"] = "thought"
    phase: AgentEventPhase
    content: str
    timestamp: str


class ToolCallEvent(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    phase: AgentEventPhase
    tool_name: str
    tool_args: dict = Field(default_factory=dict)
    call_id: str
    timestamp: str


class ToolResultEvent(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    phase: AgentEventPhase
    tool_name: str
    call_id: str
    result: object = None
    duration_ms: int = 0
    token_count: Optional[int] = None
    timestamp: str


class SlideFinalizedEvent(BaseModel):
    type: Literal["slide_finalized"] = "slide_finalized"
    phase: Literal["content_generation"] = "content_generation"
    slide_index: int
    slide_data: object = None
    preview_url: Optional[str] = None
    timestamp: str


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    phase: AgentEventPhase
    message: str
    code: Optional[str] = None
    timestamp: str


class ProgressEvent(BaseModel):
    type: Literal["progress"] = "progress"
    phase: AgentEventPhase
    current: int
    total: int
    message: str
    timestamp: str


class FileGeneratedEvent(BaseModel):
    type: Literal["file_generated"] = "file_generated"
    phase: Literal["rendering"] = "rendering"
    file_id: str
    file_name: str
    file_url: str
    file_size_bytes: int
    file_type: str = "pptx"
    slide_count: int
    timestamp: str


AgentEvent = Union[ThoughtEvent, ToolCallEvent, ToolResultEvent, SlideFinalizedEvent, ErrorEvent, ProgressEvent, FileGeneratedEvent]
