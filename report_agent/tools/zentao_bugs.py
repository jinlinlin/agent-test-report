"""F2 版本缺陷明细获取工具。"""
from __future__ import annotations

from .base import Tool, ToolResult


class GetVersionBugsTool(Tool):
    name = "get_version_bugs"
    description = "获取指定版本的全部缺陷明细，字段含模块、等级(severity)、状态(status)、resolution、激活次数。入参：version_name。"
    parameters = {"version_name": "string"}

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    def run(self, version_name: str = "", **kwargs) -> ToolResult:
        if self.cfg.zentao.mode == "mock":
            from . import mock_zentao
            return mock_zentao.fetch_bugs(version_name, self.cfg.mock.fault)
        return self._run_live(version_name)

    def _run_live(self, version_name: str) -> ToolResult:
        # 接真实禅道 RESTful API：拆「产品名 + 版本号」→ 定位 product → 分页拉全部缺陷
        # → 按 openedBuild（影响版本）“包含即计入”过滤 → 逐条补拉详情取模块名。
        # 只读（F7）：仅 GET，无任何写操作。
        from . import zentao_client as zc

        keyword, build = zc.parse_version(version_name)
        try:
            client = zc.ZentaoRestClient(self.cfg)
            product = client.find_product(keyword)
            if product is None:
                return ToolResult(ok=False, error=f"未找到产品「{keyword}」", meta={"fetch_status": "not_found"})
            raw_bugs = client.get_product_bugs(product.get("id"))
        except zc.ZentaoError as exc:
            status = "timeout" if exc.kind == "timeout" else ("not_found" if exc.kind == "not_found" else "error")
            return ToolResult(ok=False, error=str(exc), meta={"fetch_status": status})

        # 跨版本带入：“包含即计入”——影响版本逗号串中含本版本则计入（§10 已拍板）
        field_build = self.cfg.zentao.field_build
        scoped = [b for b in raw_bugs if zc.build_matches(b.get(field_build), build)]

        bugs = []
        for raw in scoped:
            detail = client.get_bug(raw.get("id"))  # 列表接口无模块名，从详情 moduleTitle 补拉
            bugs.append(self._normalize(raw, detail))
        return ToolResult(ok=True, data=bugs, meta={"fetch_status": "ok", "count": len(bugs)})

    def _normalize(self, raw: dict, detail: dict | None = None) -> dict:
        """禅道缺陷字段 → 本系统 schema（字段名走配置，应对禅道版本差异）。

        列表接口的 module 为模块 ID；模块名从单条详情的 moduleTitle 取，缺失则回退到 ID。
        """
        z = self.cfg.zentao
        module = None
        if detail:
            module = detail.get("moduleTitle")
        if not module:
            module = raw.get("moduleTitle") or raw.get(z.field_module) or "未分类"
        return {
            "id": raw.get("id"),
            "module": module,
            "severity": raw.get(z.field_severity),
            "status": raw.get(z.field_status),
            "resolution": raw.get(z.field_resolution) or "",
            "activatedCount": raw.get(z.field_activated, 0),
            "openedDate": raw.get(z.field_opened_date) or "",
        }
