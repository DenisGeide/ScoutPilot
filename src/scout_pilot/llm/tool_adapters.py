"""Provider-specific tool schema adapters."""

from __future__ import annotations

from typing import Any

from scout_pilot.tools.types import ToolFieldSchema, ToolSchema, ToolValueType


class OpenAIToolSchemaAdapter:
    """Convert provider-neutral tool schemas to OpenAI tool definitions."""

    def convert_tool(self, schema: ToolSchema) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": schema.name,
                "description": schema.description,
                "parameters": _json_schema_object(schema.input_schema.fields),
            },
        }

    def convert_tools(self, schemas: tuple[ToolSchema, ...]) -> list[dict[str, Any]]:
        return [self.convert_tool(schema) for schema in schemas]


class AnthropicToolSchemaAdapter:
    """Convert provider-neutral tool schemas to Anthropic tool definitions."""

    def convert_tool(self, schema: ToolSchema) -> dict[str, Any]:
        return {
            "name": schema.name,
            "description": schema.description,
            "input_schema": _json_schema_object(schema.input_schema.fields),
        }

    def convert_tools(self, schemas: tuple[ToolSchema, ...]) -> list[dict[str, Any]]:
        return [self.convert_tool(schema) for schema in schemas]


def _json_schema_object(fields: tuple[ToolFieldSchema, ...]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for field in fields:
        properties[field.name] = _json_schema_for_field(field)
        if field.required:
            required.append(field.name)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _json_schema_for_field(field: ToolFieldSchema) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": _json_type(field.value_type),
        "description": field.description,
    }
    if field.enum_values:
        result["enum"] = list(field.enum_values)
    if field.min_length is not None:
        result["minLength"] = field.min_length
    if field.max_length is not None:
        result["maxLength"] = field.max_length
    if field.minimum is not None:
        result["minimum"] = field.minimum
    if field.maximum is not None:
        result["maximum"] = field.maximum
    return result


def _json_type(value_type: ToolValueType) -> str:
    mapping = {
        ToolValueType.STRING: "string",
        ToolValueType.INTEGER: "integer",
        ToolValueType.NUMBER: "number",
        ToolValueType.BOOLEAN: "boolean",
        ToolValueType.OBJECT: "object",
        ToolValueType.ARRAY: "array",
    }
    return mapping[value_type]
