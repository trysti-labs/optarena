"""
optarena/_cases/_mcp_schema.py
───────────────────────────
Translates a real MCP server's `tools/list` response into the same
OpenAI-function-schema shape `_mock_service.py`'s `get_tool_schemas()`
already returns for the in-process mocks - the shape both `openai-tools`
and `ollama-tools` (`optarena/drivers/tool_chat.py`) already consume
unmodified for either backend protocol.

The translation is close to a passthrough by protocol design: an MCP tool's
`inputSchema` IS a JSON Schema object, and JSON Schema is exactly what an
OpenAI-style function schema's `parameters` field expects.
"""

from __future__ import annotations

#: Fallback for a tool with no `inputSchema` at all (the spec's own examples
#: always include one, but nothing requires it for a zero-argument tool).
_EMPTY_OBJECT_SCHEMA = {"type": "object", "properties": {}}


def mcp_tool_to_openai_schema(tool: dict) -> dict:
    """One `tools/list` entry (`{name, description, inputSchema, ...}`) ->
    one `{"type": "function", "function": {...}}` entry."""
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description") or "",
            "parameters": tool.get("inputSchema") or _EMPTY_OBJECT_SCHEMA,
        },
    }


def mcp_tools_to_openai_schemas(tools: list[dict]) -> dict[str, dict]:
    """A whole `tools/list` result -> `{tool_name: schema}`, the same dict
    shape `MOCK_SERVICES`' per-service schema tables use (so
    `SandboxedMCPService` can reuse `get_tool_schemas`-style name-ordered
    lookup instead of a parallel data structure)."""
    return {tool["name"]: mcp_tool_to_openai_schema(tool) for tool in tools}
