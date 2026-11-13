"""F4 报告渲染工具：把统计结果渲染为 Markdown（供 Agent 作为工具调用）。

附带本地图表生成（Phase 1）：charts.enabled 时先按契约数据产出 PNG 到
<output>/assets/<版本>/，报告内以相对路径引用；任何图表失败仅回退占位文字，不阻断渲染。
"""
from __future__ import annotations

from ..report.renderer import render_report
from ..report import templates as T
from .base import Tool, ToolResult
from .feishu_client import FeishuClient


def _safe_name(version_name: str) -> str:
    """与 main.py 的报告文件名规则一致，保证 assets 目录名与报告对应。"""
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in (version_name or "report"))


class RenderReportTool(Tool):
    name = "render_report"
    description = "将独立统计工具产出的结构化结果渲染为 Markdown 测试报告（含本地图表，失败降级占位；开启飞书时自动检索测试大纲/测试用例链接）。由循环注入 stats_obj、product_name 与 build。"
    parameters = {"stats_obj": "object(注入)", "product_name": "string(注入)", "build": "string(注入)"}

    def __init__(self, cfg=None) -> None:
        self.cfg = cfg  # 未注入 cfg 时不产图、不检索飞书链接，行为与纯 Markdown 渲染一致（兼容旧调用）

    def run(self, stats_obj=None, product_name: str = "", build: str = "", project_doc_links=None, **kwargs) -> ToolResult:
        if stats_obj is None:
            return ToolResult(ok=False, error="缺少统计结果，无法渲染报告")
        charts = self._render_charts(stats_obj)
        # 飞书实检（需求/大纲/用例）为事实优先，项目记忆（手册/UI/原型）补充；同键时实检覆盖记忆
        ref_links = {**(project_doc_links or {}), **self._resolve_ref_links(product_name, build)}
        embed = self._defect_embed_mode(stats_obj)
        markdown = render_report(stats_obj, charts=charts, ref_links=ref_links,
                                 defect_bitable_inline=embed)
        return ToolResult(ok=True, data=markdown,
                          meta={"ref_doc_links": ref_links, "defect_bitable_embed": embed})

    def _defect_embed_mode(self, stats_obj) -> bool:
        """第 6 节是否走 C（文档内嵌多维表格）：飞书启用 + 开 defect_bitable + 开 defect_bitable_embed + 有缺陷行。
        命中时正文只写计数引导，实体表交由 push_feishu 导入后内嵌；未命中（开关关/无飞书/无缺陷）则回退正文内嵌宽表。
        两种形态都不再创建独立多维表格知识库节点（B 已废弃）。"""
        cfg = self.cfg
        if cfg is None:
            return False
        f = cfg.feishu
        if not (f.enabled and getattr(f, "defect_bitable", False) and getattr(f, "defect_bitable_embed", False)):
            return False
        active = getattr(stats_obj, "active_bugs", None) or []
        postponed = getattr(stats_obj, "postponed_bugs", None) or []
        return bool(active or postponed)

    def _resolve_ref_links(self, product_name: str, build: str) -> dict:
        """仅当飞书启用且配齐凭证/知识库落点时，在“项目+版本”目录下检索大纲/用例文档链接；
        任何异常（网络/权限/未命中）一律返回空→渲染层降级为人工占位，绝不阻断报告生成。"""
        cfg = self.cfg
        if cfg is None:
            return {}
        f = cfg.feishu
        if not (f.enabled and product_name and build and f.app_id and f.app_secret
                and f.wiki_space_id and f.wiki_projects_parent):
            return {}
        try:
            return FeishuClient(f).find_doc_urls(product_name, build, list(T.REF_DOC_LINK_TITLES))
        except Exception:
            return {}

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
