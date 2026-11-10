"""F1 版本基础信息获取工具。"""
from __future__ import annotations

from .base import Tool, ToolResult


class GetVersionInfoTool(Tool):
    name = "get_version_info"
    description = "按版本名称获取版本基础信息与所属产品（多项目：按真实产品名在整句输入里识别）。入参：version_name。"
    parameters = {"version_name": "string", "user_input": "string(注入，供多项目产品识别)"}

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    def run(self, version_name: str = "", user_input: str = "", **kwargs) -> ToolResult:
        if self.cfg.zentao.mode == "mock":
            from . import mock_zentao
            return mock_zentao.fetch_version(version_name, self.cfg.mock.fault)
        return self._run_live(version_name, (user_input or "").strip())

    def _run_live(self, version_name: str, user_input: str = "") -> ToolResult:
        # 接真实禅道 RESTful API：多项目不靠正则猜产品，而是拉 /products（运行内缓存）用
        # 真实产品名在整句输入里做包含匹配；版本建模为缺陷的 openedBuild（兼容中文前缀如理工1.0.0）。
        # 只读（F7）：仅 GET，无任何写操作。
        from . import zentao_client as zc

        text = user_input or version_name
        # 版本：优先从精简的 version_name 取，取不到再从整句取
        build = zc.extract_build(version_name) or zc.extract_build(text)
        try:
            client = zc.ZentaoRestClient(self.cfg)
            product = client.find_product(text) or client.find_product(self.cfg.zentao.default_product)
        except zc.ZentaoError as exc:
            status = "timeout" if exc.kind == "timeout" else ("not_found" if exc.kind == "not_found" else "error")
            return ToolResult(ok=False, error=str(exc), meta={"fetch_status": status})

        if product is None:
            return ToolResult(ok=False, error=f"未从输入中识别到禅道已有产品：{text}", meta={"fetch_status": "not_found"})

        z = self.cfg.zentao
        product_name = str(product.get(z.field_product_name, "")).strip()
        # 项目负责人：仅当配置了 field_product_owner 时从产品对象取（取到人名、取不到留空）；不默认猜测字段，避免张冠李戴。
        project_owner = zc.person_name(product.get(z.field_product_owner)) if z.field_product_owner else ""
        # 报告标题用用户请求的版本全名；测试起止时间取配置占位（§10：禅道 REST 无干净的产品版本日期字段）
        return ToolResult(ok=True, data={
            "name": version_name,
            "description": str(product.get(z.field_product_desc, "") or ""),
            "test_start": z.test_start,
            "test_end": z.test_end,
            "product_id": product.get("id"),
            "product_name": product_name,
            "project_owner": project_owner,
            "build": build or "",
            "fetch_status": "ok",
        })
