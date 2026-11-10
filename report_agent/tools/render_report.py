"""F4 报告渲染工具：把统计结果渲染为 Markdown（供 Agent 作为工具调用）。"""
from __future__ import annotations

from ..report.renderer import render_report
from .base import Tool, ToolResult


class RenderReportTool(Tool):
    name = "render_report"
    description = "将独立统计工具产出的结构化结果渲染为 Markdown 测试报告。由循环注入 stats_obj。"
    parameters = {"stats_obj": "object(注入)"}

    def run(self, stats_obj=None, **kwargs) -> ToolResult:
        if stats_obj is None:
            return ToolResult(ok=False, error="缺少统计结果，无法渲染报告")
        markdown = render_report(stats_obj)
        return ToolResult(ok=True, data=markdown)
