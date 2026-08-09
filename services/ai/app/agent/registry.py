"""
Structured tool and skill registry for the ReAct agent loop.
Tools are registered with names, descriptions, and parameter schemas.
The agent uses function calling to invoke these tools.
"""

from __future__ import annotations
from typing import Any, Callable, Awaitable, Optional
from pydantic import BaseModel, Field
from app.observability.logging import get_logger

logger = get_logger(__name__)


class ToolParameter(BaseModel):
    """Schema for a single tool parameter."""
    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    enum: Optional[list[str]] = None
    # Required by JSON Schema when type == "array" (e.g. {"type": "string"}
    # for an array of strings). Ignored for other types.
    items: Optional[dict] = None


class ToolDefinition(BaseModel):
    """Schema for a registered tool."""
    name: str
    description: str
    parameters: list[ToolParameter] = Field(default_factory=list)
    returns: str = "object"


class ToolResult(BaseModel):
    """Result from a tool invocation."""
    success: bool
    data: Any = None
    error: Optional[str] = None
    duration_ms: int = 0
    token_count: Optional[int] = None


ToolFunction = Callable[..., Awaitable[ToolResult]]


class ToolRegistry:
    """
    Central registry for agent tools.
    Tools are registered at startup and looked up by name during the agent loop.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._functions: dict[str, ToolFunction] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: list[ToolParameter] | None = None,
    ) -> Callable[[ToolFunction], ToolFunction]:
        """Decorator to register a tool function."""

        def decorator(fn: ToolFunction) -> ToolFunction:
            tool_def = ToolDefinition(
                name=name,
                description=description,
                parameters=parameters or [],
            )
            self._tools[name] = tool_def
            self._functions[name] = fn
            logger.info("tool_registered", tool_name=name)
            return fn

        return decorator

    def register_tool(
        self,
        name: str,
        description: str,
        fn: ToolFunction,
        parameters: list[ToolParameter] | None = None,
    ) -> None:
        """Register a tool function directly (non-decorator style)."""
        tool_def = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters or [],
        )
        self._tools[name] = tool_def
        self._functions[name] = fn
        logger.info("tool_registered", tool_name=name)

    def get(self, name: str) -> tuple[ToolDefinition, ToolFunction] | None:
        """Look up a tool by name."""
        if name in self._tools:
            return self._tools[name], self._functions[name]
        return None

    def list_tools(self) -> list[ToolDefinition]:
        """List all registered tool definitions."""
        return list(self._tools.values())

    def get_function_schemas(self) -> list[dict]:
        """
        Return tool definitions in OpenAI function-calling format.
        Used as the `tools` parameter in LLM chat completions.
        """
        schemas = []
        for tool_def in self._tools.values():
            properties: dict[str, Any] = {}
            required: list[str] = []
            for param in tool_def.parameters:
                prop: dict[str, Any] = {
                    "type": param.type,
                    "description": param.description,
                }
                if param.enum:
                    prop["enum"] = param.enum
                if param.items:
                    prop["items"] = param.items
                properties[param.name] = prop
                if param.required:
                    required.append(param.name)

            schemas.append({
                "type": "function",
                "function": {
                    "name": tool_def.name,
                    "description": tool_def.description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            })
        return schemas


# Global tool registry singleton
_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    """Get or create the global tool registry."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        _register_default_tools(_registry)
    return _registry


def _register_default_tools(registry: ToolRegistry) -> None:
    """Register all built-in agent tools."""
    from app.agent.tools.analyze_document import AnalyzeDocumentTool
    from app.agent.tools.extract_key_data import ExtractKeyDataTool
    from app.agent.tools.extract_template_design_system import ExtractTemplateDesignSystemTool
    from app.agent.tools.web_search import WebSearchTool
    from app.agent.tools.plan_presentation_structure import PlanPresentationStructureTool
    from app.agent.tools.select_layout import SelectLayoutTool
    from app.agent.tools.design_slide_content import DesignSlideContentTool
    from app.agent.tools.validate_slide_json import ValidateSlideJsonTool
    from app.agent.tools.select_source_image import SelectSourceImageTool
    from app.agent.tools.finalize_and_render_pptx import FinalizeAndRenderPptxTool
    from app.agent.tools.ask_user_question import AskUserQuestionTool

    AnalyzeDocumentTool.register(registry)
    ExtractKeyDataTool.register(registry)
    ExtractTemplateDesignSystemTool.register(registry)
    WebSearchTool.register(registry)
    PlanPresentationStructureTool.register(registry)
    SelectLayoutTool.register(registry)
    DesignSlideContentTool.register(registry)
    ValidateSlideJsonTool.register(registry)
    SelectSourceImageTool.register(registry)
    FinalizeAndRenderPptxTool.register(registry)
    AskUserQuestionTool.register(registry)