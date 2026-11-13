# -*- coding: utf-8 -*-
"""V2-1 澄清候选工具：list_version_candidates。

按项目关键字列出该项目全部候选版本，供模糊输入时反问用户。核心口径：
- 「最新」= 版本创建时间最晚，由程序排序钉死，模型只负责把自然语言答复映射到确定候选；
- 只读，不写禅道、不改 state；候选 >1 时上层中断反问，=1 直接用，=0 走现行「版本不存在」。
mock 直接用 MOCK_VERSIONS 的 build_created 排序；live 因禅道 REST 无独立 builds 接口
（见 V1-002），降级为缺陷 openedBuild 取值集合去重 + 版本号语义倒序。
"""
from __future__ import annotations

import re

from .base import Tool, ToolResult

_VERSION_DIGITS = re.compile(r"\d+")


def _version_sort_key(build: str) -> tuple:
    """版本号语义排序键：抽取全部数字段做元组（V6.1.0 -> (6,1,0)）。取不到数字排最后。"""
    nums = tuple(int(x) for x in _VERSION_DIGITS.findall(build or ""))
    return nums if nums else ()


class ListVersionCandidatesTool(Tool):
    name = "list_version_candidates"
    description = (
        "按项目关键字列出该项目全部候选版本，按版本创建时间倒序（最新在前）。"
        "用于用户只给项目名或版本号不完整时反问候选。入参：product_hint（项目名或关键字）。"
    )
    parameters = {"product_hint": "string"}

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    def run(self, product_hint: str = "", **kwargs) -> ToolResult:
        if self.cfg.zentao.mode == "mock":
            from . import mock_zentao
            return mock_zentao.list_candidates(product_hint)
        return self._run_live(product_hint)

    def _run_live(self, product_hint: str) -> ToolResult:
        from . import zentao_client as zc

        try:
            client = zc.ZentaoRestClient(self.cfg)
            product = client.find_product(product_hint) or client.find_product(self.cfg.zentao.default_product)
        except zc.ZentaoError as exc:
            status = "timeout" if exc.kind == "timeout" else ("not_found" if exc.kind == "not_found" else "error")
            return ToolResult(ok=False, error=str(exc), meta={"fetch_status": status})

        if product is None:
            return ToolResult(ok=True, data={"product": "", "candidates": []})

        prod_name = str(product.get(self.cfg.zentao.field_product_name, "")).strip()
        try:
            bugs = client.get_product_bugs(product.get("id"))
        except zc.ZentaoError as exc:
            return ToolResult(ok=False, error=str(exc), meta={"fetch_status": "error"})

        # 该禅道 REST 无 builds 接口（V1-002），以缺陷 openedBuild 取值集合为版本候选；
        # 无法取真实 build 创建时间，降级为版本号语义倒序，「最新」取版本号最大者。
        builds = set()
        field_build = self.cfg.zentao.field_build
        for b in bugs:
            for token in str(b.get(field_build, "") or "").split(","):
                t = token.strip()
                if t:
                    builds.add(t)

        ordered = sorted(builds, key=_version_sort_key, reverse=True)
        cands = [{"name": f"{prod_name} {b}", "build": b, "created": ""} for b in ordered]
        return ToolResult(ok=True, data={"product": prod_name, "candidates": cands})
