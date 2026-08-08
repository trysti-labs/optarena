"""
`optarena._cases._mcp_schema`: MCP tools/list -> OpenAI-function-schema
translation, the shape `get_tool_schemas()` already returns for mocks and
that `tool_chat.py`'s drivers consume unmodified.
"""

from __future__ import annotations

import unittest

from optarena._cases._mcp_schema import mcp_tool_to_openai_schema, mcp_tools_to_openai_schemas


class MCPToolSchemaTranslationTests(unittest.TestCase):
    def test_full_tool_translates_to_function_schema_shape(self):
        tool = {
            "name": "get_weather",
            "description": "Get current weather information for a location",
            "inputSchema": {
                "type": "object",
                "properties": {"location": {"type": "string", "description": "City name or zip code"}},
                "required": ["location"],
            },
        }
        schema = mcp_tool_to_openai_schema(tool)
        self.assertEqual(schema, {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current weather information for a location",
                "parameters": {
                    "type": "object",
                    "properties": {"location": {"type": "string", "description": "City name or zip code"}},
                    "required": ["location"],
                },
            },
        })

    def test_missing_description_defaults_to_empty_string(self):
        schema = mcp_tool_to_openai_schema({"name": "no_desc", "inputSchema": {"type": "object", "properties": {}}})
        self.assertEqual(schema["function"]["description"], "")

    def test_missing_input_schema_defaults_to_empty_object_schema(self):
        schema = mcp_tool_to_openai_schema({"name": "no_args", "description": "d"})
        self.assertEqual(schema["function"]["parameters"], {"type": "object", "properties": {}})

    def test_list_translates_to_name_keyed_dict(self):
        tools = [
            {"name": "a", "description": "A", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "b", "description": "B", "inputSchema": {"type": "object", "properties": {}}},
        ]
        schemas = mcp_tools_to_openai_schemas(tools)
        self.assertEqual(set(schemas), {"a", "b"})
        self.assertEqual(schemas["a"]["function"]["name"], "a")


if __name__ == "__main__":
    unittest.main()
