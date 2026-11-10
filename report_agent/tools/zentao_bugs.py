"""F2 版本缺陷明细获取工具。"""
from __future__ import annotations

from .base import Tool, ToolResult


class GetVersionBugsTool(Tool):
    name = "get_version_bugs"
    description = "获取指定版本的全部缺陷明细，字段含模块、等级(severity)、状态(status)、resolution、激活次数。入参：version_name（优先复用上游 version_info）。"
    parameters = {"version_name": "string", "version_info": "dict(注入)", "user_input": "string(注入)"}

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    def run(self, version_name: str = "", version_info: dict | None = None, user_input: str = "", **kwargs) -> ToolResult:
        if self.cfg.zentao.mode == "mock":
            from . import mock_zentao
            return mock_zentao.fetch_bugs(version_name, self.cfg.mock.fault)
        return self._run_live(version_name, version_info, (user_input or "").strip())

    def _run_live(self, version_name: str, version_info: dict | None = None, user_input: str = "") -> ToolResult:
        # 优先复用上游 get_version_info 已解析的 product_id + build（避免重复拉 /products、保证口径一致）；
        # 无 version_info（如单测直调）时回退：从输入解析产品与版本。
        # → 分页拉全部缺陷 → 按 openedBuild “包含即计入”过滤 → 逐条补拉详情取模块名。只读（F7）。
        from . import zentao_client as zc

        vi = version_info or {}
        product_id = vi.get("product_id")
        build = vi.get("build") or None
        text = user_input or version_name
        if product_id is None:
            build = build or zc.extract_build(version_name) or zc.extract_build(text)
        try:
            client = zc.ZentaoRestClient(self.cfg)
            if product_id is None:
                product = client.find_product(text) or client.find_product(self.cfg.zentao.default_product)
                if product is None:
                    return ToolResult(ok=False, error=f"未从输入中识别到禅道已有产品：{text}", meta={"fetch_status": "not_found"})
                product_id = product.get("id")
            raw_bugs = client.get_product_bugs(product_id)
        except zc.ZentaoError as exc:
            status = "timeout" if exc.kind == "timeout" else ("not_found" if exc.kind == "not_found" else "error")
            return ToolResult(ok=False, error=str(exc), meta={"fetch_status": status})

        # 跨版本带入：“包含即计入”——影响版本逗号串中含本版本则计入（§10 已拍板）
        field_build = self.cfg.zentao.field_build
        scoped = [b for b in raw_bugs if zc.build_matches(b.get(field_build), build)]

        # 版本存在性判定（V1-002）：禅道把「版本」建模为缺陷 openedBuild，且该 REST 无独立 builds 接口；
        # 指定了版本号但该产品的全部缺陷 openedBuild 都不含它 → 判定版本不存在，终止且不编造。
        # 注：live 下「版本存在但零缺陷」无法与「不存在」区分，此情形按不存在处理；零缺陷场景由 mock empty_bugs 覆盖。
        if build and not scoped:
            return ToolResult(ok=False, error=f"版本不存在：{build}", meta={"fetch_status": "not_found", "count": 0})

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
        from . import zentao_client as zc
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
            "openedBuild": raw.get(z.field_build) or "",
            "openedBy": zc.person_name(raw.get(z.field_opened_by)),
            "resolvedBy": zc.person_name(raw.get(z.field_resolved_by)),
        }
