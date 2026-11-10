"""工具基类与注册表。

每个工具声明 name / description / parameters，供规划器（LLM 或确定性）选择调用。
工具统一返回 ToolResult，异常在工具内部捕获并转为 ok=False，保证循环不崩溃。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)


class Tool:
    name: str = "tool"
    description: str = ""
    parameters: Dict[str, Any] = {}  # 简化 JSON schema，用于规划器提示

    def run(self, **kwargs) -> ToolResult:  # pragma: no cover - 抽象
        raise NotImplementedError

    def spec(self) -> Dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> List[str]:
        return list(self._tools)

    def specs(self) -> List[Dict[str, Any]]:
        return [t.spec() for t in self._tools.values()]
