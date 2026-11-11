"""E2E 场景测试（对应需求文档 8.1 的 V1-xxx）。

统一用确定性规划器（关闭 LLM），保证离线可复现。
"""
from report_agent.agent.loop import ReportAgent
from report_agent.agent.planner import build_planner
from report_agent.config import load_config
from report_agent.llm.client import LLMClient
from report_agent.main import build_registry
from report_agent.trace.tracer import Tracer

BASE = {"llm.use_llm_planner": False, "zentao.mode": "mock", "trace.enabled": False, "charts.enabled": False}


def _run(user_input, **overrides):
    cfg = load_config(**{**BASE, **overrides})
    registry = build_registry(cfg)
    planner = build_planner(cfg, LLMClient(cfg), registry.specs())
    agent = ReportAgent(cfg, registry, planner, Tracer(enabled=False))
    return agent.run(user_input)


def test_v1_001_normal():
    state = _run("帮我生成 UniResearch V5.0.0 的测试报告", **{"mock.fault": "none"})
    assert state.status == "finished"
    assert state.stats is not None
    # 报告章节完整
    md = state.report_markdown
    for sec in ["## 1. 引言", "## 2. 测试概要", "## 3. 执行结果", "## 4. 功能测试", "## 5. 测试结论", "## 6. 缺陷清单"]:
        assert sec in md
    # 统计数值与源数据一致（mock 24 条）
    assert state.stats["summary"]["total"] == 24


def test_v1_002_version_not_found():
    state = _run("UniResearch V9.9.9", **{"mock.fault": "not_found"})
    assert state.status == "failed"
    assert "获取禅道数据失败，无法完成缺陷统计" in state.report_markdown


def test_v1_003_timeout():
    state = _run("UniResearch V5.0.0", **{"mock.fault": "timeout"})
    assert state.status == "failed"
    assert "获取禅道数据失败" in state.report_markdown


def test_v1_004_empty_bugs():
    state = _run("UniResearch V5.0.0", **{"mock.fault": "empty_bugs"})
    assert state.status == "finished"
    assert state.stats["summary"]["total"] == 0
    assert "未发现缺陷" in state.report_markdown


def test_v1_005_step_limit():
    state = _run("UniResearch V5.0.0", **{"mock.fault": "none", "agent.max_steps": 2})
    assert state.status == "step_limit"
    assert "执行步数超限，报告生成不完整" in state.report_markdown


def test_v1_007_missing_version_name():
    state = _run("帮我生成测试报告")
    assert state.status == "clarify"
