# -*- coding: utf-8 -*-
"""Mock 数据源契约单测（V1 尾账：product_name/build 与 live 返回结构对齐）。

意义：飞书多项目落点路由（push_feishu._resolve_parent）与参考文档检索
（render_report._resolve_ref_links → F12）分别以 version_info 的 product_name、
build 非空为前提；live 一直提供这两字段而 mock 缺失，导致这些分支离线不可测。
本文件把「mock 必须携带路由字段」钉为回归不变量。
"""
from report_agent.agent.loop import ReportAgent
from report_agent.agent.planner import build_planner
from report_agent.config import load_config
from report_agent.llm.client import LLMClient
from report_agent.main import build_registry
from report_agent.trace.tracer import Tracer
from report_agent.tools.mock_zentao import MOCK_VERSIONS, fetch_version

BASE = {"llm.use_native_fc": False, "zentao.mode": "mock", "trace.enabled": False, "charts.enabled": False}


def test_mock_versions_carry_routing_fields():
    for name, info in MOCK_VERSIONS.items():
        assert info.get("product_name"), f"mock 版本 {name} 缺少 product_name，离线无法覆盖多项目路由分支"
        assert info.get("build"), f"mock 版本 {name} 缺少 build，离线无法覆盖按版本目录检索分支"


def test_fetch_version_returns_live_aligned_fields():
    res = fetch_version("帮我生成 UniResearch V5.0.0 的测试报告")
    assert res.ok
    assert res.data["product_name"] == "UniResearch"
    assert res.data["build"] == "V5.0.0"
    assert {"name", "description", "test_start", "test_end", "product_name", "build", "fetch_status"} <= set(res.data)


def test_loop_state_carries_routing_fields():
    """完整一轮后 state.version_info 应携带非空路由字段（守卫分支离线可达）。"""
    cfg = load_config(**BASE)
    registry = build_registry(cfg)
    planner = build_planner(cfg, LLMClient(cfg), registry.specs())
    agent = ReportAgent(cfg, registry, planner, Tracer(enabled=False))
    state = agent.run("帮我生成 UniResearch V5.0.0 的测试报告")
    assert state.status == "finished"
    assert state.version_info.get("product_name") == "UniResearch"
    assert state.version_info.get("build") == "V5.0.0"
