"""Provider-neutral tool registry."""

from __future__ import annotations

from dataclasses import dataclass, field

from scout_pilot.tools.base import BaseTool
from scout_pilot.tools.types import ToolSchema


@dataclass
class ToolRegistry:
    """Registry of provider-neutral tools."""

    _tools: dict[str, BaseTool] = field(default_factory=dict)

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def require(self, name: str) -> BaseTool:
        tool = self.get(name)
        if tool is None:
            raise KeyError(f"Unknown tool: {name}")
        return tool

    def schemas(self) -> tuple[ToolSchema, ...]:
        return tuple(
            ToolSchema(
                name=tool.name,
                description=tool.description,
                input_schema=tool.input_schema,
                output_schema=tool.output_schema,
            )
            for tool in self._tools.values()
        )

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools.keys())
