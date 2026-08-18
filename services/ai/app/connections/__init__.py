"""External connections: MCP servers, image and figure renderers, data sources."""

from app.connections.catalog import (
    CONNECTIONS_BY_ID,
    KIND_DATA,
    KIND_FIGURE,
    KIND_IMAGE,
    KIND_MCP,
    ConnectionSpec,
    CredentialField,
    McpServerPreset,
    catalog_payload,
    check_mcp_url,
)

__all__ = [
    "CONNECTIONS_BY_ID",
    "KIND_DATA",
    "KIND_FIGURE",
    "KIND_IMAGE",
    "KIND_MCP",
    "ConnectionSpec",
    "CredentialField",
    "McpServerPreset",
    "catalog_payload",
    "check_mcp_url",
]
