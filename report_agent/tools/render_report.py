"""F4 报告渲染工具：把统计结果渲染为 Markdown（供 Agent 作为工具调用）。

附带本地图表生成（Phase 1）：charts.enabled 时先按契约数据产出 PNG 到
<output>/assets/<版本>/，报告内以相对路径引用；任何图表失败仅回退占位文字，不阻断渲染。
"""
from __future__ import annotations

from ..report.renderer import render_report
from .base import Tool, ToolResult


def _safe_name(version_name: str) -> str:
    """与 main.py 的报告文件名规则一致，保证 assets 目录名与报告对应。"""
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in (version_name or "report"))


class RenderReportTool(Tool):
    name = "render_report"
    description = "将独立统计工具产出的结构化结果渲染为 Markdown 测试报告（含本地图表，失败降级占位）。由循环注入 stats_obj。"
    parameters = {"stats_obj": "object(注入)"}

    def __init__(self, cfg=None) -> None:
        self.cfg = cfg  # 未注入 cfg 时不产图，行为与纯 Markdown 渲染一致（兼容旧调用）

    def run(self, stats_obj=None, **kwargs) -> ToolResult:
        if stats_obj is None:
            return ToolResult(ok=False, error="缺少统计结果，无法渲染报告")
        charts = self._render_charts(stats_obj)
        markdown = render_report(stats_obj, charts=charts)
        return ToolResult(ok=True, data=markdown)

    def _render_charts(self, stats_obj):
        """返回 {图key: 相对报告文件(output/) 的 posix 路径}；未启用/失败一律 None → 占位回退。"""
        cfg = self.cfg
        if cfg is None or not getattr(getattr(cfg, "charts", None), "enabled", False):
            return None
        try:
            from ..report.charts import render_charts

            assets_dir = cfg.report_dir / "assets" / _safe_name(stats_obj.version.name or "report")
            paths = render_charts(stats_obj, assets_dir, dpi=cfg.charts.dpi)
            return {k: p.relative_to(cfg.report_dir).as_posix() for k, p in paths.items()}
        except Exception:
            return None
