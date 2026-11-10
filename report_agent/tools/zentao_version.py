"""F1 版本基础信息获取工具。"""
from __future__ import annotations

from .base import Tool, ToolResult


class GetVersionInfoTool(Tool):
    name = "get_version_info"
    description = "按版本名称精确匹配，获取版本描述与测试起止时间。入参：version_name（字符串）。"
    parameters = {"version_name": "string"}

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    def run(self, version_name: str = "", **kwargs) -> ToolResult:
        if self.cfg.zentao.mode == "mock":
            from . import mock_zentao
            return mock_zentao.fetch_version(version_name, self.cfg.mock.fault)
        return self._run_live(version_name)

    def _run_live(self, version_name: str) -> ToolResult:
        # 接真实禅道 RESTful API：账号密码换 token → 拆「产品名 + 版本号」→ 按产品名关键字匹配 product。
        # 禅道把“版本”建模为缺陷的 openedBuild（影响版本），产品为 UniResearch。
        # 只读（F7）：仅 GET，无任何写操作。
        from . import zentao_client as zc

        keyword, build = zc.parse_version(version_name)
        try:
            client = zc.ZentaoRestClient(self.cfg)
            product = client.find_product(keyword)
        except zc.ZentaoError as exc:
            status = "timeout" if exc.kind == "timeout" else ("not_found" if exc.kind == "not_found" else "error")
            return ToolResult(ok=False, error=str(exc), meta={"fetch_status": status})

        if product is None:
            return ToolResult(ok=False, error=f"未找到产品「{keyword}」", meta={"fetch_status": "not_found"})

        z = self.cfg.zentao
        # 报告标题用用户请求的版本全名；测试起止时间取配置占位（§10：禅道 REST 无干净的产品版本日期字段）
        return ToolResult(ok=True, data={
            "name": version_name,
            "description": str(product.get(z.field_product_desc, "") or ""),
            "test_start": z.test_start,
            "test_end": z.test_end,
            "product_id": product.get("id"),
            "build": build or "",
            "fetch_status": "ok",
        })
