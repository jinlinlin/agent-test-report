# -*- coding: utf-8 -*-
"""V2-1 意图三态分类单测（纯函数，不触 loop/工具/网络）。

覆盖 classify_input：完整/只给项目/半截版本/全无线索四态，以及 resolved_version 拼接不重复项目词。
开关 agent.interactive_clarify 默认关时 loop 不调用本函数，故本步不改动任何既有行为。
"""
from report_agent.agent.planner import classify_input

PRODUCTS = ["UniResearch", "一表通"]


def test_complete_full_name_resolves_directly():
    i = classify_input("帮我生成 UniResearch V5.0.0 的测试报告", PRODUCTS)
    assert i.kind == "complete"
    assert i.product_hint == "UniResearch"
    assert i.version_hint == "V5.0.0"
    # 关键回归点：resolved_version 不能把项目词重复一次
    assert i.resolved_version == "UniResearch V5.0.0"


def test_only_project_is_incomplete():
    i = classify_input("UniResearch 出个报告", PRODUCTS)
    assert i.kind == "incomplete"
    assert i.product_hint == "UniResearch"
    assert i.version_hint == ""


def test_partial_version_is_incomplete():
    i = classify_input("出 V5 的报告", PRODUCTS)
    assert i.kind == "incomplete"
    assert i.version_hint == "V5"


def test_nothing_is_missing():
    i = classify_input("帮我出个测试报告", PRODUCTS)
    assert i.kind == "missing"
    assert i.product_hint == ""
    assert i.version_hint == ""


def test_version_without_project_is_incomplete():
    i = classify_input("生成 V6.1.0 的报告", PRODUCTS)
    assert i.kind == "incomplete"
    assert i.product_hint == ""
    assert i.version_hint == "V6.1.0"


# ---- 交互澄清端到端（开关开启，脚本化 ask 注入答复）----
from report_agent.agent.loop import ReportAgent
from report_agent.agent.planner import build_planner
from report_agent.config import load_config
from report_agent.llm.client import LLMClient
from report_agent.main import build_registry
from report_agent.trace.tracer import Tracer

OFF_BASE = {"llm.use_native_fc": False, "zentao.mode": "mock", "trace.enabled": False,
            "charts.enabled": False, "feishu.enabled": False, "report.polish_conclusion": False}
ON_BASE = {**OFF_BASE, "agent.interactive_clarify": True}


class _Ask:
    def __init__(self, answers):
        self._answers = list(answers)
        self.calls = []

    def __call__(self, question, options):
        self.calls.append((question, list(options)))
        idx = len(self.calls) - 1
        return self._answers[idx] if idx < len(self._answers) else ""


def _build(ask, interactive=True):
    cfg = load_config(**(ON_BASE if interactive else OFF_BASE))
    registry = build_registry(cfg)
    planner = build_planner(cfg, LLMClient(cfg), registry.specs())
    return ReportAgent(cfg, registry, planner, Tracer(enabled=False), ask=ask)


def test_interactive_complete_asks_nothing():
    ask = _Ask([])
    st = _build(ask).run("帮我生成 UniResearch V5.0.0 的测试报告")
    assert st.status == "finished"
    assert st.version_name == "UniResearch V5.0.0"
    assert ask.calls == []            # 无歧义不多问


def test_interactive_only_project_answers_latest():
    ask = _Ask(["最新的"])           # 只给项目→列候选→“最新的”由程序钉死为 V6.1.0
    st = _build(ask).run("UniResearch 出个报告")
    assert st.status == "finished"
    assert st.version_name == "UniResearch V6.1.0"
    assert len(ask.calls) == 1


def test_interactive_both_missing_two_rounds():
    ask = _Ask(["2", "1"])          # 先问项目(选第2个一表通项目)→再问版本(选第1个 V1.3.0)
    st = _build(ask).run("帮我出个测试报告")
    assert st.status == "finished"
    assert st.version_name == "一表通项目 V1.3.0"
    assert len(ask.calls) == 2
    assert st.clarify_count == 2


def test_interactive_clarify_count_within_limit():
    ask = _Ask(["UniResearch", "V5.0.0"])   # 项目可识别→再按版本名选定
    st = _build(ask).run("出个报告吧")
    assert st.status == "finished"
    assert st.version_name == "UniResearch V5.0.0"


def test_interactive_on_but_no_ask_handler_degrades():
    st = _build(ask=None).run("UniResearch 出个报告")   # 开启但无交互入口→优雅退回 clarify
    assert st.status == "clarify"
    assert "交互入口" in st.fail_reason


def test_interactive_off_behaves_identical_to_v1():
    ask = _Ask(["最新的"])                      # 开关关时 ask 不应被调
    st = _build(ask, interactive=False).run("UniResearch 出个报告")
    assert st.status == "clarify"              # 二态抽取不到完整版本→一次性 clarify
    assert ask.calls == []
